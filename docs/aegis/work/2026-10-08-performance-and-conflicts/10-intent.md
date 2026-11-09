# App slowdown and internal conflicts — Intent

## The owner's report

"I also noticed a slowdown process of the app. Is there any conflicts inside
the app or script? Can you conduct a comprehensive assessment and check if any
hidden bugs that needs to be fixed?"

## Goal

Find, with measurements rather than guesses, where ByteProof spends its time
and where its own parts fight each other; fix what the measurements show; and
report honestly what was checked and found healthy.

## Success evidence

- A before/after number for each change (tick cost, freeze length, dialog
  open time, wake-ups per minute).
- The conflicts found are named with the mechanism that caused them, not just
  the symptom.
- `./scripts/run_tests.sh` passes, with a regression test for each fixed bug.
- The beta is built, notarised and installed.

## Stop condition

- `done`: the numbers are measured on this machine, the fixes are in the
  installed beta, and the owner has the report.

## Non-goals

- Rewriting the live-check architecture (an event-driven Accessibility
  observer instead of polling is a real idea, but a project of its own).
- Touching the proofreading flow or the prompt contracts.
