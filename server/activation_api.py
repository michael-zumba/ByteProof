"""ByteProof license service.

Payments live in Stripe; this service turns a paid Checkout Session into one
license key, registers up to two computers against that key, and offers a
self-service portal (email magic link) to see the key and move it between
computers.

Endpoints:
  POST /api/byteproof/stripe-webhook       Stripe fulfilment + revocation
  POST /api/byteproof/activate             register this computer
  POST /api/byteproof/validate             is this license good here?
  POST /api/byteproof/deactivate           free this computer's slot
  POST /api/byteproof/portal/request       email a portal magic link
  GET  /api/byteproof/portal               portal page
  POST /api/byteproof/portal/deactivate    release a computer from the portal
  GET  /thanks?session_id=…                post-checkout page (shows the key)
  GET  /health                             liveness

Run locally:
  uvicorn server.activation_api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import stripe
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .config import Settings
from .emailer import send_license_email, send_portal_email
from .license_keys import normalise_key
from .license_signer import generate_license_key, is_configured
from .license_store import LicenseStore
from .pages import (
    message_page,
    pending_payment_page,
    portal_page,
    thanks_page,
)
from .stripe_sync import StripeGateway, StripeReader, license_from_session

log = logging.getLogger("byteproof.license")


class RateLimiter:
    """Per-IP sliding window, sized for a small single-instance service."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            calls = self._calls[client]
            while calls and now - calls[0] > self.window:
                calls.popleft()
            if len(calls) >= self.max_calls:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests. Please wait a moment and try again.",
                )
            calls.append(now)


def _now() -> int:
    return int(time.time())


class ActivateRequest(BaseModel):
    key: str = ""
    session_id: str = ""
    machine_fingerprint: str = ""
    label: str = "ByteProof"


class MachineRequest(BaseModel):
    key: str = ""
    machine_fingerprint: str = ""


class PortalRequest(BaseModel):
    email: str = ""


def create_app(
    settings: Settings | None = None,
    gateway: StripeReader | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = LicenseStore(settings.db_path)
    gateway: StripeReader = gateway or StripeGateway(settings.stripe_secret_key)
    limiter = RateLimiter(max_calls=30, window_seconds=60)
    portal_limiter = RateLimiter(max_calls=6, window_seconds=60)

    def activate_url(key: str) -> str:
        return f"byteproof://activate?key={urllib.parse.quote(key)}"

    def portal_url(token: str) -> str:
        return f"{settings.public_base}/api/byteproof/portal?token={token}"

    def license_portal_url(email: str) -> str:
        token = store.create_portal_token(email, settings.portal_token_ttl_seconds)
        return portal_url(token)

    # -- fulfilment ---------------------------------------------------------

    def fulfil_session(
        session: dict[str, Any], *, send_email_now: bool = True
    ) -> dict[str, Any] | None:
        fields = license_from_session(
            session, secret=settings.key_secret, device_limit=settings.device_limit
        )
        if fields is None:
            return None
        store.upsert_license(**fields)
        row = store.get_license(fields["key"])
        if (
            send_email_now
            and row is not None
            and not row["emailed_at"]
            and row["email"]
        ):
            sent = send_license_email(
                settings,
                row["email"],
                row["key"],
                activate_url(row["key"]),
                license_portal_url(row["email"]),
            )
            if sent:
                store.mark_emailed(row["key"])
        return fields

    def reconcile_from_stripe(days: int = 120) -> int:
        """Rebuild/refresh license rows from Stripe's paid sessions."""
        if not gateway.configured:
            return 0
        created_gte = _now() - days * 86400
        seen = 0
        try:
            for session in gateway.list_sessions(created_gte=created_gte):
                if session.get("payment_status") != "paid":
                    continue
                if fulfil_session(session):
                    seen += 1
        except Exception as exc:  # pragma: no cover - network failure path
            log.warning("Reconcile with Stripe failed: %s", exc)
            return seen
        log.info("Reconciled %s paid checkout sessions from Stripe", seen)
        return seen

    last_reconcile = {"at": 0.0}

    def find_license(key: str) -> dict[str, Any] | None:
        row = store.get_license(key)
        if row is None and gateway.configured:
            # The database may be fresh or restored; rebuild from Stripe. A
            # miss can also be a typo, so do not scan Stripe more than once
            # every few minutes.
            now = time.monotonic()
            if now - last_reconcile["at"] > 300:
                last_reconcile["at"] = now
                reconcile_from_stripe()
            row = store.get_license(key)
        return row

    # -- maintenance --------------------------------------------------------

    stop_event = threading.Event()

    def backup_database() -> None:
        try:
            backups = settings.data_dir / "backups"
            destination = backups / time.strftime("licenses-%Y-%m-%d.sqlite3")
            store.backup_to(destination)
            keep = sorted(backups.glob("licenses-*.sqlite3"))[-14:]
            for old in sorted(backups.glob("licenses-*.sqlite3"))[:-14]:
                if old not in keep:
                    old.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - disk failure path
            log.warning("Backup failed: %s", exc)

    def maintenance_loop() -> None:
        stop_event.wait(90)
        while not stop_event.is_set():
            try:
                reconcile_from_stripe()
                if settings.daily_backup:
                    backup_database()
            except Exception as exc:  # pragma: no cover - background path
                log.warning("Maintenance run failed: %s", exc)
            stop_event.wait(max(settings.reconcile_interval_seconds, 300))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Iterator[None]:
        store.init()
        seed_internal_keys()
        thread = threading.Thread(
            target=maintenance_loop, name="license-maintenance", daemon=True
        )
        thread.start()
        log.info("ByteProof license service ready (%s)", settings.db_path)
        yield
        stop_event.set()

    def seed_internal_keys() -> None:
        for raw in settings.internal_keys:
            key = normalise_key(raw)
            if not key:
                continue
            store.upsert_license(
                key=key,
                session_id=None,
                email=settings.support_email,
                source="internal",
                device_limit=None,
            )

    app = FastAPI(title="ByteProof License API", lifespan=lifespan)

    # -- health -------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        counts = store.counts()
        return {
            "status": "ok",
            "license_signer_configured": is_configured(),
            "stripe_configured": gateway.configured,
            "licenses": counts["licenses"],
            "activations": counts["activations"],
        }

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(
            message_page(
                "ByteProof license service",
                "This is the ByteProof license service. Buy ByteProof, or "
                "manage an existing license, from the links in your purchase "
                "email.",
                settings,
            )
        )

    # -- Stripe webhook -----------------------------------------------------

    def handle_event(event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        data = (event.get("data") or {}).get("object") or {}
        if event_type in (
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        ):
            if data.get("payment_status") != "paid":
                log.info(
                    "Checkout session %s is not paid yet (%s); waiting for the "
                    "async confirmation.",
                    data.get("id"),
                    event_type,
                )
                return
            fields = fulfil_session(data)
            log.info(
                "Fulfilled %s (%s)",
                fields["key"] if fields else "no license",
                data.get("id"),
            )
        elif event_type == "charge.refunded":
            if data.get("refunded") is True:
                intent = str(data.get("payment_intent") or "")
                revoked = store.revoke_by_payment_intent(intent) if intent else 0
                log.info("Refund revoked %s license(s) for %s", revoked, intent)
        elif event_type == "charge.dispute.created":
            intent = str(data.get("payment_intent") or "")
            revoked = store.revoke_by_payment_intent(intent) if intent else 0
            log.info("Dispute revoked %s license(s) for %s", revoked, intent)

    @app.post("/api/byteproof/stripe-webhook")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not settings.stripe_webhook_secret:
            raise HTTPException(status_code=500, detail="Webhook secret not configured.")
        payload = await request.body()
        try:
            event = stripe.Webhook.construct_event(
                payload,
                stripe_signature or "",
                settings.stripe_webhook_secret,
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")

        plain = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        event_id = str(plain.get("id") or "")
        if event_id and not store.record_event(event_id, str(plain.get("type") or "")):
            return {"received": True, "duplicate": True}
        handle_event(plain)
        return {"received": True}

    # -- activation ---------------------------------------------------------

    def resolve_key(*, key: str, session_id: str) -> str:
        key = normalise_key(key)
        if key:
            return key
        session_id = session_id.strip()
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="A license key (or a checkout session) is required.",
            )
        if not gateway.configured:
            raise HTTPException(
                status_code=400, detail="A license key is required."
            )
        try:
            session = gateway.retrieve_session(session_id)
        except stripe.StripeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not verify the checkout session: {exc}",
            )
        if session.get("payment_status") != "paid":
            raise HTTPException(
                status_code=402,
                detail=(
                    "This payment has not completed yet. Alipay and similar "
                    "methods can take a moment to confirm - please try again "
                    "shortly."
                ),
            )
        fields = license_from_session(
            session, secret=settings.key_secret, device_limit=settings.device_limit
        )
        if fields is None:
            raise HTTPException(status_code=400, detail="Checkout session has no license.")
        store.upsert_license(**fields)
        return str(fields["key"])

    @app.post("/api/byteproof/activate")
    def activate(request: Request, req: ActivateRequest) -> dict[str, Any]:
        limiter.check(request)
        machine_fp = req.machine_fingerprint.strip()
        if not machine_fp:
            raise HTTPException(status_code=400, detail="A machine fingerprint is required.")
        key = resolve_key(key=req.key, session_id=req.session_id)
        license = find_license(key)
        if license is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "We could not find that license key. Check it against your "
                    "purchase email, or contact support."
                ),
            )
        if license["revoked"]:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This license was refunded or revoked and is no longer "
                    "valid. Contact support if you think this is a mistake."
                ),
            )
        ok, _error, count = store.register_activation(
            key, machine_fp, req.label, license["device_limit"]
        )
        if not ok:
            limit = license["device_limit"]
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This license has reached its limit of {limit} computers. "
                    "Deactivate another computer, or release a slot from the "
                    f"license portal ({settings.public_base}/api/byteproof/portal)."
                ),
            )
        try:
            signed = generate_license_key(
                str(license["email"] or ""), "unlimited", machine_fp
            )
        except Exception as exc:
            log.exception("License signing failed")
            raise HTTPException(
                status_code=500,
                detail=(
                    "The license could not be issued because the signing key "
                    "is not configured on the server."
                ),
            ) from exc
        return {
            "ok": True,
            "license_key": signed,
            "email": license["email"],
            "device_count": count,
            "device_limit": license["device_limit"],
        }

    @app.post("/api/byteproof/validate")
    def validate(request: Request, req: MachineRequest) -> dict[str, Any]:
        limiter.check(request)
        key = normalise_key(req.key)
        machine_fp = req.machine_fingerprint.strip()
        if not key or not machine_fp:
            raise HTTPException(
                status_code=400, detail="A license key and machine fingerprint are required."
            )
        license = find_license(key)
        if license is None:
            return {"ok": False, "valid": False, "revoked": False,
                    "error": "Unknown license key."}
        machines = store.activations(key)
        registered = any(row["machine_fp"] == machine_fp for row in machines)
        if not registered:
            return {
                "ok": False,
                "valid": False,
                "revoked": bool(license["revoked"]),
                "error": "This computer is not activated for this license key.",
            }
        if license["revoked"]:
            return {
                "ok": True,
                "valid": False,
                "revoked": True,
                "error": "This license was refunded or revoked.",
            }
        return {
            "ok": True,
            "valid": True,
            "revoked": False,
            "device_count": len(machines),
            "device_limit": license["device_limit"],
        }

    @app.post("/api/byteproof/deactivate")
    def deactivate(request: Request, req: MachineRequest) -> dict[str, Any]:
        limiter.check(request)
        key = normalise_key(req.key)
        machine_fp = req.machine_fingerprint.strip()
        if not key or not machine_fp:
            raise HTTPException(
                status_code=400, detail="A license key and machine fingerprint are required."
            )
        if not store.deactivate(key, machine_fp):
            raise HTTPException(
                status_code=404,
                detail="This computer is not registered for that license key.",
            )
        return {"ok": True}

    # -- portal -------------------------------------------------------------

    @app.post("/api/byteproof/portal/request")
    def portal_request(request: Request, req: PortalRequest) -> dict[str, Any]:
        portal_limiter.check(request)
        email = req.email.strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Please enter a valid email address.")
        licenses = store.licenses_for_email(email)
        if not licenses and gateway.configured:
            reconcile_from_stripe()
            licenses = store.licenses_for_email(email)
        if licenses:
            send_portal_email(settings, email, license_portal_url(email))
        return {
            "ok": True,
            "message": (
                "If we have a license for that address, a portal link is on "
                "its way. The link expires in 30 minutes."
            ),
        }

    @app.get("/api/byteproof/portal")
    def portal(
        request: Request,
        token: str = "",
        released: int = 0,
    ) -> HTMLResponse:
        portal_limiter.check(request)
        email = store.portal_token_email(token) if token else None
        if not email:
            return HTMLResponse(
                message_page(
                    "License portal",
                    "To open your licenses, use the portal link from your "
                    "purchase email. If it has expired, request a new link "
                    "from the app (Settings → License → Manage my licenses).",
                    settings,
                ),
                status_code=200,
            )
        licenses = store.licenses_for_email(email)
        entries = [
            (license, store.activations(str(license["key"]))) for license in licenses
        ]
        return HTMLResponse(
            portal_page(
                email=email,
                licenses=entries,
                token=token,
                settings=settings,
                released=bool(released),
            )
        )

    @app.post("/api/byteproof/portal/deactivate")
    def portal_deactivate(
        token: str = Form(...),
        machine_fp: str = Form(...),
    ) -> RedirectResponse:
        email = store.portal_token_email(token)
        if not email:
            return RedirectResponse(
                f"{settings.public_base}/api/byteproof/portal", status_code=303
            )
        for license in store.licenses_for_email(email):
            if store.deactivate(str(license["key"]), machine_fp):
                break
        return RedirectResponse(
            f"{settings.public_base}/api/byteproof/portal"
            f"?token={urllib.parse.quote(token)}&released=1",
            status_code=303,
        )

    # -- thank-you page -----------------------------------------------------

    @app.get("/thanks")
    def thanks(session_id: str = "") -> HTMLResponse:
        session_id = session_id.strip()
        if not session_id:
            return HTMLResponse(
                message_page(
                    "ByteProof",
                    "This page shows your license key after a purchase. Open "
                    "the link from your purchase email to see it.",
                    settings,
                ),
                status_code=400,
            )
        if not gateway.configured:
            return HTMLResponse(
                message_page(
                    "ByteProof",
                    "Stripe is not configured on this server.",
                    settings,
                ),
                status_code=503,
            )
        try:
            session = gateway.retrieve_session(session_id)
        except stripe.StripeError:
            return HTMLResponse(
                message_page(
                    "ByteProof",
                    "We could not find that checkout session.",
                    settings,
                ),
                status_code=404,
            )
        if session.get("payment_status") != "paid":
            return HTMLResponse(pending_payment_page(settings, session_id))
        fields = fulfil_session(session)
        if fields is None:
            return HTMLResponse(
                message_page("ByteProof", "That session has no license.", settings),
                status_code=404,
            )
        return HTMLResponse(
            thanks_page(
                key=str(fields["key"]),
                activate_url=activate_url(str(fields["key"])),
                portal_url=license_portal_url(str(fields["email"])),
                email=str(fields["email"]),
                settings=settings,
            )
        )

    # -- admin --------------------------------------------------------------

    def require_admin(token: str | None) -> None:
        if not settings.admin_token:
            raise HTTPException(status_code=503, detail="Admin API is not configured.")
        if not token or not hmac.compare_digest(token, settings.admin_token):
            raise HTTPException(status_code=403, detail="Not authorised.")

    @app.get("/api/byteproof/admin/licenses")
    def admin_list(
        x_admin_token: str | None = Header(default=None),
        email: str = "",
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        if email:
            rows = store.licenses_for_email(email)
        else:
            rows = store.list_licenses()
        return {
            "licenses": [
                {
                    "key": row["key"],
                    "email": row["email"],
                    "source": row["source"],
                    "revoked": bool(row["revoked"]),
                    "device_limit": row["device_limit"],
                    "devices": [
                        {
                            "machine_fp": machine["machine_fp"],
                            "label": machine["label"],
                            "last_seen": machine["last_seen"],
                        }
                        for machine in store.activations(str(row["key"]))
                    ],
                }
                for row in rows
            ]
        }

    @app.post("/api/byteproof/admin/revoke")
    def admin_revoke(
        x_admin_token: str | None = Header(default=None),
        key: str = "",
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        target = normalise_key(key)
        if not target:
            raise HTTPException(status_code=400, detail="A license key is required.")
        if not store.set_revoked(target, True):
            raise HTTPException(status_code=404, detail="Unknown license key.")
        return {"ok": True, "key": target, "revoked": True}

    @app.post("/api/byteproof/admin/restore")
    def admin_restore(
        x_admin_token: str | None = Header(default=None),
        key: str = "",
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        target = normalise_key(key)
        if not target:
            raise HTTPException(status_code=400, detail="A license key is required.")
        if not store.set_revoked(target, False):
            raise HTTPException(status_code=404, detail="Unknown license key.")
        return {"ok": True, "key": target, "revoked": False}

    @app.post("/api/byteproof/admin/resend")
    def admin_resend(
        x_admin_token: str | None = Header(default=None),
        key: str = "",
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        target = normalise_key(key)
        license = store.get_license(target)
        if license is None:
            raise HTTPException(status_code=404, detail="Unknown license key.")
        if not license["email"]:
            raise HTTPException(status_code=400, detail="That license has no email.")
        sent = send_license_email(
            settings,
            str(license["email"]),
            str(license["key"]),
            activate_url(str(license["key"])),
            license_portal_url(str(license["email"])),
        )
        return {"ok": True, "sent": sent}

    return app


app = create_app()
