"""Environment-backed configuration for the ByteProof license service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _split_values(raw: str) -> tuple[str, ...]:
    return tuple(
        part.strip() for part in raw.replace(",", " ").split() if part.strip()
    )


@dataclass(frozen=True)
class Settings:
    """Runtime settings; tests build one directly, production reads the env."""

    data_dir: Path = Path("/data")
    public_base_url: str = "https://api.bytemind.co.nz"
    product_url: str = "https://www.bytemind.co.nz/byteproof"
    support_email: str = "bytemind.nz@gmail.com"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    key_secret: str = ""
    internal_keys: tuple[str, ...] = ()
    admin_token: str = ""
    device_limit: int = 2
    portal_token_ttl_seconds: int = 1800
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    resend_api_key: str = ""
    backup_email: str = ""
    reconcile_interval_seconds: int = 86400
    daily_backup: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        smtp_user = _env("BYTEPROOF_SMTP_USER")
        return cls(
            data_dir=Path(_env("BYTEPROOF_DATA_DIR") or "/data"),
            public_base_url=_env(
                "BYTEPROOF_PUBLIC_BASE_URL", "https://api.bytemind.co.nz"
            ).rstrip("/"),
            product_url=_env(
                "BYTEPROOF_PRODUCT_URL", "https://www.bytemind.co.nz/byteproof"
            ),
            support_email=_env("BYTEPROOF_SUPPORT_EMAIL", "bytemind.nz@gmail.com"),
            stripe_secret_key=_env("STRIPE_SECRET_KEY"),
            stripe_webhook_secret=_env("STRIPE_WEBHOOK_SECRET"),
            key_secret=_env("BYTEPROOF_KEY_SECRET"),
            internal_keys=_split_values(_env("BYTEPROOF_INTERNAL_KEYS")),
            admin_token=_env("BYTEPROOF_ADMIN_TOKEN"),
            device_limit=_env_int("BYTEPROOF_DEVICE_LIMIT", 2),
            portal_token_ttl_seconds=_env_int(
                "BYTEPROOF_PORTAL_TOKEN_TTL", 1800
            ),
            smtp_host=_env("BYTEPROOF_SMTP_HOST"),
            smtp_port=_env_int("BYTEPROOF_SMTP_PORT", 587),
            smtp_user=smtp_user,
            smtp_password=os.environ.get("BYTEPROOF_SMTP_PASSWORD", ""),
            smtp_from=_env("BYTEPROOF_SMTP_FROM") or smtp_user,
            smtp_tls=_env_bool("BYTEPROOF_SMTP_TLS", True),
            resend_api_key=_env("RESEND_API_KEY"),
            backup_email=_env("BYTEPROOF_BACKUP_EMAIL") or _env("BYTEPROOF_SMTP_FROM"),
            reconcile_interval_seconds=_env_int(
                "BYTEPROOF_RECONCILE_INTERVAL", 86400
            ),
            daily_backup=_env_bool("BYTEPROOF_DAILY_BACKUP", True),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "byteproof-licenses.sqlite3"

    @property
    def public_base(self) -> str:
        return self.public_base_url.rstrip("/")
