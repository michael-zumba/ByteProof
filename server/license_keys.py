"""License-key derivation.

Every Stripe purchase gets one key, derived deterministically from the
Checkout Session id. That means Stripe is the purchase ledger: if the license
database is ever lost, every key can be rebuilt from Stripe's records, and a
retried webhook always produces the same key.

The alphabet is Crockford base32 (no I, L, O or U) so keys can be read over
the phone without ambiguity.
"""

from __future__ import annotations

import hashlib
import hmac

KEY_PREFIX = "BYTP"
KEY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KEY_BODY_LENGTH = 20
KEY_GROUP_SIZE = 4


def derive_license_key(
    session_id: str,
    secret: str,
    *,
    prefix: str = KEY_PREFIX,
    length: int = KEY_BODY_LENGTH,
) -> str:
    """Return the license key for a Checkout Session.

    ``secret`` is the service's key secret; without it the key cannot be
    derived, which keeps session ids alone from being enough to mint keys.
    """
    if not session_id:
        raise ValueError("session_id is required to derive a license key")
    if not secret:
        raise ValueError("a non-empty key secret is required")
    digest = hmac.new(
        secret.encode("utf-8"),
        f"byteproof-license:{session_id}".encode(),
        hashlib.sha256,
    ).digest()
    number = int.from_bytes(digest, "big")
    chars: list[str] = []
    for _ in range(length):
        number, remainder = divmod(number, 32)
        chars.append(KEY_ALPHABET[remainder])
    body = "".join(chars)
    groups = "-".join(
        body[i : i + KEY_GROUP_SIZE] for i in range(0, len(body), KEY_GROUP_SIZE)
    )
    return f"{prefix}-{groups}"


def normalise_key(raw: str) -> str:
    """Trim a pasted key; keys are case-insensitive and shown upper-case."""
    return " ".join(raw.split()).upper()


def mask_key(key: str) -> str:
    key = normalise_key(key)
    if len(key) <= 8:
        return key
    return f"{key[:9]}…{key[-4:]}"


def looks_like_license_key(value: str) -> bool:
    return normalise_key(value).startswith(f"{KEY_PREFIX}-")
