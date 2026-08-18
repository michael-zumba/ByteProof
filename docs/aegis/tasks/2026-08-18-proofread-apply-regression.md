# TaskStartSnapshot — 2026-08-18 Proofread/apply regression (EndNote citations)

## Task

Review the ByteProof proofread/apply path and fix hidden bugs where conflicting
code paths cause severe performance drag ("takes ages") and misplaced edits
("loses its position") when the selected text contains EndNote-embedded
citations. Speed and accuracy are non-negotiable; edits must remain
character-level tracked changes.

## Baseline (current state)

- Repo: ByteProof, branch `main`, clean worktree at commit `f3676b9`.
- App version installed by user: 1.6.11 (settings confirm `app_version: 1.6.11`,
  provider DeepSeek, comment type None, auto_apply on).
- 1.6.10 → 1.6.11 contains exactly one functional commit (`17b1144`):
  - Removed the `abs(current_start - start_offset) > 10` abort guard.
  - Added unconditional `start_offset = current_start` re-anchor in
    `apply_corrections_with_diff`.
  - Added APPLY_REVIEW status + manual Apply button.
- Prior analysis (previous turn) identified the conflicting code:
  - Apply re-anchors to Word's current selection but reuses proofread-time
    absolute citation field spans → stale mapping, misplaced character-level
    edits.
  - `missing_hidden_chars != 0` triggers a per-character AppleScript scan of the
    whole selection (`get_selection_hidden_spans`, one range read per char) even
    when the mismatch is negative or caused by field re-layout → minutes of
    hanging on macOS.
  - Manual Apply (`_apply_pending_word`) drops `field_info`, so citation fields
    are neither protected nor offset-compensated.
  - Field hidden totals are summed per field without merging, so nested /
    overlapping EndNote fields over-count hidden chars (false scan triggers).
  - Proofread and apply each re-implement the scan-trigger logic (can drift).

## Change Necessity

- User-visible need: proofreading selections containing EndNote citations must
  finish quickly and apply tracked changes at the correct positions, including
  when Word's selection shifts during AI processing.
- No-change / non-code option: reverting to 1.6.10 restores the fast-fail
  behavior but silently drops edits when the selection moves (the bug 1.6.11
  was meant to fix); not acceptable.
- Why code change is necessary: the conflicting mapping paths live in source;
  only code can make apply re-map citations against the current selection and
  gate the expensive scan on real evidence.
- Minimum change boundary:
  1. Apply re-locates citation fields against Word's current selection whenever
     the selection moved (or field info was not supplied), else reuses
     proofread mapping.
  2. Hidden-scan gate is strict (`missing > 0`), shared by proofread and apply,
     with merged field hidden totals.
  3. Manual Apply path self-locates fields and protects citation spans.
  4. Best-effort selection restore after applying edits.
- Decision: code-change

## PatchShape / Owner triage

- Canonical owner: `apply_corrections_with_diff` + citation mapping contract
  between `src/logic.py` and `src/word_integration.py`.
- UpwardDrillSignal: none — symptom reproduces from the 1.6.11 commit diff and
  the code paths are the direct producers of both the scan cost and the stale
  mapping.
- RippleSignal: apply path is shared by proofread (auto_apply) and manual
  Apply; both consumers verified in tests. No fallback/legacy path is added;
  the existing APPLY_REVIEW safety is retained.
- Decision: fix owner

## Verification plan

- Unit tests: drifted-selection-with-fields remap; scan gating (0/negative
  missing → no scan; positive → bounded scan); self-located fields on manual
  apply protect citations; merged hidden totals for overlapping fields.
- Existing suite: `venv/bin/python -m pytest tests/test_smoke.py -q` (PyQt6
  available in `venv`).
- Confidence target: A (direct regression tests) for the mapping and scan-gate
  fixes; B for selection restore (best-effort, no live Word available).

## Verification result (2026-08-18)

- Implemented in `src/logic.py`:
  - `_locate_citation_spans()` — shared citation locator (by-text fast path,
    document-position fallback with bounded scan); used by proofread and apply.
  - `_compute_field_hidden_total()` — merges overlapping/nested field hidden
    blocks before summing (no more false scan triggers from nested EndNote
    fields).
  - `_scan_tracked_deletions()` — strict gate (`missing > 0`), clamped bound,
    merged exclusion spans, cancellable; shared by proofread and apply.
  - `apply_corrections_with_diff()` — re-locates citation fields against
    Word's current selection whenever the selection moved; never mixes a new
    anchor with stale absolute field positions; manual Apply self-locates and
    protects citations; best-effort selection restore after edits.
  - `_run_with_cancel()` typing widened (pre-existing pyright mismatch).
- Implemented in `src/word_integration.py`: `set_selection_range()` on base,
  Windows (`Selection.SetRange`), and macOS (`select myRange`).
- Tests added to `tests/test_smoke.py` (all pass):
  `test_apply_corrections_relocates_fields_when_selection_moves`,
  `test_apply_corrections_skips_hidden_scan_without_evidence`,
  `test_apply_corrections_self_locates_fields_for_manual_apply`,
  `test_compute_field_hidden_total_merges_overlapping_fields`,
  `test_scan_tracked_deletions_bounds_and_merges`.
- `venv/bin/python tests/test_smoke.py` → `ALL_SMOKE_TESTS_PASSED`.
- `venv/bin/pyright src/logic.py src/word_integration.py` → 0 errors.
- Not changed (deliberate): 1.6.4 mandatory internal Language review (feeds
  proofread quality; latency is product-level and reported to the user).

## Follow-up: nested-field offset bug (live Word reproduction)

Live two-paragraph EndNote reproduction captured in `/tmp/byteproof-dev-run.log`:
selection doc range 238756-253219 (14463 chars), visible 2968 chars, 11 fields
enumerated. The field hidden total was 24 chars too high, which shifted every
edit after the citation into the wrong paragraph/spot.

Root cause: Word enumerated a nested inner field inside an outer EndNote field.
The raw values showed field 2 `code=(240984,241023)` and field 3
`code=(241000,241021), result=(-1,241022)` — field 3's code lies entirely inside
field 2's code. The outer field's code range already accounts for the nested
field's hidden characters, so counting both double-counted 24 hidden chars.

Fix: `_drop_nested_fields()` in `src/word_integration.py` keeps only outermost
fields; macOS field parsing and Windows field enumeration both use it. Dropping
the nested field makes the hidden total exact (11495) and `missing_hidden_chars`
equal zero.

Regression test: `test_macos_field_spans_drop_nested_fields`. Full suite still
`ALL_SMOKE_TESTS_PASSED`; pyright on `src/` 0 errors.
