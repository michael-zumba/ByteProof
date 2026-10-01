"""Check the licence signing key matches the key the app verifies with.

A wrong or truncated ``BYTEPROOF_LICENSE_PRIVATE_KEY`` on the server does not
break anything visible server-side: it happily signs licences that every
customer's app then rejects with "signature is not valid". This script
compares the private key's public half with the public key embedded in
``src/licensing.py``.

    python scripts/check_signing_key.py
    BYTEPROOF_LICENSE_PRIVATE_KEY="$(cat key.pem)" python scripts/check_signing_key.py

Exit code 0 = matching; 1 = mismatch or unreadable key.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)


def fingerprint(key: object) -> str:
    # Works for both a private key (use its public half) and a public key.
    public = key.public_key() if hasattr(key, "public_key") else key  # type: ignore[attr-defined]
    der = public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def app_public_key() -> object:
    source = (ROOT / "src" / "licensing.py").read_text(encoding="utf-8")
    match = re.search(
        r'PUBLIC_KEY_PEM\s*=\s*b?"""(.*?)"""', source, flags=re.DOTALL
    )
    if not match:
        raise SystemExit("Could not find PUBLIC_KEY_PEM in src/licensing.py")
    return serialization.load_pem_public_key(match.group(1).encode("utf-8"))


def server_private_key() -> object:
    from server.license_signer import _load_private_key

    return _load_private_key()


def main() -> int:
    if not (
        os.environ.get("BYTEPROOF_LICENSE_PRIVATE_KEY", "").strip()
        or os.environ.get("BYTEPROOF_GENERATOR_PATH", "").strip()
    ):
        print(
            "Set BYTEPROOF_LICENSE_PRIVATE_KEY (the PEM text) or "
            "BYTEPROOF_GENERATOR_PATH (a copy of tools/generate_license.py)."
        )
        return 2

    private = server_private_key()
    app_public = app_public_key()
    server_fp = fingerprint(private)
    app_fp = fingerprint(app_public)

    print(f"Server signing key:  {server_fp}")
    print(f"App public key:      {app_fp}")
    if server_fp == app_fp:
        print("MATCH: the server signs licences this app build will accept.")
        return 0
    print(
        "MISMATCH: licences signed with this key would be rejected by the "
        "app.\nCheck that the server has the private key from "
        "tools/generate_license.py (and that it is the same pair the shipped "
        "app embeds)."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
