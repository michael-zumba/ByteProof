# Stripe payments and ByteMind-owned licensing — Design

## Goal

Replace Polar with Stripe Checkout for payments, and replace Polar's license
key service with a small ByteMind-owned license service. The customer buys
once, receives one license key, and that key activates ByteProof on up to two
computers. Payment methods must include Alipay (WeChat Pay is not available to
New Zealand Stripe accounts and is out of scope). The owner's two internal
Polar keys keep working.

## Decisions

1. **Payments: Stripe Checkout via a Payment Link**, with Stripe Tax enabled
   (NZ GST registration, tax-inclusive NZ$49). Dynamic payment methods:
   card, Apple Pay, Google Pay, Link, Alipay.
2. **Licensing: our own service** (the existing FastAPI app in `server/`,
   revived and hardened), deployed on Render Starter + persistent disk.
3. **One key per purchase**, derived deterministically from the Stripe
   Checkout Session id
   (`BYTP-XXXX-XXXX-XXXX-XXXX`, base32 of HMAC-SHA256(secret, session_id)).
   Stripe is the purchase ledger; a lost database can be rebuilt from Stripe
   without losing anybody's key.
4. **Two computers per key**, enforced server-side by machine fingerprint.
   The server returns the existing RSA-signed, machine-bound license format
   the desktop app already validates offline.
5. **Self-service license portal** (email magic link, no password): shows the
   key, the activated computers, and lets the customer release a slot.
6. **Refunds and chargebacks revoke the key** via Stripe webhooks.
7. **Polar is retired from the runtime path.** Already-activated Polar
   machines keep working from local storage; the owner's two Polar keys are
   imported as internal licenses (no device limit) so they keep activating.

## Non-goals

- Subscriptions or recurring billing (ByteProof is a one-time purchase).
- WeChat Pay (blocked by Stripe for NZ accounts; revisit with a non-NZ Stripe
  account or a second provider).
- Tax registrations outside NZ (Stripe Tax collects what the registrations
  allow; more registrations can be added later in the Stripe Dashboard).
- Moving the website off GitHub Pages.

## Architecture

```
Buyer ──> Stripe Checkout (Payment Link) ──> webhook ──> License service
              │                                              │
              │  success_url                                 │ derives key
              v                                              │ from session id
        /thanks page (shows key, opens app)                  │
                                                             v
ByteProof app ── POST /activate {key, fingerprint} ──> registers machine (max 2)
              <─ signed machine-bound license key ──────────┘
              ── POST /validate  (startup, best effort)
              ── POST /deactivate (frees the slot)

Customer ──> /portal (email magic link) ──> see key, release a computer
```

### Components

- `server/activation_api.py` — HTTP surface: webhook, activate, validate,
  deactivate, portal, thanks page, admin.
- `server/license_store.py` — SQLite storage (licenses, activations, webhook
  events, portal tokens) with WAL mode.
- `server/license_keys.py` — deterministic key derivation and the RSA-signed
  machine-bound license format shared with the desktop app.
- `server/stripe_sync.py` — Stripe lookups and the nightly reconcile that
  rebuilds license rows from paid Checkout Sessions.
- `server/emailer.py` — license email, portal link email (SMTP; optional
  Resend API key).
- `src/license_api.py` — desktop client for the service (replaces
  `src/polar.py` in the runtime path).

## Data model

```
licenses(key PK, session_id UNIQUE NULL, email, created_at, revoked,
         device_limit NULL, source 'stripe'|'internal', last_event_at)
activations(key, machine_fp, label, created_at, last_seen, PK(key,machine_fp))
webhook_events(id PK, type, received_at)          -- idempotency
portal_tokens(token_hash PK, email, expires_at)   -- magic links
```

`device_limit NULL` means unlimited (internal licenses). Activation is
idempotent per (key, machine_fp): a reinstall or re-activation returns the
same signed key without consuming a slot.

## Service API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/byteproof/activate` | `{key, machine_fingerprint, label}` → `{license_key, email, device_count, device_limit}` |
| POST | `/api/byteproof/validate` | `{key, machine_fingerprint}` → `{ok, valid, revoked, device_count}` |
| POST | `/api/byteproof/deactivate` | `{key, machine_fingerprint}` → `{ok}` |
| POST | `/api/byteproof/portal/request` | `{email}` → always `{ok}`; emails a magic link when a license exists |
| GET | `/api/byteproof/portal?token=…` | HTML: key, computers, release buttons |
| POST | `/api/byteproof/portal/deactivate` | `{token, machine_fp}` |
| GET | `/thanks?session_id=…` | Post-checkout page: key + "Open ByteProof" |
| POST | `/api/byteproof/stripe-webhook` | `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `charge.refunded`, `charge.dispute.created` |
| GET | `/health` | Liveness + signer configured |

Admin (header `X-Admin-Token`, env `BYTEPROOF_ADMIN_TOKEN`): list licenses,
revoke/restore, re-send key email. A `scripts/license_admin.py` CLI wraps it.

## Behaviour details

- **Fulfilment** runs only when `payment_status == "paid"`. Alipay is a
  delayed method, so `checkout.session.completed` may arrive unpaid and the
  real signal is `checkout.session.async_payment_succeeded`.
- **Idempotency**: every webhook event id is recorded; repeated deliveries are
  no-ops. Key derivation is deterministic, so a double fulfilment produces the
  same key.
- **Revocation**: full refund or dispute → `revoked = 1`. The app shows a
  blocking license dialog on the next successful online check.
- **Offline behaviour**: the signed key validates offline (signature +
  fingerprint); a failed online check never locks a working license.
- **Portal**: tokens are random 32-byte values, stored hashed, 30-minute
  expiry, single use per action. `request` never reveals whether an email is
  known.
- **Emails**: license email carries the key, a `byteproof://activate?key=…`
  button, the portal link, and the 2-computer note. SMTP if configured, else
  Resend if `RESEND_API_KEY` is set; otherwise the key is still shown on the
  thanks page.
- **Backups**: nightly `VACUUM INTO` snapshot on the disk, plus a weekly
  backup email to the support address when SMTP is configured. Keys are
  re-derivable from Stripe regardless.

## Desktop app changes

- `src/settings.py`: `LICENSE_API_URL` (default
  `https://api.bytemind.co.nz`), `PURCHASE_URL` (Stripe Payment Link);
  Polar constants removed from the runtime path.
- `src/license_api.py` (new): activate/validate/deactivate against the service.
- `src/activation.py`: keys go to the service; `byteproof://activate?key=…`
  and `?session=…` handled; legacy email-server path removed; dev-email path
  unchanged (still local-only).
- `src/licensing.py`: new `provider: "stripe"` records keep the signed
  machine-bound key plus the purchase key; existing `polar` records keep
  validating locally; revoked handling surfaced to the UI.
- `src/gui.py`: license tab copy (key from the receipt email; "Manage my
  licenses" opens the portal); activation dialog wording; revoked notice.
- Version: beta bump + `version_info.txt` mirror per the repo rule.

## Operations

- Render Starter web service (Singapore) + 1 GB persistent disk mounted at
  `/data`; Docker image from the existing `Dockerfile`.
- Env: `STRIPE_SECRET_KEY` (restricted `rk_` key: checkout sessions read,
  charges read, customers read), `STRIPE_WEBHOOK_SECRET`,
  `BYTEPROOF_LICENSE_PRIVATE_KEY`, `BYTEPROOF_KEY_SECRET`,
  `BYTEPROOF_INTERNAL_KEYS`, `BYTEPROOF_DATA_DIR=/data`,
  `BYTEPROOF_ADMIN_TOKEN`, SMTP/Resend vars.
- `scripts/stripe_setup.py` creates/verifies the product, price, Payment
  Link, and webhook endpoint for a given key (sandbox first, live later).

## Security

- The desktop app never holds a Stripe key; it only talks to the license
  service.
- The service holds a restricted Stripe key with read-only scopes.
- License keys are bearer secrets: stored in SQLite on the disk, hashed
  portal tokens, no keys in logs.
- Activation and portal endpoints are rate limited per IP.

## Testing

1. `pytest` server suite: activation limit, idempotent re-activation,
   deactivation frees a slot, revoked rejection, webhook idempotency,
   deterministic derivation, internal keys, portal tokens, reconcile.
2. Stripe sandbox end-to-end: checkout → webhook → key → 2 activations →
   third refused → deactivate → third accepted → refund revokes.
3. Alipay delayed-payment path: unpaid `completed` then
   `async_payment_succeeded` issues exactly one key.
4. Desktop app: existing suite plus new activation/validation/deactivation
   tests against a fake service; live run against a local service with a
   sandbox key and the two internal keys.

## Rollout

1. Land the service and app changes on a branch; prove tests 1–4.
2. Owner creates the live Stripe Payment Link + webhook (checklist, or the
   setup script with a restricted key) and deploys Render.
3. Update `byteproof.html` and `BYTEMIND_SETUP.md` to Stripe copy.
4. Ship a beta build; owner installs, buys a live NZ$49 license with a real
   card, activates two machines, and refunds one test purchase to confirm
   revocation.
