# Comment insertion and the Proposed Changes pane — Task Intent

## Requested outcome

The owner proofread an academic paragraph and reported two defects: the
comment function did not add comments (Word showed an open comment box with
nothing in it), and the main window's Proposed Changes pane drew whole
paragraphs as replaced instead of as track-changes-style pinpoint edits.

## Goal

A finished proofread adds its reviewer note to the Word document, and the
Proposed Changes pane marks only what actually changed.

## Success evidence

- The failure in the field log is explained by a mechanism, not a guess, and
  the new logic is covered by tests that fail against the old code.
- `./scripts/run_tests.sh` passes.
- The beta is built, installed, and carries the new logic.

## Stop condition

- `done`: suite passes, beta installed, owner told what to test in Word.
- `needs-verification`: the Word comment path cannot be exercised end to end
  on this machine and has to be confirmed by the owner.

## Non-goals

- Reworking the prompt contracts (that was the earlier 2026-09-23 workstream).
- Changing how edits are applied inside the document.
