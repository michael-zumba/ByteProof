# Live Proofread Preview — Task Intent

## Requested outcome

Ship a beta of a Grammarly-style live proofread preview in ByteProof: when the
user selects text in a supported word-processing or email app, show pink-red
dashed underlines under words the AI would change, a hover popup with
track-changes-style suggested edits, and one-click apply — with an on/off
switch in the General settings and strict token efficiency.

## Goal

Selecting text in Word, Pages, Mail, or Outlook automatically shows live
underline previews (no proofread click needed); hovering shows a
track-changes-style popup; clicking applies the change; the General-settings
toggle turns the feature off; repeated tests prove function, efficiency, and
accuracy.

## Success evidence

- Feature builds and runs on macOS from source and from a packaged `.app`.
- Unit + integration tests green, including: edit parsing, range mapping,
  trigger decisions, cache hits, settings persistence, and apply plans.
- Live end-to-end runs in at least two real apps (TextEdit/Mail/Pages/Word)
  prove underlines, hover popup, and click-to-apply behave as specified.
- Efficiency measurements show: no provider call on unchanged selection, one
  debounced call per selection change, cache hits produce zero calls, and
  output is capped at the configured preview token limit.
- Accuracy: a fixed corpus of defective sentences is corrected as expected
  across repeated runs.

## Stop condition

- `done`: all evidence above is true and the full test suite passes.
- `blocked`: a required dependency (for example a Word API limitation that
  cannot be worked around) is missing after the blocked-audit threshold.
- `needs-verification`: implementation exists but evidence is insufficient.
- `scope-exceeded`: continuing would leave the beta scope or non-goals.

## Non-goals

- No Windows live-preview support in this beta (macOS only).
- No changes to the existing Word tracked-changes proofread or hotkey flow.
- No cloud token spend by default: previews default to the Local AI model.

## Constraints

- Python 3.13 and existing dependencies only; no new runtime packages.
- Prompt files in `prompt/` are canonical; run `python scripts/embed_prompts.py`
  after editing them.
- Follow the existing defensive exception-handling and GUI style.
- Branch: `codex/live-proofread-preview`.

## Baseline refs (acknowledged)

- `src/logic.py` — `polish_selection_once`, `proofread_with_provider`,
  `resolve_provider_connection`, `load_polish_prompt`, `get_access_status`.
- `src/generic_editing.py` — AX selection read/apply and clipboard fallbacks.
- `src/gui.py` — `SettingsDialog` General page, worker pattern, app tracking.
- `src/settings.py` — settings schema and persistence.
- `src/word_integration.py` — AppleScript Word integration.
- `src/hotkeys.py`, `src/local_model.py`, `scripts/embed_prompts.py`,
  `tests/test_smoke.py`.

## Todo map

1. Spec + plan artifacts (this directory + `docs/superpowers/`).
2. `src/live_preview.py` pure core (parse, map, cache, decision) with TDD.
3. Preview prompt asset + provider call in `logic.py` with TDD.
4. AX geometry + range replacement helpers in `generic_editing.py`.
5. `src/live_overlay.py` underline overlay + hover popup.
6. `src/live_service.py` orchestrator (monitor, debounce, cache, apply).
7. Word native underlines + apply + position estimation.
8. Settings schema + General-page controls.
9. Integration + E2E harness and efficiency assertions.
10. Live test campaign (real apps, repeated) + packaged build verification.

## Risk hints

- Word on macOS may not expose character bounds via AX; Word uses native
  in-document formatting and estimated popup anchoring instead.
- Local model latency affects preview responsiveness; mitigated by debounce,
  async worker, and caching.
- Scrolling/moving the host window invalidates overlay coordinates; mitigated
  by a bounded refresh timer.

