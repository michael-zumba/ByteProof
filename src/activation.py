# pyright: reportAttributeAccessIssue=false
"""Automatic license activation after Stripe payment.

Two supported paths:

1. Deep link:  byteproof://activate?key=...   (click from the ByteMind
   fulfilment email; the key is validated locally and activated).

2. Server lookup: byteproof://activate?email=... or the "I've Paid" button in
   the app. ByteProof asks the ByteMind activation API to verify the payment
   for that email and return a signed license key.

The activation API endpoint must be hosted by ByteMind (Stripe webhook ->
license generation). Until it is live, path 1 (emailed key) works fully and
path 2 returns a clear "server not ready" message.
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

ACTIVATION_API_URL = "https://www.bytemind.co.nz/api/byteproof/activate"
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


def activate_with_email(email: str) -> dict:
    """Ask the ByteMind server to verify payment and return a license key."""
    payload = {
        "email": email.strip(),
        "machine_fingerprint": _get_machine_fingerprint(),
        "app": APP_NAME,
    }
    request = urllib.request.Request(
        ACTIVATION_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}-Activation/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": _api_error(exc)}

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


def activate_from_url(url: str) -> dict:
    """Handle byteproof://activate?key=... or ?email=..."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != URL_SCHEME:
        return {"ok": False, "error": "This is not a ByteProof activation link."}
    params = urllib.parse.parse_qs(parsed.query)

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
