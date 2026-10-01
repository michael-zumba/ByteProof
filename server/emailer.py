"""License and portal emails.

Two delivery paths, chosen by configuration:
  1. SMTP (BYTEPROOF_SMTP_* variables) — what the owner already uses.
  2. Resend (RESEND_API_KEY) — better deliverability once the domain's DNS
     records exist.

Every send is best-effort: a failed email never blocks a payment or an
activation, and the key is always visible on the thank-you page and in the
license portal.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from .config import Settings


def _html_shell(title: str, body: str, settings: Settings) -> str:
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f6f7f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f2430;">
    <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
      <h1 style="font-size:20px;margin:0 0 16px;">{title}</h1>
      <div style="background:#ffffff;border-radius:12px;padding:24px;line-height:1.55;">
        {body}
      </div>
      <p style="font-size:12px;color:#6b7280;margin-top:20px;">
        ByteMind Ltd · <a href="{settings.product_url}" style="color:#6b7280;">{settings.product_url}</a>
        · <a href="mailto:{settings.support_email}" style="color:#6b7280;">{settings.support_email}</a>
      </p>
    </div>
  </body>
</html>"""


def _key_block(key: str) -> str:
    return (
        '<p style="font-size:22px;font-weight:600;letter-spacing:1px;'
        'font-family:ui-monospace,Menlo,Consolas,monospace;margin:16px 0;">'
        f"{key}</p>"
    )


def license_email_bodies(
    key: str,
    activate_url: str,
    portal_url: str,
    settings: Settings,
) -> tuple[str, str, str]:
    subject = "Your ByteProof license key"
    text = (
        "Thank you for buying ByteProof.\n\n"
        f"Your license key:\n\n    {key}\n\n"
        "Activate this computer by clicking this link on the computer where\n"
        "ByteProof is installed:\n"
        f"{activate_url}\n\n"
        "Or open ByteProof, go to Settings -> License ->\n"
        '"Already Paid? Activate with License Key" and paste the key.\n\n'
        "Your key works on up to 2 computers. To move ByteProof to another\n"
        "computer, open the license portal and release a slot:\n"
        f"{portal_url}\n\n"
        "Keep this email: the key and the portal link are all you need.\n\n"
        "ByteMind Ltd\n"
        f"{settings.product_url}\n"
    )
    html = _html_shell(
        "Your ByteProof license key",
        "<p>Thank you for buying ByteProof. Your license key is:</p>"
        + _key_block(key)
        + f'<p><a href="{activate_url}" '
        'style="display:inline-block;background:#2f6fed;color:#fff;'
        'padding:12px 20px;border-radius:8px;text-decoration:none;'
        'font-weight:600;">Activate this computer</a></p>'
        "<p>If the button does not open ByteProof, go to "
        "<strong>Settings → License → “Already Paid? Activate with License "
        "Key”</strong> and paste the key above.</p>"
        "<p>Your key works on up to <strong>2 computers</strong>. To move "
        "ByteProof to another computer, open the license portal and release "
        f'a slot: <a href="{portal_url}">{portal_url}</a>.</p>'
        "<p>Keep this email: the key and the portal link are all you need.</p>",
        settings,
    )
    return subject, text, html


def portal_email_bodies(
    portal_url: str,
    settings: Settings,
) -> tuple[str, str, str]:
    subject = "Your ByteProof license portal link"
    text = (
        "Use this link to see your ByteProof license key and manage the\n"
        "computers it is active on (the link expires in 30 minutes):\n\n"
        f"{portal_url}\n\n"
        "If you did not ask for this, you can ignore this email.\n\n"
        "ByteMind Ltd\n"
        f"{settings.product_url}\n"
    )
    html = _html_shell(
        "Your ByteProof license portal link",
        "<p>Use this link to see your license key and manage the computers it "
        "is active on. The link expires in 30 minutes.</p>"
        f'<p><a href="{portal_url}">Open the license portal</a></p>'
        "<p>If you did not ask for this, you can ignore this email.</p>",
        settings,
    )
    return subject, text, html


def _smtp_send(
    settings: Settings,
    to_email: str,
    subject: str,
    text: str,
    html: str,
    attachment: tuple[str, bytes] | None = None,
) -> bool:
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = to_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    if attachment is not None:
        name, payload = attachment
        message.add_attachment(
            payload,
            maintype="application",
            subtype="sqlite3",
            filename=name,
        )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
        return True
    except Exception as exc:  # pragma: no cover - network failure path
        print(f"Email send failed via SMTP: {exc}")
        return False


def _resend_send(
    settings: Settings,
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> bool:
    if not settings.resend_api_key:
        return False
    payload = json.dumps(
        {
            "from": f"ByteProof <{settings.smtp_from or settings.support_email}>",
            "to": [to_email],
            "subject": subject,
            "text": text,
            "html": html,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # pragma: no cover - network failure path
        print(f"Email send failed via Resend: {exc}")
        return False


def send_email(
    settings: Settings,
    to_email: str,
    subject: str,
    text: str,
    html: str,
    attachment: tuple[str, bytes] | None = None,
) -> bool:
    if not to_email:
        return False
    if _smtp_send(settings, to_email, subject, text, html, attachment):
        return True
    if _resend_send(settings, to_email, subject, text, html):
        return True
    print(
        f"Email to {to_email} skipped: no SMTP or Resend configuration "
        "(payment and activation are unaffected)."
    )
    return False


def send_license_email(
    settings: Settings,
    to_email: str,
    key: str,
    activate_url: str,
    portal_url: str,
) -> bool:
    subject, text, html = license_email_bodies(
        key, activate_url, portal_url, settings
    )
    return send_email(settings, to_email, subject, text, html)


def send_portal_email(
    settings: Settings,
    to_email: str,
    portal_url: str,
) -> bool:
    subject, text, html = portal_email_bodies(portal_url, settings)
    return send_email(settings, to_email, subject, text, html)
