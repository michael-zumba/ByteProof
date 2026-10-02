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

1. **Live purchase test** (owner): buy one NZ$49 licence on the website, check
   the key on the thank-you page and by email, activate it in the app, then
   refund it in Stripe and confirm the app reports the revocation.
2. Confirm in the Stripe Dashboard that **Alipay** is enabled under Payment
   methods and that the NZ GST registration is active under Tax — the
   restricted keys cannot read either setting.
3. Retire Polar once the website change has been live for a while.
4. Windows packaging and the public release remain owner-triggered, per the
   release policy.
