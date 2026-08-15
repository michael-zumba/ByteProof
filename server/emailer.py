"""LEGACY: send the ByteProof activation email after a Stripe checkout.

Polar is the canonical payment + licensing owner, and Polar sends license-key
emails itself. This Stripe-era emailer is kept only for the legacy dev server.

Configuration (environment variables):
  BYTEPROOF_SMTP_HOST       e.g. smtp.gmail.com
  BYTEPROOF_SMTP_PORT       default 587
  BYTEPROOF_SMTP_USER       the sending account
  BYTEPROOF_SMTP_PASSWORD   app password (never your normal password)
  BYTEPROOF_SMTP_FROM       the From address (defaults to SMTP_USER)
  BYTEPROOF_SMTP_TLS        "true" (default) or "false"

If the SMTP variables are missing, the webhook still completes and the
activation email is skipped (logged) so payments are never blocked by email.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

ACTIVATION_LINK_TEMPLATE = "byteproof://activate?session={session_id}"


def _build_message(to_email: str, session_id: str) -> EmailMessage:
    link = ACTIVATION_LINK_TEMPLATE.format(session_id=session_id)
    subject = "Your ByteProof license is ready"
    text = (
        "Thank you for buying ByteProof.\n\n"
        "Your license is ready and will activate automatically.\n\n"
        "Click this link on the computer where ByteProof is installed:\n"
        f"{link}\n\n"
        "If the app doesn't open, run ByteProof and go to "
        "Settings -> License -> \"Already Paid? Activate with Email\", then "
        "enter the email you used at checkout.\n\n"
        "Each license works on up to 2 computers. To free a slot, choose "
        "\"Deactivate This Computer\" in Settings -> License.\n\n"
        "ByteMind Ltd\nhttps://www.bytemind.co.nz/byteproof"
    )
    html = (
        "<p>Thank you for buying ByteProof.</p>"
        "<p>Your license is ready and will activate automatically.</p>"
        f'<p><a href="{link}">Click here to activate ByteProof on this computer</a></p>'
        "<p>If the app doesn't open, run ByteProof and go to "
        "<strong>Settings → License → \"Already Paid? Activate with "
        "Email\"</strong>, then enter the email you used at checkout.</p>"
        "<p>Each license works on up to 2 computers. To free a slot, choose "
        "<strong>\"Deactivate This Computer\"</strong> in Settings → License.</p>"
        "<p>— ByteMind Ltd</p>"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("BYTEPROOF_SMTP_FROM") or os.environ.get(
        "BYTEPROOF_SMTP_USER", ""
    )
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send_activation_email(to_email: str, session_id: str) -> bool:
    """Send the activation email. Returns False when not configured/failed."""
    host = os.environ.get("BYTEPROOF_SMTP_HOST", "").strip()
    user = os.environ.get("BYTEPROOF_SMTP_USER", "").strip()
    password = os.environ.get("BYTEPROOF_SMTP_PASSWORD", "")
    if not host or not user or not password:
        print("Activation email skipped: SMTP not configured.")
        return False

    try:
        port = int(os.environ.get("BYTEPROOF_SMTP_PORT", "587"))
        use_tls = os.environ.get("BYTEPROOF_SMTP_TLS", "true").lower() != "false"
        msg = _build_message(to_email, session_id)
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Activation email sent to {to_email}")
        return True
    except Exception as exc:
        print(f"Activation email failed: {exc}")
        return False
