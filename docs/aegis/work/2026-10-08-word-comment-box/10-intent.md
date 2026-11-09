# Writing in a Word comment box, and Live Check in the comment — Intent

## The owner's report

"In word or pages, the proofreading function is not working in the comment
box. so when I add comment, and try to write something inside the comment box,
the editing is not able to achieve... I think there is some script conflict
with the comment. When the app is on, the comment is also hard to display (it
always disappears). Also check the live suggestion function, see if any hidden
bugs."

## Goal

With ByteProof running, writing in a Word comment box behaves exactly as it
does with ByteProof closed: characters land, the draft stays open and focused,
and Live Check can review the comment text (and apply its suggestions) without
ever disturbing the box.

## Success evidence

- The failure is reproduced on this machine, and the mechanism is named: which
  of ByteProof's reads does it, and why it needs a keystroke to be in flight.
- A control run (ByteProof stopped) types into the same comment box cleanly.
- Tests cover the decision ("the composer is read through Accessibility and
  AppleScript is never asked", "an apply rewrites the box", "a manual
  proofread stands down while the box has the keyboard"), and they fail
  against the old code.
- `./scripts/run_tests.sh` passes.
- A beta is built and installed for the owner to test in real Word.

## Stop condition

- `done`: suite passes, beta installed, owner told what to test.
- `needs-verification`: applying a suggestion inside the composer, and Undo,
  are exercised on this machine but their end-to-end pass still needs the
  owner's own minute in Word (the writer was hardened after the first live
  observation, see the checkpoint).

## Non-goals

- Changing how posted comments are proofread (Live Check rewrites those as a
  whole; that path is untouched).
- Adding a panel that pops up while the owner types without asking (the
  trigger rules - stable text, minimum words, pointer with the text - are
  unchanged).
