# Live edits in the Codex composer, and the hover that comes back — Task Intent

## Requested outcome

The owner read the latest `capture.log` and reported that live applies in the
Codex/ChatGPT composer leave the edited text "mismatched — some words are not
properly ordered". Alongside the fix, two behaviours were asked for:

1. Moving the pointer away from a selected passage and back over it must
   activate the live suggestions again — the hover is not one-off.
2. After applying (one or all), the text stays selected: hovering the edited
   text must capture it fresh and the next apply must be accurate.

## Goal

An apply either lands the text exactly where the span is, or it is repaired,
or it stops and says so. Never again "the new words exist somewhere, so this
counts as applied". And the hover/apply loop keeps working on the text as it
now is.

## Success evidence

- Failing tests first for: the late-landing repair, refusing an unexplained
  landing, the positional write evidence, the hover re-arm, and the
  post-apply re-anchor.
- Full gate green on the released commit (version check, ruff, all three test
  files).
- **2.2.3-beta.1** built, signed, notarized, installed to `/Applications`, and
  running on the release machine for the owner to test.

## Stop condition

- `done`: the beta is installed and the owner has the behaviour changes to
  try. Official release only when the owner explicitly says so.

## Non-goals

- No change to the trigger's pointer rules (the "only suggest while the
  pointer is with the selection" gate stays as it is).
- No change to which apps are supported, and no new provider calls: the
  re-arm serves the panel from the preview cache whenever it can.
- No release: the public update feed keeps advertising 2.2.2.
