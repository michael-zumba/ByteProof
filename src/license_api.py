"""Desktop client for the ByteProof license service.

The service (see ``server/``) owns the Stripe-side records: it turns a paid
checkout into one license key, registers up to two computers against that key,
and issues the signed, machine-bound license the app stores. This module is
the only place the app talks to it.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

import certifi

from .settings import APP_NAME, LICENSE_API_URL

REQUEST_TIMEOUT = 30


class LicenseApiError(Exception):
    """A license-service call failed; ``str(error)`` is user-readable."""


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {}
        detail = (
            parsed.get("detail")
            or parsed.get("error")
            or parsed.get("message")
            or body
        )
        if isinstance(detail, list):
            detail = "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in detail
            )
        text = str(detail).strip()
        return text[:400] if text else f"The license service returned {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return (
            "Cannot reach the ByteProof license service. Check your internet "
            "connection and try again."
        )
    return f"License request failed: {exc}"


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = LICENSE_API_URL.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}-License/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except Exception as exc:
        raise LicenseApiError(_error_text(exc)) from exc


def machine_fingerprint() -> str:
    from .licensing import _get_machine_fingerprint

    return _get_machine_fingerprint()


def activate_key(key: str, label: str = "") -> dict[str, Any]:
    """Register this computer for a license key."""
    return _post(
        "/api/byteproof/activate",
        {
            "key": key.strip(),
            "machine_fingerprint": machine_fingerprint(),
            "label": label or "ByteProof",
        },
    )


def activate_session(session_id: str, label: str = "") -> dict[str, Any]:
    """Activate straight from a Stripe checkout link."""
    return _post(
        "/api/byteproof/activate",
        {
            "session_id": session_id.strip(),
            "machine_fingerprint": machine_fingerprint(),
            "label": label or "ByteProof",
        },
    )


def validate_key(key: str, fingerprint: str = "") -> dict[str, Any]:
    return _post(
        "/api/byteproof/validate",
        {
            "key": key.strip(),
            "machine_fingerprint": fingerprint or machine_fingerprint(),
        },
    )


def deactivate_key(key: str, fingerprint: str = "") -> dict[str, Any]:
    return _post(
        "/api/byteproof/deactivate",
        {
            "key": key.strip(),
            "machine_fingerprint": fingerprint or machine_fingerprint(),
        },
    )


def request_portal_link(email: str) -> dict[str, Any]:
    return _post("/api/byteproof/portal/request", {"email": email.strip()})
