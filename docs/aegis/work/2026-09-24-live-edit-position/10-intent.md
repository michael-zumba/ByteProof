# Live edits that keep their place — Task Intent

## Requested outcome

The owner asked for the latest `capture.log` to be read and for the live
suggestion feature to be fixed for position: "when I'm doing live editing or
live suggestions, on many occasions it loses its position, so the edited
content loses its original position, making the output inconsistent and
mismatched". The manual proofread is reported as working perfectly; only the
live-suggestion apply path is in scope.

## Goal

Every write the live preview performs either lands exactly on the previewed
span or does not happen at all, and a refused write leaves the user's own
selection and document exactly as they were.

## Success evidence

- `capture.log` read; each failure class in it traced to a code path.
- Word: a live edit verified against a real document reads back from the
  extent it wrote (probe on a scratch document, before and after).
- Accessibility apps: no keystroke is delivered unless the sub-range is
  provably the span; a refused write puts the app's selection back; an app
  that rewrites the text without confirming stops the batch.
- Regression tests that fail against the previous code for each of those.
- `./scripts/run_tests.sh` green, version bumped, beta installed.

## Stop condition

- `done`: suite passes, the beta is built and installed, and the checkpoint
  names what was fixed and what is still open.

## Non-goals

- Changing the manual proofread flow (reported working).
- Making live apply succeed in apps that refuse programmatic selection writes:
  refusing honestly is the intended outcome there.
- Publishing a release: this ships as a beta first.
