"""SQLite storage for licenses, activations, portal links and webhook events.

One connection per call keeps the FastAPI threadpool honest without leaning
on a shared connection; WAL mode lets the nightly reconcile read while a
webhook writes.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses (
    key            TEXT PRIMARY KEY,
    session_id     TEXT UNIQUE,
    payment_intent TEXT,
    email          TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT 'stripe',
    revoked        INTEGER NOT NULL DEFAULT 0,
    revoked_at     INTEGER,
    device_limit   INTEGER,
    created_at     INTEGER NOT NULL,
    fulfilled_at   INTEGER,
    emailed_at     INTEGER,
    amount_total   INTEGER,
    currency       TEXT
);
CREATE INDEX IF NOT EXISTS idx_licenses_email ON licenses(email);
CREATE INDEX IF NOT EXISTS idx_licenses_intent ON licenses(payment_intent);

CREATE TABLE IF NOT EXISTS activations (
    key        TEXT NOT NULL,
    machine_fp TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    PRIMARY KEY (key, machine_fp)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    received_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_tokens (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LicenseStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # -- plumbing -----------------------------------------------------------

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    # -- licenses -----------------------------------------------------------

    def upsert_license(
        self,
        *,
        key: str,
        session_id: str | None,
        email: str,
        source: str = "stripe",
        device_limit: int | None = None,
        payment_intent: str | None = None,
        amount_total: int | None = None,
        currency: str | None = None,
    ) -> bool:
        """Insert a license if new; returns True when it was created."""
        now = int(time.time())
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT key FROM licenses WHERE key = ?", (key,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE licenses
                       SET email = COALESCE(NULLIF(?, ''), email),
                           payment_intent = COALESCE(?, payment_intent),
                           amount_total = COALESCE(?, amount_total),
                           currency = COALESCE(?, currency)
                     WHERE key = ?
                    """,
                    (email, payment_intent, amount_total, currency, key),
                )
                return False
            conn.execute(
                """
                INSERT INTO licenses (
                    key, session_id, payment_intent, email, source,
                    device_limit, created_at, fulfilled_at,
                    amount_total, currency
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    session_id,
                    payment_intent,
                    email,
                    source,
                    device_limit,
                    now,
                    now,
                    amount_total,
                    currency,
                ),
            )
            return True

    def get_license(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row(
                conn.execute(
                    "SELECT * FROM licenses WHERE key = ?", (key,)
                ).fetchone()
            )

    def get_license_by_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row(
                conn.execute(
                    "SELECT * FROM licenses WHERE session_id = ?", (session_id,)
                ).fetchone()
            )

    def licenses_for_email(self, email: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM licenses
                 WHERE lower(email) = lower(?)
                 ORDER BY created_at DESC
                """,
                (email.strip(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_licenses(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM licenses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_emailed(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE licenses SET emailed_at = ? WHERE key = ?",
                (int(time.time()), key),
            )

    def set_revoked(self, key: str, revoked: bool) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE licenses SET revoked = ?, revoked_at = ? WHERE key = ?",
                (1 if revoked else 0, int(time.time()) if revoked else None, key),
            )
            return cursor.rowcount > 0

    def revoke_by_payment_intent(self, payment_intent: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE licenses
                   SET revoked = 1, revoked_at = ?
                 WHERE payment_intent = ? AND revoked = 0
                """,
                (int(time.time()), payment_intent),
            )
            return cursor.rowcount

    # -- activations --------------------------------------------------------

    def activations(self, key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM activations
                 WHERE key = ?
                 ORDER BY created_at
                """,
                (key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def register_activation(
        self,
        key: str,
        machine_fp: str,
        label: str,
        device_limit: int | None,
    ) -> tuple[bool, str | None, int]:
        """Register a computer; returns (ok, error, device_count).

        Re-registering the same fingerprint is idempotent and never consumes
        a second slot. The limit is checked inside an IMMEDIATE transaction so
        two simultaneous activations cannot both take the last seat.
        """
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            same = conn.execute(
                "SELECT 1 FROM activations WHERE key = ? AND machine_fp = ?",
                (key, machine_fp),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM activations WHERE key = ?", (key,)
            ).fetchone()["n"]
            if same:
                conn.execute(
                    """
                    UPDATE activations
                       SET last_seen = ?, label = COALESCE(NULLIF(?, ''), label)
                     WHERE key = ? AND machine_fp = ?
                    """,
                    (now, label, key, machine_fp),
                )
                return True, None, count
            if device_limit is not None and count >= device_limit:
                return False, "device_limit", count
            conn.execute(
                """
                INSERT INTO activations (key, machine_fp, label, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, machine_fp, label, now, now),
            )
            return True, None, count + 1

    def deactivate(self, key: str, machine_fp: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM activations WHERE key = ? AND machine_fp = ?",
                (key, machine_fp),
            )
            return cursor.rowcount > 0

    # -- webhook idempotency ------------------------------------------------

    def record_event(self, event_id: str, event_type: str) -> bool:
        """Record an event id; False when this delivery is a duplicate."""
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO webhook_events (id, type, received_at)
                    VALUES (?, ?, ?)
                    """,
                    (event_id, event_type, int(time.time())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    # -- portal tokens ------------------------------------------------------

    def create_portal_token(self, email: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portal_tokens (token_hash, email, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (_hash_token(token), email.strip().lower(), now, now + ttl_seconds),
            )
        return token

    def portal_token_email(self, token: str) -> str | None:
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT email, expires_at FROM portal_tokens
                 WHERE token_hash = ?
                """,
                (_hash_token(token),),
            ).fetchone()
        if not row or int(row["expires_at"]) < int(time.time()):
            return None
        return str(row["email"])

    # -- maintenance --------------------------------------------------------

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("VACUUM INTO ?", (str(destination),))

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            licenses = conn.execute("SELECT COUNT(*) AS n FROM licenses").fetchone()["n"]
            activations = conn.execute(
                "SELECT COUNT(*) AS n FROM activations"
            ).fetchone()["n"]
        return {"licenses": licenses, "activations": activations}
