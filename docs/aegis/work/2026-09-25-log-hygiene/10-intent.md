# Log hygiene and paste verification — Task Intent

## Requested outcome

The owner asked for the **latest log record** to be read and for anything that
needed fixing to be fixed: "check the latest log record, to see if any hidden
bugs needs to fix", then "if anything needs to fix, please do for me".

In scope: the newest runtime record in the support folder (`capture.log`, the
session of 2026-09-25 13:52–13:59, plus `debug_hotkeys.log` written in the same
minute), everything it showed, and the code paths behind each line.

## Goal

The support log holds only what it is kept for, the app writes no user typing
to disk, and an apply that lands is never reported as a failure.

## Success evidence

- The log read end to end, with each failure traced to the line of code that
  caused it.
- A failing test for every defect before its fix, run red first.
- `scripts/run_tests.sh` / `scripts/run_tests_ci.py` green, `ruff` clean,
  `scripts/check_version.py` agreeing.
- A beta built, notarized, installed to `/Applications`, and running.

## Stop condition

- `done`: the five defects are fixed, tested, and shipped as a beta the owner
  can test, and the checkpoint names what is still open.

## Non-goals

- Publishing a release. Per the release policy this ships as a beta first; the
  public update feed is untouched.
- Redesigning the live preview or the verification strategy.
- The two cosmetic observations in the log that are not defects (the
  Accessibility "editable" probe accepting a splitter, and Outlook's
  Accessibility text write always falling back to the paste path). Both are
  recorded in the checkpoint as follow-ups.
