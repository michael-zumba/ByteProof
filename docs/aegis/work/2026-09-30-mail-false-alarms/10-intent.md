# Mail false alarms — Task Intent

## Requested outcome

The owner reported that while editing email and using the proofreader,
ByteProof "kept telling me something wrong", and asked for the newest runtime
record to be read and investigated. The investigation (2026-09-29 session in
`capture.log`, 21:01) found two false alarms and the owner approved both fixes:
"implement those two as the next beta (2.2.2-beta.5), test-first, per the
beta-first release policy".

## Goal

A manual proofread read that the live preview's Command-C read has shut out of
the copy rate limit is retried once and never reported as "No text selected",
and a Mail/Outlook/Pages paste that a real copy proves landed is no longer
reported as "Could not confirm the paste".

## Success evidence

- A failing test for each defect before its fix, run red first.
- `scripts/run_tests_ci.py` green with `BYTEPROOF_CI_PROGRESS=1`, `ruff`
  clean, `scripts/check_version.py` agreeing at 2.2.2-beta.5.
- A beta built, signed, installed to `/Applications`, and the installed
  version confirmed.

## Stop condition

- `done`: both fixes tested and shipped as a beta the owner can retest, and
  the checkpoint names what is still open.

## Non-goals

- Publishing a release. Per the release policy this ships as a beta first; the
  public update feed is untouched.
- A second paste when the copy shows the original text is still selected (the
  live apply's `not_applied` retry stays live-only; the manual apply keeps its
  single write and reports honestly).
- Redesigning the paste-verification strategy or the live preview.
