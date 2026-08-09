"""Pure license-registry logic shared by the activation API and tests.

The desktop app cannot enforce a device limit by itself: a signed key copied
to another computer would still validate locally if it were not machine-bound.
This module keeps the authoritative per-email device registry on the server.
Every activation registers the machine fingerprint; a license never exceeds
MAX_MACHINES_PER_LICENSE registered computers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

MAX_MACHINES_PER_LICENSE = 2


def parse_dev_emails(raw: str | None) -> set[str]:
    """Parse the BYTEPROOF_DEV_EMAILS value (comma/space separated)."""
    if not raw:
        return set()
    return {
        part.strip().lower()
        for part in raw.replace(",", " ").split()
        if part.strip()
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_payment(payments_path: Path, email: str) -> None:
    email = email.strip().lower()
    if not email:
        return
    payments = load_json(payments_path)
    record = payments.setdefault(email, {"paid": True, "sessions": []})
    record["paid"] = True
    record["sessions"].append(int(time.time()))
    save_json(payments_path, payments)


def is_paid(payments: dict[str, Any], email: str) -> bool:
    return bool(payments.get(email.strip().lower(), {}).get("paid"))


def get_machines(licenses: dict[str, Any], email: str) -> dict[str, Any]:
    return dict(licenses.get(email.strip().lower(), {}))


def register_machine(
    licenses_path: Path,
    email: str,
    machine_fp: str,
    issue_key: Callable[[str, str], str],
    device_limit: int | None = MAX_MACHINES_PER_LICENSE,
) -> tuple[bool, str | None, str | None]:
    """Register a machine and return (ok, key, error).

    Re-requesting the same machine returns its existing key. A third machine is
    rejected until one of the first two is deactivated.
    """
    email = email.strip().lower()
    licenses = load_json(licenses_path)
    machines = get_machines(licenses, email)

    if machine_fp in machines:
        return True, machines[machine_fp]["key"], None

    if device_limit is not None and len(machines) >= device_limit:
        return (
            False,
            None,
            f"This license has reached its device limit ({device_limit} computers). "
            "Deactivate another computer to free a slot.",
        )

    key = issue_key(email, machine_fp)
    machines[machine_fp] = {
        "key": key,
        "activated_at": int(time.time()),
    }
    licenses[email] = machines
    save_json(licenses_path, licenses)
    return True, key, None


def deactivate_machine(licenses_path: Path, email: str, machine_fp: str) -> bool:
    """Remove this machine from the license; returns True if a slot was freed."""
    email = email.strip().lower()
    licenses = load_json(licenses_path)
    machines = get_machines(licenses, email)
    if machine_fp not in machines:
        return False

    del machines[machine_fp]
    if machines:
        licenses[email] = machines
    else:
        licenses.pop(email, None)
    save_json(licenses_path, licenses)
    return True


def validate_machine(
    licenses: dict[str, Any],
    email: str,
    machine_fp: str,
    device_limit: int | None = MAX_MACHINES_PER_LICENSE,
) -> dict[str, Any]:
    machines = get_machines(licenses, email)
    return {
        "valid": machine_fp in machines,
        "machine_registered": machine_fp in machines,
        "device_count": len(machines),
        "device_limit": device_limit,
    }
