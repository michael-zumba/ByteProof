"""Small CLI for the ByteProof licence service admin API.

    BYTEPROOF_ADMIN_TOKEN=... python scripts/license_admin.py list
    BYTEPROOF_ADMIN_TOKEN=... python scripts/license_admin.py list --email buyer@example.com
    BYTEPROOF_ADMIN_TOKEN=... python scripts/license_admin.py revoke BYTP-...
    BYTEPROOF_ADMIN_TOKEN=... python scripts/license_admin.py restore BYTP-...
    BYTEPROOF_ADMIN_TOKEN=... python scripts/license_admin.py resend BYTP-...

Set BYTEPROOF_LICENSE_API to point at a local or staging service.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = os.environ.get(
    "BYTEPROOF_LICENSE_API", "https://api.bytemind.co.nz"
).rstrip("/")
ADMIN_TOKEN = os.environ.get("BYTEPROOF_ADMIN_TOKEN", "").strip()


def _request(method: str, path: str, params: dict[str, str] | None = None) -> Any:
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "X-Admin-Token": ADMIN_TOKEN,
            "Accept": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail")
        except Exception:
            detail = body
        print(f"Error {exc.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Cannot reach {BASE_URL}: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listed = sub.add_parser("list", help="List licences.")
    listed.add_argument("--email", default="")
    listed.add_argument("--json", action="store_true")

    for name in ("revoke", "restore", "resend"):
        command = sub.add_parser(name, help=f"{name.capitalize()} a licence.")
        command.add_argument("key")

    args = parser.parse_args()
    if not ADMIN_TOKEN:
        print("Set BYTEPROOF_ADMIN_TOKEN first.", file=sys.stderr)
        return 2

    if args.command == "list":
        data = _request(
            "GET",
            "/api/byteproof/admin/licenses",
            {"email": args.email} if args.email else None,
        )
        licenses = data.get("licenses", [])
        if args.json:
            print(json.dumps(licenses, indent=2))
            return 0
        if not licenses:
            print("No licences found.")
            return 0
        for license_row in licenses:
            state = "revoked" if license_row["revoked"] else "active"
            limit = license_row["device_limit"]
            limit_text = "unlimited" if limit is None else f"{limit} max"
            print(
                f"{license_row['key']}  {license_row['email'] or '(no email)':30}  "
                f"{license_row['source']:8}  {state:8}  {limit_text}  "
                f"{len(license_row['devices'])} device(s)"
            )
            for device in license_row["devices"]:
                print(
                    f"    - {device['label'] or 'computer'} "
                    f"({device['machine_fp'][:12]}…)"
                )
        return 0

    data = _request(
        "POST",
        f"/api/byteproof/admin/{args.command}",
        {"key": args.key},
    )
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
