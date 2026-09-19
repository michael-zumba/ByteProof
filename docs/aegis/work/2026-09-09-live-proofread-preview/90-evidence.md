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

## Thirteenth fix round (2.1.1-beta.10) — Apply All lost the position after an Outlook paste

Owner report: after applying live suggestions, the document content was
mismatched/misplaced - edits no longer landed on the selected words.

### What `capture.log` showed

The 18:59 and 19:00 Outlook sessions both went wrong the same way:

* the first span was written with the fallback clipboard paste;
* the AX read-back still did not match (`paste verify: mismatch`);
* a second read showed the range *had* changed, so the retry was refused
  (`range changed after paste ... refusing retry`);
* the next span could no longer be located (`could not locate a span ...
  near rel=81`), and every following span was skipped or failed the same way,
  while the document had already shifted under the first write.

The old code applied suggestions left-to-right and shifted every following
offset by the previous edit's length delta. After an unverified write it kept
going, using an AXValue that was stale or only partly updated. The next paste
therefore targeted an offset that no longer existed in the real field. A
correctly applied edit could also be misread as a failure when Outlook stored
a newline in its own form: the failing spans were all shorter/longer by 1-14
characters around paragraph text, and `_verify_range_write` compared the
stored `\r`/`\r\n` representation with the requested `\n` byte-for-byte.

### The fix

* **Apply backwards.** `_apply_all_locked` now sorts pending spans by start
  descending. An edit can only move text *after* the spans still waiting, so
  their preview offsets stay valid without any delta arithmetic. Word's
  `_word_doc_delta` is reset to 0 before each reverse step for the same
  reason. This is the same right-to-left order `apply_corrections_with_diff`
  already uses for the manual Word flow.
* **Verify newline form, not newline bytes.** `normalize_line_endings` was
  added to `utils`; `_same_text`, `_range_write_candidates` and
  `_value_holds` let write verification accept `\n`, `\r` and `\r\n`
  spellings of the same text. Spaces, quotes and words are still compared
  exactly, and the length search is bounded by the number of line breaks, so
  a genuine mismatch cannot be papered over. The selected-text check, the
  delayed-write check, the pre-paste range guard, the retry guard and the
  full-selection paste wait all use the same rule.
* **Never apply from a stale element.** `_sync_selection` invalidates the
  per-pid AX element cache before reading the selection. The cache is what
  makes the 350 ms poll cheap, but its only key is the pid - a focus move from
  Outlook's list/search field into the compose body could otherwise leave the
  range selection on one element while the paste landed in another.

### Evidence

* Tests: 362 green — `test_hardening.py` 121, `test_live_preview.py` 137
  (new: backwards apply keeps a stale AXValue from moving the next span;
  newline-normalised write verification), `test_smoke.py` 104. Ruff clean;
  version markers agree.
* Build: Apple Silicon DMG built, notarized, stapled and installed; running
  app is 2.1.1-beta.10. DMG sha256
  `8a5a75822d41109b36c586f5a8aa4727a5a91546b6911d1dbc71816380bb849c`.

## Fourteenth fix round (2.1.1-beta.11) — the owner UX pass

Owner report, five items: the main window popped up on every Cmd-Tab after
using the app; Apply All and the other live hotkeys did not fire reliably;
chosen hotkeys could silently collide with another app; a non-expert needed a
"restore defaults" button; and the Undo pill appeared at the old suggestion
card position for an unclear length of time.

### The Cmd-Tab popup

`capture.log` had repeated pairs: a helper activation was ignored, then a few
seconds later `APP: activation — showing the main window`. The 8-second helper
grace was the only defence; after it expired, any `ApplicationActivate` (Dock,
Cmd-Tab pass, or an accessory window waking the app) ran the
`show(); raise(); activateWindow()` branch. ByteProof was also a regular Dock
app, so it appeared in the Cmd-Tab list at all.

Fix, default-on:

* `general.menu_bar_only` defaults to `True`.
* On macOS the app sets `NSApplicationActivationPolicyAccessory` at startup;
  it has no Dock icon and is not in Cmd-Tab. Turning the setting off promotes
  it back to `Regular` at runtime.
* `Info.plist` ships `LSUIElement=True` so the packaged app starts that way.
* The activation event filter refuses to show the hidden window in this mode;
  the window is opened deliberately from the menu bar icon or its hotkey.
* The "Keep running in the menu bar" switch is forced on while menu-bar-only
  is on, so the user cannot make the app unreachable.

Verified live: `/Applications/ByteProof.app` 2.1.1-beta.11 runs with
`activationPolicy() == 1` (accessory), and `capture.log` shows
`APP: activation policy menu-bar-only`.

### Hotkeys: stored Cmd+Shift+. could never match

`settings.json` held `apply_all_hotkey: "<cmd>+<shift>+."`, and
`debug_hotkeys.log` showed it parsed as char `.` with `variants={'.'}`. On
macOS, Shift+period produces `>` in `charactersIgnoringModifiers`, so the
handler never matched. The parser only knew the `:`/`"` shifted pairs for `;`
and `'`. It now covers every shifted US punctuation key (`.>`, `,<`, `/?`,
`- _`, `= +`, `[{`, `]}`, `\|`, `` `~ `` and the number row), in both
directions, so a stored shortcut matches the event whichever spelling arrives.
Duplicate canonical registration is also logged instead of silently letting a
later shortcut overwrite an earlier one. The General > Hotkeys and Live Check >
Hotkeys fields round-trip unchanged; Apply All defaults to Cmd+Shift+Return
again via Restore Default Settings.

### Hotkey conflict warning

`find_hotkey_conflicts` now checks, on Save:

* duplicates between the four ByteProof actions;
* a short curated list of OS shortcuts (Spotlight, screenshots, Emoji,
  Force Quit, standard Copy/Paste/Save/Quit/…);
* the menu equivalents of currently running apps, read from their AX menu
  bars (`AXMenuItemCmdChar` + `AXMenuItemCmdModifiers`) in a daemon thread
  bounded at 1.5 s so a busy app cannot freeze Settings.

The result appears as a gentle "That shortcut is already in use" dialog with
**Choose Another** (focuses the field and keeps the dialog open) and **Save
Anyway**. An unchanged form the user has already acknowledged is not nagged
again.

### Restore Default Settings

General now has a **Restore default settings** button. It resets the
`general`, `live_preview` and `automation` sections to the shipped defaults
(hotkeys, menu-bar mode, timing, app rules, style, context, automation),
rebuilds those three Settings pages in place, and leaves `providers` (API
keys), `license`, `local_model`, `active_provider` and update state untouched.

### Undo pill placement and lifetime

The pill used `_last_anchor`, which is the suggestion card's remembered
position; dragging the card decided where Undo appeared. It now anchors to the
**applied range** (`UndoStep.abs_start` via `AXBoundsForRange`), falling back
to the selection/cursor when the app exposes no bounds (Word), so it appears
next to the words that were actually changed. `UNDO_AVAILABLE_MS` is 12
seconds: long enough to notice and reach, short enough not to hover over the
document after the user moves on.

### Evidence

* Tests: 369 green — `test_hardening.py` 128 (new: shifted-punctuation
  parser, duplicate/system conflict report, reset preserves keys/license,
  menu-bar-only blocks the activation popup, Undo anchors to the edited
  range, Reset rebuilds the pages), `test_live_preview.py` 137,
  `test_smoke.py` 104. Ruff clean; version markers agree.
* Build: Apple Silicon DMG built, notarized, stapled, installed and launched.
  `Info.plist` carries `LSUIElement=True`; the running process is accessory.
  DMG sha256
  `7e722067acdc895fc5d4f9400252141112ebcf027503ba1c6f60e4ad7405d881`.
* `debug_hotkeys.log` from the new build shows
  `Parsed hotkey: <cmd>+<shift>+. -> variants={'.', '>'}`.

## 2026-09-18 - Settings restyle evidence (2.1.1-beta.13)

* Tests: 377 green - test_hardening.py 136, test_live_preview.py 137,
  test_smoke.py 104. Each file run through scripts/run_tests_ci.py.
* Lint: python -m ruff check src tests clean. scripts/check_version.py agrees:
  2.1.1-beta.13 (tuple 2, 1, 1, 13).
* Palette: no #[0-9A-Fa-f]{6} literal inside the SettingsDialog region
  (was 36 distinct values, 202 occurrences).
* Type and geometry, asserted rather than eyeballed: page title 17px, blurb
  12px, section heading 11px DemiBold with letter spacing 0.8, row title 13px,
  row helper 12px; every SettingsRow on the General page shares x=0, one width,
  and one right edge for its control column.
* Switches paint: sampling a grabbed dialog finds the green track (#1a3a2a) on
  a checked switch and the light track (#e2ddd1) on an unchecked one, so the
  indicator images really load.
* Structure, read off the built dialog: General 6 sections / 11 rows, Live
  Check 3 / 21, Automation 1 / 1, Updates 1 / 0, zero QGroupBox on any page.
* Renders for comparison: /tmp/byteproof-settings-review/before (2.1.1-beta.11)
  and /after (2.1.1-beta.13), 7 pages each at 1000x700, offscreen. The vision
  audit tool in this session has no backend configured, so these were verified
  by measurement (above) and are for the owner's eye.
* Build: Apple Silicon DMG built, notarized, stapled and installed; the
  installed bundle reports 2.1.1-beta.13 and is signed by team 9AMNWJRC93.
  DMG sha256 c71ad0a4ca8439a8a048a41644be01759a5c9ac4e8b5d17ffebd70329b063f9f.
  (2.1.1-beta.12 was built and superseded before hand-off by the type-tracking
  and row-unification fixes; only beta.13 was installed for testing.)

### 2026-09-18 - Consistency evidence (2.1.1-beta.14)

* Tests: 381 green - test_hardening.py 140 (5 new: no private control
  stylesheets, typography owned by the sheet, one height per button/select role
  across every page, one card padding, app-list rows cover their items),
  test_live_preview.py 137, test_smoke.py 104.
* Lint: ruff clean across src and tests. scripts/check_version.py agrees:
  2.1.1-beta.14 (tuple 2, 1, 1, 14).
* Font sizes in the dialog: 11 (hint, status, section label), 12 (helper,
  value, button and control labels), 13 (row, card title, badge-free names),
  15 (hero), 17 (page title), 22 (version). Nine sizes before, six roles now,
  and no inline font-size survives in the SettingsDialog region.
* Buttons: 13 bespoke stylesheets removed. Measured through the sheet: base
  buttons 32px, small disclosure buttons 26px, link buttons text-height, one
  height per role on every page.
* Cards: ProviderCard, LicenseCard, SettingsCard and SettingsCallout all
  measured at 16/14 padding; the licence card no longer falls back to Qt's
  default.
* Build: Apple Silicon DMG built, notarized, stapled and installed; the
  installed bundle reports 2.1.1-beta.14. DMG sha256
  a9eba88c99454afc55502c97e313aa1d9c807b62cc87ec4734d8965e1fa80c42.
  Renders: /tmp/byteproof-settings-review/before (beta.11) and /after (beta.14).

### 2026-09-18 - Stylesheet-boundary evidence (2.1.1-beta.15)

* Tests: 384 green - test_hardening.py 143 (new: the window sheet stays inside
  the window; the running app's window cannot restyle the dialog, measured
  through combo height, the 17/13/12/11 type scale and switch pixels), plus the
  sheet-state inventory test; test_live_preview.py 137, test_smoke.py 104.
* Lint: ruff clean. scripts/check_version.py agrees: 2.1.1-beta.15 (tuple
  2, 1, 1, 15).
* Boundary: 48 window selectors scoped to #RootPanel; 0 bare widget selectors
  remain outside the allowlist (asserted).
* Dialog sheet: 102 rules, braces balanced; owns the base font, the checkbox
  indicator (including hover and focus), every combo state and popup row,
  button pressed and disabled, and scrollbars.
* Build: Apple Silicon DMG built, notarized, stapled and installed; the
  installed bundle reports 2.1.1-beta.15. DMG sha256
  68e60ad3c0d19076161a39e7105fc267b83f51412691c4010cbec4620021a8c8.
  Renders: /tmp/byteproof-settings-review/before (beta.11) and /after (beta.15).

### 2026-09-18 - beta.16 evidence

* Tests: 390 green - test_hardening.py 149 (new: number fields show their
  number; the Automation page is structured like the others and its list has
  room), test_live_preview.py 137, test_smoke.py 104. One intermittent
  teardown crash was seen twice across many runs (offscreen Qt, after all tests
  passed); three consecutive hardening runs were clean afterwards.
* Lint: ruff clean. scripts/check_version.py agrees: 2.1.1-beta.16 (tuple
  2, 1, 1, 16).
* Packaging: Apple Silicon DMG built, notarized, stapled and installed.
  Gatekeeper: spctl reports "accepted, source=Notarized Developer ID"; the
  bundle reports 2.1.1-beta.16, signed by team 9AMNWJRC93, bundle id
  nz.co.bytemind.byteproof. DMG sha256
  a80326196f10468c9e706783b7133ff68eadd194bb786b41826a310d89806280.
* The owner installs from /Applications/ByteProof.app; the previous source-run
  instance is stopped so hotkeys belong to one process.

### 2026-09-18 - beta.17 evidence (menu bar crash and packaging)

* Crash reports read in full:
  ByteProof-2026-09-18-202742.ips and ByteProof-2026-09-18-202816.ips, both
  SIGABRT ("Abort trap: 6") with the same lastExceptionBacktrace:
  NSStatusItem popUpStatusItemMenu: -> NSSceneStatusItem
  _beginExpandedInterfaceSession: -> NSMenuTrackingSession beginTrackingSession
  -> libqcocoa.dylib -> -[NSEvent clickCount] -> objc_exception_throw.
* Reproduction note: synthetic CGEvent clicks do not take the crashing path (a
  posted click leaves the app alive), so the owner's real click was the
  verification - the menu opened and the app survived.
* Tests: 391 green twice in a row - test_hardening.py 150 (new: the menu bar
  menu is opened by us on macOS; the test opens the real popup and asserts the
  click and double-click wiring), test_live_preview.py 137, test_smoke.py 104. Ruff clean;
  scripts/check_version.py agrees: 2.1.1-beta.17 (tuple 2, 1, 1, 17).
* Packaging: Apple Silicon DMG built, notarized, stapled and installed. spctl:
  accepted, source=Notarized Developer ID; bundle reports 2.1.1-beta.17, signed
  by team 9AMNWJRC93, bundle id nz.co.bytemind.byteproof. DMG sha256
  5b23c7e4b0c8d894caf5491810a35e852039766fb21b0d7a31e73471477c7a8e.

### 2026-09-18 - 2.2.0 release evidence

* CI tag run 35382550774: Tests (macos-14) passed in 1m3s; Windows installer (x64)
  succeeded; the informational Windows test job was still running when checked.
* Release assets after the local upload: ByteProof_Installer_AppleSilicon.dmg
  (34343730 bytes, sha256 1addacc77dbcf8d418d0ed2dd3dd807af827ae1873af30d631a8729955b73d8a),
  ByteProof_Windows.zip (sha256 10375dbffd9be57e767cd7f41801dfe129a5542616cc699df3c7f09b0ca4c1eb),
  ByteProof_Installer_x64.msix (sha256 23c81d0b0f31821b1cc8cd55f3567bb4bf301ea8e92fc04499a43d6d6d8f2ef5).
* Local install: /Applications/ByteProof.app reports 2.2.0 and is running (pid 13640).
* Blocked step, with the exact error: invoking .venv_x86/bin/python3 under
  arch -x86_64 prints "Bad CPU type in executable"; arch -x86_64 /usr/bin/true
  fails the same way, so Rosetta 2 is not present. Both the venv Python and the
  python.org framework Python are universal2 (x86_64 + arm64 slices verified).
* Live feed: still advertises 2.1.0 (curl of
  https://www.bytemind.co.nz/byteproof-version.json returned version 2.1.0), so
  no installed copy has been told about 2.2.0 yet.

### 2026-09-20 08:44 NZST - 2.2.0 announcement evidence

* Root cause, read-only: the release page had v2.2.0 published
  2026-09-18T18:53:38Z (draft=false, prerelease=false) while
  https://www.bytemind.co.nz/byteproof-version.json still served 2.1.0.
  `releases/latest/download/ByteProof_Installer_Intel.dmg` returned HTTP 404;
  the Apple Silicon DMG and the Windows ZIP returned 200.
* Rosetta 2 still absent: `arch -x86_64 /usr/bin/true` -> "Bad CPU type in
  executable"; `sudo -n true` -> "sudo: a password is required", so the Intel
  build cannot be produced unattended.
* Artifacts verified from the release itself (downloaded over HTTPS, not read
  from dist/): ByteProof_Installer_AppleSilicon.dmg, 34343730 bytes, sha256
  1addacc77dbcf8d418d0ed2dd3dd807af827ae1873af30d631a8729955b73d8a - the
  mounted bundle reports CFBundleShortVersionString 2.2.0, signed by
  "Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)", and
  `spctl -a -t install` accepts it (source=Notarized Developer ID).
  ByteProof_Windows.zip sha256
  10375dbffd9be57e767cd7f41801dfe129a5542616cc699df3c7f09b0ca4c1eb.
* Feed behaviour checked with the app's own code (src/app_version.py, venv
  Python 3.13.7): is_newer("2.2.0", "2.1.0") True and
  is_newer("2.2.0", "2.2.0") False, so an installed 2.1.0 is offered the
  update once and 2.2.0 installs are not re-nagged; the arm64 and Windows keys
  resolve to allowed-host URLs while the Intel key resolves to none; both
  published digests verify against the downloaded artifacts, and flipping one
  byte in the ZIP makes verification fail.
* Website push 9be8298 (michael-zumba/bytemind-website), only
  byteproof-version.json and byteproof.html staged - the repo's unrelated dirty
  files were left untouched. GitHub Pages picked it up on the 4th poll
  (~40 s): the live feed serves 2.2.0, and the live page's three download links
  (Apple Silicon latest, Intel v2.1.0 pinned, Windows latest) each return 200.
