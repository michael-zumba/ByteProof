"""ByteProof activation API and Stripe webhook.

Endpoints:
  POST /api/byteproof/stripe-webhook  Stripe webhook for checkout.session.completed
  POST /api/byteproof/activate        Called by the app's "I've Paid" button and
                                      by byteproof://activate deep links
  POST /api/byteproof/deactivate      Frees this computer's license slot
  POST /api/byteproof/validate        Server-side license validation
  GET  /health                        Health check

Security model (mirrors VoiceInk/Polar):
- Every license key is RSA-signed and bound to the machine fingerprint that
  requested activation, so a copied key cannot be used on another computer.
- The server keeps the authoritative device registry (2 computers max per
  license). A third computer is rejected until one is deactivated.
- Stripe checkout sessions are verified server-side before any key is issued.

Deployment:
  pip install -r server/requirements.txt
  export STRIPE_SECRET_KEY=sk_live_...
  export STRIPE_WEBHOOK_SECRET=whsec_...
  export BYTEPROOF_DATA_DIR=/var/lib/byteproof
  uvicorn server.activation_api:app --host 0.0.0.0 --port 8000

The license private key lives in the gitignored tools/generate_license.py.
Point BYTEPROOF_GENERATOR_PATH at a copy of that file on the server.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import stripe
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from .activation_core import (
    deactivate_machine,
    is_paid,
    load_json,
    record_payment,
    register_machine,
    validate_machine,
)
from .license_signer import generate_license_key, is_configured

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BYTEPROOF_DATA_DIR", BASE_DIR / "data"))
PAYMENTS_FILE = DATA_DIR / "payments.json"
LICENSES_FILE = DATA_DIR / "licenses.json"

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

app = FastAPI(title="ByteProof Activation API")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "license_signer_configured": is_configured(),
    }


@app.post("/api/byteproof/stripe-webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
) -> dict[str, bool]:
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured.")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload,
            stripe_signature or "",
            STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        details = session.get("customer_details") or {}
        email = details.get("email") or session.get("customer_email")
        record_payment(PAYMENTS_FILE, email or "")

    return {"received": True}


class ActivateRequest(BaseModel):
    email: str = ""
    session_id: str = ""
    machine_fingerprint: str = ""
    app: str = "ByteProof"


@app.post("/api/byteproof/activate")
def activate(req: ActivateRequest) -> dict[str, str]:
    machine_fp = req.machine_fingerprint or ""
    email = ""

    if req.session_id:
        if not STRIPE_SECRET_KEY:
            raise HTTPException(status_code=500, detail="Stripe secret key not configured.")
        try:
            session = stripe.checkout.Session.retrieve(
                req.session_id.strip(),
                api_key=STRIPE_SECRET_KEY,
            )
        except stripe.error.StripeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not verify checkout session: {exc.user_message or exc}",
            )
        if session.get("payment_status") != "paid":
            raise HTTPException(
                status_code=402,
                detail="Payment is not complete yet. Please try again after checkout.",
            )
        details = session.get("customer_details") or {}
        email = (
            details.get("email") or session.get("customer_email") or ""
        ).strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Checkout session has no email.")
        # Record it locally so the email-only path also works for this buyer.
        record_payment(PAYMENTS_FILE, email)
    elif req.email.strip():
        email = req.email.strip().lower()
        if not is_paid(load_json(PAYMENTS_FILE), email):
            raise HTTPException(
                status_code=404,
                detail="No paid license found for this email. If you just purchased, "
                "wait a minute for the payment confirmation and try again.",
            )
    else:
        raise HTTPException(status_code=400, detail="Email or session_id is required.")

    try:
        ok, key, error = register_machine(
            LICENSES_FILE,
            email,
            machine_fp,
            lambda e, fp: generate_license_key(e, "unlimited", fp),
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=(
                "License signing failed on the server. "
                "Check BYTEPROOF_LICENSE_PRIVATE_KEY."
            ),
        ) from exc
    if not ok:
        raise HTTPException(status_code=403, detail=error)
    return {"license_key": key}


class DeactivateRequest(BaseModel):
    email: str
    machine_fingerprint: str = ""
    app: str = "ByteProof"


@app.post("/api/byteproof/deactivate")
def deactivate(req: DeactivateRequest) -> dict[str, bool]:
    freed = deactivate_machine(
        LICENSES_FILE,
        req.email,
        req.machine_fingerprint or "",
    )
    if not freed:
        raise HTTPException(
            status_code=404,
            detail="No registered activation found for this computer.",
        )
    return {"ok": True}


class ValidateRequest(BaseModel):
    email: str
    machine_fingerprint: str = ""
    app: str = "ByteProof"


@app.post("/api/byteproof/validate")
def validate(req: ValidateRequest) -> dict[str, Any]:
    return validate_machine(
        load_json(LICENSES_FILE),
        req.email,
        req.machine_fingerprint or "",
    )
