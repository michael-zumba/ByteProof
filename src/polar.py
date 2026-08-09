"""Polar license-key integration (the VoiceInk licensing approach).

Polar is the payment + licensing provider:
- Customers pay through a Polar checkout page.
- Polar emails them a license key and shows it in their customer portal.
- The app activates the key with this computer's fingerprint (Polar enforces
  the activation limit, e.g. 2 devices, on its durable servers).

The app calls Polar's public customer-portal endpoints directly, so no
custom activation server is required.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

import certifi

from .licensing import _get_machine_fingerprint
from .settings import APP_NAME, POLAR_API_URL, POLAR_ORGANIZATION_ID


class PolarError(Exception):
    pass


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not POLAR_ORGANIZATION_ID:
        raise PolarError(
            "ByteProof is not yet configured for license activation. "
            "Contact ByteMind support."
        )
    url = POLAR_API_URL.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}-Activation/1.0",
        },
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail") or body
        except Exception:
            detail = body
        raise PolarError(f"Polar error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise PolarError(f"Cannot reach Polar: {exc.reason}") from exc


def activate_key(license_key: str, label: str = "ByteProof") -> dict[str, Any]:
    """Register this computer as an activation for the license key."""
    machine_fp = _get_machine_fingerprint()
    data = _post(
        "/v1/customer-portal/license-keys/activate",
        {
            "key": license_key.strip(),
            "organization_id": POLAR_ORGANIZATION_ID,
            "label": label,
            "conditions": {"machine_fingerprint": machine_fp},
            "meta": {"machine_fingerprint": machine_fp},
        },
    )
    # The activate endpoint returns the activation object itself at the top
    # level; tolerate a nested "activation" shape as well.
    activation = data if data.get("id") else (data.get("activation") or {})
    license_key_info = data.get("license_key") or {}
    return {
        "ok": True,
        "key": license_key.strip(),
        "activation_id": activation.get("id"),
        "status": license_key_info.get("status"),
        "limit_activations": license_key_info.get("limit_activations"),
        "expires_at": license_key_info.get("expires_at"),
    }


def validate_key(license_key: str, activation_id: str) -> dict[str, Any]:
    """Validate this activation with Polar."""
    return _post(
        "/v1/customer-portal/license-keys/validate",
        {
            "key": license_key,
            "organization_id": POLAR_ORGANIZATION_ID,
            "activation_id": activation_id,
            "conditions": {"machine_fingerprint": _get_machine_fingerprint()},
        },
    )


def deactivate_key(license_key: str, activation_id: str) -> dict[str, Any]:
    """Free this computer's activation slot on Polar."""
    return _post(
        "/v1/customer-portal/license-keys/deactivate",
        {
            "key": license_key,
            "organization_id": POLAR_ORGANIZATION_ID,
            "activation_id": activation_id,
        },
    )
