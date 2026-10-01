"""License activation for ByteProof.

Payments run through Stripe Checkout; the ByteMind license service issues one
key per purchase and enforces the 2-computer limit. The app sends its machine
fingerprint to that service, stores the signed machine-bound license it
returns, and can deactivate the computer to free a slot.

Developer access is unchanged: a known developer email unlocks full access
only when this machine has an explicit local configuration (see
``settings.developer_emails``), so a published address can never unlock a
shipped build.
"""

from __future__ import annotations

import os
import platform
import urllib.parse
from typing import Any

from . import license_api
from .licensing import (
    _get_machine_fingerprint,
    activate_dev_license,
    activate_license,
    activate_service_license,
    delete_license_data,
    get_license_info,
)
from .settings import SUPPORT_EMAIL, developer_emails

URL_SCHEME = "byteproof"


def _looks_like_email(value: str) -> bool:
    return value.count("@") == 1 and "." in value.split("@")[1]


def _machine_label() -> str:
    return platform.node() or "ByteProof"


def _store_service_result(
    result: dict[str, Any],
    purchase_key: str = "",
) -> dict[str, Any]:
    stored = activate_service_license(result, purchase_key=purchase_key)
    if not stored.get("valid"):
        return {"ok": False, "error": stored.get("error") or "Activation failed."}
    return {
        "ok": True,
        "email": stored.get("email", ""),
        "key_display": stored.get("key_display", ""),
    }


def activate_with_key(value: str) -> dict[str, Any]:
    """Activate with a license key from the purchase receipt email."""
    value = value.strip()
    if not value:
        return {"ok": False, "error": "Please enter your license key."}

    if _looks_like_email(value):
        email = value.lower()
        if email in {e.lower() for e in developer_emails()}:
            result = activate_dev_license(email)
            if not result.get("valid"):
                return {
                    "ok": False,
                    "error": result.get("error") or "Activation failed.",
                }
            return {"ok": True, "email": email}
        return {
            "ok": False,
            "error": (
                "That looks like an email address, not a license key. Your "
                "key is in the receipt email from your purchase - it starts "
                "with \"BYTP-\" (or use the button in that email to activate "
                "this computer). Need help? Email "
                f"{SUPPORT_EMAIL} and include your receipt."
            ),
        }

    if "|" in value:
        # A pre-2026 signed key from the old ByteMind server: those customers
        # keep working without the license service, exactly as before.
        legacy = activate_license(value)
        if legacy.get("valid"):
            return {"ok": True, "email": legacy.get("email", "")}
        return {
            "ok": False,
            "error": legacy.get("error") or "Invalid license key.",
        }

    try:
        result = license_api.activate_key(value, label=_machine_label())
    except license_api.LicenseApiError as exc:
        return {"ok": False, "error": str(exc)}
    return _store_service_result(
        result, purchase_key=str(result.get("key") or value)
    )


def activate_with_session(session_id: str) -> dict[str, Any]:
    """Activate straight from a checkout link (``?session=…``)."""
    session_id = session_id.strip()
    if not session_id:
        return {"ok": False, "error": "This activation link is incomplete."}
    try:
        result = license_api.activate_session(session_id, label=_machine_label())
    except license_api.LicenseApiError as exc:
        return {"ok": False, "error": str(exc)}
    return _store_service_result(result, purchase_key=str(result.get("key") or ""))


def activate_with_email(email: str) -> dict[str, Any]:
    """Compatibility entry point; email input never unlocks a customer build."""
    return activate_with_key(email)


def deactivate_license() -> dict[str, Any]:
    """Free this computer's slot, then remove the local license.

    If the service cannot be reached, the local license is still removed (the
    customer can release the slot later from the license portal), so a network
    problem never traps them.
    """
    info = get_license_info()
    if info.get("status") != "licensed":
        return {"ok": False, "error": "No active license found on this computer."}

    if info.get("provider") == "stripe":
        key = info.get("purchase_key") or info.get("raw_key") or ""
        if key:
            try:
                license_api.deactivate_key(key, _get_machine_fingerprint())
            except license_api.LicenseApiError as exc:
                message = str(exc)
                if "not registered" not in message.lower() and (
                    "cannot reach" not in message.lower()
                ):
                    return {"ok": False, "error": message}

    delete_license_data()
    return {"ok": True, "email": info.get("email", "")}


def validate_license_remote() -> dict[str, Any]:
    """Best-effort online validation of the current license.

    A network failure is reported as ``ok: False`` but never locks a working
    license; only an explicit revocation does.
    """
    info = get_license_info()
    if info.get("status") != "licensed":
        return {"ok": False, "error": "No active license found on this computer."}

    provider = info.get("provider", "")
    if provider == "stripe":
        key = info.get("purchase_key") or ""
        if not key:
            return {"ok": True, "provider": provider}
        try:
            result = license_api.validate_key(key, _get_machine_fingerprint())
        except license_api.LicenseApiError as exc:
            return {"ok": False, "error": str(exc)}
        if result.get("revoked"):
            return {
                "ok": False,
                "revoked": True,
                "error": (
                    "This license was refunded or revoked. Contact "
                    f"{SUPPORT_EMAIL} if you think this is a mistake."
                ),
            }
        if not result.get("valid"):
            return {
                "ok": False,
                "error": result.get("error")
                or "This license is not valid on this computer.",
            }
        return {"ok": True, "status": "valid"}

    # Developer, legacy signed, and retired Polar records are all local-only
    # now; nothing to check online.
    return {"ok": True, "provider": provider}


def activate_from_url(url: str) -> dict[str, Any]:
    """Handle ``byteproof://activate?key=…`` and ``?session=…`` links.

    Only a real license key (or a Stripe checkout session) activates; the app
    still asks the user to confirm before a web page can trigger activation.
    """
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != URL_SCHEME:
        return {"ok": False, "error": "This is not a ByteProof activation link."}
    params = urllib.parse.parse_qs(parsed.query)

    key = (params.get("key") or [""])[0].strip()
    if key:
        return activate_with_key(key)

    session_id = (params.get("session") or [""])[0].strip()
    if session_id:
        return activate_with_session(session_id)

    return {
        "ok": False,
        "error": "The activation link is missing a license key.",
    }


def register_url_scheme() -> None:
    """Register the byteproof:// URL scheme on Windows (macOS uses the bundle)."""
    if platform.system() != "Windows":
        return
    try:
        import sys
        import winreg

        exe = os.path.abspath(sys.executable)
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\byteproof\shell\open\command",
        )
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')
        winreg.CloseKey(key)
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\byteproof",
        )
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:ByteProof Activation")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(key)
    except Exception:
        pass
