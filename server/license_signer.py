"""Sign ByteProof license keys on the server.

The RSA private key is never committed to the repository. It is loaded from:
  1. BYTEPROOF_LICENSE_PRIVATE_KEY  - the PEM text (newlines as \\n), or
  2. BYTEPROOF_GENERATOR_PATH       - a local copy of tools/generate_license.py
                                     (convenient for local development).
"""

from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _load_private_key() -> Any:
    pem_text = os.environ.get("BYTEPROOF_LICENSE_PRIVATE_KEY", "").strip()
    if pem_text:
        pem = pem_text.replace("\\n", "\n").encode("utf-8")
        return serialization.load_pem_private_key(pem, password=None)

    generator_path = os.environ.get("BYTEPROOF_GENERATOR_PATH", "")
    if generator_path and Path(generator_path).exists():
        spec = importlib.util.spec_from_file_location(
            "byteproof_license_generator", generator_path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load the license generator module.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return serialization.load_pem_private_key(module.PRIVATE_KEY_PEM, password=None)

    raise RuntimeError(
        "License private key is not configured. Set BYTEPROOF_LICENSE_PRIVATE_KEY "
        "or BYTEPROOF_GENERATOR_PATH."
    )


_private_key: Any | None = None


def _get_private_key() -> Any:
    global _private_key
    if _private_key is None:
        _private_key = _load_private_key()
    return _private_key


def generate_license_key(
    email: str,
    expiry_date: str = "unlimited",
    machine_fp: str = "",
) -> str:
    """Return a key in the same format the desktop app validates."""
    email_enc = base64.urlsafe_b64encode(email.encode("utf-8")).decode("utf-8")
    expiry_enc = base64.urlsafe_b64encode(expiry_date.encode("utf-8")).decode("utf-8")
    machine_fp_enc = base64.urlsafe_b64encode(machine_fp.encode("utf-8")).decode("utf-8")
    signed_data = (email_enc + "|" + expiry_enc + "|" + machine_fp_enc).encode("utf-8")

    signature = _get_private_key().sign(
        signed_data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    signature_enc = base64.urlsafe_b64encode(signature).decode("utf-8")
    return email_enc + "|" + expiry_enc + "|" + machine_fp_enc + "|" + signature_enc
