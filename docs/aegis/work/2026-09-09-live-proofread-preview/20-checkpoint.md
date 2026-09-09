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
- [ ] UI/UX Step 2: onboarding & trust readiness card
- [ ] UI/UX Step 3: shared design tokens + diff-view reuse
- [ ] UI/UX Step 4: reason color dots, status chip, quit flow
- [ ] Integrate branch (user decision: merge / PR / keep)

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
