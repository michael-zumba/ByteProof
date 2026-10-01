"""Test doubles and payload builders for the license-service suite."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any

import stripe

WEBHOOK_SECRET = "whsec_test_secret"
KEY_SECRET = "test-key-secret"


class FakeGateway:
    """A stand-in for StripeGateway backed by in-memory sessions."""

    def __init__(self, sessions: dict[str, dict[str, Any]] | None = None) -> None:
        self.sessions = sessions or {}
        self.configured = True

    def add(self, session: dict[str, Any]) -> None:
        self.sessions[str(session["id"])] = session

    def retrieve_session(self, session_id: str) -> dict[str, Any]:
        if session_id not in self.sessions:
            raise stripe.InvalidRequestError("No such checkout session", "id")
        return self.sessions[session_id]

    def retrieve_charge(self, charge_id: str) -> dict[str, Any]:  # pragma: no cover
        raise stripe.InvalidRequestError("No such charge", "id")

    def list_sessions(self, created_gte: int | None = None) -> Iterator[dict[str, Any]]:
        return iter(self.sessions.values())


def paid_session(
    session_id: str = "cs_test_paid_1",
    email: str = "buyer@example.com",
    payment_intent: str = "pi_test_1",
    payment_status: str = "paid",
) -> dict[str, Any]:
    return {
        "id": session_id,
        "payment_status": payment_status,
        "customer_details": {"email": email},
        "customer_email": None,
        "payment_intent": payment_intent,
        "amount_total": 4900,
        "currency": "nzd",
    }


def webhook_event(
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
) -> tuple[bytes, str]:
    payload = json.dumps(
        {"id": event_id, "type": event_type, "data": {"object": obj}}
    ).encode("utf-8")
    timestamp = int(time.time())
    signed = f"{timestamp}.{payload.decode('utf-8')}"
    signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), signed.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return payload, f"t={timestamp},v1={signature}"
