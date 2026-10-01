"""Shared fixtures for the license-service tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from server.config import Settings
from server.tests.helpers import KEY_SECRET, WEBHOOK_SECRET, FakeGateway


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _test_signer(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sign license keys with a throwaway key instead of the real one."""
    import server.license_signer as signer

    monkeypatch.setattr(signer, "_private_key", signing_key)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        public_base_url="https://api.test",
        stripe_secret_key="sk_test_dummy",
        stripe_webhook_secret=WEBHOOK_SECRET,
        key_secret=KEY_SECRET,
        internal_keys=("BYTEPROOF_-AD00C43E-0421-4EB7-83DC-457B1A85EB19",),
        admin_token="admin-test-token",
        daily_backup=False,
        reconcile_interval_seconds=3600,
    )


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    import server.activation_api as api

    def fake_license_email(
        settings: Settings,
        to_email: str,
        key: str,
        activate_url: str,
        portal_url: str,
    ) -> bool:
        captured.append(
            {"kind": "license", "to": to_email, "key": key, "portal": portal_url}
        )
        return True

    def fake_portal_email(settings: Settings, to_email: str, portal_url: str) -> bool:
        captured.append({"kind": "portal", "to": to_email, "portal": portal_url})
        return True

    monkeypatch.setattr(api, "send_license_email", fake_license_email)
    monkeypatch.setattr(api, "send_portal_email", fake_portal_email)
    return captured
