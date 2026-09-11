# Live Proofread Preview — Checkpoint 1

## Current todo

- [x] Spec, plan, long-task records
- [x] Task 1: pure core (parse, map, trigger, cache)
- [x] Task 2: preview prompt + provider call + request refactor
- [x] Task 3: AX selection details, bounds, range replacement
- [x] Task 4: overlay underlines + hover popup
- [x] Task 5: live service orchestrator
- [x] Task 6: Word native underlines, sub-range apply, suggestion card
- [x] Task 7: settings schema + General-page controls
- [x] Task 8: end-to-end + cache-efficiency tests
- [x] Task 9: Word/TextEdit visual runs + accuracy corpus + packaged build
      (Mail compose and Pages partially environment-blocked; see evidence)
- [x] Task 10: full verification (suite green, installer notarized)
- [x] Polish round from Word testing: "Suggested changes" title, premium
      popup/card redesign with auto-sizing, direct background apply/apply-all,
      persistent marks + hover-after-deselect, reselect fix, permissive
      app/browser compatibility, AX descendant text search
- [x] Second polish round: Google-styled popup, single-control rebuild fix,
      working single Apply, accumulating marks across selections, hover
      survival across rect refreshes, wider AX search for Pages
- [x] Third polish round: ephemeral non-tracked Word underlines with exact
      restore, pinpoint word diffs, stable non-activating popup/card,
      wider hover targets, brighter UI
- [x] Fourth polish round (1.9.0-beta.2): popup click stealing fixed,
      Word mark journal + self-heal on crash/kill, leftover marks cleaned
      from the live document
- [x] Design pivot (1.9.0-beta.3): selection-triggered suggestion panel only;
      no in-place marks, no Word formatting, no overlay tracking
- [x] Fifth polish round (1.9.0-beta.4): worker cancellation restored,
      safe thread lifecycle, selection re-verification before show/apply,
      bounded retry after transient provider failures, Word hidden-char
      (tracked deletions/fields) offset compensation, fuzzy-map span fix,
      Escape-to-dismiss, multi-display panel placement, apply-feedback
      toasts, panel keeps remaining suggestions when the selection survives
- [x] Sixth fix round (1.9.0-beta.5): apply fixed for non-Word apps
      (parameterized-replace probe, attribute writes, paste fallback with
      per-step AX error logging); Pages/Mail selection via throttled
      clipboard-preserving Cmd+C read; full-selection paste apply when no
      absolute range exists; broader AX search + diagnostics
- [x] Seventh fix round (1.9.0-beta.6): apply instrumentation (entry/sync/
      result logging, sync retry + honest toasts, System Events paste
      fallback + read-back verification, permission-transition logging)
- [x] Eighth fix round (1.9.0-beta.7): browsers apply via real paste with
      positional verification (Chrome's AX writes fake success); selection-
      scoring element search
- [x] Ninth fix round (1.9.0-beta.8): every AX write verified positionally
      with paste fallback (Outlook's fake-success writes); no_permission
      log spam silenced
- [x] Aesthetic round (1.9.0-beta.9): shadowed card, dividers, reason
      chips, diff arrows, dimmed context
- [x] Interaction round (1.9.0-beta.10): black-box shadow removed (opaque
      card), drag-to-move by header, pop-in fade/grow animation, softer
      borders/buttons/diff wells; spurious "could not verify" fixed
      (two-poll glitch debounce, sync compares the previewed text only,
      frontmost-target check for stale results, 3× sync retry)
- [x] Editable-context gate (1.9.0-beta.11): suggestions only in editable
      contexts — settable-attribute probing (PDF/webpage reads skipped),
      Word/Pages/Mail context rules incl. Mail compose-window detection
- [x] Suggestion style + latency instrumentation (1.9.0-beta.13): strict vs
      "Polish language (preserve meaning)" mode with its own prompt and a
      Settings selector; timestamped capture.log and provider_ms logging
- [x] UI/UX protocols spec adopted (docs/superpowers/specs/
      2026-09-09-live-proofread-uiux-protocols.md); Step 1 done
      (1.9.0-beta.14): loading pill while the AI works, undo pill after
      every apply (10 s, restores at the recorded absolute range; the
      full-selection variant verifies the selection first)
- [x] UI/UX Step 2: onboarding & trust readiness card
- [x] UI/UX Step 3: shared design tokens + diff-view reuse
- [x] UI/UX Step 4: reason color dots, status chip, quit flow
- [x] Backlog: P2.3 dismiss-per-suggestion, P2.5 panel position memory
      (P4.4 per-app filter deferred)
- [x] Released as ByteProof 2.0.0 (branch merged to main, installers
      published, update feed live)

## Completed evidence

- 37/37 `tests/test_live_preview.py` pass (`QT_QPA_PLATFORM=offscreen ./venv/bin/python -m pytest tests/test_live_preview.py -q`).
- Full smoke suite passes: `ALL_SMOKE_TESTS_PASSED`.
- Real DeepSeek accuracy corpus (`tests/corpus_live.py`) run twice: `CORPUS_RESULT failures=0` both times.
- Real TextEdit live run: trigger, DeepSeek preview (3 edits), dashed pink-red underlines
  pixel-verified on screen, hover popup window + red/green track-changes text
  pixel-verified, click-on-underline replaced "teh" with "the" in the document
  (verified via AXValue).
- Word AppleScript surface live-verified: selection positions, `create range`,
  `underline dot dot dash`, RGB color, `track revisions`, position/zoom info.

## Fixes found by the live campaign

- Repeated identical edits now map to distinct occurrences (removed pair dedupe).
- Overlay `setMask` now uses window-local coordinates.
- Removed the `Qt.WindowType.Tool` flag: it broke translucent-window compositing
  when the app also has a normal window.
- Hover/click input moved from the overlay window to a listen-only Quartz
  event tap (global mouse events do not reach masked non-key windows).
- `ax_replace_range` probes `AXUIElementSetParameterizedAttributeValue` before
  calling it; TextEdit uses the selected-text fallback successfully.

## Next

1. Word visual run (native underline + card + click apply).
2. Mail compose and Pages runs.
3. Packaged build via `./build_macos.sh` and packaged verification.
4. Final suite + evidence bundle + finishing-a-development-branch menu.

## Drift check

Scope unchanged (macOS beta, same four defaults). No new owners or schema
changes beyond the planned `settings["live_preview"]`. Decision: `continue`.

## Checkpoint (2.0.1-beta.2) — ChatGPT/Codex apply refusal diagnosed and fixed

### Diagnosis (from capture.log 17:45–17:47)

"0 of N changes applied" in ChatGPT (bundle `com.openai.chat`) was NOT offset
drift: every value-slice diagnostic matched the original document text at the
exact expected code-point offsets. The document was never modified — the
refusal guard worked as intended.

The real cause: ChatGPT applies `AXSelectedTextRange` writes ASYNCHRONOUSLY.
The immediate readback still shows the old range (the log shows the selection
trailing exactly one edit behind), so the confirmation failed and the paste
was refused every time. `AXSelectedText` writes are ignored by that app.

### Fix (beta.2)

- `ax_replace_range` now re-reads the range confirmation over a short window
  (`RANGE_CONFIRM_RETRIES=6 × 0.12 s`) and also accepts a selected-text match
  on the original span as proof the range landed.
- New `before_text` parameter (passed from `_apply_one`/`_apply_all` via the
  original span slice): the AX selected-text write is skipped unless the range
  verifiably holds the original text — writing into a stale range in an async
  app could otherwise corrupt the document.
- Paste guards tightened: the retry-via-System-Events only fires when the
  range still holds the expected original text; a paste whose verification
  lags gets one settle re-check before any retry.
- A delayed AX write that already applied the edit is recognised as
  "Applied." instead of a false failure or a duplicate paste.
- 4 new tests (async range, selected-text confirmation, stale-range skip,
  delayed-write recognition); full suite 229 passed.

### Next

- Owner re-test in ChatGPT/Codex (Apply and Apply all on a selection with
  several suggestions).
- If range confirmation still fails there, consider per-app full-selection
  paste fallback (Mail/Pages path) — needs an owner decision on the UX.

## Checkpoint (2.0.2-beta.1) — full-app review fixes implemented

Implements the 2026-09-10 review (`docs/aegis/plans/2026-09-10-codebase-review-roadmap.md`).
256 tests pass; the suite now runs in CI on macOS and Windows with lint and a
version-consistency check.

### Security / licensing
- The public support address is no longer a master key. `DEVELOPER_EMAILS`
  ships empty; developer access needs explicit local configuration
  (`dev-access.json` via `scripts/dev_access.py`, or `BYTEPROOF_DEV_EMAILS`).
  Verified: the owner's machine is licensed through **Polar**, so this cannot
  affect their access.
- `byteproof://` activation asks the user to confirm the key first.
- Revoked licences are detected again (`validate_license_remote` returns
  `ok=False`; the handler tested a `valid` key that never existed).
- `settings.json` and `license.json` are written atomically with `0600`.
- `capture.log` records lengths and digests instead of document text.

### Update path
- Beta builds are offered later releases: `2.0.1-beta.2 < 2.0.1 < 2.0.2`.
  Because the old numbering (`2.0.1-beta.N`) sorts below the released `2.0.1`,
  this beta is numbered **2.0.2-beta.1** so testers are not told to "update"
  to the older release.
- Downloads are restricted to GitHub/ByteMind hosts over https and verified
  against a published SHA-256 when the feed provides one.
- The startup update notice is a toast, not a modal; the modal update dialog
  can no longer re-enter itself from its nested event loop (this was a real
  hang that the version fix exposed).

### Document safety
- Word: AppleScript timeout, Track-Changes restored on error paths, guarded and
  read-back-verified live edits, Windows `apply_live_edit` implemented,
  comments verified before pasting, diagnostics in `capture.log`.
- Live preview: guarded undo with a bounded stack, clipboard restored only
  after the paste is observed, secure fields skipped, paywall respected,
  dismissal sticky, parameterized AX write converted to UTF-16 and verified.
- Engine: truncated replies are refused instead of auto-applied; entitlement is
  checked before any local model download; the local output cap fits the
  server context.

### Not done (deliberately deferred, needs a decision or a larger change)
- Moving the 350 ms AX poll off the GUI thread (large refactor; the timeouts
  and bounded waits remove the worst freezes for now).
- Windows live preview (macOS-only engine; `apply_live_edit` now exists so the
  port is unblocked).
- Accessibility metadata/i18n, dark mode, per-suggestion review queue in the
  main window, document-level compliance checks, managed cloud credits,
  opt-in telemetry.

## Checkpoint (2.0.2-beta.2) — focus-independent applies + faster live loop

Owner request: (a) an apply must still happen when the edited window is not
frontmost, (b) a new selection must not interrupt an in-flight apply nor
auto-trigger a preview of its own, (c) make live detection and applying faster
without losing accuracy.

### Focus independence
- `_begin_apply`/`_end_apply` bracket every apply and stop the poll timer for
  its duration; `_sample` returns immediately while `_applying`. A new
  selection or another app coming forward can no longer abort the edits or
  spawn a second preview.
- The apply targets the captured app and range; `_read_selection_state` reads
  that app through AX/AppleScript, which works in the background. AX-write
  capable apps (TextEdit, Notes, ChatGPT) apply with no activation at all.
- When a paste is required, the target is activated only if it is not already
  frontmost, and `_restore_user_focus` hands focus back to the app the user
  actually moved to (tracked per span in `_note_foreign_frontmost`).
- `_keep_panel_for_detached_target` keeps the panel for up to 20 s (revalidated
  every 1.5 s, dismissed when the captured app closes or its selection changes)
  so Apply/Apply all is still clickable after switching apps.
- `_suppress_current_selection` records (pid, text) selections that appeared
  during an apply and ignores them for 15 s, so "selecting something to read"
  never triggers a preview.

### Speed (no change in accuracy)
- Poll 350 → 250 ms; default preview delay 900 → 600 ms.
- `_mac_ax_text_element`: returns the focused element immediately when it is
  the text field (the common case) and otherwise caches the found element for
  2.5 s; caches are dropped when the frontmost app changes.
- The editable-context probe is cached per (ApplicationServices, pid, element).
- The Word document name is read lazily at apply time instead of on every
  preview (one AppleScript call saved per preview).
- Apply: activation and its sleep are skipped when the target is already
  frontmost, paste confirmation interval 150 → 80 ms, settle 350 → 150 ms.
- Word: poll reads use a 3 s timeout and the service backs off 5 s when Word
  stops answering (modal dialog) rather than blocking the UI thread per tick.
- The live service is not started for offscreen Qt runs, so the test suite no
  longer drives real Accessibility/AppleScript against the user's apps (this
  was exposing a busy Word and hanging the suite).

## Checkpoint (2.0.2-beta.4) — browser applies fixed

Owner report: in a browser (Safari, typing in a web text box) Apply sometimes
answered "could not identify the selection, please try again".

`capture.log` showed the cause (the surrounding pid=9 lines were test noise):

```
[21:02:58] LIVE APPLY ALL: app='Safari' has_range=True count=3
[21:02:59] LIVE APPLY ALL SYNC FAIL: selection changed: previewed='Also check the log. sometimes ' now=''
```

The preview read the selection correctly a second earlier; the Accessibility
re-read at apply time returned an empty string. Web views re-render
continuously and Safari frequently answers `AXSelectedText` with nothing even
while the selection is intact, so the pre-apply verification refused a perfectly
good selection.

Fixes:
- `_read_selection_state` drops the cached AX element and re-reads once with a
  freshly discovered element when the first answer is empty (a stale cached
  element in a re-rendering page was the likely trigger).
- `_sync_selection` treats an *unreadable* re-read as "ask the app": it copies
  the selection for real (`get_selection_by_copy`, Cmd+C posted to the target
  process, clipboard restored). Matching text lets the apply proceed; text that
  genuinely differs still refuses. Word keeps using AppleScript, which is
  reliable there.
- Failure messages now name the app and the remedy ("Click into the text,
  select it again, and press Apply") instead of a generic retry prompt.
- Offscreen runs skip the network-backed startup work (update check, remote
  licence validation); their TLS worker threads were crashing the test process.

## Checkpoint (2.0.2-beta.5) — current model defaults + Settings tidy-up

### Provider models (verified 2026-09-10 against vendor docs)

| Provider | Was | Now | Source |
| --- | --- | --- | --- |
| DeepSeek | `deepseek-v4-flash` | `deepseek-flash` | api-docs.deepseek.com (V4.1-Flash; the v4-flash model was retired, the name still resolves) |
| Google Gemini | `gemini-2.5-flash` | `gemini-3.8-flash` | ai.google.dev OpenAI-compatibility page |
| Groq | `llama-3.1-70b-versatile` (removed by Groq) | `openai/gpt-oss-120b` | Groq model catalogue |
| OpenAI | `gpt-4o` | `gpt-5.5` | OpenAI GPT-5.5 announcement |
| Anthropic | `claude-sonnet-4-20250514` | `claude-sonnet-5` | Anthropic model/ID list (verified Sep 8, 2026) |
| xAI | `grok-3-beta` | `grok-4.6` | docs.x.ai (model name is `grok-4.6`) |
| Perplexity | `sonar-pro` | unchanged | Perplexity model list |

Base URLs were all still correct and are unchanged.

`SUPERSEDED_DEFAULT_MODELS` + `refresh_superseded_models()` move an install to
the new default when the saved model is one ByteProof previously shipped, so a
never-customised provider cannot stay on a retired model. A model the user
chose (or a local model) is never touched, and the change is persisted through
the atomic 0600 settings writer.

### Settings panel: one entry point per page

Decision: keep the **labelled sidebar rows**, remove the duplicate icon-only
buttons. Reasons: two entry points to the same page is confusing; the rows are
discoverable and standard; the 34x34 unlabelled buttons were the least
discoverable part of the window. The icons moved onto the rows, which now also
carry live status — `License  ✓` (or trial days / free mode) and `Updates •`
when a version is waiting.

### Test infrastructure

The combined `pytest tests/` run intermittently segfaulted on macOS: Qt C++
objects are released by Python's garbage collector, so a window created by one
file could be destroyed while a later file was constructing its own.
`scripts/run_tests.sh` (and CI) now run one test file per process: 273 tests,
three deterministic clean runs.

## Checkpoint (2.0.2-beta.6) — browser applies when the selection disappears

Follow-up report of "could not identify selection" in browsers. The log showed
the exact state:

```
[06:37:45] LIVE PREVIEW: app='Safari' chars=179
[06:37:52] LIVE APPLY ALL: app='Safari' has_range=True count=2
[06:37:53] copy attempt 'process' -> <len=0>
[06:37:53] LIVE SYNC: selection unreadable (AX and copy both empty)
```

Clicking the suggestion panel leaves the captured app behind ByteProof. While
it is not frontmost, WebKit reports no `AXSelectedText` and ignores a posted
Command-C, so the pre-apply verification had nothing to compare and refused.

Fixes:
- `_sync_selection` brings the captured app forward when it is not already
  frontmost and re-reads the selection (the apply needs it frontmost for the
  paste regardless). The app the user was actually using is remembered so
  `_restore_user_focus` hands focus back afterwards.
- If AX, the copy and the re-read all come back empty, the document itself is
  the authority: `field_text_at()` reads the text at the captured range and a
  match lets the apply proceed. The write path re-verifies the same range with
  `before_text` before writing, so nothing is ever written blind.
- A genuinely changed document still refuses with the same message.

Covered by three tests: the reported case applies, a changed range refuses, and
the bring-forward path activates the app before verifying. 276 tests pass.

## Checkpoint (2.0.2-beta.7) — Mail apply path fixed

Reported as "some bugs when using it in Mail". The log showed two separate
defects:

1. **Refusing when one clipboard read missed.**
   `[06:46:14] copy attempt 'process' -> <len=0>` followed by
   `LIVE FULL APPLY: selection read failed` - though the same read had returned
   178 characters two seconds earlier. Mail selections can only be read by
   copying, and Mail intermittently ignores a process-targeted Command-C.
   `_apply_full_selection` now retries through every copy strategy before
   refusing, the poll's clipboard read gets one retry, and a genuine failure
   reports the actionable message.

2. **Possible double paste.**
   `[06:46:26] mac_replace: paste was not observed in the target` followed by
   `[06:46:27] LIVE FULL APPLY: system-events paste sent`. Mail exposes no
   Accessibility text, so an unconfirmed paste is not proof that nothing
   happened - yet the old code pasted a second time over a selection that had
   probably already been replaced, which could duplicate the paragraph.

   The fallback now requires evidence from a real copy taken after the attempt:
   the ORIGINAL text (nothing happened -> retry), the CORRECTED text (already
   applied -> no retry), or nothing readable (unknown -> report for review and
   never paste again). Verification also uses the copy read rather than the
   single-attempt AX read.

Tests: flaky read retried, no second paste after a successful one, retry when
the text is provably unchanged, and the actionable refusal message.

## Checkpoint (2.0.2-beta.8) — suggest only after a real selection

Owner report: Mail's Edit menu blinked constantly (the app looked like it was
working in the background the whole time), and suggestions should only appear
for a genuine selection of at least a few words.

### Why Mail blinked

A Mail compose selection can only be read by *posting Command-C* (Mail exposes
no AX selection), and the poll did that every ~2.5 s for as long as a compose
window was frontmost. macOS flashes the corresponding Edit-menu item for every
delivered key equivalent - hence the blinking. The log showed reads at
07:11:19, :21, :22, :24, :26, :29, :31, :32.

### Fix: read only when a selection was just made

`_selection_gesture_seen()` now gates the clipboard read:

* within `SELECTION_MOUSE_WINDOW_S` (1.2 s) of a left mouse-up - a drag or
  click selection just finished, so a read is worth its side effect;
* otherwise once, after the user has stopped interacting for
  `SELECTION_IDLE_SETTLE_S` (1.2 s), which also catches keyboard selections
  (Shift+arrows, Cmd+A) - and only once per burst of activity;
* never in a loop while the user types or reads.

`GenericTextEditor.idle_seconds()` / `mouse_up_seconds()` read the system event
source; if the OS returns nothing, the gate falls back to the previous
behaviour rather than silently disabling Mail/Pages support. The poll also
posts one keystroke per read, trying the second copy strategy only when a mouse
selection was just made.

### Minimum selection

`evaluate_trigger` requires `MIN_PREVIEW_WORDS` (3) in addition to the 8-char
floor, so a stray word or two never pings the model. The threshold is
configurable in Settings -> Live Suggestions (1-10 words) and stored as
`live_preview.min_words`.

## Checkpoint (2.0.2-beta.9) — Mail keystroke storm stopped

Follow-up: Mail still lagged, beeped and flashed its Edit menu. The log showed
why - a burst of copy keystrokes right after clicking Apply:

```
[07:32:18] copy attempt 'process' -> <len=0>
[07:32:18] copy attempt 'process' -> <len=0>
[07:32:18] copy attempt 'system'  -> <len=0>
[07:32:19] copy attempt 'system_events' -> <len=0>
[07:32:22] LIVE FULL APPLY: selection read failed
```

Each attempt posts Command-C: it beeps when there is nothing to copy, flashes
the Edit menu, and blocks the UI thread while waiting - the three symptoms at
once.

1. **The apply read a background window.** A copy of a non-active window
   returns nothing (and beeps), and the previous retry logic multiplied one
   miss into three strategies by three rounds. The full-selection apply now
   brings the captured app forward first (the AX path already did) and retries
   at most once, spaced past the copy rate limit - three keystrokes instead of
   ten.
2. **Any mouse-up armed a read**, so clicking anywhere in Mail posted a copy
   with no selection. The gate now needs a recent *drag* (a click is not a
   selection); the once-after-idle path still covers keyboard selections.
3. **Hard guard:** `MIN_COPY_INTERVAL_S` (0.5 s) between any two copy
   keystrokes in `generic_editing`, whatever code path asks. Per-attempt wait
   reduced from 0.4 s to 0.25 s.

## Checkpoint (2.0.2-beta.10) — Settings sidebar half height

Owner report: the Settings menu column showed only about half its height, so
"Updates" was cut off and needed scrolling.

Cause: a regression from removing the duplicate License/Updates icon buttons.
Their bar sat under the page list, and when it went, a trailing
`addStretch(1)` remained - the list and the stretch each claimed half of the
column, so only four of six rows were visible.

Fixed and polished:
- the list fills the column; at the dialog's default size the last row ends at
  275 px inside a 509 px viewport, with no scrollbar
- small "SETTINGS" heading above the list
- tighter rows (42 px) and margins so six rows plus the heading sit comfortably
- explicit policies: vertical scrollbar only when needed, never horizontal
  (a status suffix such as "License  (5d left)" must not spawn one), text
  elided, no frame

A regression test asserts every row is inside the viewport and that neither
scrollbar appears.

## Checkpoint (2.0.2-beta.11) — hotkeys, silence in Mail/Pages, General page

Four owner requests.

### 1. Hotkey to toggle live suggestions
`Cmd+Shift+L` (configurable). Flips `live_preview.enabled`, saves, refreshes
the service, updates the readiness row and shows a toast. Registered with the
existing hotkey facade, so it works in any app.

### 2. Hotkey to Apply All
`Cmd+Shift+Return` (configurable) applies every suggestion in the visible
panel via a new `LivePreviewService.apply_all_now()`, which returns False when
nothing is on screen so the shortcut stays silent. The hotkey parser now
understands `<return>`/`<enter>` (matched by keycode, like Escape), the Qt
round-trip maps Return both ways, and `display_hotkey` renders
`Cmd+Shift+L` / `Cmd+Shift+↩`.

### 3. No more beeping in Mail and Pages
Every Command-C posted to read a clipboard-only selection makes the app beep
when nothing is selected. A copy is now spent only when the user really
selected something:
- a new observe-only `_InputWatcher` records the last drag, double-click or
  Shift/Cmd chord; the read happens only within a short window after one of
  those and never while typing, reading or clicking around (a click is not a
  selection). The timing-probe gate remains as the fallback when the monitor
  cannot be installed.
- an apply no longer re-reads the selection for clipboard-only apps (the paste
  consumed it, so the read posted Command-C with nothing selected - the beep);
  the idle read stays disarmed until the user interacts again.
- post-paste verification tries the free Accessibility read first and at most
  one copy.

### 4. General settings page
Sections reordered by use - Live Suggestions, App & Window, Microsoft Word,
Hotkeys, Proofreading Style, Proofreading Settings - with a one-line subtitle
and muted helper text under the live controls (naming the real hotkeys), under
Track Changes, under the hotkey list, and under the temperature slider. The
Word options moved out of the mixed "Preferences" card into their own.

## Checkpoint (2.0.2-beta.12) — Live Check menu, per-app triggers, cleaner pages

Three owner requests.

### Naming
The feature is **Live Check** in the interface: short, familiar next to Word's
spell check, and it no longer reads as a beta experiment. The settings key
stays `live_preview`, so nothing migrates.

### Live Check is its own top-level menu (after General)
It holds everything about the feature, mirroring Automation's fold/unfold:

- master switch;
- **Show Apps** folds out the per-app trigger list - the apps ByteProof knows
  about (Word, Mail, Outlook, Pages, TextEdit, Notes, Chrome, Safari, Edge,
  ChatGPT), each with a checkbox, plus **Add App…** (chooses from the apps
  running right now) and an **Allow other apps** fallback;
- **Suggestions**: style, minimum words, wait after selecting, prefer local AI;
- **Hotkeys**: turn Live Check on/off, apply all suggestions.

Engine: `live_preview.app_rules` maps an app identifier (bundle id, name
fragment, or `"*"` for everything else) to enabled/disabled; an empty map keeps
the previous behaviour (every app). `evaluate_trigger` returns `app_disabled`
for an excluded app, which the poll logs once rather than every tick.

### General page: explanations moved into ⓘ tooltips
New `info_icon()` / `labelled_with_info()` helpers put the rationale on hover
instead of as paragraphs of grey text. Converted: Track Changes, the
temperature slider, the proofreading settings rows (spelling, style, comment
type, context) and the hotkey fields. General now reads as five clean cards:
App & Window, Microsoft Word, Hotkeys, Proofreading Style, Proofreading
Settings.

### Fold state
`_toggle_live_apps` tracks its state explicitly: `isVisible()` is always False
while a page is not the one on screen, which inverted the toggle.

## Checkpoint (2.0.2-beta.13) — Live Check "Add App" lists installed apps

Owner report: Add App showed a long list of contexts. It listed *running*
processes; it now uses the same chooser as Automation's "Choose App…".

- `SettingsDialog.choose_installed_app(existing=…)` is the shared picker:
  applications from `~/Applications`, `/Applications` and
  `/System/Applications` (105 on this machine), each with its **real icon**
  from the bundle, a search field, and double-click to choose.
- `_choose_app_for_trigger` (Automation) wraps it with the existing-rule
  filter; Live Check passes the markers already in its list.
- The chosen app is added to the list with its icon and checked; the list
  folds out so the new row is visible; the value lands in
  `live_preview.app_rules` as its bundle id.
- Rows for the known apps also show real icons when those apps are installed.

Tests: the scanner returns named apps with real icons; Add App appends the
chosen app with the right marker, checkbox state and rule; list rows carry
icons when installed.

## Checkpoint (2.0.2-beta.14) — clicking License in Settings

Owner report: the License row in Settings did nothing when clicked.

Cause: the row label carries a status suffix ("License  ✓", "Updates •") that
the sidebar status added, but `change_page()` resolved pages by the label text.
The lookup missed, no page was switched, and the previous page stayed on
screen. `Updates` only worked while it had no dot.

Fixed by removing the fragile coupling:
- each sidebar row stores the page it opens (`Qt.ItemDataRole.UserRole`) and
  `change_page()` uses that; the label map remains only as a fallback
- `_row_for_page()` replaces the hard-coded row numbers used for the
  License/Updates icons, the Local AI shortcuts and the License shortcut
- a regression test clicks every row and asserts the expected page appears,
  including with a licence badge and a pending-update dot present

## Checkpoint (2.0.2-beta.15) — File/Edit menus: no more beeping in Mail/Pages

Owner report: opening Mail or Pages produced two or three beeps in the
background. Same root cause as before - a Mail/Pages selection can only be
read by copying, and posting Command-C to an app with **nothing selected**
makes it beep. The previous rounds reduced *when* that happened; this removes
the keystroke entirely.

macOS exposes a running app's menu bar through Accessibility, including each
item's key equivalent and whether it is enabled:

- `_mac_copy_menu_item()` finds Edit ▸ **Copy** by its *key equivalent*
  (Command-C with no extra modifiers), so it is language-independent and skips
  lookalikes such as Safari's "Copy Search Terms".
- `AXEnabled == False` ⇒ the app has nothing selected: the read returns
  immediately **without sending anything** - no beep, no menu flash, and about
  0.05 s instead of up to 1.2 s of attempts.
- Otherwise the item is performed with `AXPress`, which runs the app's own Copy
  command. Again no key equivalent, so nothing can beep or flash.
- Posting Command-C is kept only as a fallback for apps whose menu cannot be
  read.

Verified live: Safari with no selection returned "" in 0.05 s with the log line
"copy selection skipped: the app reports nothing selected"; TextEdit with a
selection returned the text via "copy selection used the app's Copy menu
command".

## Released (2.0.2) — 2026-09-11

Owner instructed the official release of 2.0.2. Three things had to be fixed
first, and one of them explains why the release took so long.

### Why the release stalled

Every push and tag ran the test suite on macOS and Windows, and the Windows
job hung for the full 20 minute job timeout on the first test file with no
output at all. That left `windows: needs: test` skipped, so the Windows
installer was never built and the GitHub release was never created - the macOS
DMGs had been finished and notarized since 11:05.

Diagnosis, in evidence order:

1. A provisional CI log (verbose pytest, `faulthandler_timeout`) named the
   macOS hang outright: `tests/test_smoke.py::test_local_download_worker_*`
   was blocked inside `src/gui.py::check_api_keys` → `QMessageBox.exec()`.
   The startup timer opens a modal "download a local AI model" dialog, which
   can never be dismissed offscreen, so the nested event loop never returned.
   It only reproduced on a machine with **no local model installed**, which is
   why it passed locally and on the previous release.
2. The Windows job flushed its buffered progress line at cancellation:
   `.......F..............F..................................F.....F`, i.e. 64
   tests done, four failures (8, 23, 58, 64), then stuck on test 65. The
   hosted Windows runner then stopped reporting altogether - job, step and
   cancellation timers all failed to land - so no traceback could be read.

Fixes:

- `check_api_keys()` returns immediately when the UI is offscreen; no modal is
  ever opened where nobody can answer it.
- `tools/` (the RSA signing key and `generate_license.py`) is gitignored, so
  the three signed-key tests now skip where the generator is absent instead of
  failing.
- Windows-unsafe assertions are platform-aware: POSIX file modes, the
  `<cmd>` → `<ctrl>` hotkey default, the macOS-only Live Check page controls,
  and the download test now names the running platform's feed field.
- `scripts/run_tests_ci.py` runs each test file in its own process group, streams
  its output and kills the tree when a file stops progressing, so a wedged test
  can no longer hold a job (and a release) hostage; `conftest.py` prints each
  test as it starts under `BYTEPROOF_CI_PROGRESS=1`.
- macOS is now the release gate; the Windows suite still runs informationally,
  and the Windows *packaging* job remains a hard gate (it succeeded).

### Also shipped

- Proposed Changes renders exactly like 2.0.0 again: the 2.0.2 hardening pass
  had switched the unchanged manuscript to full contrast and underlined every
  inserted word, which made the page read as one busy block. Unchanged text is
  dimmed again and inserts carry colour + weight only; the renderer output is
  byte-identical to 2.0.0 for the sample corpus (verified by diffing
  `diff_html` between the two revisions).

### Evidence

- macOS CI: `74 passed` (hardening), `126 passed` (live preview),
  `101 passed, 3 skipped` (smoke), job `success`.
- Release `v2.0.2` published with four assets: Apple Silicon DMG (34,405,482 B,
  sha256 `cb0230ae…`), Intel DMG (35,552,368 B, sha256 `7045239c…`), Windows zip
  (48,784,576 B, sha256 `30b592a1…`) and the Store MSIX.
- Feed `https://www.bytemind.co.nz/byteproof-version.json` serves 2.0.2 with a
  `sha256` map; all three download URLs answer.

## Checkpoint (2.0.3-beta.3) — Word with tracked changes: 40s apply → sub-second

Owner report: Word lagged badly and the app froze, worst when the selected text
contained tracked changes.

`capture.log` gave the shape of it: a five-suggestion Apply All started at
14:04:11 and the first result only appeared at 14:04:51 - 40 seconds - and an
earlier four-suggestion batch took 15 seconds. The poll also logged repeated
`WORD: AppleScript timed out after 3s; Word is probably showing a dialog`.

Cause: the visible→document position mapping. Word counts tracked deletions and
field codes in document positions but omits them from `content`, so an offset in
the previewed text has to be translated before a range can be written.
`get_selection_hidden_spans` did that by scanning the selection **one character
at a time** in AppleScript (`create range` → read `content`, per character), and
`_word_compensated_span` re-read the selection and re-ran that scan **for every
span**. A 700-character selection with five suggestions therefore meant roughly
3,500 Word operations, each one forcing Word to re-resolve its revisions.

Fix:

- `live_doc_positions()` finds each position by binary search on the only
  monotone quantity Word exposes - the length of the visible content from the
  selection start, which grows by one per visible character and not at all
  across hidden ones. That is ~10 reads per offset instead of one per character.
- A batch shares one selection read and one mapping call, and every applied edit
  shifts the map by its own length change (the inserted text carries no hidden
  characters, so the arithmetic is exact).
- The mapping call is bounded at 5s, and when it fails the offsets are left raw
  rather than falling back to the character scan - on a busy Word that fallback
  is hundreds of timed-out reads, which is the freeze being removed. Word's own
  before-text guard then refuses a write it cannot place, so accuracy is kept.
- The poll read is bounded at 1.5s instead of 3s: a Word showing a modal dialog
  used to stall the UI thread for three seconds per tick before backing off.

Evidence: `tests/test_live_preview.py::test_word_batch_maps_positions_once_for_every_suggestion`
asserts one mapping call and zero character scans for a two-suggestion batch,
and that the second span is written at the position shifted by the first edit's
length change. The scan stays covered as the fallback path. 309 tests pass.

The Windows path used COM `Range.Revisions` (already O(revisions)) and now uses
the same binary search, where each probe is an in-process call.

## Released (2.1.0) — 2026-09-11

Owner instructed the official release, named 2.1 (three parts are required by
`scripts/check_version.py`, so the shipped version is `2.1.0`). It carries the
three fixes from this session:

- Live Check applies every suggestion it can place (Teams stopped at the first
  miss and dropped the rest).
- Word with tracked changes is fast again: a five-suggestion apply went from 40
  seconds to the region of a second, and a busy Word can stall a poll at most
  1.5s instead of 3s.
- The in-app updater downloads again (GitHub's release-asset host).

Artifacts: Apple Silicon DMG (34,402,497 B, sha256 `5f3a4bba…`), Intel DMG
(35,562,591 B, sha256 `ee0306fa…`), Windows zip (48,792,831 B, sha256
`89241293…`) and the Store MSIX, all attached to the published release
`v2.1.0` ("ByteProof 2.1").

Two process notes worth keeping:

1. `tools/bump_version.py` carried the previous release's `sha256` map into the
   new feed, which would have made every 2.1.0 download fail its integrity
   check. The tool now empties the map on a bump and the checksums are filled
   in from the built artifacts (done here by hand, in the same commit as the
   feed). Verified end to end afterwards: the app's own `download_update()`
   fetched the published DMG from the live feed and the published SHA-256
   matched.
2. `tools/` is gitignored (it holds the signing key), so that tool fix is local
   only - it cannot regress the repository, but it also is not reviewed by CI.

## Checkpoint (2.1.1-beta) — Live Check list rebuilt, Undo content-addressed

Owner report: the Live Check page was "in a mass", Mail did nothing, and Undo
was not reliable.

### The list was drawn twice

Measured offscreen before the fix: `item rect = 437x32 at x=4` but
`row widget = 399x32 at x=60`. Qt lays an item widget out inside the item's
*decoration* area, so with the item's own text, icon and checkbox still set,
the delegate painted them **underneath** the row widget: doubled names, a
second checkbox, every row indented by 54px and running 18px past the item's
right edge. That is the "mass".

The list is now:

* `LiveAppsList`, a `QListWidget` whose `resizeEvent` re-applies each item's
  size hint, so a row spans the viewport however the width changes (window
  resize, resize while another settings page is showing, add, delete, fold);
* `BlankRowDelegate`, which paints nothing - the row widget is the whole
  visual, so nothing can be drawn twice;
* data-only items (marker, name, on/off in item roles) so the delegate has
  nothing to draw even by accident;
* one row widget per app: checkbox, 22px icon, name (elided past 48 chars, full
  name in the tooltip), and a ✕ delete button with a hover state.

Verified numerically rather than by eye: every row widget's geometry now equals
its item rect (437x34 at x=0) in all five width-change scenarios above, and a
regression test asserts it. The header is one line - "Apps that trigger it" ⓘ
with the Show/Hide control beside it - instead of a label with a large button
on its own row.

### Undo trusted an offset

`capture.log` showed only `LIVE UNDO: the applied text is no longer where it
was written` - the recorded offset had drifted (apps reflow and normalise what
they insert), the fallback offset was then stale, and the undo either refused
or restored the wrong place.

Each apply now also records the text *around* the edit (`UndoStep.needle`, 32
characters each side, with the offset inside it). Undo searches for that block
first - it moves with the edit wherever the app put it - and only then falls
back to a nearby offset (64 characters), and otherwise refuses honestly instead
of guessing. Every attempt and outcome is logged; the pill is offered for 30s
instead of 10s. Covered by tests: a document that moves 500 characters between
the apply and the undo still restores exactly, and text that is simply gone is
left alone.

### The owner's log was polluted by the test suite

The tests exercise the live service, which writes diagnostics to `capture.log`
in the real support folder - the same file the owner reads to report a problem.
`conftest.py` now points every support-directory lookup at a temporary folder;
a full run adds no lines to the real log (verified by line count).
