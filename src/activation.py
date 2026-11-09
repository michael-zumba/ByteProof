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
        # A licence bound before the fingerprint was made stable is moved to
        # the stable identity here, silently: this is the moment the network
        # is there and the customer is not being asked for anything.
        migrate_to_stable_fingerprint()
        info = get_license_info()
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
            if str(result.get("reason") or "") == "not_activated":
                # The server knows the key but this computer is not one of its
                # activations: the slot was released (in the portal or on
                # another computer), or this is a different machine now. The
                # license must not keep working here: a released slot has to
                # mean released, or moving a licence to a new computer could
                # never take the old one out of use. (A licence bound to the
                # *old* fingerprint is a different case, and is moved across
                # above, before this check.)
                return {
                    "ok": False,
                    "deactivated": True,
                    "error": (
                        "This computer is no longer activated for your license "
                        "key. It was released from the license, or the license "
                        "is now active on other computers instead. Enter your "
                        "key again to reactivate this computer, or release a "
                        "slot in the license portal."
                    ),
                }
            return {
                "ok": False,
                "error": result.get("error")
                or "This license is not valid on this computer.",
            }
        return {"ok": True, "status": "valid"}

    # Developer, legacy signed, and retired Polar records are all local-only
    # now; nothing to check online.
    return {"ok": True, "provider": provider}


def migrate_to_stable_fingerprint() -> dict[str, Any] | None:
    """Re-bind a stored licence to the stable machine identity.

    ByteProof's licence fingerprint used to include the machine's first
    network interface and its hostname, so a VPN, a new adapter, a virtual
    machine's regenerated MAC or a DHCP-supplied hostname changed it: the
    stored licence stopped matching, the app fell back to trial/free mode and
    the customer had to enter the key again - and each re-activation took
    another seat on the service (owner report, 2026-10-08: four activations
    for one computer).

    The licence is re-activated against the stable fingerprint and the old
    seat is released, so nothing is used up and the customer never sees it.
    Returns None when there is nothing to move or the service cannot be
    reached; the licence keeps working either way.
    """
    from .licensing import (
        _get_machine_fingerprint,
        _legacy_machine_fingerprint,
    )

    info = get_license_info()
    if info.get("status") != "licensed" or info.get("provider") != "stripe":
        return None
    stored = str(info.get("machine_fp") or "")
    stable = _get_machine_fingerprint()
    if not stored or stored == stable:
        return None
    if stored != _legacy_machine_fingerprint():
        # Bound to some other identity: not ours to move.
        return None
    key = str(info.get("purchase_key") or "").strip()
    if not key:
        return None
    try:
        result = license_api.activate_key(key, label=_machine_label())
    except license_api.LicenseApiError:
        return None
    except Exception:
        return None
    if not result or result.get("ok") is False:
        return None
    stored_result = activate_service_license(
        result, purchase_key=str(result.get("key") or key)
    )
    if not stored_result.get("valid"):
        return None
    try:
        # Free the seat the old value took; the customer should not pay for
        # the machine's network identity having changed.
        license_api.deactivate_key(key, stored)
    except Exception:
        pass
    return {
        "ok": True,
        "migrated": True,
        "email": stored_result.get("email", ""),
    }


def apply_remote_validation(result: dict[str, Any]) -> dict[str, str]:
    """Act on a validate_license_remote() result.

    A licence the server has revoked, or a computer whose activation was
    released, must stop working here: the local copy is removed so the app
    falls back to trial/free mode. Network problems only warn.
    """
    if result.get("revoked") or result.get("deactivated"):
        delete_license_data()
        kind = "revoked" if result.get("revoked") else "deactivated"
        return {
            "kind": kind,
            "message": str(
                result.get("error")
                or "This computer is no longer licensed."
            ),
        }
    if result.get("ok") is False:
        return {
            "kind": "warning",
            "message": str(
                result.get("error")
                or "Your license could not be verified online."
            ),
        }
    return {"kind": "none", "message": ""}


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
