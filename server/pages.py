"""Small server-rendered pages: thank-you, license portal, plain messages."""

from __future__ import annotations

import time
from html import escape
from typing import Any

from .config import Settings

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #f6f7f9; color: #1f2430;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif;
  line-height: 1.55;
}
.wrap { max-width: 640px; margin: 0 auto; padding: 40px 20px 60px; }
h1 { font-size: 24px; margin: 0 0 6px; }
h2 { font-size: 16px; margin: 28px 0 8px; }
p.lead { color: #4b5563; margin: 0 0 24px; }
.card {
  background: #fff; border-radius: 14px; padding: 22px 24px;
  box-shadow: 0 1px 2px rgba(16,24,40,.06); margin-bottom: 16px;
}
.key {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 20px; font-weight: 600; letter-spacing: .5px;
  background: #f3f5f9; border-radius: 8px; padding: 12px 14px;
  display: inline-block; margin: 8px 0;
}
.btn {
  display: inline-block; background: #2f6fed; color: #fff; font-weight: 600;
  padding: 12px 20px; border-radius: 9px; text-decoration: none; border: 0;
  font-size: 15px; cursor: pointer;
}
.btn.secondary { background: #e8ecf4; color: #1f2430; }
.muted { color: #6b7280; font-size: 13px; }
.row {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 12px 0; border-top: 1px solid #eef1f6;
}
.row:first-of-type { border-top: 0; }
.pill {
  font-size: 12px; padding: 3px 9px; border-radius: 999px;
  background: #e7f3ea; color: #1d6b3a; font-weight: 600;
}
.pill.revoked { background: #fdeaea; color: #a12222; }
.pill.internal { background: #eef0ff; color: #3b3f8f; }
footer { margin-top: 28px; color: #6b7280; font-size: 13px; }
a { color: #2f6fed; }
"""


def _shell(title: str, body: str, settings: Settings) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · ByteProof</title>
  <style>{STYLE}</style>
</head>
<body>
  <div class="wrap">
    {body}
    <footer>
      ByteMind Ltd ·
      <a href="{escape(settings.product_url)}">bytemind.co.nz/byteproof</a> ·
      <a href="mailto:{escape(settings.support_email)}">{escape(settings.support_email)}</a>
    </footer>
  </div>
</body>
</html>"""


def message_page(title: str, message: str, settings: Settings) -> str:
    return _shell(
        title,
        f"<h1>{escape(title)}</h1><div class=\"card\"><p>{escape(message)}</p></div>",
        settings,
    )


def thanks_page(
    *,
    key: str,
    activate_url: str,
    portal_url: str,
    email: str,
    settings: Settings,
) -> str:
    return _shell(
        "Thanks for buying ByteProof",
        f"""
        <h1>Thanks for buying ByteProof</h1>
        <p class="lead">Your license key is below. We have also emailed it to
        {escape(email or "your checkout address")}.</p>
        <div class="card">
          <h2>Your license key</h2>
          <div class="key">{escape(key)}</div>
          <p><a class="btn" href="{escape(activate_url)}">Activate this computer</a></p>
          <p class="muted">If the button does not open ByteProof, open the app,
          go to Settings → License → “Already Paid? Activate with License
          Key”, and paste the key above.</p>
        </div>
        <div class="card">
          <h2>Two computers, moveable</h2>
          <p>Your key works on up to <strong>2 computers</strong>. To move it
          to another computer, release a slot in the license portal.</p>
          <p><a class="btn secondary" href="{escape(portal_url)}">Manage my licenses</a></p>
        </div>
        """,
        settings,
    )


def pending_payment_page(settings: Settings, session_id: str) -> str:
    return _shell(
        "Waiting for payment confirmation",
        f"""
        <h1>Almost there</h1>
        <div class="card">
          <p>Stripe has not confirmed this payment yet. Some payment methods
          (Alipay, for example) confirm a little later.</p>
          <p><a class="btn" href="/thanks?session_id={escape(session_id)}">
          Check again</a></p>
          <p class="muted">Your license key is emailed as soon as the payment
          confirms. If this page keeps appearing, contact
          {escape(settings.support_email)}.</p>
        </div>
        """,
        settings,
    )


def _machine_row(machine: dict[str, Any], token: str) -> str:
    label = str(machine.get("label") or "").strip() or "This computer"
    last_seen = int(machine.get("last_seen") or 0)
    when = (
        time.strftime("%d %b %Y", time.localtime(last_seen)) if last_seen else "—"
    )
    fingerprint = str(machine.get("machine_fp") or "")
    short = escape(fingerprint[:12])
    return f"""
    <div class="row">
      <div>
        <div><strong>{escape(label)}</strong></div>
        <div class="muted">{short}… · activated {when}</div>
      </div>
      <form method="post" action="/api/byteproof/portal/deactivate">
        <input type="hidden" name="token" value="{escape(token)}">
        <input type="hidden" name="machine_fp" value="{escape(fingerprint)}">
        <button class="btn secondary" type="submit">Release</button>
      </form>
    </div>"""


def _license_card(
    license: dict[str, Any],
    machines: list[dict[str, Any]],
    token: str,
) -> str:
    revoked = bool(license.get("revoked"))
    source = str(license.get("source") or "stripe")
    if revoked:
        pill = '<span class="pill revoked">Refunded / revoked</span>'
    elif source == "internal":
        pill = '<span class="pill internal">Internal</span>'
    else:
        pill = '<span class="pill">Active</span>'
    limit = license.get("device_limit")
    limit_text = "unlimited computers" if limit is None else f"up to {limit} computers"
    rows = "".join(_machine_row(m, token) for m in machines)
    if not rows:
        rows = '<div class="row muted">No computers activated yet.</div>'
    created = int(license.get("created_at") or 0)
    bought = time.strftime("%d %b %Y", time.localtime(created)) if created else "—"
    return f"""
    <div class="card">
      <div class="row">
        <div>
          <div class="key">{escape(str(license["key"]))}</div>
          <div class="muted">Issued {bought} · {escape(limit_text)}</div>
        </div>
        {pill}
      </div>
      {rows}
    </div>"""


def portal_page(
    *,
    email: str,
    licenses: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    token: str,
    settings: Settings,
    released: bool = False,
) -> str:
    released_note = (
        '<p class="pill">That computer has been released.</p>' if released else ""
    )
    cards = "".join(_license_card(item, machines, token) for item, machines in licenses)
    return _shell(
        "Your ByteProof licenses",
        f"""
        <h1>Your ByteProof licenses</h1>
        <p class="lead">Signed in as {escape(email)}. Releasing a computer
        frees the slot for another one.</p>
        {released_note}
        {cards}
        <div class="card">
          <p class="muted">Lost the key email? It is shown above — you can
          copy it any time. Need help? {escape(settings.support_email)}.</p>
        </div>
        """,
        settings,
    )
