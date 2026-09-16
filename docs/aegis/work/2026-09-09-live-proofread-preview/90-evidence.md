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

## Eighth fix round (1.9.0-beta.7) — browser apply

Beta.6 traces showed Chrome accepting the AXSelectedText/Range writes with a
success code while never committing them to the page (the toast said
"Applied." and the Gmail draft was untouched). Browsers now skip the AX
attribute write entirely and use a real paste: select the sub-range via AX,
Cmd+V (process-targeted, then a System Events keystroke retry), then verify
positionally by slicing the element's AXValue at the edited range — with
honest messages when verification is inconclusive. The AX text-element
search also scores candidates by who actually holds a non-empty selection,
so Gmail's subject field can no longer steal edits meant for the body.

## Ninth fix round (1.9.0-beta.11) — editable-context gate

Live suggestions must only appear for editable content, not for reading:

- `selection_details` now probes `AXUIElementIsAttributeSettable` for the
  value/selected-text/range attributes on both the found element and the
  focused element, and reports `editable` plus the element role. Read-only
  selections (PDF text, browsed web pages) are static elements that expose
  nothing settable, so they are skipped before any provider call.
- Word counts as editable (active document via AppleScript). Pages counts
  as editable when its canvas element exists (a caret proves an active
  editing session). Mail uses AppleScript to check that the frontmost
  window is a compose window (its title matches an outgoing draft's
  subject) rather than the viewer, so reading a received message never
  triggers the clipboard read.
- Gmail keeps working because the focused compose textarea is settable,
  while selecting plain page text in Chrome/Safari is now ignored.

Tests: 79 pass (4 new covering settable probing, read-only skipping, and
Mail viewer-vs-compose detection).

## Interaction round (1.9.0-beta.10)

- The translucent shadow margin from beta.9 rendered as a black box on the
  test machine (the known Tool + translucent compositing problem), so the
  card is fully opaque again; depth now comes from the 20px radius, soft
  borders, hairline dividers, the inset diff wells, and button press states.
- The card is draggable by its header (open-hand cursor, clamped to the
  screen on release) and pops in with a 170 ms fade + grow animation.
- Spurious "could not verify the selection" toasts fixed: selection changes
  are only committed after two agreeing polls (single glitch reads can no
  longer hide the panel or poison the seen text), the apply-time sync
  compares against the previewed text only, stale preview results are
  dropped by frontmost-app check instead of the glitchy text compare, and
  the sync retries three times before failing.

Tests: 75 pass (drag, pop-in completion, updated hide timing).

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

## Tenth fix round (2.1.1-beta.7) — background window, table text, comments

Owner report, verbatim: *"when I try to apply changes, it will start to pop up
the main app window. If main app is close in the background, it should not pop
up."* Plus two feature changes: table **cell** text must be proofread (only a
whole-table selection stays refused), and Word **comment** editing must be
proofread live.

### The main window popped up because our own card woke the app

`eventFilter` shows the hidden window on `QEvent.ApplicationActivate`, which is
how a Dock-icon click gets the window back. The suggestion card, the Undo pill
and the toast are non-activating Qt tool windows, but macOS still activates the
app when one of them is clicked - so the click that started an apply looked
exactly like "the user wants the window back", and the window opened over the
document mid-apply.

What the log shows is the same activation from the other side: at 11:40:09 the
owner clicked Apply All, and one second later `LIVE APPLY ALL SYNC FAIL:
selection changed: previewed=<len=205 ...> now=<len=0>` - Word's own selection
read back empty immediately after the click. Word reports no selection while it
is not the active application, so the click on the card must have taken
activation away from Word; an activation is exactly what the window handler
acts on. (The handler could not be exercised by hand here - synthesising a
click needs an Accessibility grant this shell does not have - so the behaviour
is pinned by tests that deliver `ApplicationActivate` in both states, and the
beta logs which branch it took.)

Fix, both halves:

- every helper window now reports its life cycle (`LivePreviewService
  .helper_activity`, emitted when the card/pill is shown, hidden, dragged into
  place, or when an apply starts and ends) and the main window keeps the
  auto-show off for `HELPER_ACTIVATION_GRACE_S` (8s) afterwards;
- an activation while the pointer is resting on a visible helper counts as ours
  too (`_helper_woke_the_app`), which is exactly the shape of a click on Apply;
- `apply_nonactivating_panel` is now applied to the Undo pill and the hover
  popup as well (it was only on the card), and the toast gets it in
  `ToastNotification.__init__`.

Tests: `test_an_activation_caused_by_our_own_helper_leaves_the_window_hidden`
(activations before, during and after the grace window),
`test_the_cursor_resting_on_a_helper_counts_as_ours`,
`test_showing_and_hiding_the_card_reports_helper_activity`.

### Table text is prose; the table itself is not

`is_selection_in_table` asked Word for `count of tables of myRange`, and Word
counts a table as soon as the selection is anywhere **inside** it - so every
cell text was refused. Probed against Word 16 on this machine (scratch
document): a cell selection, a sub-range inside a cell and a two-cell selection
all answer `1`, and the same is true for the whole table; the flag alone cannot
tell them apart.

`selection_scope()` replaces it and answers `main` / `comments` /
`whole_table` / `other_story`. It is one call per **new** selection (cached
against the selection it was probed for), uses Word information flags rather
than localised story names, and only reports `whole_table` when the selection
covers a table's whole range:

```applescript
set tableList to tables of myRange
repeat with aTable in tableList
    set tableRange to text object of aTable
    if selStart ≤ (start of content of tableRange) and selEnd ≥ (end of content of tableRange)
```

Both flows use it: Live Check skips a whole-table selection (once per app, with
a toast explaining what to select instead) and the manual flow returns
`TABLE_SKIPPED_STATUS`. Writing is still guarded: Word ends every cell with a
`Chr(7)` cell mark, so a suggestion that would change how many of them the span
holds is refused (`cell_mark_mismatch`, `TABLE_STRUCTURE_MESSAGE`) - table
structure can never be rewritten by a language edit.

Verified against real Word (scratch document, unsaved, then closed):

```
cell text   -> main          (proofread)
whole table -> whole_table   (refused)
main text   -> main
```

### Word comments have their own story

A comment selection reports `story type = comments story` and positions
relative to the **comment** (start 1, end 23 for a 22-character comment), while
`create range active document start/end` addresses the manuscript. Applying
through document ranges would therefore have edited the paper instead of the
comment. Probes also showed that `set range` cannot build a writable sub-range
inside a comment (the result cannot even be read back), while
`set content of selection` works and is repeatable.

So comments get their own write path: `replace_comment_selection` rewrites the
whole selection with the corrected text - guarded by the same document name,
the same comment text, Track Changes restored, and a read-back. Live Check
builds that text from the captured comment plus the suggestions being applied
(`_apply_comment_batch`), re-anchors the suggestions that remain on screen, and
Undo is the same write in reverse. Selections in headers, footers and notes,
which have the same coordinate problem, are now skipped instead of written
through document ranges.

A comment being *written* is not a story yet: AppleScript reports nothing at
all for it, so Live Check falls back to Accessibility for that selection - but
only when the focused element says it is a comment area, because writing
through Accessibility on a guess could land in the body.

Verified against real Word (scratch document, unsaved, then closed):

```
comment selection -> comments
read: text='typod wrods in a comment' start=1 end=25 context='' ''
write -> True 'Applied.'
comment after: 'typed words in a remark'
document body: unchanged
```

Manual flow: comment text is reported as `COMMENT_SKIPPED_STATUS` (that flow
writes through document ranges) and the GUI says so instead of touching the
manuscript.

Tests: `test_word_scope_probe_reads_word_information_flags`,
`test_a_whole_table_is_skipped_but_its_text_is_proofread`,
`test_a_suggestion_that_drops_a_cell_mark_is_refused`,
`test_comment_writes_never_use_a_document_range`,
`test_apply_all_in_a_comment_rewrites_the_comment`,
`test_apply_one_in_a_comment_keeps_the_other_suggestions`,
`test_undo_in_a_comment_puts_the_text_back`,
`test_a_whole_table_selection_never_starts_a_preview`.

Suite: `scripts/run_tests_ci.py` green (test_hardening, test_live_preview,
test_smoke). `ruff check src tests` clean. Every new/changed AppleScript body
compile-checked with `osacompile`.

The background-window path is logged: `APP: activation ignored — a floating
helper was just used` / `... the pointer is on a floating helper` when an
activation is treated as ours, and `APP: activation — showing the main window`
when it is not. The owner's next beta run can therefore say exactly which one
happened if the window still appears.

## Eleventh fix round (2.1.1-beta.8) — undo, long selections, the pointer rule

Owner report: *"After applying live suggestions, and if user click undo, it
says cannot undo because text changed."* Plus: selections of more than four or
five paragraphs produce no suggestions, and the panel should only appear while
the pointer is still with the selected text.

### Undo failed because our own pill had taken the focus

`capture.log` (17 Sep, 10:46) shows the whole sequence: `LIVE APPLY ALL` on
Safari's text field, both ranges written and verified, then two restores, each
refused with `Could not read the focused text field.`, reported to the user as
**"Could not undo — the text had changed."**

Nothing had changed. Clicking the Undo pill activates ByteProof, and
`_mac_ax_text_element(pid)` resolves the target through
`AXUIElementCreateApplication(pid)` +
`kAXFocusedUIElementAttribute` - which an application that is *not* active does
not have. Every read and write then failed, and the message blamed the text.
The apply path had always activated the target for exactly this reason
(`LIVE SYNC: verified after bringing the app forward` in the same log); undo
never did.

Fix: `_perform_undo` activates the app the record belongs to, waits (bounded:
`TARGET_ACTIVATE_TIMEOUT_S`) until it is frontmost, and only then restores.
When the app cannot be reached at all, the message now says so
("ByteProof could not reach Safari…") instead of blaming the text.
`_live_edit_text` also stopped refusing to read a field whenever the *current*
selection happens to be in Word, so an undo of an edit made elsewhere still
searches the document it wrote to.

### The record is already complete; the matching is what had to be tightened

The owner's proposal - keep original and edited text in a temp file and only
undo on a 100% match - was reviewed and deliberately not implemented as such:

* the record already holds everything the file would (the text written, the
  text to restore, the surrounding 32 characters from both sides, the offset,
  the document name); the failure above was mechanical, not missing data;
* a file adds a second, *stale* source of truth: after an app restart or a
  crash it would offer an undo whose text no longer matches anything, which is
  exactly the risk the owner asked to avoid;
* the pill is the only way to ask for an undo and it lives for 15 seconds, so
  a disk record buys no capability - an undo whose record is gone is gone.

What changed instead, so that a *match* is now provable in every path:

* the app the record belongs to must be reachable (above);
* the text written must still be where the record says: the recorded needle
  must occur exactly once, or the recorded offset must hold exactly that text
  (`_undo_target`, unchanged - it already refuses to guess);
* for Word, the text recorded is now what Word *stored*, not what was sent:
  Word rewrites what it is given (smart quotes, autocorrect), and the undo's
  own before-text guard compares against the document. `read_range_text()`
  reads the range back after a live edit (verified against Word: `4-9` →
  `'quick'`, an empty range → `''`, whole document round-trips byte for byte),
  and `UndoStep.applied` carries that spelling.

Tests: `test_undo_brings_the_target_app_forward_first`,
`test_undo_says_why_when_the_app_cannot_be_reached`,
`test_undo_reads_the_app_it_wrote_to_after_a_switch`,
`test_a_word_undo_records_what_word_stored`,
`test_a_word_undo_without_a_readable_range_keeps_the_sent_text`.

### Long selections were refused by a limit nobody could see

`LIVE SKIP: too_long` appears 124 times in `capture.log` (20:24-20:26 and 14:41
are Microsoft Word). The cap was `max_chars: 1500` - about three paragraphs -
and it was not exposed in Settings at all, so four or five paragraphs silently
produced nothing: exactly the owner's report. `evaluate_trigger` is the gate.

* `DEFAULT_MAX_CHARS` is now 4000 (six to eight paragraphs). The request itself
  is bounded by `PREVIEW_MAX_EDITS` (12) and `PREVIEW_MAX_OUTPUT_TOKENS` (512),
  so a longer selection costs input tokens and nothing else.
* Existing installs migrate: a stored value of exactly the old built-in default
  (1500) is raised, a value the user chose is left alone
  (`_migrate_live_preview_limits`). Verified against a copy of the owner's real
  `settings.json`: 1500 → 4000.
* The limit is now a Settings > Live Check control (500-20,000 characters).
* A selection that is still too long is no longer silent: the reason is logged
  once and the user is told to raise the limit.

Word read timing was measured before raising the cap (scratch document): the
selection read is an AppleScript round trip of ~300 ms at 93, 500 and 4,000
characters alike - the text length is not what costs - so the 1.5 s poll
timeout still has plenty of headroom.

Tests: `test_a_five_paragraph_selection_is_checked_now`,
`test_the_old_selection_limit_is_migrated`,
`test_a_selection_that_is_too_long_is_explained_once`.

### Suggestions now wait for the pointer to be with the text

Owner request, assessed as feasible and implemented behind a setting that
defaults on:

> only trigger the live editing suggestion when the mouse is also around the
> selected text… I don't want it to interrupt user's work

`_pointer_near_selection()` answers with two signals, either one enough:

* the pointer is within `POINTER_NEAR_SELECTION_PX` (150 px) of the selection's
  own screen rectangle, from `ax_bounds_for_range` - asked for the first and
  last character only (one AX round trip each, cached for a second), and never
  for Word, whose tree is large and whose panel has always been placed at the
  pointer;
* otherwise, the pointer has not moved further than that from where it was
  when the selection appeared. `_poll` samples the pointer every tick, so the
  reference is the position from the tick *before* the selection was committed
  (`_pointer_at_capture`), which needs no screen-coordinate conversion from the
  AppKit event monitor and works in every app, Word included.

Neither signal available (no rectangle, no reference) means "yes": an
unmeasurable pointer must never take the feature away. A gated selection is not
marked as seen, so the panel still appears the moment the pointer comes back.
Turn the rule off with Settings > Live Check ("Only suggest while the pointer is
still at the selected text" → `live_preview.require_pointer_near`).

Note on scope: the pointer rule governs *new* previews only. A panel that is
already on screen is never taken away by moving the pointer, because reading
the suggestions is exactly what the pointer usually goes to do.

Tests: `test_suggestions_wait_while_the_pointer_is_away`,
`test_a_known_selection_rectangle_is_measured_directly`,
`test_the_pointer_rule_can_be_turned_off_and_fails_open`,
`test_the_poll_waits_for_the_pointer_before_spending_a_request` (three real
poll ticks: selection, walk away, come back).

### One self-inflicted regression, caught by the suite

The first version of the pointer change accidentally duplicated the poll's
"selection committed" block, so the second copy never ran and the panel was no
longer hidden when the selection went away.
`test_service_shows_panel_on_result_and_hides_on_selection_change` failed and
the duplicate was removed. Suite: 356 tests green (`run_tests_ci.py`), ruff
clean.

## Twelfth fix round (2.1.1-beta.9) — finishing the undo when the pill has the focus

The owner's next log check showed the beta.8 installer had landed at 11:10,
but the process still running was the **beta.7** one from 10:05 (`ps`), so none
of the eleventh-round fixes had been exercised yet. The real
`settings.json` still held `max_chars: 1500` and `app_version: 2.1.1-beta.7`.

Before restarting, the code path was audited one more time and two gaps were
found in the beta.8 work:

### The clipboard-only undo had the same focus bug

The beta.8 fix activated the target before an Accessibility undo, but the
`mode == "full"` path (Mail compose, Pages) still called
`get_selection_light()` while ByteProof owned the keyboard focus. A background
clipboard-only app cannot answer that read, so `current` came back empty and
the undo reported "Selection changed — could not undo." on an untouched
selection. It now:

* activates the record's app first, with the same bounded wait as the range
  path, and says "could not reach Mail" if that fails;
* reads with `_read_full_selection_with_retries(attempts=2)`, the same
  rate-limit-aware bounded read the full apply uses;
* still refuses unless the selection holds exactly the corrected text
  (whitespace-insensitive at the edges, as before).

### A correct undo could bounce the panel straight back

After an apply, `_seen_text`/`_previewed_text` hold the corrected text. Undo
restored the original selection, the poll then read it as a new selection and
could re-show cached suggestions within a second - a correct undo that looks
like it did nothing. Every undo state now records `selection_before`, and a
successful restore marks that text as seen again
(`_remember_selection_after_undo`), so the poll stays quiet until the user
makes a new selection.

### The migration only ever existed in memory

`load_runtime_settings` starts from a defaults dict whose `app_version` is
already `APP_VERSION`, then `_stamp_version_and_save` compared that with
itself and returned without writing. The 1,500 -> 4,000 limit migration and
the corrupted-hotkey repair therefore changed the live session but never
reached `settings.json`, and `last_run_version` was never copied from the
file, so the post-update Accessibility hint could not trigger either. The
loaded version and `last_run_version` are now preserved until the save.
Verified against a copy of the owner's real settings file:
`max_chars: 1500 -> 4000`, and the restart then showed on disk
`app_version: 2.1.1-beta.9`, `last_run_version: 2.1.1-beta.9`,
`max_chars: 4000`, `require_pointer_near: true`.

### Evidence

* Tests: 360 green — `test_hardening.py` 121 (four new: restored selection is
  marked seen; full undo activates Mail first; unreachable full undo refuses
  without pasting; the migration reaches the file), `test_live_preview.py`
  135, `test_smoke.py` 104. Ruff clean; version markers agree.
* Build: Apple Silicon DMG built, notarized, stapled and installed; running app
  is 2.1.1-beta.9 (`/Applications`, PID confirmed after restart).
  DMG sha256 `5609188026d52fe56c2f50a3b22e2ffc05d82f2cdf72303570bab8cb0d8666eb`.

### What was *not* adopted from the owner's temp-file proposal

The existing in-memory stack already stores original, edited, surrounding
context, document, and offset per apply; undo targets the newest state and
refuses when a unique, exact match cannot be proved. A temp file would add a
second source of truth that can be stale after a crash, and the 15-second pill
is the only undo entry point, so a disk record buys no capability. The
matching was tightened instead (unique needle, 64-character relocation window,
editor-side `before_text` guard, Word read-back).
