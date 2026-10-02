# Live cutover — 2026-10-02

The Stripe licensing migration is live on the production paths. This note
records what moved and what is left.

## Live now

| Piece | State |
| --- | --- |
| Stripe (live account `acct_1U8x8a2BJbkpIesf`) | Product `prod_VMg2Qo89P7u2bM`, price `price_1ULwgu2BJbkpIesfs1siXnAk` (NZ$49, tax-inclusive), Payment Link <https://buy.stripe.com/fZueV61d99tv52vaOfgYU00> (redirects to the thank-you page), webhook `we_1ULwqf2BJbkpIesfu7IRLfBp` with five events |
| Licence service | Render `byteproof-license-service` (Docker, Singapore, Starter + 1 GB disk at `/data`), custom domain `api.bytemind.co.nz` (verified, certificate issued, HTTP→HTTPS), `/health` reports signer + Stripe configured and the fingerprint the app embeds (`fa2f8694b2b77138`) |
| Licence email | Gmail SMTP on the service; a real licence email was accepted by Gmail (`"sent": true`) |
| Website | `byteproof.html` and `zh/byteproof.html` sell through the Stripe link and name Alipay (website commit `1169616`), verified on the live pages |
| App | 2.3.0-beta.1 built, **notarised and stapled**, installed to `/Applications`; 2.2.3-beta.2 kept in `previous-versions/` |

## Verified against the live service

- Activated an owner key through the real desktop client over HTTPS, verified
  the returned licence against the app's embedded public key, validated
  online, then deactivated (activations back to 0).
- Webhook route answers 400 to an unsigned probe; `/thanks` reaches Stripe and
  reports an unknown session; the portal pages render; a portal request never
  reveals whether an email is known.

## Left to do

1. Confirm in the Stripe Dashboard that **Alipay** is enabled under Payment
   methods and that the NZ GST registration is active under Tax — the
   restricted keys cannot read either setting.
2. Retire Polar once the website change has been live for a while.
3. Windows packaging and the public release: **done 2026-10-02** as 2.3.0 —
   see `docs/aegis/work/2026-10-02-release-2.3.0/`.

## The live purchase test — passed 2026-10-02

The owner bought a real licence (NZ$1 after a single-use NZ$48 promotion
code), and the whole cycle behaved:

1. Stripe Checkout → `checkout.session.completed` → licence
   `BYTP-TSAS-1P7A-4Y4F-VGQB-DRGV` for `zyq.michael@gmail.com`; the key
   arrived by email.
2. Activated in the installed 2.3.0-beta.1 app (one device recorded).
3. **Found and fixed a real bug here**: the thank-you page said "We could not
   find that checkout session" because the deployed `STRIPE_SECRET_KEY` was
   the setup key, which cannot read Checkout Sessions. The owner added
   Checkout Sessions: Read and Charges: Read to that key, and reads worked
   immediately. The service was then made resilient (`2fce6f4`): `/thanks`
   answers from the stored licence first, a broken read reports itself
   honestly, and `/health` exposes `stripe_read_ok`.
4. Redeploy (Deploy latest commit) preserved every licence row — the
   persistent disk earned its keep.
5. Refunded the charge: `charge.refunded` revoked the licence; a fresh
   machine is refused; the app showed "This license was refunded or revoked"
   on its next launch and dropped to free mode.
