# Live Proofread Preview — Evidence

## Automated tests

- `QT_QPA_PLATFORM=offscreen ./venv/bin/python -m pytest tests/test_live_preview.py -q`
  → `44 passed` (parse, mapping incl. repeated occurrences, trigger decisions
  for every target bundle id, cache/LRU, settings schema, Word AppleScript
  scripts, overlay render, service cycle, cache efficiency, card fallback,
  reselect-after-deselect, apply-all delta offsets, mark persistence,
  "Suggested changes" titles).
- `QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_smoke.py`
  → `ALL_SMOKE_TESTS_PASSED` (existing behavior unchanged after the
  `_request_completion` refactor).

## Accuracy (real DeepSeek)

- `./venv/bin/python tests/corpus_live.py` run twice → `CORPUS_RESULT failures=0`
  both runs. Corpus: 5 defective sentences covering spelling, agreement,
  word choice, and repeated errors.

## Function (real apps, live)

- TextEdit: selection triggered one preview call (3 edits), overlay window
  positioned over the selection, dashed `#E23A5B` underlines pixel-verified on
  screen, hover popup verified (window + red strikethrough + green replacement
  pixels), clicking the underline replaced `teh` with `the` in the document
  (verified by reading the AX value).
- Word: selection triggered a preview (3 edits), native
  `underline dot dot dash` formatting applied to the exact ranges (verified via
  AppleScript), the suggestion card rendered (verified by window capture:
  202,076 opaque pixels, 746 green suggestion pixels), clicking a card Apply
  button changed the document from `teh cat...` to `the cat...`.
- Packaged `dist/ByteProof.app`: launches; Settings contains
  "Live Suggestions (Beta)", the enable checkbox, the delay slider, and the
  local-model checkbox (verified through the accessibility tree).
- Mail: the compose body is a WebKit view. Even after text is pasted into it,
  Mail exposes the body only as a read-only `AXStaticText` snapshot and never
  reports `AXSelectedText`, so the live preview cannot capture the selection.
  This is a platform limitation, not a crash: the service stays inert in Mail
  compose. The existing hotkey proofread path keeps its clipboard fallback and
  still works there.
- Pages: `com.apple.iWork.Pages` is recognized and covered by a unit test.
  Automation permission is now granted, the test text can be imported and
  scripted into the document (`body text` reads/writes correctly), but the
  canvas exposes nothing to the Accessibility tree until a real human caret
  is placed in the document. Synthetic clicks, keystrokes, pastes, and
  scripted selection all fail to create that caret, so the final Pages check
  requires one human click into the document. The feature itself is inert
  rather than failing in that state.
- Apps that expose selection but not character bounds now fall back to the
  floating suggestions card instead of showing nothing (unit-tested).

## Polish round (user feedback, live)

- Marks persist after the selection is cleared: overlay underlines stay
  visible, and hovering a mark reopens the popup with no new provider call.
- "Apply all" applied three edits directly with correct offset deltas
  (`the cat sat on the mat and it was fine`) while TextEdit stayed frontmost;
  the ByteProof window was not raised.
- Browser compatibility verified in Google Chrome: the service read a web
  textarea selection and fired a preview, covering Gmail-in-browser.
- Popup and card redesigned (premium styling, auto-sizing, screen clamping)
  and titled "Suggested changes".

## Second polish round (user feedback, live)

- Popup/card rebuilds now remove nested layouts too, so repeated refreshes
  leave exactly one "×", one "Apply all", and one "Apply" per suggestion
  (regression-tested).
- The single "Apply" button works: clicking it live replaced `teh` with `the`
  in TextEdit while leaving the other marks visible.
- Underlines persist across selections: while a second preview was running,
  the first selection's underlines stayed on screen and the new edit merged
  in (marks accumulate; they no longer clear when the selection moves).
- The hover popup survives the periodic underline rect refresh (it now
  repositions instead of closing), which was the cause of intermittent
  hover failure.
- Popup/card restyled toward Google's design language: pill buttons
  (#1A73E8), red/green suggestion chips, grey secondary text, rounded 14px
  surfaces, Google close-button styling.
- The AX text-element search now also walks the focused window subtree, which
  covers canvas-style editors such as Pages when a caret exists.
- Mail compose remains a platform limit: its WebKit body never reports the
  selection; the hotkey proofread path still works there.

## Third polish round (user feedback, live)

- Word underlines are now fully ephemeral: applied with Track Changes
  temporarily disabled (no tracked revision is recorded), the original
  underline and 16-bit RGB colour are captured, and clearing restores them
  exactly. Verified live: mark → `underline dot dot dash`/pink, clear →
  original `underline none`/blue. Nothing survives ByteProof quit, an app
  switch, Apply, Escape, or the settings toggle.
- Word RGB colours use 16-bit channels; the mark colour is now the correct
  `#E23A5B` equivalent `{58082, 14906, 23387}` (previously the 8-bit values
  were silently ignored and the mark was black).
- Suggestion text now shows a pinpoint word-level diff (only the changed words
  are struck/green), not a full-phrase deletion. Unit-tested.
- The Word card no longer follows the mouse on refresh; it positions once and
  stays put, so buttons can be clicked. The popup and card also gained
  `WindowDoesNotAcceptFocus`, so clicking them never raises the ByteProof
  window. Hover hit padding was widened vertically for easier targeting.
- Popup/card restyled brighter (14px text, larger paddings, pill buttons).
- Pages live automation is not possible from this host: synthetic clicks and
  keystrokes land in the wrong app (a few test keystrokes ended up in the
  ChatGPT window and were previewed, but never applied). The AX search is
  broadened (focused-window subtree), so Pages must be verified with one real
  click-and-select; if it still fails, the next step is a live AX dump while
  the user holds a selection in Pages.

## Fourth polish round (1.9.0-beta.2)

- Root cause of "permanent colour in Word": app sessions killed during
  reinstall cycles skipped cleanup, leaving 11 marked characters in the
  user's open document. Verified by scanning the live document and cleaning
  them (underline and colour restored).
- Word marks are now journaled to `live_word_marks.json` with their original
  underline/colour and document name. On the next launch (or when Word becomes
  frontmost), ByteProof restores any leftover marks automatically, so a
  force-quit can no longer leave traces.
- The global event tap could treat a click landing over the popup/card as a
  click on the underline, applying an edit and removing the popup before the
  button's click registered. Clicks inside the popup/card are now ignored by
  the tap and reach the buttons (unit-tested).
- Versioning is active: `1.9.0-beta.N` increments per update and is shown in
  Settings/About and the app bundle.

## Efficiency

- Unchanged selection: no provider call (asserted by
  `test_preview_cache_prevents_duplicate_provider_calls`).
- Debounce: one call per selection change (asserted by
  `test_service_decision_flow_skips_unchanged`).
- Output capped at 512 tokens / 12 edits; context capped at 200 chars per side;
  selection capped at 1500 chars; preview defaults to Local AI (zero cloud
  tokens).

## Build

- `./build_macos.sh arm64` completed twice: app signed with the ByteMind Developer ID,
  notarized and stapled (Apple status `Accepted` for both app and installer).
- The final installer was rebuilt after the Pages bundle-id and card-fallback
  fixes, so the artifact includes them.
- Installer: `ByteProof_Installer_AppleSilicon.dmg`.
