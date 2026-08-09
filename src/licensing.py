import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import time
import uuid
from typing import Any, cast

from .settings import DEVELOPER_EMAILS, get_app_support_dir

TRIAL_DAYS = 7
FREE_MODE_DAILY_PROOFREAD_LIMIT = 3
LICENSE_FILE_NAME = "license.json"
USAGE_FILE_NAME = "usage.json"
TRIAL_SECONDARY_KEY = "ByteProofTrialStart"
TRIAL_SECONDARY_DOMAIN = "com.bytemind.byteproof"
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAsyjv3trVqn9LBHHjX6M+
jtYxqz7BEZDNpzvtcCBPcufjap1Lhb2ZXgtX3yNRXKFXVYfG2AScOK9d6fbxENYA
E8w+Y9nCD4HDLcW90/YHFWOVhj1cuHvvSFmnrZ5jSaUrmutQIdou6tTaiGs7VaJ4
G+AsJY0Ltb82CdG0dQeTdRZAjqHsfiWEQAYey06BJmzoL7MrCsvCD2gcRpqzcBK/
CvbulzeYAJocQtZ/7GmtsJ62iZGn1FVkJJS4RJ4ttyuYjRKdGvT8V8YmQtKytboX
bL6g7z9FbSY0haKL+I0JXpnVsobZwKd3CZc3HNTtKIlyeKY7/GkSqGuzdatujI2x
6IRpUQyHwR7mqZPuCf6jZb3xct2zq0vx/7H2l1f/m7L2NPs+GrJ43bsVd3GNR5js
wA1FsgnEGFitw8p6FOs0AFGJESeLbNEKAte3lDzOd99F/+FWFAasz6FiNbdJbRJm
AAKVrwi2O4+0D+OrFYJT5CI3qI2otd60+0l8TKy8aPbUFMYVAjzkhH7FzQOdsW6q
XsCBhUMyHgeHpoMctPKlzg6tXY2XEcc9L3qu3P8ZWqF8yHUjgLLfkdFMpy4cYSyW
MN40IKz4aFAU+0bnEyx2d9nByt3ycpxvBLweiqgWwtASUn45lhKcQYPwnGieGDjq
FqhvRsRazTRzGo6jeWhnr2ECAwEAAQ==
-----END PUBLIC KEY-----"""


def _get_license_path() -> str:
    support_dir = get_app_support_dir()
    os.makedirs(support_dir, exist_ok=True)
    return os.path.join(support_dir, LICENSE_FILE_NAME)


def _secure_store_set(value: str) -> None:
    """Save the license payload in the OS credential store (best effort)."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-a",
                    "ByteProof",
                    "-s",
                    "com.bytemind.byteproof",
                    "-w",
                    value,
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        elif system == "Windows":
            import ctypes
            from ctypes import wintypes

            class _Credential(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", ctypes.c_wchar_p),
                    ("Comment", ctypes.c_wchar_p),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.c_void_p),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", ctypes.c_wchar_p),
                    ("UserName", ctypes.c_wchar_p),
                ]

            advapi32 = ctypes.windll.advapi32
            advapi32.CredWriteW.argtypes = [
                ctypes.POINTER(_Credential),
                wintypes.DWORD,
            ]
            advapi32.CredWriteW.restype = wintypes.BOOL
            blob = ctypes.create_unicode_buffer(value)
            cred = _Credential()
            cred.Type = 1  # CRED_TYPE_GENERIC
            cred.TargetName = "com.bytemind.byteproof|ByteProof"
            cred.UserName = "ByteProof"
            cred.CredentialBlobSize = len(value.encode("utf-16-le"))
            cred.CredentialBlob = ctypes.cast(blob, ctypes.c_void_p)
            cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
            advapi32.CredWriteW(ctypes.byref(cred), 0)
    except Exception:
        pass


def _secure_store_get() -> str | None:
    """Read the license payload from the OS credential store (best effort)."""
    system = platform.system()
    try:
        if system == "Darwin":
            completed = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    "ByteProof",
                    "-s",
                    "com.bytemind.byteproof",
                    "-w",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if completed.returncode == 0:
                return completed.stdout.rstrip("\n")
        elif system == "Windows":
            import ctypes
            from ctypes import wintypes

            class _Credential(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", ctypes.c_wchar_p),
                    ("Comment", ctypes.c_wchar_p),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.c_void_p),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", ctypes.c_wchar_p),
                    ("UserName", ctypes.c_wchar_p),
                ]

            advapi32 = ctypes.windll.advapi32
            advapi32.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_Credential)),
            ]
            advapi32.CredReadW.restype = wintypes.BOOL
            advapi32.CredFree.argtypes = [ctypes.c_void_p]
            advapi32.CredFree.restype = None
            pcred = ctypes.POINTER(_Credential)()
            if not advapi32.CredReadW(
                "com.bytemind.byteproof|ByteProof",
                1,
                0,
                ctypes.byref(pcred),
            ):
                return None
            try:
                cred = pcred.contents
                raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
                return raw.decode("utf-16-le")
            finally:
                advapi32.CredFree(pcred)
    except Exception:
        pass
    return None


def _secure_store_delete() -> None:
    """Remove the license payload from the OS credential store (best effort)."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                [
                    "security",
                    "delete-generic-password",
                    "-a",
                    "ByteProof",
                    "-s",
                    "com.bytemind.byteproof",
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        elif system == "Windows":
            import ctypes
            from ctypes import wintypes

            advapi32 = ctypes.windll.advapi32
            advapi32.CredDeleteW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            advapi32.CredDeleteW.restype = wintypes.BOOL
            advapi32.CredDeleteW("com.bytemind.byteproof|ByteProof", 1, 0)
    except Exception:
        pass


def _load_license_data() -> dict[str, Any] | None:
    path = _get_license_path()
    if not os.path.exists(path):
        return _secure_store_load_fallback()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return _secure_store_load_fallback()


def _save_license_data(data: dict[str, Any]) -> None:
    path = _get_license_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _secure_store_set(json.dumps(data, ensure_ascii=False))


def _secure_store_load_fallback() -> dict[str, Any] | None:
    raw = _secure_store_get()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_license_data() -> None:
    """Remove the local license file and its OS credential-store copy."""
    path = _get_license_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    _secure_store_delete()


def validate_license_key(license_key: str) -> dict[str, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        return {
            "valid": False,
            "error": "Cryptography library not available. Please reinstall the application.",
        }

    key = license_key.strip()
    parts = key.split("|")
    if len(parts) != 4:
        return {"valid": False, "error": "Invalid license key format."}

    email_enc = parts[0]
    expiry_enc = parts[1]
    machine_fp_enc = parts[2]
    signature_enc = parts[3]

    signed_data = (email_enc + "|" + expiry_enc + "|" + machine_fp_enc).encode("utf-8")

    try:
        signature = base64.urlsafe_b64decode(signature_enc)
    except Exception:
        return {"valid": False, "error": "Invalid signature format."}

    try:
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
    except Exception:
        return {"valid": False, "error": "Unable to load verification key."}

    try:
        verifier = cast(Any, public_key)
        verifier.verify(
            signature,
            signed_data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except InvalidSignature:
        return {"valid": False, "error": "License key signature is not valid. This key may be forged."}
    except Exception:
        return {"valid": False, "error": "License key verification failed."}

    try:
        email = base64.urlsafe_b64decode(email_enc).decode("utf-8")
        expiry_str = base64.urlsafe_b64decode(expiry_enc).decode("utf-8")
        machine_fp_b64 = base64.urlsafe_b64decode(machine_fp_enc).decode("utf-8")
    except Exception:
        return {"valid": False, "error": "License key contains corrupted payload."}

    expiry_date: float | None = None
    if expiry_str.lower() != "unlimited":
        try:
            expiry_date = float(expiry_str)
            if expiry_date < time.time():
                return {
                    "valid": False,
                    "email": email,
                    "error": "License has expired.",
                }
        except ValueError:
            return {"valid": False, "error": "Invalid expiry date in license."}

    return {
        "valid": True,
        "email": email,
        "expiry": expiry_date,
        "machine_fp": machine_fp_b64,
        "raw_key": key,
    }


def activate_license(license_key: str) -> dict[str, Any]:
    """Activate with a legacy signed license key (kept for existing users)."""
    result = validate_license_key(license_key)
    if not result["valid"]:
        return result

    machine_fp = _get_machine_fingerprint()
    licensed_machine = result.get("machine_fp", "")

    if licensed_machine and licensed_machine != machine_fp:
        return {
            "valid": False,
            "error": "This license key is registered to a different computer.",
        }

    _save_license_data({
        "email": result["email"],
        "expiry": result.get("expiry"),
        "key": result.get("raw_key", ""),
        "activated_at": time.time(),
        "machine_fp": machine_fp,
    })

    return {"valid": True, "email": result["email"]}


def activate_polar_license(result: dict[str, Any]) -> dict[str, Any]:
    """Store a Polar activation (key + activation id) as this machine's license."""
    license_key = result.get("key", "").strip()
    activation_id = result.get("activation_id") or ""
    if not license_key or not activation_id:
        return {
            "valid": False,
            "error": "Polar did not return a valid activation for this key.",
        }
    status = str(result.get("status") or "")
    if status in ("revoked", "disabled", "expired"):
        return {
            "valid": False,
            "error": f"This license key is {status}. Please contact ByteMind support.",
        }

    expires_at = result.get("expires_at") or None
    expiry: float | None = None
    if expires_at:
        try:
            from datetime import datetime, timezone

            expiry = datetime.fromisoformat(
                str(expires_at).replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            expiry = None

    _save_license_data({
        "provider": "polar",
        "key": license_key,
        "activation_id": activation_id,
        "email": "",
        "expiry": expiry,
        "expires_at": expires_at,
        "limit_activations": result.get("limit_activations"),
        "activated_at": time.time(),
        "machine_fp": _get_machine_fingerprint(),
    })
    return {
        "valid": True,
        "email": "",
        "key_display": _display_key(license_key),
        "expiry": expiry,
    }


def activate_dev_license(email: str) -> dict[str, Any]:
    """Activate full access for a known developer email (no Polar required)."""
    email = email.strip().lower()
    if email not in {e.lower() for e in DEVELOPER_EMAILS}:
        return {
            "valid": False,
            "error": "This email is not registered for developer access.",
        }
    _save_license_data({
        "provider": "dev",
        "email": email,
        "key": "",
        "activation_id": "",
        "expiry": None,
        "activated_at": time.time(),
        "machine_fp": _get_machine_fingerprint(),
    })
    return {"valid": True, "email": email}


def _display_key(license_key: str) -> str:
    key = license_key.strip()
    if len(key) <= 8:
        return key
    return "…" + key[-4:]


def _validated_license_data() -> dict[str, Any] | None:
    """Load the stored license and verify it is still valid.

    Polar licenses are verified against the stored activation (Polar enforces
    the machine binding and device limit server-side). Legacy signed keys must
    still cryptographically validate and match this computer's fingerprint.
    """
    def _validate(data: dict[str, Any] | None) -> dict[str, Any] | None:
        if not data:
            return None

        provider = data.get("provider", "")
        machine_fp = _get_machine_fingerprint()

        if provider == "polar":
            if not data.get("activation_id"):
                return None
            stored_fp = data.get("machine_fp", "")
            if stored_fp and stored_fp != machine_fp:
                return None
            expiry = data.get("expiry")
            if expiry is not None and expiry < time.time():
                return None
            return data

        if provider == "dev":
            email = str(data.get("email", "")).strip().lower()
            if email not in {e.lower() for e in DEVELOPER_EMAILS}:
                return None
            return data

        if not data.get("key"):
            return None

        result = validate_license_key(data["key"])
        if not result["valid"]:
            return None

        key_machine = result.get("machine_fp", "")
        if key_machine and key_machine != machine_fp:
            return None
        stored_fp = data.get("machine_fp", "")
        if stored_fp and stored_fp != machine_fp:
            return None

        expiry = data.get("expiry")
        if expiry is not None and expiry < time.time():
            return None

        return data

    data = _validate(_load_license_data())
    if data is not None:
        return data

    # Self-heal: if the license file is missing, corrupt, or edited, try the
    # OS credential-store copy. It still has to pass signature validation.
    fallback = _secure_store_load_fallback()
    if fallback:
        return _validate(fallback)
    return None


def is_licensed() -> bool:
    return _validated_license_data() is not None


def get_license_info() -> dict[str, Any]:
    data = _validated_license_data()
    if not data:
        return {"status": "unlicensed"}
    provider = data.get("provider", "legacy")
    key = data.get("key", "")
    return {
        "status": "licensed",
        "email": data.get("email", "Unknown"),
        "expiry": data.get("expiry"),
        "activated_at": data.get("activated_at"),
        "provider": provider,
        "raw_key": data.get("key", ""),
        "key_display": _display_key(key) if key else "",
        "activation_id": data.get("activation_id", ""),
    }


def get_trial_status(first_run_ts: float | None) -> dict[str, Any]:
    if first_run_ts is None:
        return {"in_trial": True, "days_left": TRIAL_DAYS, "trial_expired": False}

    elapsed = time.time() - first_run_ts
    days_elapsed = elapsed / 86400.0
    days_left = max(0, TRIAL_DAYS - int(days_elapsed))

    return {
        "in_trial": days_left > 0,
        "days_left": days_left,
        "trial_expired": days_left <= 0,
    }


def _get_usage_path() -> str:
    return os.path.join(get_app_support_dir(), USAGE_FILE_NAME)


def _load_usage_data() -> dict[str, Any]:
    path = _get_usage_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_usage_data(data: dict[str, Any]) -> None:
    path = _get_usage_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


def get_daily_usage() -> dict[str, Any]:
    """Return today's proofread count and the free-mode daily limit."""
    data = _load_usage_data()
    today = _today_key()
    count = 0
    if data.get("date") == today:
        count = int(data.get("count", 0))
    return {
        "date": today,
        "count": count,
        "limit": FREE_MODE_DAILY_PROOFREAD_LIMIT,
    }


def get_total_usage() -> int:
    return int(_load_usage_data().get("total", 0))


def get_trial_usage() -> int:
    return int(_load_usage_data().get("trial_count", 0))


def record_proofread_usage() -> None:
    """Record one completed proofread and the running trial total."""
    data = _load_usage_data()
    today = _today_key()
    if data.get("date") != today:
        data["date"] = today
        data["count"] = 0
    data["count"] = int(data.get("count", 0)) + 1
    data["total"] = int(data.get("total", 0)) + 1
    if get_trial_status(ensure_trial_started())["in_trial"]:
        data["trial_count"] = int(data.get("trial_count", 0)) + 1
    _save_usage_data(data)


def get_access_status() -> dict[str, Any]:
    """Return the current access tier: licensed, trial, or limited free mode."""
    lic = get_license_info()
    if lic.get("status") == "licensed":
        return {
            "tier": "licensed",
            "licensed": True,
            "in_trial": False,
            "trial_expired": False,
            "free_mode_allowed": False,
            "daily_count": 0,
            "daily_limit": FREE_MODE_DAILY_PROOFREAD_LIMIT,
            "trial_usage": get_trial_usage(),
            "total_usage": get_total_usage(),
        }

    trial = get_trial_status(ensure_trial_started())
    if trial["in_trial"]:
        return {
            "tier": "trial",
            "licensed": False,
            "in_trial": True,
            "trial_expired": False,
            "days_left": trial["days_left"],
            "free_mode_allowed": False,
            "daily_count": 0,
            "daily_limit": FREE_MODE_DAILY_PROOFREAD_LIMIT,
            "trial_usage": get_trial_usage(),
            "total_usage": get_total_usage(),
        }

    daily = get_daily_usage()
    return {
        "tier": "free",
        "licensed": False,
        "in_trial": False,
        "trial_expired": True,
        "free_mode_allowed": daily["count"] < daily["limit"],
        "daily_count": daily["count"],
        "daily_limit": daily["limit"],
        "trial_usage": get_trial_usage(),
        "total_usage": get_total_usage(),
    }


def _trial_secondary_read() -> float | None:
    """Read the trial start from a second system location, if present."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(  # pyright: ignore[reportAttributeAccessIssue]
                winreg.HKEY_CURRENT_USER,  # pyright: ignore[reportAttributeAccessIssue]
                r"Software\ByteMind\ByteProof",
            ) as key:
                value, _ = winreg.QueryValueEx(  # pyright: ignore[reportAttributeAccessIssue]
                    key, TRIAL_SECONDARY_KEY
                )
                return float(value)
        if system == "Darwin":
            completed = subprocess.run(
                ["defaults", "read", TRIAL_SECONDARY_DOMAIN, TRIAL_SECONDARY_KEY],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if completed.returncode == 0:
                return float(completed.stdout.strip())
    except Exception:
        pass
    return None


def _trial_secondary_write(trial_start: float) -> None:
    """Persist the trial start to a second system location."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            key = winreg.CreateKey(  # pyright: ignore[reportAttributeAccessIssue]
                winreg.HKEY_CURRENT_USER,  # pyright: ignore[reportAttributeAccessIssue]
                r"Software\ByteMind\ByteProof",
            )
            winreg.SetValueEx(  # pyright: ignore[reportAttributeAccessIssue]
                key,
                TRIAL_SECONDARY_KEY,
                0,
                winreg.REG_SZ,  # pyright: ignore[reportAttributeAccessIssue]
                str(trial_start),
            )
            winreg.CloseKey(key)  # pyright: ignore[reportAttributeAccessIssue]
        elif system == "Darwin":
            subprocess.run(
                [
                    "defaults",
                    "write",
                    TRIAL_SECONDARY_DOMAIN,
                    TRIAL_SECONDARY_KEY,
                    "-string",
                    str(trial_start),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
    except Exception:
        pass


def ensure_trial_started() -> float:
    """Start (or read) the trial, using the earliest known start across two
    storage locations so deleting one location cannot reset the trial."""
    trial_path = os.path.join(os.path.dirname(_get_license_path()), ".trial_start")
    primary: float | None = None
    if os.path.exists(trial_path):
        try:
            with open(trial_path, "r") as f:
                primary = float(f.read().strip())
        except (ValueError, OSError):
            pass

    secondary = _trial_secondary_read()
    known = [ts for ts in (primary, secondary) if ts is not None]
    if known:
        earliest = min(known)
        # Heal whichever location is missing or newer, so both stay in sync.
        if primary is None or primary != earliest:
            os.makedirs(os.path.dirname(trial_path), exist_ok=True)
            with open(trial_path, "w") as f:
                f.write(str(earliest))
        if secondary is None or secondary != earliest:
            _trial_secondary_write(earliest)
        return earliest

    now = time.time()
    os.makedirs(os.path.dirname(trial_path), exist_ok=True)
    with open(trial_path, "w") as f:
        f.write(str(now))
    _trial_secondary_write(now)
    return now


def _get_machine_fingerprint() -> str:
    sources = []
    try:
        sources.append(str(uuid.getnode()))
    except Exception:
        sources.append(os.path.expanduser("~"))
    try:
        sources.append(platform.node() or "unknown")
    except Exception:
        pass
    if platform.system() == "Darwin":
        # Stable hardware UUID; uuid.getnode() can be unstable across restarts
        # on some macOS versions, which would invalidate machine-locked keys.
        try:
            completed = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', completed.stdout)
            if match:
                sources.append(match.group(1))
        except Exception:
            pass
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                sources.append(str(machine_guid))
        except Exception:
            pass
    try:
        if os.path.exists("/etc/machine-id"):
            with open("/etc/machine-id", "r") as f:
                sources.append(f.read().strip())
    except Exception:
        pass
    joined = "|".join(sources)
    return base64.urlsafe_b64encode(
        hashlib.sha256(joined.encode()).digest()[:16]
    ).decode("utf-8")
