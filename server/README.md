# ByteProof licence service

Turns a Stripe payment into one ByteProof licence key and enforces the
2-computer limit. Stripe is the purchase ledger; this service owns the
activations, the licence portal, and refund revocation.

The full setup guide (Stripe account, tax, Render, DNS, email, website
cutover, support operations) is `STRIPE_LICENSING_SETUP.md` in the repository
root; this file is the technical reference for the service itself.

```
Buyer -- Stripe Checkout (Payment Link) -- webhook --> this service
   |                                                     | key = HMAC(session id)
   +-- success page shows the key                         | emails the key
                                                         v
ByteProof app -- POST /activate {key, fingerprint} --> up to 2 computers
              <-- signed, machine-bound licence -------+
Customer -- email magic link --> portal: see the key, release a computer
Stripe refund / chargeback --> key revoked on the next online check
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/byteproof/stripe-webhook` | Stripe fulfilment and revocation |
| POST | `/api/byteproof/activate` | Register this computer (key, or `session_id` from a checkout link) |
| POST | `/api/byteproof/validate` | Is this licence good on this computer? |
| POST | `/api/byteproof/deactivate` | Free this computer's slot |
| POST | `/api/byteproof/portal/request` | Email a licence-portal magic link |
| GET | `/api/byteproof/portal?token=...` | Portal page: key, computers, release buttons |
| POST | `/api/byteproof/portal/deactivate` | Release a computer from the portal |
| GET | `/thanks?session_id=...` | Post-checkout page that shows the key |
| GET | `/health` | Liveness, signer/Stripe status, row counts |
| GET/POST | `/api/byteproof/admin/...` | Admin list, revoke, restore, resend (`X-Admin-Token`) |

## Configuration (environment)

| Variable | Purpose |
| --- | --- |
| `STRIPE_SECRET_KEY` | Stripe **restricted** key (`rk_`). The server needs Checkout Sessions: Read and Charges: Read only. |
| `STRIPE_WEBHOOK_SECRET` | Signing secret of the webhook endpoint (`whsec_...`). |
| `BYTEPROOF_KEY_SECRET` | Secret that derives licence keys from Checkout Session ids. **Keep it:** without it, keys cannot be re-derived after a database loss. |
| `BYTEPROOF_LICENSE_PRIVATE_KEY` | RSA private key (PEM) that signs machine-bound licences. Same key as `tools/generate_license.py`. |
| `BYTEPROOF_INTERNAL_KEYS` | Comma/space-separated owner keys (no device limit, no expiry). |
| `BYTEPROOF_ADMIN_TOKEN` | Enables the admin API and `scripts/license_admin.py`. |
| `BYTEPROOF_DATA_DIR` | SQLite location (Render disk, e.g. `/data`). |
| `BYTEPROOF_PUBLIC_BASE_URL` | Public base URL used in emails, links and redirects. |
| `BYTEPROOF_SMTP_*` | SMTP delivery (`HOST`, `PORT`, `USER`, `PASSWORD`, `FROM`, `TLS`). |
| `RESEND_API_KEY` | Alternative delivery path; used when SMTP is not configured. |
| `BYTEPROOF_BACKUP_EMAIL` | Receives the daily database snapshot when SMTP or Resend works. |

Emails are best-effort: a delivery failure never blocks a sale or an
activation, and the key is always shown on the thank-you page and in the
portal.

## Local development

```bash
./scripts/run_server_tests.sh                  # creates .venv_server on first run

export STRIPE_SECRET_KEY=sk_test_...           # or a stripe sandbox key
export STRIPE_WEBHOOK_SECRET=whsec_...         # from `stripe listen`
export BYTEPROOF_KEY_SECRET=dev-key-secret
export BYTEPROOF_DATA_DIR=/tmp/byteproof-data
export BYTEPROOF_LICENSE_PRIVATE_KEY="$(python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("gen", "tools/generate_license.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.PRIVATE_KEY_PEM.decode())
PY
)"
uvicorn server.activation_api:app --host 127.0.0.1 --port 8000
```

In another terminal, forward real Stripe events (sandbox or test mode):

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.async_payment_succeeded,charge.refunded,charge.dispute.created,charge.dispute.closed \
  --forward-to http://127.0.0.1:8000/api/byteproof/stripe-webhook
```

And, to capture licence emails locally instead of sending them:

```bash
pip install aiosmtpd
python scripts/dev_smtp_catcher.py --directory /tmp/byteproof-mail
```

## Stripe setup

```bash
STRIPE_API_KEY=sk_... python scripts/stripe_setup.py \
    --base-url https://api.bytemind.co.nz
```

Creates (or reuses) the product, a tax-inclusive NZ$49 price, the Payment
Link whose success page shows the key, and the webhook endpoint with the five
events this service needs. Add `--no-automatic-tax` for an unclaimed sandbox
(no head office address or tax registration).

Enable Alipay in the Stripe Dashboard under **Settings -> Payment methods**.
WeChat Pay is not available to New Zealand Stripe accounts.

## Data and recovery

SQLite on `BYTEPROOF_DATA_DIR` holds licences, activations, portal tokens and
webhook ids. Nightly jobs reconcile paid Checkout Sessions from Stripe and
write `backups/licenses-YYYY-MM-DD.sqlite3` (last 14 kept).

Recovery is designed so a lost database is not a lost sale: licence keys are
derived from the Checkout Session id with `BYTEPROOF_KEY_SECRET`, so
`/activate` re-derives any key by reconciling Stripe, and a support email can
be re-sent from the portal or the admin API.

## Operations

- Run one instance (SQLite + Render disk) behind a health check on `/health`.
- Rotate `STRIPE_WEBHOOK_SECRET` and `BYTEPROOF_ADMIN_TOKEN` if they leak.
- `scripts/license_admin.py` lists, revokes, restores and re-sends licences.
- The desktop app only ever calls `/activate`, `/validate`, `/deactivate` and
  `/portal/request`; it never holds a Stripe key.
