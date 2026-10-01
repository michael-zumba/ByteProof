# Stripe licensing — mock rounds and the bugs they found (2026-10-02)

Four mock rounds of the purchase → activation → use → support cycle were
written and run (`server/tests/test_lifecycle_simulation.py`,
`tests/test_license_cycle.py`). Writing them turned up seven issues; six are
fixed, one is a documented trade-off.

## The rounds

| Round | What it simulates | Where |
| --- | --- | --- |
| 1 | Buy, activate two computers, use, release one in the portal, move to a new computer | `server/tests/test_lifecycle_simulation.py::test_round1_…` |
| 2 | Messy key paste, reinstall, repeat buyer, expired portal link, support resend, delayed (Alipay-style) payment, free promotion code, redelivered webhook | `test_round2_…` |
| 3 | Key guessing, forged portal token, admin without token, unsigned webhook, unknown event, the 3rd-computer limit, rate limiting | `test_round3_…` |
| 4 | Refund, disputed charge, won dispute, lost dispute, wiped database, same-day backups and restore | `test_round4_…` |
| Usage cycle | Trial → licensed → released → revoked in the real app client over real HTTP (localhost stub of the service) | `tests/test_license_cycle.py` |

## Bugs found and fixed

1. **A released computer kept working.** Releasing a slot in the portal (or
   changing hardware) left the old machine's local licence valid forever, so
   "2 computers" was not really enforced. The service now returns
   `reason: "not_activated"`, and the app removes its local copy and shows an
   "Activation Released" notice (`src/activation.py`,
   `apply_remote_validation`). Verified live against the running service.
2. **Backups silently failed on the second run of a day.** `VACUUM INTO`
   refuses to overwrite, so a restart plus the nightly job skipped the
   backup. The snapshot is now staged and replaced atomically; the weekly
   email attachment (promised by the design) is implemented too.
3. **A 100%-off purchase never got a licence.** Stripe reports
   `payment_status: "no_payment_required"` for a fully discounted session;
   only `"paid"` was accepted. Both are fulfilled now
   (`is_paid_session`).
4. **`/thanks` could be used to hammer Stripe.** Anybody could loop that
   public URL and make the service call the Stripe API once per request; it
   is now rate limited like the other public endpoints.
5. **Recovery could be blocked by the reconcile throttle.** After a database
   wipe, a first mistyped key would suppress the reconcile for five minutes
   and make real customers see "unknown key". An empty licence table now
   always reconciles.
6. **A won chargeback kept the customer locked out.** `charge.dispute.created`
   revoked, and nothing restored. `charge.dispute.closed` with
   `status: "won"` now restores licences revoked *by that dispute* (the store
   records a revocation reason so a real refund is never undone).
7. **Trade-off, not fixed:** two different webhook events for the same paid
   session (e.g. `completed` *and* `async_payment_succeeded`) could in
   principle both send the licence email if they are processed at the same
   instant. The key is the same and the customer can ignore the duplicate;
   serialising it would need a lock around the send.

## Test-hygiene bug found

Tests read the *real* macOS `com.bytemind.byteproof` trial marker, so on a
machine whose real trial had expired every "fresh" test looked like free
mode. The root `conftest.py` now stubs the secondary trial store for every
test (two existing tests were working around it individually).

## Counts after the work

- App: 466 tests across `tests/test_*.py`, ruff clean.
- Service: 29 tests (`./scripts/run_server_tests.sh`), ruff clean.
- Live rehearsal: released-computer fix exercised against a running service
  over HTTP with the real desktop client.

## Setup guide

`STRIPE_LICENSING_SETUP.md` (repository root) is the full operator guide:
Stripe account, tax, payment methods, product/Payment Link/webhook, Render
deployment and secrets, DNS, email, website cutover, beta release, day-2
support, backups/recovery, monitoring and troubleshooting.
