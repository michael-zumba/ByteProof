"""End-to-end tests for the ByteProof license service."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

import server.activation_api as api
from server.config import Settings
from server.license_keys import derive_license_key, mask_key, normalise_key
from server.license_store import LicenseStore
from server.tests.helpers import (
    KEY_SECRET,
    FakeGateway,
    paid_session,
    webhook_event,
)


@pytest.fixture
def client(
    settings: Settings,
    gateway: FakeGateway,
    sent_emails: list[dict[str, Any]],
) -> Iterator[TestClient]:
    app = api.create_app(settings, gateway)
    with TestClient(app) as test_client:
        yield test_client


def post_webhook(
    client: TestClient,
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
) -> Any:
    payload, signature = webhook_event(event_id, event_type, obj)
    return client.post(
        "/api/byteproof/stripe-webhook",
        content=payload,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )


def fulfil(client: TestClient, session: dict[str, Any] | None = None) -> str:
    session = session or paid_session()
    response = post_webhook(
        client,
        f"evt_{session['id']}",
        "checkout.session.completed",
        session,
    )
    assert response.status_code == 200
    return derive_license_key(str(session["id"]), KEY_SECRET)


def verify_signed_key(
    public_key: rsa.RSAPublicKey,
    signed: str,
    *,
    machine_fp: str,
    email: str,
) -> None:
    email_enc, expiry_enc, fp_enc, signature_enc = signed.split("|")
    data = f"{email_enc}|{expiry_enc}|{fp_enc}".encode()
    public_key.verify(
        base64.urlsafe_b64decode(signature_enc),
        data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    assert base64.urlsafe_b64decode(fp_enc).decode("utf-8") == machine_fp
    assert base64.urlsafe_b64decode(email_enc).decode("utf-8") == email
    assert base64.urlsafe_b64decode(expiry_enc).decode("utf-8") == "unlimited"


# -- key derivation ---------------------------------------------------------


def test_key_derivation_is_stable_and_unambiguous() -> None:
    first = derive_license_key("cs_test_1", "secret")
    assert first == derive_license_key("cs_test_1", "secret")
    assert first != derive_license_key("cs_test_2", "secret")
    assert first != derive_license_key("cs_test_1", "other-secret")
    assert first.startswith("BYTP-")
    assert set(first.replace("BYTP-", "").replace("-", "")) <= set(
        "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    )
    assert len(first.replace("-", "")) == 24  # BYTP + 20 body characters
    assert normalise_key(" bytp-abcd ") == "BYTP-ABCD"
    assert mask_key(first).endswith(first[-4:])


# -- fulfilment -------------------------------------------------------------


def test_health_reports_signer_and_stripe(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["license_signer_configured"] is True
    assert body["stripe_configured"] is True


def test_paid_session_issues_one_key_and_one_email(
    client: TestClient,
    settings: Settings,
    sent_emails: list[dict[str, Any]],
) -> None:
    session = paid_session()
    key = fulfil(client, session)

    # Stripe retries and also sends an async confirmation: same key, one email.
    post_webhook(
        client, f"evt_{session['id']}", "checkout.session.completed", session
    )
    post_webhook(
        client,
        f"evt_{session['id']}_async",
        "checkout.session.async_payment_succeeded",
        session,
    )
    license_emails = [mail for mail in sent_emails if mail["kind"] == "license"]
    assert len(license_emails) == 1
    assert license_emails[0]["key"] == key
    assert license_emails[0]["to"] == "buyer@example.com"
    assert "token=" in license_emails[0]["portal"]

    store = LicenseStore(settings.db_path)
    license = store.get_license(key)
    assert license is not None
    assert license["email"] == "buyer@example.com"


def test_webhook_ignores_unpaid_completed_event_then_fulfils_on_async(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    # Alipay-style flow: completed arrives before the money lands.
    unpaid = paid_session(session_id="cs_test_alipay", payment_status="unpaid")
    response = post_webhook(
        client, "evt_alipay_1", "checkout.session.completed", unpaid
    )
    assert response.status_code == 200
    key = derive_license_key("cs_test_alipay", KEY_SECRET)
    assert not sent_emails

    paid = dict(unpaid, payment_status="paid")
    post_webhook(
        client,
        "evt_alipay_2",
        "checkout.session.async_payment_succeeded",
        paid,
    )
    assert [mail["key"] for mail in sent_emails] == [key]
    assert client.post(
        "/api/byteproof/validate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    ).json()["error"] == "This computer is not activated for this license key."


def test_invalid_webhook_signature_is_rejected(client: TestClient) -> None:
    payload, _ = webhook_event("evt_bad", "checkout.session.completed", {})
    response = client.post(
        "/api/byteproof/stripe-webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 400


# -- activation -------------------------------------------------------------


def test_two_computers_then_limit_and_deactivation_frees_a_slot(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
    sent_emails: list[dict[str, Any]],
) -> None:
    key = fulfil(client)
    public_key = signing_key.public_key()

    first = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-a", "label": "MacBook"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["device_count"] == 1
    assert body["device_limit"] == 2
    verify_signed_key(
        public_key,
        body["license_key"],
        machine_fp="machine-a",
        email="buyer@example.com",
    )

    second = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-b"},
    )
    assert second.status_code == 200
    assert second.json()["device_count"] == 2

    third = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-c"},
    )
    assert third.status_code == 403
    assert "limit of 2 computers" in third.json()["detail"]

    # Re-activating the same computer is idempotent, not a third seat.
    again = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    )
    assert again.status_code == 200
    assert again.json()["device_count"] == 2

    assert client.post(
        "/api/byteproof/deactivate",
        json={"key": key, "machine_fingerprint": "machine-b"},
    ).status_code == 200

    replacement = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-c"},
    )
    assert replacement.status_code == 200
    assert replacement.json()["device_count"] == 2


def test_activation_with_checkout_session_and_unpaid_rejection(
    client: TestClient,
    gateway: FakeGateway,
) -> None:
    session = paid_session(session_id="cs_test_session_flow")
    gateway.add(session)
    response = client.post(
        "/api/byteproof/activate",
        json={"session_id": "cs_test_session_flow", "machine_fingerprint": "m1"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "buyer@example.com"

    gateway.add(paid_session(session_id="cs_test_unpaid", payment_status="unpaid"))
    unpaid = client.post(
        "/api/byteproof/activate",
        json={"session_id": "cs_test_unpaid", "machine_fingerprint": "m1"},
    )
    assert unpaid.status_code == 402


def test_unknown_key_is_a_clear_404(client: TestClient) -> None:
    response = client.post(
        "/api/byteproof/activate",
        json={"key": "BYTP-XXXX-XXXX-XXXX-XXXX", "machine_fingerprint": "m1"},
    )
    assert response.status_code == 404
    assert "could not find that license key" in response.json()["detail"].lower()


def test_activation_recovers_when_the_database_is_empty(
    settings: Settings,
    gateway: FakeGateway,
    sent_emails: list[dict[str, Any]],
) -> None:
    """A fresh database plus Stripe's ledger still activates a buyer."""
    session = paid_session(session_id="cs_test_recovered", email="lost@example.com")
    gateway.add(session)
    app = api.create_app(settings, gateway)
    with TestClient(app) as client:
        key = derive_license_key("cs_test_recovered", KEY_SECRET)
        response = client.post(
            "/api/byteproof/activate",
            json={"key": key, "machine_fingerprint": "machine-x"},
        )
    assert response.status_code == 200
    assert response.json()["email"] == "lost@example.com"


def test_refund_revokes_the_key(
    client: TestClient,
) -> None:
    key = fulfil(client)
    client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    )
    response = post_webhook(
        client,
        "evt_refund_1",
        "charge.refunded",
        {"id": "ch_1", "refunded": True, "payment_intent": "pi_test_1"},
    )
    assert response.status_code == 200

    checked = client.post(
        "/api/byteproof/validate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    ).json()
    assert checked["ok"] is True
    assert checked["revoked"] is True

    blocked = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-b"},
    )
    assert blocked.status_code == 403
    assert "refunded" in blocked.json()["detail"].lower()


# -- internal keys ----------------------------------------------------------


def test_internal_keys_activate_without_a_device_limit(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    key = "byteproof_-ad00c43e-0421-4eb7-83dc-457b1a85eb19"
    for machine in ("owner-mac", "owner-pc", "owner-backup"):
        response = client.post(
            "/api/byteproof/activate",
            json={"key": key, "machine_fingerprint": machine},
        )
        assert response.status_code == 200, response.text
    assert not sent_emails  # internal keys never send a purchase email
    licenses = client.get(
        "/api/byteproof/admin/licenses",
        headers={"X-Admin-Token": "admin-test-token"},
    ).json()["licenses"]
    internal = [row for row in licenses if row["source"] == "internal"]
    assert len(internal) == 1
    assert internal[0]["device_limit"] is None
    assert len(internal[0]["devices"]) == 3


# -- portal -----------------------------------------------------------------


def test_portal_shows_the_key_and_releases_a_computer(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    key = fulfil(client)
    for machine in ("machine-a", "machine-b"):
        client.post(
            "/api/byteproof/activate",
            json={"key": key, "machine_fingerprint": machine, "label": machine},
        )
    token = parse_qs(
        urlparse(sent_emails[0]["portal"]).query
    )["token"][0]

    page = client.get(f"/api/byteproof/portal?token={token}")
    assert page.status_code == 200
    assert key in page.text
    assert "machine-a" in page.text

    released = client.post(
        "/api/byteproof/portal/deactivate",
        data={"token": token, "machine_fp": "machine-b"},
        follow_redirects=False,
    )
    assert released.status_code == 303
    assert "released=1" in released.headers["location"]

    third = client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-c"},
    )
    assert third.status_code == 200


def test_portal_request_never_leaks_whether_an_email_exists(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    fulfil(client)
    sent_emails.clear()
    known = client.post(
        "/api/byteproof/portal/request", json={"email": "buyer@example.com"}
    )
    unknown = client.post(
        "/api/byteproof/portal/request", json={"email": "nobody@example.com"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]
    assert len([m for m in sent_emails if m["kind"] == "portal"]) == 1


def test_portal_rejects_an_expired_or_forged_token(
    client: TestClient,
    settings: Settings,
) -> None:
    store = LicenseStore(settings.db_path)
    token = store.create_portal_token("buyer@example.com", ttl_seconds=-1)
    page = client.get(f"/api/byteproof/portal?token={token}")
    assert page.status_code == 200
    assert "License portal" in page.text
    form = client.post(
        "/api/byteproof/portal/deactivate",
        data={"token": "forged", "machine_fp": "machine-a"},
        follow_redirects=False,
    )
    assert form.status_code == 303


# -- thanks page ------------------------------------------------------------


def test_thanks_page_shows_the_key_and_waits_for_alipay(
    client: TestClient,
    gateway: FakeGateway,
) -> None:
    gateway.add(paid_session(session_id="cs_test_thanks", email="t@example.com"))
    page = client.get("/thanks?session_id=cs_test_thanks")
    assert page.status_code == 200
    assert derive_license_key("cs_test_thanks", KEY_SECRET) in page.text

    gateway.add(
        paid_session(
            session_id="cs_test_thanks_pending",
            email="t@example.com",
            payment_status="unpaid",
        )
    )
    pending = client.get("/thanks?session_id=cs_test_thanks_pending")
    assert "Almost there" in pending.text


# -- admin ------------------------------------------------------------------


def test_admin_requires_the_token_and_can_revoke_and_restore(
    client: TestClient,
) -> None:
    key = fulfil(client)
    assert client.get("/api/byteproof/admin/licenses").status_code == 403
    revoked = client.post(
        "/api/byteproof/admin/revoke",
        headers={"X-Admin-Token": "admin-test-token"},
        params={"key": key},
    )
    assert revoked.status_code == 200
    assert client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    ).status_code == 403
    restored = client.post(
        "/api/byteproof/admin/restore",
        headers={"X-Admin-Token": "admin-test-token"},
        params={"key": key},
    )
    assert restored.status_code == 200
    assert client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": "machine-a"},
    ).status_code == 200


def test_license_store_backup(tmp_path: Path) -> None:
    store = LicenseStore(tmp_path / "db.sqlite3")
    store.init()
    store.upsert_license(
        key="BYTP-TEST-TEST-TEST-TEST",
        session_id="cs_backup",
        email="b@example.com",
        device_limit=2,
    )
    backup = tmp_path / "backups" / "snapshot.sqlite3"
    store.backup_to(backup)
    assert backup.exists()
    restored = LicenseStore(backup)
    assert restored.get_license("BYTP-TEST-TEST-TEST-TEST") is not None
