"""License activation: Polar is the canonical payment + licensing owner.

Customers buy on Polar's checkout page, Polar emails them a license key, and
the app activates that key with this computer's fingerprint. Polar enforces
the device limit on its own durable servers - no custom activation server
whose data could be lost on redeploy.

Legacy Stripe-era code below (the old email/activation server) is retained
only for local development and any pre-Polar buyers. It is unreachable while
``POLAR_ORGANIZATION_ID`` is configured, which is the production default, and
should be deleted once no pre-Polar buyer remains.

Known developer emails can always unlock full access locally, without a key.
"""

import json
import os
import platform
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi

from . import polar
from .licensing import (
    _get_machine_fingerprint,
    activate_dev_license,
    activate_license,
    activate_polar_license,
    delete_license_data,
    get_license_info,
    validate_license_key,
)
from .settings import APP_NAME, DEVELOPER_EMAILS, POLAR_ORGANIZATION_ID

URL_SCHEME = "byteproof"

# ---------------------------------------------------------------------------
# Legacy Stripe-era activation server (dev-only; unreachable while Polar is
# configured). Retained for pre-Polar buyers and local testing. Remove these
# URLs together with server/ once the legacy registry is retired.
# ---------------------------------------------------------------------------
LEGACY_ACTIVATION_API_URL = "https://byteproof-api.onrender.com/api/byteproof/activate"
LEGACY_ACTIVATION_DEACTIVATE_URL = "https://byteproof-api.onrender.com/api/byteproof/deactivate"
LEGACY_ACTIVATION_VALIDATE_URL = "https://byteproof-api.onrender.com/api/byteproof/validate"


def _looks_like_email(value: str) -> bool:
    return value.count("@") == 1 and "." in value.split("@")[1]


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


def _server_activate_with_email(email: str) -> dict:
    """Legacy fallback: verify payment on the old Stripe-era ByteMind server."""
    data = _post_json(
        LEGACY_ACTIVATION_API_URL,
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


def _server_deactivate(email: str) -> dict:
    """Legacy fallback: free this machine's slot on the old ByteMind server."""
    data = _post_json(
        LEGACY_ACTIVATION_DEACTIVATE_URL,
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
    return {"ok": True, "email": email}


def activate_with_key(value: str) -> dict:
    """Activate with a Polar license key (or legacy email while Polar is off)."""
    value = value.strip()
    if not value:
        return {"ok": False, "error": "Please enter your license key."}

    if _looks_like_email(value):
        email = value.lower()
        if email in {e.lower() for e in DEVELOPER_EMAILS}:
            result = activate_dev_license(email)
            if not result.get("valid"):
                return {
                    "ok": False,
                    "error": result.get("error") or "Activation failed.",
                }
            return {"ok": True, "email": email}
        if POLAR_ORGANIZATION_ID:
            return {
                "ok": False,
                "error": (
                    "Your license key was sent to your inbox after purchase - "
                    "open Settings → License and paste the key instead of "
                    "your email."
                ),
            }
        # Legacy Stripe-era flow: verify the email on the ByteMind server.
        return _server_activate_with_email(email)

    if POLAR_ORGANIZATION_ID:
        try:
            activation = polar.activate_key(
                value,
                label=platform.node() or "ByteProof",
            )
        except polar.PolarError as exc:
            return {"ok": False, "error": str(exc)}
        result = activate_polar_license(activation)
        if not result.get("valid"):
            return {
                "ok": False,
                "error": result.get("error") or "Activation failed.",
            }
        return {
            "ok": True,
            "email": "",
            "key_display": result.get("key_display", ""),
        }

    # Polar not configured: accept support-issued signed keys as before.
    result = validate_license_key(value)
    if not result["valid"]:
        return {"ok": False, "error": result.get("error") or "Invalid license key."}
    activate_license(value)
    return {"ok": True, "email": result["email"]}


def activate_with_email(email: str) -> dict:
    """Compatibility entry point; handles developer and legacy emails."""
    return activate_with_key(email)


def deactivate_license() -> dict:
    """Free this computer's slot, then remove the local license."""
    info = get_license_info()
    if info.get("status") != "licensed":
        return {"ok": False, "error": "No active license found on this computer."}

    if info.get("provider") == "polar":
        try:
            polar.deactivate_key(
                info.get("raw_key", ""),
                info.get("activation_id", ""),
            )
        except polar.PolarError as exc:
            if "404" not in str(exc):
                return {"ok": False, "error": str(exc)}
    elif info.get("provider") == "legacy" and info.get("email"):
        result = _server_deactivate(info["email"])
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "Deactivation failed."}

    delete_license_data()
    return {"ok": True, "email": info.get("email", "")}


def validate_license_remote() -> dict:
    """Best-effort online validation of the current license."""
    info = get_license_info()
    if info.get("status") != "licensed":
        return {"ok": False, "error": "No active license found on this computer."}

    if info.get("provider") == "polar":
        try:
            result = polar.validate_key(
                info.get("raw_key", ""),
                info.get("activation_id", ""),
            )
        except polar.PolarError as exc:
            return {"ok": False, "error": str(exc)}
        status = result.get("status") or "granted"
        if status in ("revoked", "disabled", "expired"):
            return {"ok": False, "error": f"This license key is {status}."}
        return {"ok": True, "status": status}

    if info.get("provider") == "legacy" and info.get("email"):
        data = _post_json(
            LEGACY_ACTIVATION_VALIDATE_URL,
            {
                "email": info["email"],
                "machine_fingerprint": _get_machine_fingerprint(),
                "app": APP_NAME,
            },
        )
        # Normalize the legacy server's {"valid": ...} shape to the same
        # {"ok": ...} contract Polar uses, so callers never mix the two.
        if data.get("valid"):
            return {"ok": True, "status": "valid", "email": info["email"]}
        return {
            "ok": False,
            "error": data.get("error") or (
                "This license is no longer valid on this computer."
            ),
        }

    # Developer licenses are local; nothing to validate online.
    return {"ok": True, "provider": info.get("provider")}


def activate_from_url(url: str) -> dict:
    """Handle byteproof://activate?key=... (or ?email=... for legacy/dev)."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != URL_SCHEME:
        return {"ok": False, "error": "This is not a ByteProof activation link."}
    params = urllib.parse.parse_qs(parsed.query)

    key = (params.get("key") or [""])[0].strip()
    if key:
        return activate_with_key(key)

    email = (params.get("email") or [""])[0].strip()
    if email:
        return activate_with_email(email)

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
