#!/usr/bin/env python3
"""Manage this machine's developer access to ByteProof.

ByteProof ships with **no** developer identities: an email address that is
publicly known must never unlock the app. Developer access is instead granted
per machine through ``dev-access.json`` in the ByteProof support folder, which
this script writes.

Usage
-----
    python scripts/dev_access.py add owner@example.com
    python scripts/dev_access.py list
    python scripts/dev_access.py remove owner@example.com
    python scripts/dev_access.py clear

The file is never committed and is not created by installers or builds, so a
customer cannot obtain developer access by knowing an address.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.settings import DEV_ACCESS_FILE, get_app_support_dir


def _path() -> str:
    return os.path.join(get_app_support_dir(), DEV_ACCESS_FILE)


def _load() -> list[str]:
    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        emails = data.get("emails", []) if isinstance(data, dict) else data
        return [str(item).strip().lower() for item in emails if str(item).strip()]
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return []


def _save(emails: list[str]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"emails": emails}, handle, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["add", "remove", "list", "clear"]
    )
    parser.add_argument("email", nargs="?", default="")
    args = parser.parse_args()

    emails = _load()
    if args.action == "list":
        print(f"Dev access file: {_path()}")
        print("Emails:", ", ".join(emails) if emails else "(none)")
        return 0

    if args.action == "clear":
        _save([])
        print("Developer access cleared on this machine.")
        return 0

    email = args.email.strip().lower()
    if not email or "@" not in email:
        parser.error("an email address is required for add/remove")

    if args.action == "add":
        if email not in emails:
            emails.append(email)
        _save(emails)
        print(f"Added {email} to {_path()}")
        print("Restart ByteProof (or reopen the License tab) to pick it up.")
    else:
        emails = [item for item in emails if item != email]
        _save(emails)
        print(f"Removed {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
