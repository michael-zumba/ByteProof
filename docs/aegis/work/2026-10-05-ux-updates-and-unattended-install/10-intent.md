# Licence link, Automation layout, unattended macOS updates, and a hidden-bug pass — Task Intent

## Requested outcome

The owner asked for four small changes, then asked for a hidden-bug pass:

1. **Licence → Stripe.** In Settings → License, an unlicensed user's
   "Purchase License" button must open Stripe Checkout directly, not the
   product website's pricing page.
2. **Automation layout.** "Add Trigger / Remove Selected / Reset Defaults"
   read as if they float over the trigger list. Make the page look
   professional.
3. **Unattended macOS updates.** Auto-download already worked, but the install
   did not: it warned the user to close ByteProof and drag the app to
   Applications. The owner wants download → install → relaunch with no
   intervention.
4. **Live suggestion panel.** The pop-up still looks amateurish in its
   surface, colour choice, and inner display; the buttons themselves feel
   premium and should keep that quality.
5. **Hidden bugs.** Check the app for hidden bugs and fix the real ones.

## Scope and authority

- Everything ships as a beta first (`2.3.1-beta.5`), installed to
  `/Applications` for the owner to test. No public release, no push.
- The two UI changes are presented as before/after renders in this record;
  the owner can ask for a different direction and it can be adjusted in the
  next beta.
- The update change must keep the old bundle recoverable when anything fails;
  a failed update may never leave the user without a working app.
- The bug pass fixes only defects with evidence and a regression test; no
  refactors, no behaviour changes beyond the defect.

## Success evidence

- A regression test proving the purchase URL is a Stripe link.
- Before/after renders of the Automation page and the suggestion panel.
- A test that runs the updater helper against a stub bundle: it waits, swaps,
  relaunches, and rolls back on failure.
- The full gate green (`scripts/run_tests_ci.py`, `ruff`, `check_version.py`).
- A signed beta built, installed, and reporting `2.3.1-beta.5`.

## Stop condition

- `done`: beta.5 is installed and this record names what changed, what was
  deliberately left alone, and what the owner still has to approve.
