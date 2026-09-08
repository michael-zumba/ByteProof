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
