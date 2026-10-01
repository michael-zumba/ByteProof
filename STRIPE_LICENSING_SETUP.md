# ByteProof payments and licence server — setup guide

This is the step-by-step guide for taking ByteProof's payments and licence
issuing live: Stripe takes the money, a small service on Render issues one
key per purchase and enforces the 2-computer limit.

Follow it in order. Parts A and B can be done in Stripe **test mode** first
(recommended), and repeated with live keys when the rehearsal passes.

---

## 0. The moving pieces

| Piece | What it does | Where it lives | Cost |
| --- | --- | --- | --- |
| Stripe | Checkout page, tax, receipts, refunds | stripe.com (your account) | Per transaction (Stripe's fees) |
| Licence service | Issues keys, 2-computer limit, portal, revocation | Render (`server/` in this repo) | ~US$7/month + ~US$0.25 for the 1 GB disk |
| Website | The Buy button that opens Stripe Checkout | GitHub Pages (`ByteMind_Website`) | Free |
| Email | Sends the key and portal links | Gmail SMTP (already used) or Resend | Free at this volume |
| ByteProof app | Activates with the key, phones home to validate | macOS/Windows builds | — |

Flow: **Buyer → Stripe Checkout → webhook → licence service → key emailed →
app activates on ≤2 computers → portal to move between computers → refund
revokes.**

What you need before starting:

- Access to the ByteMind Stripe account (NZ entity) and to its Dashboard.
- Access to the Render account that hosts the service.
- Access to the DNS provider for `bytemind.co.nz`.
- The signing key file `tools/generate_license.py` (local, never committed).
- About an hour, plus a real card for the final live test purchase.

---

## Part A — Stripe

### A1. Confirm the account

1. Sign in at <https://dashboard.stripe.com>.
2. **Settings → Business details**: the legal entity must be ByteMind Ltd,
   country New Zealand, with a **head office address**. Stripe Tax will not
   work without it (it refuses to save an `automatic_tax` Payment Link).
3. **Settings → Payouts**: confirm bank details and that payouts are enabled.
   A live test purchase cannot be refunded to a card if payouts are blocked
   during verification.

### A2. Turn on tax (NZ GST)

1. **Tax → Registrations → Add registration**.
2. Choose **New Zealand**, registration type **GST**, enter your IRD GST
   number and the date you registered, then save.
3. Back on **Tax**, confirm the registration shows as *active* and that the
   address matches the business details.
4. Check the price behaviour: the Payment Link uses a **tax-inclusive**
   NZ$49 price, so a New Zealand customer always pays NZ$49 with 15% GST
   inside it; a customer outside New Zealand pays NZ$49 with no NZ GST.

> Stripe only collects tax for jurisdictions where you have an active
> registration. Selling digital services to the UK or the EU normally means
> registering there too; add those registrations the same way when you are
> ready, and Stripe Tax starts collecting automatically.

### A3. Enable the payment methods

1. **Settings → Payment methods**.
2. Enable, and keep enabled:
   - **Cards** (Visa, Mastercard, Amex, JCB…)
   - **Apple Pay** and **Google Pay** (they ride on the card rails)
   - **Link** (Stripe's one-click wallet)
   - **Alipay** ← this is the one for Chinese customers
3. **WeChat Pay cannot be enabled on a New Zealand Stripe account.** Stripe
   supports WeChat Pay only for accounts in AU, CA, EU, GB, HK, JP, SG, US
   and a few others. Alipay is the supported Chinese wallet here; WeChat
   would need a Stripe account in a supported country or a second provider
   (e.g. Airwallex) later.

You do **not** need to do anything else for these to appear: the service
never pins `payment_method_types`, so Checkout shows the dynamic set
Stripe thinks is most relevant to each customer.

### A4. Create the product, price, Payment Link and webhook

The script does all four, idempotently. Run it from the repository root.

**Option 1 — the script (recommended).** Create a temporary key in
**Developers → API keys → Create restricted key** with:

| Resource | Permission |
| --- | --- |
| Products | Write |
| Prices | Write |
| Payment Links | Write |
| Webhook Endpoints | Write |

then:

```bash
cd "/path/to/ByteProof"
STRIPE_API_KEY=rk_live_... .venv_server/bin/python scripts/stripe_setup.py \
    --base-url https://api.bytemind.co.nz
```

(`.venv_server` is created by `./scripts/run_server_tests.sh`; any Python
with `stripe` installed works, e.g. `python scripts/stripe_setup.py`.)

It prints something like:

```
Product:      prod_...
Price:        price_... (49.00 nzd, tax-inclusive)
Payment Link: https://buy.stripe.com/...
Webhook:      we_... -> https://api.bytemind.co.nz/api/byteproof/stripe-webhook
Webhook secret (store as STRIPE_WEBHOOK_SECRET): whsec_...
```

Record the **Payment Link** and the **webhook secret** — you will need both
in Parts B and D. For an unclaimed test sandbox add `--no-automatic-tax`
(test sandboxes have no tax registration); never use that flag for live.

**Option 2 — by hand.** In the Dashboard: **Products → Add product**
(name ByteProof, description "ByteProof desktop proofreading — one licence,
up to 2 computers.", price NZ$49 one-time, **tax behaviour: inclusive**),
then **Payment Links → New** (choose the price, set the after-payment
redirect to `https://api.bytemind.co.nz/thanks?session_id={CHECKOUT_SESSION_ID}`,
turn on automatic tax, and leave payment methods dynamic), then
**Developers → Webhooks → Add endpoint** to
`https://api.bytemind.co.nz/api/byteproof/stripe-webhook` with these events:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `charge.refunded`
- `charge.dispute.created`
- `charge.dispute.closed`

The first two matter for Alipay: delayed methods confirm *after* the customer
leaves Checkout, so the key is issued when the async event arrives.

### A5. Create the key the server itself uses

**Developers → API keys → Create restricted key**, name it
`byteproof-license-service`, and give it exactly:

| Resource | Permission |
| --- | --- |
| Checkout Sessions | Read |
| Charges | Read |

Nothing else. The running service never writes to Stripe, so it does not
need write scopes (and a leaked key cannot move money). Copy the `rk_live_...`
value into Part B as `STRIPE_SECRET_KEY`.

### A6. (Optional) Rehearse in test mode

Do Part B with **test-mode** keys and the test Payment Link first
(`sk_test_...`/`rk_test_...`, and a webhook created against the same URL).
Pay with card `4242 4242 4242 4242`, any future expiry, any CVC, any email.
Everything in Part E's "verification" list works identically in test mode,
with no real money. When it all passes, repeat A4/A5 with live keys and swap
the four secret values on Render.

---

## Part B — the licence service on Render

### B1. Deploy

1. Sign in at <https://dashboard.render.com>.
2. **New → Blueprint**, choose the `michael-zumba/ByteProof` repository.
   Render reads `render.yaml` and proposes one service,
   `byteproof-license-service` (Docker, Starter plan, Singapore region,
   1 GB disk mounted at `/data`, health check `/health`).
3. Apply. Render asks for the secrets marked `sync: false`; use the table
   below.

### B2. Environment variables

| Variable | Value | Record it? |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | the restricted key from A5 (`rk_live_...`) | on Render only |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` from A4 | on Render only |
| `BYTEPROOF_KEY_SECRET` | a long random string (e.g. `openssl rand -hex 32`) | **yes — losing it means keys can no longer be re-derived** |
| `BYTEPROOF_LICENSE_PRIVATE_KEY` | the PEM text from `tools/generate_license.py` (between and including the BEGIN/END lines) | keep with your backups |
| `BYTEPROOF_INTERNAL_KEYS` | `BYTEPROOF_-AD00C43E-0421-4EB7-83DC-457B1A85EB19,BYTEPROOF_-01654728-D3AB-4274-9AE3-B1E5CA0342B2` | no |
| `BYTEPROOF_ADMIN_TOKEN` | a long random string | **yes — needed for the support CLI** |
| `BYTEPROOF_PUBLIC_BASE_URL` | `https://api.bytemind.co.nz` | set by the blueprint |
| `BYTEPROOF_DATA_DIR` | `/data` | set by the blueprint |
| `BYTEPROOF_SMTP_HOST` | `smtp.gmail.com` (or your provider) | no |
| `BYTEPROOF_SMTP_PORT` | `587` | no |
| `BYTEPROOF_SMTP_USER` | the Gmail address that sends licence emails | no |
| `BYTEPROOF_SMTP_PASSWORD` | a Gmail **app password**, never the account password | on Render only |
| `BYTEPROOF_SMTP_FROM` | `licenses@bytemind.co.nz` (must be sendable by the account) | no |
| `BYTEPROOF_BACKUP_EMAIL` | an address that should receive the weekly database snapshot | no |

The blueprint's `autoDeploy: false` means pushes do not go live by
themselves: press **Manual Deploy → Deploy latest commit** when you want a
new version, which is what you want for a payment system.

### B3. Email

Gmail SMTP (fastest, no new vendor):

1. Turn on 2-Step Verification on the Google account.
2. Google Account → **Security → App passwords** → create one named
   "ByteProof licence service".
3. Use it as `BYTEPROOF_SMTP_PASSWORD`.

Gmail's limits (about 500 recipients/day) are far above this volume. If
deliverability becomes a problem, create a free Resend account, add a domain
there, and publish the DNS records Resend shows you for `bytemind.co.nz`;
then set `RESEND_API_KEY` on Render. The service prefers SMTP when both are
present.

Test the local behaviour any time with:

```bash
pip install aiosmtpd
python scripts/dev_smtp_catcher.py --directory /tmp/byteproof-mail
```

### B4. Custom domain and DNS

1. Render → the service → **Settings → Custom Domains → Add**,
   `api.bytemind.co.nz`.
2. Render shows a CNAME target such as
   `byteproof-license-service.onrender.com`.
3. At your DNS provider add **one new record** (do not touch the existing
   `www`/apex records that serve the website):

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| CNAME | `api` | `byteproof-license-service.onrender.com` | 300 |

4. Wait a few minutes; Render issues the TLS certificate automatically.

### B5. Verify the service

```bash
curl -s https://api.bytemind.co.nz/health
```

Expected:

```json
{"status":"ok","license_signer_configured":true,"stripe_configured":true,
 "licenses":2,"activations":0}
```

- `license_signer_configured:false` → `BYTEPROOF_LICENSE_PRIVATE_KEY` is
  missing or malformed.
- `stripe_configured:false` → `STRIPE_SECRET_KEY` is missing.
- `licenses:2` at first boot → the two owner keys were seeded.

Then run the licence-service test suite locally against the code you just
deployed:

```bash
./scripts/run_server_tests.sh
```

### B6. Point Stripe at the deployment

If you deployed before creating the webhook, go back to A4 and create it now
(the URL must be the final `https://api.bytemind.co.nz/...`). Stripe retries
failed deliveries for up to three days, so small deploy windows do not lose
sales.

---

## Part C — Switching the website

Two files, in `ByteMind_Website`:

- `byteproof.html` (English)
- `zh/byteproof.html` (Chinese)

In each:

1. **Buy button** (line ~540): replace the `https://buy.polar.sh/...` href
   with the live Payment Link from A4.
2. **"Polar emails your key…" copy** (line ~547): change to "Stripe emails
   your key after checkout" (zh: "Stripe 会把密钥发到你的邮箱"), and mention
   that **Alipay / 支付宝** is accepted.
3. **"Secure checkout via Polar"** (line ~559): change to Stripe
   (zh: "通过 Stripe 安全结账").
4. **The second mention** (line ~669): same wording change.

Commit and push the website repository as usual (GitHub Pages redeploys in
about a minute). Keep the Polar account open until this change is live.

---

## Part D — Releasing the app

The app ships pointing at `https://api.bytemind.co.nz` and at the website's
buy section. To cut a beta:

```bash
cd "/path/to/ByteProof"
python tools/bump_version.py 2.3.0-beta.2 "Stripe checkout and the licence service"
python scripts/check_version.py     # must print the same version twice
./build_macos.sh                    # add notarisation before a public release
```

Install the beta on two machines, then work through Part E.

If you want the app's **Purchase** button to open Stripe directly instead of
the website, set `BYTEPROOF_PURCHASE_URL` to the Payment Link and rebuild.

---

## Part E — Going live, in order

1. `curl https://api.bytemind.co.nz/health` → both flags true.
2. Buy one real licence on the website with a real card. Confirm:
   - the checkout shows NZ$49 (GST inside for a NZ address),
   - the thank-you page shows a `BYTP-...` key,
   - the key email arrives with a working activation link,
   - Stripe shows `checkout.session.completed` delivered with a 200.
3. Paste the key into ByteProof on two machines (Settings → License →
   "Already Paid? Activate with License Key"). Confirm a third is refused
   with the limit message.
4. In the app, **Manage My Licenses** → the portal link email arrives →
   release one computer → the released machine shows the "Activation
   Released" notice at next launch and asks for the key again.
5. Refund the test purchase in Stripe. The next launch shows
   **License Revoked**. (Support can undo this with `restore` below.)
6. Buy one more licence and keep it, so the account has a clean live
   transaction.

---

## Part F — Day-2 operations

### Support CLI

```bash
export BYTEPROOF_LICENSE_API=https://api.bytemind.co.nz
export BYTEPROOF_ADMIN_TOKEN=...        # the value from B2

python scripts/license_admin.py list
python scripts/license_admin.py list --email buyer@example.com
python scripts/license_admin.py revoke BYTP-....
python scripts/license_admin.py restore BYTP-....
python scripts/license_admin.py resend BYTP-....
```

Typical tasks:

| Customer says | Do this |
| --- | --- |
| "I lost my key" | `resend` (or tell them to use Manage My Licenses) |
| "I'm on my third computer" | the portal: release the old computer, then activate |
| "I changed hardware" | `list --email`, release the stale fingerprint in the portal |
| "Refund me" | refund in Stripe; the key revokes itself |
| "I won my chargeback" | `restore` (automatic when Stripe reports the dispute won) |
| "My key stopped working" | `list --email`; check `revoked`; check activations |

### Backups and recovery

- The service keeps SQLite on the Render disk and writes a daily snapshot to
  `/data/backups/licenses-YYYY-MM-DD.sqlite3` (last 14), plus a weekly copy
  by email when `BYTEPROOF_BACKUP_EMAIL` is set.
- **Worst case (disk lost):** deploy again with the same
  `BYTEPROOF_KEY_SECRET` and `BYTEPROOF_LICENSE_PRIVATE_KEY`. Every key is
  re-derived from Stripe on first use, so customers can activate
  immediately; existing activated machines keep working offline regardless.
- **Moving the service:** copy the SQLite file out of `/data` first, or
  accept re-activation (customers are not locked out — their signed licences
  stay valid locally).
- **Rotating secrets:** `STRIPE_WEBHOOK_SECRET` and `BYTEPROOF_ADMIN_TOKEN`
  can be rotated freely. Rotate `BYTEPROOF_KEY_SECRET` only if you accept
  that old keys must be looked up in the database (keep a backup), because
  they can no longer be re-derived from Stripe.

### Monitoring

- Render → the service → **Logs**: look for `Fulfilled`, `Refund revoked`,
  `Reconciled`, and any `Email send failed`.
- Render → **Events**: re-deploys and restarts.
- Stripe → **Developers → Webhooks → the endpoint**: a red row means a
  delivery failed; press *Resend*. The nightly reconcile also repairs a
  missed fulfilment within a day.
- Stripe → **Payments**: refunds and disputes are the two events that revoke.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| App: "could not find that license key" | typo, or the webhook never arrived | check the key in the portal; check the webhook log; the reconcile fixes it within a day |
| App: "reached its limit of 2 computers" | two other computers hold the seats | portal → release one |
| No key email | SMTP not configured or rejected | Render logs → `Email send failed`; the key is still on the thank-you page and `resend` works |
| Stripe shows the webhook failing (400) | wrong `STRIPE_WEBHOOK_SECRET` | copy the signing secret again (B2) |
| Stripe shows the webhook failing (503/500) | service down or no disk/DB | check `/health` and Render logs |
| Alipay not offered | not enabled in Stripe, or customer not in an Alipay-eligible market | Settings → Payment methods |
| Tax is zero on a NZ order | no active registration, or head office address missing | Tax → Registrations (A2) |
| Tax is charged to overseas buyers | a registration exists for their country | expected; Stripe collects where you are registered |
| Portal link "expired" | links last 30 minutes | request a new one from the app, or `resend` |
| After a redeploy, licences are missing | disk not mounted at `/data` | Render → Disks; the blueprint creates one |
| Local test: activation fails | service reachable? `BYTEPROOF_LICENSE_API_URL` correct? | `curl .../health` |

## Cost summary

| Item | Monthly |
| --- | --- |
| Render Starter instance | ~US$7 |
| 1 GB persistent disk | ~US$0.25 |
| Stripe | per-transaction fees only (plus Alipay's rate where used) |
| Gmail SMTP or Resend | free at this volume |

Check Render's checkout for current prices; the blueprint shows them before
you apply.
