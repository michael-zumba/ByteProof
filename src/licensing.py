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

from .settings import get_app_support_dir

TRIAL_DAYS = 7
LICENSE_FILE_NAME = "license.json"
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


def _load_license_data() -> dict[str, Any] | None:
    path = _get_license_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_license_data(data: dict[str, Any]) -> None:
    path = _get_license_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def is_licensed() -> bool:
    data = _load_license_data()
    if not data:
        return False

    expiry = data.get("expiry")
    if expiry is not None and expiry < time.time():
        return False

    stored_fp = data.get("machine_fp", "")
    if stored_fp and stored_fp != _get_machine_fingerprint():
        return False

    return bool(data.get("key"))


def get_license_info() -> dict[str, Any]:
    data = _load_license_data()
    if not data:
        return {"status": "unlicensed"}
    expiry = data.get("expiry")
    if expiry is not None and expiry < time.time():
        return {"status": "expired", "email": data.get("email", "Unknown")}
    stored_fp = data.get("machine_fp", "")
    if stored_fp and stored_fp != _get_machine_fingerprint():
        return {"status": "unlicensed"}
    return {
        "status": "licensed",
        "email": data.get("email", "Unknown"),
        "expiry": expiry,
        "activated_at": data.get("activated_at"),
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


def ensure_trial_started() -> float:
    trial_path = os.path.join(os.path.dirname(_get_license_path()), ".trial_start")
    if os.path.exists(trial_path):
        try:
            with open(trial_path, "r") as f:
                return float(f.read().strip())
        except (ValueError, OSError):
            pass
    now = time.time()
    os.makedirs(os.path.dirname(trial_path), exist_ok=True)
    with open(trial_path, "w") as f:
        f.write(str(now))
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
