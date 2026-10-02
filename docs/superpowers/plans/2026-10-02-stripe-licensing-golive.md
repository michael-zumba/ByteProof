# Stripe licensing go-live plan

Design: `docs/superpowers/specs/2026-10-02-stripe-licensing-design.md`.
Status: **built and verified in a Stripe sandbox** (see "Verification" below).
What remains is account-level work that needs the owner's Stripe, Render and
DNS access.

## What is already done

- `server/` is a working licence service: Stripe webhook fulfilment
  (including delayed methods), one key per purchase derived from the Checkout
  Session id, a 2-computer limit, signed machine-bound licences, an email
  magic-link portal, refund/chargeback revocation, admin API and nightly
  reconciliation/backups.
- The desktop app talks to it (`src/license_api.py`, `src/activation.py`,
  `src/licensing.py`, `src/gui.py`): key activation, `byteproof://activate`
  links (key or session), online validation with a revoked notice, offline
  tolerance, "Manage My Licences", and pre-Polar signed keys still activate
  locally. Polar is no longer in the runtime path.
- Operator tooling: `scripts/stripe_setup.py`, `scripts/license_admin.py`,
  `scripts/run_server_tests.sh`, `scripts/dev_smtp_catcher.py`, a CI job for
  the service, and a Render blueprint with the persistent disk.

## Go-live checklist (owner)

### 1. Stripe (live mode)

**Done on 2026-10-02** — see
`docs/aegis/work/2026-10-02-stripe-licensing/30-live-stripe-setup.md`:
product `prod_VMg2Qo89P7u2bM`, price `price_1ULwgu2BJbkpIesfs1siXnAk`
(NZ$49, tax-inclusive), Payment Link
<https://buy.stripe.com/fZueV61d99tv52vaOfgYU00>, webhook
`we_1ULwqf2BJbkpIesfu7IRLfBp`. The pre-existing link was adopted and now
redirects to the thank-you page; the webhook signing secret was recorded at
setup time (it goes to Render only).

Still to confirm in the Dashboard: **Alipay** enabled under Payment methods,
and the NZ GST registration active under Tax (the setup key could not read
either). The service needs its **own** restricted key with Checkout
Sessions: Read and Charges: Read; the setup key cannot read sessions or
charges.

### 2. Render (licence service)

1. Render → **New → Blueprint**, pick this repository; it reads `render.yaml`.
2. Fill the `sync: false` secrets: `STRIPE_SECRET_KEY` (the restricted key
   from step 1.4), `STRIPE_WEBHOOK_SECRET`, `BYTEPROOF_KEY_SECRET` (a long
   random string — record it), `BYTEPROOF_LICENSE_PRIVATE_KEY` (the PEM from
   `tools/generate_license.py`), `BYTEPROOF_INTERNAL_KEYS` (the two
   `BYTEPROOF_-...` keys), `BYTEPROOF_ADMIN_TOKEN` (record it), and the SMTP
   values for `licenses@bytemind.co.nz` (or a Resend key).
3. Add the custom domain `api.bytemind.co.nz`; Render prints the CNAME to add
   at the DNS provider for `bytemind.co.nz`.
4. Wait for `https://api.bytemind.co.nz/health` to show
   `"license_signer_configured": true` and `"stripe_configured": true`.
5. Confirm the deployed disk is mounted at `/data` (the health endpoint's
   `licenses` count must survive a restart).

### 3. Website

**Edits made in the working tree, not yet pushed** (the push waits for
`/health` to answer):

- `byteproof.html:540` and `zh/byteproof.html:540`: swap the `buy.polar.sh`
  href for the Payment Link URL.
- `byteproof.html:547` / `zh/byteproof.html:547`: "Polar emails your key" ->
  "Stripe emails your key" (zh: Polar 会把密钥发到你的邮箱 -> Stripe 会把密钥发到你的邮箱),
  and note Alipay / 支付宝 is accepted.
- `byteproof.html:559` / `zh/byteproof.html:559`: "Secure checkout via Polar"
  -> "Secure checkout via Stripe" (zh: 通过 Polar 安全结账 -> 通过 Stripe 安全结账).
- `byteproof.html:669` / `zh/byteproof.html:669`: same wording change.

### 4. App release

1. Bump `APP_VERSION` (next release line, e.g. `2.3.0-beta.1`) and mirror it
   in `version_info.txt`; `python scripts/check_version.py` must pass.
2. Build and install the beta per the usual beta process.
3. In the installed beta: Settings → License → Purchase (opens the website),
   buy a live NZ$49 licence with a real card, paste the key, check the
   licence shows; repeat on a second machine; check the third is refused;
   release a slot in the portal; refund the test purchase and confirm the app
   shows the revoked notice.

### 5. Retire Polar

After the beta is proven and no Polar purchase can happen (website switched):

- The owner's two keys already work through `BYTEPROOF_INTERNAL_KEYS`.
- Keep the Polar account until the last Polar-era installer is gone, then
  cancel it. Nothing in the app or service calls Polar any more.

## Rollback

The previous commit on `beta/2.2.3` still has the Polar integration. If the
new service misbehaves before the website is switched, revert the app commit
and leave the website alone; if it misbehaves after the switch, point the
website back at the Polar link (the Polar account is still open) while the
service is fixed. Licence keys already issued keep working offline regardless.

## Verification already run (sandbox)

1. `server/tests` — 17 service tests: issuance, idempotent webhooks, delayed
   payments, activation limit, deactivation, revocation, portal, internal
   keys, reconcile, backup.
2. `tests/` — 464 app tests, including the new licence-service flows.
3. Sandbox, real browser and card: two real Checkout payments through the
   Payment Link; `checkout.session.completed` delivered over the wire;
   keys `BYTP-...` issued; licence email captured with key, activation link
   and portal link; thank-you page shows the key.
4. Sandbox, real desktop client: two computers activate, the third is
   refused, deactivation frees the slot, a replacement activates, an offline
   check never locks a working licence, and both owner keys activate on three
   computers each with no limit.
5. Sandbox portal: magic link opens the portal, the release button frees a
   computer, and the freed seat can be used immediately.
6. Sandbox refund: `charge.refunded` revokes the key; the app reports
   "refunded or revoked"; a new computer is refused.
7. Sandbox delayed method (SEPA, same code path as Alipay): the unpaid
   `checkout.session.completed` was ignored and the later
   `checkout.session.async_payment_succeeded` issued exactly one key.
8. Four mock rounds of the full lifecycle, plus the app-side usage cycle
   (`server/tests/test_lifecycle_simulation.py`,
   `tests/test_license_cycle.py`) — see
   `docs/aegis/work/2026-10-02-stripe-licensing/20-mock-rounds-and-fixes.md`
   for the seven issues they found and the six that were fixed.
