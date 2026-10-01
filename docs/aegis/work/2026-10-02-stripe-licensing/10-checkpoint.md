# Stripe licensing — checkpoint (2026-10-02)

Branch: `codex/stripe-licensing` (commit `feat: Stripe checkout and a
ByteMind-owned license service`), based on `beta/2.2.3`.
Spec: `docs/superpowers/specs/2026-10-02-stripe-licensing-design.md`.
Go-live plan: `docs/superpowers/plans/2026-10-02-stripe-licensing-golive.md`.

## State

Code complete and verified in a Stripe sandbox. Polar is out of the runtime
path; the only remaining work needs the owner's Stripe, Render and DNS
access, plus the website link swap and a beta install.

## Delivered

- `server/`: licence service — Stripe webhook fulfilment (immediate and
  delayed payment methods), deterministic keys from Checkout Session ids,
  a 2-computer limit, signed machine-bound licences, email magic-link
  portal, refund/chargeback revocation, admin API, nightly Stripe
  reconciliation and SQLite backups.
- App: `src/license_api.py` client; activation by key or `?session=` link;
  revoked-licence notice; offline tolerance; "Manage My Licenses"; pre-Polar
  signed keys still activate locally.
- Owner keys `BYTEPROOF_-AD00C43E-…` and `BYTEPROOF_-01654728-…` are
  unlimited internal licences on the service.
- Tooling: `scripts/stripe_setup.py`, `scripts/license_admin.py`,
  `scripts/run_server_tests.sh`, `scripts/dev_smtp_catcher.py`,
  `.github/workflows/license-service.yml`, updated `render.yaml`
  (Singapore, starter plan, 1 GB disk).
- Version bumped to `2.3.0-beta.1` (public update feed untouched for a
  pre-release).

## Evidence

- Server suite: 17 tests (`./scripts/run_server_tests.sh`).
- App suite: 464 tests across `tests/test_*.py`, plus ruff clean.
- Sandbox E2E: two real Checkout payments (browser, test card) through the
  Payment Link, webhook delivered over the wire, keys `BYTP-…` issued,
  licence email captured with key + activation link + portal link,
  thank-you page correct.
- Live desktop-client E2E: two computers activate, third refused,
  deactivation frees a slot, replacement activates, offline check does not
  lock, both owner keys unlimited.
- Portal: magic link, release button, freed seat reusable.
- Refund: `charge.refunded` revokes; app reports revoked; new activation
  refused.
- Delayed method (SEPA, same path as Alipay): unpaid
  `checkout.session.completed` ignored, `async_payment_succeeded` issued
  exactly one key.
- Alipay availability for New Zealand confirmed in Stripe's docs; WeChat
  Pay is not available to NZ accounts.

## Next steps (owner access needed)

1. Stripe live: enable Alipay, confirm the GST registration, create the
   restricted key, run `scripts/stripe_setup.py`.
2. Render: create the Blueprint service with the secrets from
   `server/README.md`, add `api.bytemind.co.nz`, check `/health`.
3. Website: swap both `byteproof.html` and `zh/byteproof.html` Polar links
   for the live Payment Link and mention Alipay.
4. Build and install the 2.3.0-beta.1 beta, buy one live licence, activate
   two machines, release one, refund the test purchase.
5. Keep the Polar account until the last Polar-era installer is gone.

## Sandbox left behind for inspection

Stripe sandbox `acct_1ULpGyGcUP9ytL8Z` (expires 2026-10-08) with the product,
price, Payment Link, webhook and test purchases. `stripe listen`/`stripe`
on this machine currently default to that sandbox profile: run
`stripe login` (or `stripe sandbox claim`) before using the live account.
