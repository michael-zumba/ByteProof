# Live Stripe setup — 2026-10-02

Part A of `STRIPE_LICENSING_SETUP.md` was executed against the live ByteMind
Stripe account (`acct_1U8x8a2BJbkpIesf`).

## What exists now

| Object | ID / value |
| --- | --- |
| Product | `prod_VMg2Qo89P7u2bM` — “ByteProof”, description and icon added |
| Price | `price_1ULwgu2BJbkpIesfs1siXnAk` — NZ$49, one-time, **tax-inclusive** |
| Payment Link | `plink_1ULwji2BJbkpIesfzyU6kG6E` — <https://buy.stripe.com/fZueV61d99tv52vaOfgYU00> |
| Webhook endpoint | `we_1ULwqf2BJbkpIesfu7IRLfBp` → `https://api.bytemind.co.nz/api/byteproof/stripe-webhook` |

The Payment Link already existed; it was **adopted** rather than duplicated:
its completion behaviour now redirects to
`https://api.bytemind.co.nz/thanks?session_id={CHECKOUT_SESSION_ID}`, Stripe
Tax is on, promotion codes are allowed, and the `product=byteproof` metadata
is set. Its URL is unchanged, so any copy already in circulation still works.

Webhook events: `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `charge.refunded`,
`charge.dispute.created`, `charge.dispute.closed`.

The webhook signing secret (`whsec_…`) was printed during setup. It is a
secret: it belongs in Render’s `STRIPE_WEBHOOK_SECRET`, never in this repo.

## The key used for setup

The restricted key supplied for setup can write Products, Prices, Payment
Links and Webhook Endpoints, but it **cannot** read Checkout Sessions or
Charges — so it is *not* the key for the running service. Two separate keys
are needed:

1. Setup key (the one used today): rotate or delete it once the deployment is
   verified, since a write-capable key is no longer needed.
2. Service key: a new restricted key with **Checkout Sessions: Read** and
   **Charges: Read** only. That is what goes on Render as
   `STRIPE_SECRET_KEY`.

## Website

`byteproof.html` and `zh/byteproof.html` were edited in the working tree (not
committed, not pushed): the buy button points at the Payment Link above, the
copy says Stripe instead of Polar, and the Chinese page now mentions
支付宝/Alipay. `scripts/check_site.py` passes.

The push should wait until the licence service answers `/health`, so no
customer can pay while the webhook has nowhere to land.

## Still to do (needs Render access)

1. Create the Render Blueprint service with the secrets from
   `STRIPE_LICENSING_SETUP.md` Part B2.
2. Point `api.bytemind.co.nz` at it (CNAME to the Render service).
3. `curl https://api.bytemind.co.nz/health` → signer and Stripe configured.
4. Push the website change; run the live purchase test (Part E).
5. Confirm in the Stripe Dashboard that **Alipay** is enabled under Payment
   methods and that the NZ GST registration is active under Tax (the key used
   for setup could not read either setting).
