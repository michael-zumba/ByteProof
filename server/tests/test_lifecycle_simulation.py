"""Four mock rounds of the customer lifecycle.

Round 1  the normal customer: buy, activate two computers, use, move on
Round 2  awkward but legitimate: reinstalls, repeat buyers, expired links
Round 3  abuse and tamper: guessing, forgery, replays, rate limits
Round 4  lifecycle and disaster: refunds, disputes, a wiped database, backups

Everything runs through the real HTTP surface and the real SQLite store, with
Stripe replaced by fixture sessions and signed webhook bodies, so the rounds
are deterministic, offline, and safe to run in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import server.activation_api as api
from server.config import Settings
from server.license_keys import derive_license_key
from server.license_store import LicenseStore
from server.tests.helpers import (
    KEY_SECRET,
    FakeGateway,
    fulfil,
    paid_session,
    post_webhook,
    verify_signed_key,
)


def _portal_token(mail: dict[str, Any]) -> str:
    return parse_qs(urlparse(mail["portal"]).query)["token"][0]


def _activate(
    client: TestClient, key: str, machine: str, label: str = ""
) -> Any:
    return client.post(
        "/api/byteproof/activate",
        json={"key": key, "machine_fingerprint": machine, "label": label},
    )


def _validate(client: TestClient, key: str, machine: str) -> dict[str, Any]:
    return client.post(
        "/api/byteproof/validate",
        json={"key": key, "machine_fingerprint": machine},
    ).json()


# ---------------------------------------------------------------------------
# Round 1 - the normal customer
# ---------------------------------------------------------------------------


def test_round1_buy_activate_use_and_move_to_a_new_computer(
    client: TestClient,
    gateway: FakeGateway,
    signing_key: rsa.RSAPrivateKey,
    sent_emails: list[dict[str, Any]],
) -> None:
    # --- buy -------------------------------------------------------------
    session = paid_session(email="round1@example.com")
    gateway.add(session)  # the thank-you page re-reads the session from Stripe
    key = fulfil(client, session)
    assert [mail["key"] for mail in sent_emails] == [key]
    assert sent_emails[0]["to"] == "round1@example.com"
    assert _portal_token(sent_emails[0])

    # The customer can find the key again on the thank-you page.
    thanks = client.get(f"/thanks?session_id={session['id']}")
    assert key in thanks.text

    # --- activate two computers -----------------------------------------
    first = _activate(client, key, "macbook", "MacBook Pro")
    assert first.status_code == 200, first.text
    assert first.json()["device_count"] == 1
    assert first.json()["device_limit"] == 2
    verify_signed_key(
        signing_key.public_key(),
        first.json()["license_key"],
        machine_fp="macbook",
        email="round1@example.com",
    )

    second = _activate(client, key, "windows-pc", "Office PC")
    assert second.status_code == 200
    assert second.json()["device_count"] == 2

    # --- use: both computers validate online ----------------------------
    assert _validate(client, key, "macbook") == {
        "ok": True,
        "valid": True,
        "revoked": False,
        "device_count": 2,
        "device_limit": 2,
    }
    assert _validate(client, key, "windows-pc")["valid"] is True

    # --- the office PC is released in the portal and must stop ----------
    token = _portal_token(sent_emails[0])
    portal = client.get(f"/api/byteproof/portal?token={token}")
    assert key in portal.text
    assert "Office PC" in portal.text

    released = client.post(
        "/api/byteproof/portal/deactivate",
        data={"token": token, "machine_fp": "windows-pc"},
        follow_redirects=False,
    )
    assert released.status_code == 303

    stale = _validate(client, key, "windows-pc")
    assert stale["valid"] is False
    assert stale["reason"] == "not_activated"

    # --- a new computer takes the freed seat ----------------------------
    replacement = _activate(client, key, "new-macbook", "New MacBook")
    assert replacement.status_code == 200
    assert replacement.json()["device_count"] == 2
    # The released computer cannot sneak back in while both seats are taken.
    assert _activate(client, key, "windows-pc").status_code == 403


# ---------------------------------------------------------------------------
# Round 2 - awkward but legitimate
# ---------------------------------------------------------------------------


def test_round2_reinstall_and_messy_key_paste(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    key = fulfil(client)
    messy = f"  {key.lower()}  "
    first = _activate(client, messy, "laptop")
    assert first.status_code == 200
    assert first.json()["device_count"] == 1

    # Reinstalling the app keeps the same hardware fingerprint: same seat,
    # no extra email, same signed licence.
    again = _activate(client, key, "laptop")
    assert again.status_code == 200
    assert again.json()["device_count"] == 1
    # A new signature (RSA-PSS salts every signature); the same seat and the
    # same licence are what matter.
    verify_signed_key(
        signing_key.public_key(),
        again.json()["license_key"],
        machine_fp="laptop",
        email="buyer@example.com",
    )


def test_round2_repeat_buyer_gets_a_second_independent_key(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    first_key = fulfil(
        client, paid_session(session_id="cs_repeat_1", email="repeat@example.com")
    )
    second_key = fulfil(
        client,
        paid_session(
            session_id="cs_repeat_2",
            email="repeat@example.com",
            payment_intent="pi_repeat_2",
        ),
    )
    assert first_key != second_key

    # Each purchase carries its own two seats.
    for machine in ("first-a", "first-b"):
        assert _activate(client, first_key, machine).status_code == 200
    for machine in ("second-a", "second-b"):
        assert _activate(client, second_key, machine).status_code == 200
    assert _activate(client, first_key, "first-c").status_code == 403

    # The portal lists both licences for that email.
    client.post("/api/byteproof/portal/request", json={"email": "repeat@example.com"})
    portal_mail = [mail for mail in sent_emails if mail["kind"] == "portal"][-1]
    page = client.get(f"/api/byteproof/portal?token={_portal_token(portal_mail)}")
    assert first_key in page.text
    assert second_key in page.text


def test_round2_expired_link_reshoot_and_support_resend(
    client: TestClient,
    settings: Settings,
    sent_emails: list[dict[str, Any]],
) -> None:
    key = fulfil(client)
    expired = LicenseStore(settings.db_path).create_portal_token(
        "buyer@example.com", ttl_seconds=-1
    )
    page = client.get(f"/api/byteproof/portal?token={expired}")
    assert key not in page.text

    sent_emails.clear()
    response = client.post(
        "/api/byteproof/portal/request", json={"email": "buyer@example.com"}
    )
    assert response.status_code == 200
    fresh = [mail for mail in sent_emails if mail["kind"] == "portal"]
    assert len(fresh) == 1
    assert key in client.get(
        f"/api/byteproof/portal?token={_portal_token(fresh[0])}"
    ).text

    # Support can re-send the purchase email from the admin API.
    sent_emails.clear()
    resent = client.post(
        "/api/byteproof/admin/resend",
        headers={"X-Admin-Token": "admin-test-token"},
        params={"key": key},
    )
    assert resent.status_code == 200
    assert resent.json()["sent"] is True
    assert sent_emails[-1]["kind"] == "license"


def test_round2_alipay_style_delayed_payment(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    session = paid_session(
        session_id="cs_round2_delayed",
        email="alipay@example.com",
        payment_status="unpaid",
    )
    assert post_webhook(
        client, "evt_r2_1", "checkout.session.completed", session
    ).status_code == 200
    assert not sent_emails
    key = derive_license_key("cs_round2_delayed", KEY_SECRET)
    assert client.post(
        "/api/byteproof/validate",
        json={"key": key, "machine_fingerprint": "m1"},
    ).json()["error"] == "Unknown license key."

    paid = dict(session, payment_status="paid")
    assert post_webhook(
        client,
        "evt_r2_2",
        "checkout.session.async_payment_succeeded",
        paid,
    ).status_code == 200
    assert [mail["key"] for mail in sent_emails] == [key]

    # Stripe may redeliver the same event; the key and the email do not repeat.
    assert post_webhook(
        client,
        "evt_r2_2",
        "checkout.session.async_payment_succeeded",
        paid,
    ).json().get("duplicate") is True
    assert len(sent_emails) == 1


def test_round2_free_promotion_code_still_gets_a_licence(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    """A 100%-off code makes payment_status "no_payment_required"."""
    session = paid_session(
        session_id="cs_round2_free",
        email="reviewer@example.com",
        payment_status="no_payment_required",
    )
    key = fulfil(client, session)
    assert [mail["key"] for mail in sent_emails] == [key]
    assert _activate(client, key, "reviewer-mac").status_code == 200


# ---------------------------------------------------------------------------
# Round 3 - abuse and tamper
# ---------------------------------------------------------------------------


def test_round3_guessing_forgery_and_replays(
    client: TestClient,
    sent_emails: list[dict[str, Any]],
) -> None:
    # A key that was never issued, however plausible it looks.
    made_up = client.post(
        "/api/byteproof/activate",
        json={"key": "BYTP-2M4K-9PQR-7TXV-3HJW-5NCB", "machine_fingerprint": "m1"},
    )
    assert made_up.status_code == 404
    assert "could not find that license key" in made_up.json()["detail"].lower()

    # No fingerprint, no seat.
    assert client.post(
        "/api/byteproof/activate",
        json={"key": "BYTP-2M4K-9PQR-7TXV-3HJW-5NCB", "machine_fingerprint": ""},
    ).status_code == 400

    # A forged portal token must not release a real computer.
    key = fulfil(client)
    assert _activate(client, key, "m1").status_code == 200
    client.post(
        "/api/byteproof/portal/deactivate",
        data={"token": "not-a-real-token", "machine_fp": "m1"},
        follow_redirects=False,
    )
    assert _validate(client, key, "m1")["valid"] is True

    # Admin API needs the exact token.
    assert client.get("/api/byteproof/admin/licenses").status_code == 403
    assert (
        client.get(
            "/api/byteproof/admin/licenses",
            headers={"X-Admin-Token": "wrong-token"},
        ).status_code
        == 403
    )

    # An unsigned or badly signed webhook is rejected outright.
    bad = client.post(
        "/api/byteproof/stripe-webhook",
        content=b'{"id": "evt_forged", "type": "checkout.session.completed"}',
        headers={"Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert bad.status_code == 400

    # A genuine event type we do not handle is a harmless no-op.
    assert post_webhook(
        client, "evt_round3_unrelated", "invoice.paid", {"id": "in_1"}
    ).status_code == 200

    # The limit message tells the customer how to get unstuck.
    assert _activate(client, key, "m2").status_code == 200
    limited = _activate(client, key, "m3")
    assert limited.status_code == 403
    assert "limit of 2 computers" in limited.json()["detail"]
    assert "portal" in limited.json()["detail"]

    # The tampered / failed attempts never issued a licence email.
    assert len([mail for mail in sent_emails if mail["kind"] == "license"]) == 1


def test_round3_rate_limit_stops_a_key_guessing_script(
    settings: Settings,
    gateway: FakeGateway,
    sent_emails: list[dict[str, Any]],
) -> None:
    app = api.create_app(settings, gateway)
    with TestClient(app) as script:
        statuses = [
            script.post(
                "/api/byteproof/activate",
                json={
                    "key": f"BYTP-GUESS-{index:04d}-0000-0000-0000",
                    "machine_fingerprint": "attacker",
                },
            ).status_code
            for index in range(40)
        ]
    assert statuses.count(429) >= 10
    assert statuses[-1] == 429


# ---------------------------------------------------------------------------
# Round 4 - lifecycle and disaster
# ---------------------------------------------------------------------------


def test_round4_refund_revokes_and_support_can_restore(
    client: TestClient,
) -> None:
    key = fulfil(client)
    assert _activate(client, key, "m1").status_code == 200
    assert _validate(client, key, "m1")["valid"] is True

    post_webhook(
        client,
        "evt_r4_refund",
        "charge.refunded",
        {"id": "ch_1", "refunded": True, "payment_intent": "pi_test_1"},
    )
    revoked = _validate(client, key, "m1")
    assert revoked["revoked"] is True and revoked["valid"] is False
    assert _activate(client, key, "m2").status_code == 403

    # Goodwill: support restores the licence and the customer can move on.
    restored = client.post(
        "/api/byteproof/admin/restore",
        headers={"X-Admin-Token": "admin-test-token"},
        params={"key": key},
    )
    assert restored.status_code == 200
    assert _validate(client, key, "m1")["valid"] is True
    assert _activate(client, key, "m2").status_code == 200


def test_round4_dispute_revokes_and_a_won_dispute_restores(
    client: TestClient,
) -> None:
    key = fulfil(
        client,
        paid_session(
            session_id="cs_round4_dispute",
            email="dispute@example.com",
            payment_intent="pi_dispute",
        ),
    )
    assert _activate(client, key, "m1").status_code == 200

    post_webhook(
        client,
        "evt_r4_dispute_open",
        "charge.dispute.created",
        {"id": "dp_1", "payment_intent": "pi_dispute", "status": "needs_response"},
    )
    assert _validate(client, key, "m1")["revoked"] is True

    # We won the dispute: the customer is not punished for the bank's error.
    post_webhook(
        client,
        "evt_r4_dispute_won",
        "charge.dispute.closed",
        {"id": "dp_1", "payment_intent": "pi_dispute", "status": "won"},
    )
    assert _validate(client, key, "m1")["valid"] is True

    # A lost dispute stays revoked.
    post_webhook(
        client,
        "evt_r4_dispute_lost_open",
        "charge.dispute.created",
        {"id": "dp_2", "payment_intent": "pi_dispute", "status": "needs_response"},
    )
    post_webhook(
        client,
        "evt_r4_dispute_lost",
        "charge.dispute.closed",
        {"id": "dp_2", "payment_intent": "pi_dispute", "status": "lost"},
    )
    assert _validate(client, key, "m1")["revoked"] is True


def test_round4_wiped_database_rebuilds_from_stripe(
    settings: Settings,
    gateway: FakeGateway,
    sent_emails: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    session = paid_session(
        session_id="cs_round4_recovery", email="recover@example.com"
    )
    gateway.add(session)

    # The disaster: the service comes up with an empty data directory and a
    # customer activates with the key from their purchase email.
    fresh = Settings(
        data_dir=tmp_path / "fresh",
        public_base_url="https://api.test",
        stripe_secret_key="sk_test_dummy",
        stripe_webhook_secret="whsec_test_secret",
        key_secret=KEY_SECRET,
        internal_keys=(),
        admin_token="admin-test-token",
        daily_backup=False,
    )
    app = api.create_app(fresh, gateway)
    with TestClient(app) as recovered:
        key = derive_license_key("cs_round4_recovery", KEY_SECRET)
        activated = _activate(recovered, key, "survivor")
        assert activated.status_code == 200, activated.text
        assert activated.json()["email"] == "recover@example.com"

        # The portal still works after the rebuild (it reconciles too).
        recovered.post(
            "/api/byteproof/portal/request", json={"email": "recover@example.com"}
        )
    assert [mail["key"] for mail in sent_emails if mail["kind"] == "license"] == [key]


def test_round4_backup_twice_in_one_day_and_restore(
    client: TestClient,
    settings: Settings,
) -> None:
    key = fulfil(client)
    assert _activate(client, key, "m1").status_code == 200

    # Running the backup twice on the same day must not fail or skip.
    for _ in range(2):
        response = client.post(
            "/api/byteproof/admin/backup",
            headers={"X-Admin-Token": "admin-test-token"},
        )
        assert response.status_code == 200
        assert response.json()["bytes"] > 0

    snapshot = max((settings.data_dir / "backups").glob("licenses-*.sqlite3"))
    restored = LicenseStore(snapshot)
    restored.init()
    assert restored.get_license(key) is not None
    assert [row["machine_fp"] for row in restored.activations(key)] == ["m1"]
