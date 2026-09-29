# Suggestion panel polish — Task Intent

## Requested outcome

The owner sent a screenshot of the live "Suggested changes (5)" panel and
asked: "help polish the UI/UX of the live suggested pop up window display…
without touching the function script… Just the UI/UX."

The owner approved the short design with two calls: per-row Apply becomes a
tonal button with one filled primary ("Apply all"), and the category tag moves
inside its suggestion card.

## Goal

The panel reads as designed rather than assembled: one visual unit per
suggestion, one clear primary action, calmer diffs — with zero changes to
signals, handlers, strings, ordering, counts, colors that carry meaning
(severity dots), or placement/timing behaviour.

## Success evidence

- Failing tests first for the three decisions that can regress (panel-only diff
  style, tag grouping, button hierarchy).
- The full gate green (`scripts/run_tests_ci.py`, `ruff`, `check_version.py`).
- Before/after offscreen renders of the same synthetic content (no user text)
  attached next to this record.
- A beta built, notarized, installed to `/Applications`, and running.

## Stop condition

- `done`: the polish shipped as 2.2.2-beta.6 for the owner to look at, and the
  checkpoint names the deliberate limits.

## Non-goals

- No behaviour changes: no new controls, no rewording, no reordering, no new
  hover-only actions, no changes to the apply/undo/selection logic.
- The main window's review view keeps its current diff spelling.
- No public release; the update feed is untouched (pre-release bump).
