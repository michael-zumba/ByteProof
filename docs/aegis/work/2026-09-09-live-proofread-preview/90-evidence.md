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

## Design pivot (1.9.0-beta.3)

Native in-document marks and overlay underlines were the source of repeated
platform conflicts (Word formatting traces, Chrome/Gmail flicker, Mail/Pages
AX gaps). The live preview now uses the simpler model the user approved:

- Selecting text in any app that exposes a selection debounces and asks the AI
  for a cached compact edit list.
- A single non-activating "Suggested changes" panel appears near the selection
  with pinpoint word diffs, per-edit Apply rows, Apply all, and a close button.
- Nothing in the source document is ever formatted, underlined, or modified
  until the user clicks an edit. Applying replaces only the exact range
  (Word: content replacement with the user's existing Track Changes state).
- The panel is a true macOS non-activating panel (`Qt.Tool` +
  `WindowDoesNotAcceptFocus` + AppKit nonactivating mask), so clicking it
  never raises the ByteProof main window.
- The event tap, underline overlay, Word formatting/journal/heal code paths
  are no longer used by the live service.

Tests: 51 pass (panel show/hide on selection change, per-edit apply with
offset shifting, apply-all with deltas, cache behaviour, diff rendering,
widget layout invariants). Full smoke suite passes.

## Fifth polish round (1.9.0-beta.4)

Review of the pivot found and fixed accuracy, lifecycle, and UX gaps:

- Cancellation restored: the preview worker again receives a cancel event,
  so toggling the feature off or quitting aborts an in-flight provider call
  (previously it burned tokens for up to 120 s and could outlive the app).
  Worker teardown now goes through `finished` + `deleteLater` (no
  "QThread destroyed while running" risk), and cancelled runs stay silent.
- Stale-apply protection: the provider call takes seconds, so the service
  re-reads the selection range before showing results and before every
  apply. Re-selecting the same phrase elsewhere now applies at the new
  position instead of corrupting the old one.
- Panel survival after Apply: after a single apply the selection is
  re-read; when the document still holds the expected post-edit text, the
  panel stays open with the remaining suggestions (offsets shifted),
  otherwise it closes deterministically.
- Transient failures retry silently (5 s cooldown, 3 attempts per burst,
  one toast per burst); free-limit and missing-API-key results now show a
  real message instead of a silently vanishing panel.
- Fuzzy mapping is conservative again: candidates must match the needle's
  length (a "goes" lookalike can no longer match "go" and eat the next
  word) and spans use the matched words' real extents.
- Word apply compensates tracked deletions and field codes: when the
  selection range length proves hidden characters exist, edits after them
  are shifted to the correct absolute positions (unit-tested), including
  mid-Apply-all state changes; clean documents skip the scan entirely.
- UX: Escape dismisses the panel (observe-only global key monitor, active
  only while the panel is visible), the panel clamps to the screen the
  selection lives on (multi-display), Apply/Apply-all show a toast
  ("Applied." / "Applied N of M suggestions." / the failure reason), the
  panel header shows the suggestion count, and the Settings tooltip no
  longer describes the retired underline model.

Tests: 64 pass in `tests/test_live_preview.py` (13 new covering the above).
Smoke suite unchanged (the environment-dependent
`test_capture_diagnostics_shape` fails the same way on the pre-pivot HEAD
when the test process lacks Accessibility permission).

## Sixth fix round (1.9.0-beta.5) — non-Word apps

Live testing of beta.4 showed Word fully working while Gmail/Outlook showed
the panel but could not apply, and Pages/Mail showed nothing.

- Apply root cause: this PyObjC build does not export
  `AXUIElementSetParameterizedAttributeValue` /
  `kAXReplaceRangeWithTextParameterizedAttribute`, so the only remaining
  apply path (set AXSelectedTextRange + write AXSelectedText) failed
  silently in Chrome/Outlook and no paste fallback existed in the live path.
  `ax_replace_range` now runs a full chain: parameterized replace (when
  available) → range+selected-text writes → clipboard-preserving paste over
  the sub-range, with every AX error code logged to capture.log and a
  best-effort post-paste verification. A sub-range paste is refused unless
  the range can be selected first (or the span covers the whole selection),
  so a paste can never land in the wrong place.
- Pages/Mail reading: their editors never expose AX selection. The service
  now falls back to a throttled (2.5 s, backing off to 8 s while empty),
  clipboard-preserving Cmd+C read for Mail compose and Pages (gated on a
  live editable element for Pages). Apply for these selections pastes the
  fully corrected text over the current selection (sub-ranges are impossible
  without an absolute range) with an honest "Applied all suggestions to the
  selection." toast.
- Diagnostics: the AX text-element search gained an application-level tier
  and logs the found element role; selection_details logs per-attribute
  error codes once per app; Pages still awaiting a live pass — if it still
  shows nothing, capture.log now says exactly which attribute is missing.

Tests: 68 pass in `tests/test_live_preview.py` (4 new).

## Seventh fix round (1.9.0-beta.6) — apply instrumentation

Live beta.5 testing: panels appear in Pages/Mail/Gmail/Outlook but Apply still
changed nothing, and capture.log showed no apply-path entries at all — the
clicks were failing silently before the apply code ran. Beta.6 makes every
step visible and resilient:

- Every Apply/Apply-all logs its entry, the sync verdict (with the seen vs
  current text snippets), the apply result, and the post-apply state.
- Selection re-verification retries once after 150 ms (AX reads glitch
  transiently) and reports "Could not verify the selection — please reselect
  and try again." instead of closing the panel silently.
- The full-selection paste path (Pages/Mail) logs each step, retries through
  a System Events keystroke when the process-targeted paste fails, and
  verifies by reading the selection back (honest "please check the document"
  message when verification is inconclusive).
- AX trust transitions are logged ("LIVE PERMISSION: trusted=…"), replacing
  the per-poll no_permission spam and exposing the intermittent trust flips
  seen during testing.
- ax_replace_range logs its entry parameters and the not-trusted bail.

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
