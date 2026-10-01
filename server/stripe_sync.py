"""Stripe reads for the license service.

The purchase ledger lives in Stripe; this module only reads from it. It is
also the recovery path: if the license database is ever lost, reconciling
paid Checkout Sessions rebuilds every license row (keys are derived from the
session id, so they never need to be stored to be recovered).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import stripe

from .license_keys import derive_license_key


class StripeReader(Protocol):
    """The slice of Stripe the license service needs (fakeable in tests)."""

    configured: bool

    def retrieve_session(self, session_id: str) -> dict[str, Any]: ...

    def retrieve_charge(self, charge_id: str) -> dict[str, Any]: ...

    def list_sessions(
        self, created_gte: int | None = None
    ) -> Iterator[dict[str, Any]]: ...


def _plain(value: Any) -> Any:
    """Convert Stripe objects (and containers) to plain Python data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    if hasattr(value, "items"):
        return {key: _plain(item) for key, item in value.items()}
    return value


def is_paid_session(session: dict[str, Any]) -> bool:
    """True when a Checkout Session is settled enough to fulfil.

    ``no_payment_required`` is what Stripe reports for a session that costs
    nothing after a 100%-off promotion code, which must still get a licence.
    """
    return str(session.get("payment_status") or "") in (
        "paid",
        "no_payment_required",
    )


def session_email(session: dict[str, Any]) -> str:
    details = session.get("customer_details") or {}
    email = details.get("email") or session.get("customer_email") or ""
    return str(email).strip().lower()


def license_from_session(
    session: dict[str, Any],
    *,
    secret: str,
    device_limit: int,
) -> dict[str, Any] | None:
    """Turn a paid Checkout Session into license-row fields."""
    session_id = str(session.get("id") or "")
    if not session_id or not is_paid_session(session):
        return None
    return {
        "key": derive_license_key(session_id, secret),
        "session_id": session_id,
        "email": session_email(session),
        "payment_intent": session.get("payment_intent") or None,
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "device_limit": device_limit,
    }


class StripeGateway:
    """Thin, plain-data wrapper around the Stripe SDK."""

    def __init__(self, api_key: str) -> None:
        self._client = stripe.StripeClient(api_key) if api_key else None

    @property
    def configured(self) -> bool:
        return self._client is not None

    def retrieve_session(self, session_id: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Stripe is not configured on this server.")
        session = self._client.v1.checkout.sessions.retrieve(session_id)
        return _plain(session)

    def retrieve_charge(self, charge_id: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Stripe is not configured on this server.")
        return _plain(self._client.v1.charges.retrieve(charge_id))

    def list_sessions(
        self, created_gte: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every Checkout Session, newest first (all pages)."""
        if self._client is None:
            raise RuntimeError("Stripe is not configured on this server.")
        params: dict[str, Any] = {"limit": 100}
        if created_gte is not None:
            params["created"] = {"gte": int(created_gte)}
        for session in self._client.v1.checkout.sessions.list(params).auto_paging_iter():
            yield _plain(session)
