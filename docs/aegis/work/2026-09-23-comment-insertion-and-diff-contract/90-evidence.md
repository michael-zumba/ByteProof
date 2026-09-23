# Comment insertion and the Proposed Changes pane — Evidence

## The comment failure, as it happened

The owner's log (`capture.log`, two attempts, 14:01:50 and 14:02:15):

```
WORD:   Comment triggered via 'menu_insert'
WORD: Comment insertion failed: Word did not open a comment box, so the
      comment text was not inserted (it would otherwise be typed into the
      document).
```

Reproduced on this machine in a scratch document with the shipped code: the
menu click fires, `count of comments` stays 0, the guard raises, and the box
Word opened stays empty (the owner's "comment box is open, but no comments
input inside").

## What Word actually does

- `make new comment` is impossible: Word's dictionary answers
  `Can't make class comment (-2710)`.
- After Insert ▸ Comment, the box is a *draft*: `count of comments` remains 0
  while the Accessibility tree shows `Post comment (Cmd + Enter)`,
  `Cancel new comment draft`, and the composer text area.
- The Insert ▸ Comment menu item has no keyboard equivalent
  (`AXMenuItemCmdChar` missing, `AXMenuItemCmdModifiers` = 8), so the removed
  `⌥⌘A`/`⇧⌘A` fallbacks could never have worked; `⌥⌘A` was tested and did
  nothing.
- The Review ribbon does expose a `New Comment` button ("Insert a Comment"),
  which the app now presses as its second trigger.
- A box opened by automation does not take keyboard focus (AXFocused stays
  false and setting it is refused), which is why the note is written through
  Accessibility first and the paste is gated on the composer holding focus.
- The detector itself was measured against a real draft box: 97-161 ms per
  check, found at depth 14 of the window tree.

## The diff rendering, measured

Same paragraph, same edit, only the junk heuristic changed (share of the
rendered text inside struck or inserted spans):

| Edit to a 130-word paragraph | default heuristic | `autojunk=False` |
|---|---|---|
| delete " directly rather than infer it after the fact," | 96.5% | 4.9% |
| rewrite one sentence | 93% | under 50% |
| change two words | 26.3% | 3.3% |
| reword one clause | 1.2% | 1.2% |

The same paragraph and the same edit, rendered by the app's own review view
(`diff-before.png` is the shipped renderer, `diff-after.png` is the fix):
the old rendering is the "entire replacement" in the owner's screenshot.

## Automated tests

- New in `tests/test_hardening.py`: two review-diff tests (both fail with the
  heuristic back on), two span-splitting tests, and five comment tests (draft
  box written through Accessibility, focused-box paste fallback, no typing
  without focus, no typing without a box, ribbon second trigger).
- `./scripts/run_tests.sh` → `test_hardening.py 168 passed`,
  `test_live_preview.py 137 passed`, `test_smoke.py 108 passed`,
  "All test files passed."

## Shipped build

- `tools/bump_version.py 2.2.1-beta.8` (pre-release: the public feed keeps
  advertising the last release).
