# pyright: reportAttributeAccessIssue=false
"""Automatic license activation after Stripe payment.

Supported paths:

1. Stripe session deep link: byteproof://activate?session=cs_...  (set as the
   Stripe payment link success URL; the app verifies the checkout session with
   the ByteMind server, which issues a machine-bound permanent key).

2. Email deep link: byteproof://activate?email=... or the "I've Paid" button
   in the app. ByteProof asks the ByteMind activation API to verify the
   payment for that email and return a signed license key.

3. Manual key: byteproof://activate?key=... (support-issued machine-bound key,
   validated locally and activated).

The server enforces a maximum of two activated computers per license.
Deactivation (Settings -> License -> Deactivate This Computer) frees a slot.
"""

import json
import os
import platform
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi

from .licensing import (
    _get_machine_fingerprint,
    activate_license,
    validate_license_key,
)
from .settings import APP_NAME

ACTIVATION_API_URL = "https://api.bytemind.co.nz/api/byteproof/activate"
ACTIVATION_DEACTIVATE_URL = "https://api.bytemind.co.nz/api/byteproof/deactivate"
ACTIVATION_VALIDATE_URL = "https://api.bytemind.co.nz/api/byteproof/validate"
URL_SCHEME = "byteproof"


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _api_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            return str(parsed.get("error") or parsed.get("message") or body)
        except Exception:
            return f"Server error ({exc.code}): {body[:200]}"
    if isinstance(exc, urllib.error.URLError):
        return f"Cannot reach the activation server: {exc.reason}"
    return f"Activation request failed: {exc}"


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}-Activation/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": _api_error(exc)}


def activate_with_email(email: str) -> dict:
    """Ask the ByteMind server to verify payment and return a license key."""
    data = _post_json(
        ACTIVATION_API_URL,
        {
            "email": email.strip(),
            "machine_fingerprint": _get_machine_fingerprint(),
            "app": APP_NAME,
        },
    )

    key = data.get("license_key") or data.get("key")
    if not key:
        return {
            "ok": False,
            "error": data.get("error") or "The server did not return a license key.",
        }
    result = validate_license_key(key)
    if not result["valid"]:
        return {"ok": False, "error": result.get("error") or "Invalid license key."}
    activate_license(key)
    return {"ok": True, "email": result["email"]}


def activate_with_session(session_id: str) -> dict:
    """Verify a Stripe checkout session and activate this computer."""
    data = _post_json(
        ACTIVATION_API_URL,
        {
            "session_id": session_id.strip(),
            "machine_fingerprint": _get_machine_fingerprint(),
            "app": APP_NAME,
        },
    )

    key = data.get("license_key") or data.get("key")
    if not key:
        return {
            "ok": False,
            "error": data.get("error") or "The server did not return a license key.",
        }
    result = validate_license_key(key)
    if not result["valid"]:
        return {"ok": False, "error": result.get("error") or "Invalid license key."}
    activate_license(key)
    return {"ok": True, "email": result["email"]}


def deactivate_license() -> dict:
    """Ask the server to free this computer's slot, then remove the local key."""
    from .licensing import get_license_info

    info = get_license_info()
    email = info.get("email", "")
    if not email:
        return {"ok": False, "error": "No active license found on this computer."}

    data = _post_json(
        ACTIVATION_DEACTIVATE_URL,
        {
            "email": email,
            "machine_fingerprint": _get_machine_fingerprint(),
            "app": APP_NAME,
        },
    )
    if not data.get("ok"):
        return {
            "ok": False,
            "error": data.get("error") or "The server could not deactivate this license.",
        }

    from .licensing import delete_license_data

    delete_license_data()
    return {"ok": True, "email": email}


def validate_license_remote() -> dict:
    """Best-effort server-side validation of the current license."""
    from .licensing import get_license_info

    info = get_license_info()
    email = info.get("email", "")
    if not email:
        return {"ok": False, "error": "No active license found on this computer."}
    return _post_json(
        ACTIVATION_VALIDATE_URL,
        {
            "email": email,
            "machine_fingerprint": _get_machine_fingerprint(),
            "app": APP_NAME,
        },
    )


def activate_from_url(url: str) -> dict:
    """Handle byteproof://activate?session=... or ?key=... or ?email=..."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != URL_SCHEME:
        return {"ok": False, "error": "This is not a ByteProof activation link."}
    params = urllib.parse.parse_qs(parsed.query)

    session_id = (params.get("session") or [""])[0].strip()
    if session_id:
        return activate_with_session(session_id)

    key = (params.get("key") or [""])[0].strip()
    if key:
        result = validate_license_key(key)
        if not result["valid"]:
            return {"ok": False, "error": result.get("error") or "Invalid license key."}
        activate_license(key)
        return {"ok": True, "email": result["email"]}

    email = (params.get("email") or [""])[0].strip()
    if email:
        return activate_with_email(email)

    return {
        "ok": False,
        "error": "The activation link is missing a key or an email address.",
    }


def register_url_scheme() -> None:
    """Register the byteproof:// URL scheme on Windows (macOS uses the bundle)."""
    if platform.system() != "Windows":
        return
    try:
        import winreg
        exe = os.path.abspath(__import__("sys").executable)
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
