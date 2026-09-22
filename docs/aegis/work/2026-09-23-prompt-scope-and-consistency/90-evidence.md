# Proofreading prompt scope and consistency — Evidence

## Before the change (same provider, same paragraphs)

| Probe | General Editing | Academic Journal (Top-Tier) |
|---|---|---|
| "These findings are **important** because…" | kept, 3/3 runs | rewritten to "findings **matter**", 2/3 runs |
| "results demonstrate… causes… proves…" | softened lightly (0.94 similarity) | softened plus an invented caveat (0.80 similarity) |
| ", indicating that" | rewritten 3/3 | rewritten 3/3 |
| markers, numbers, citations, terms | preserved | preserved |

## After the change

- `./venv/bin/python tests/prompt_contract_live.py` → `PROMPT_CONTRACT_OK`
  ("important" kept 3/3, ", indicating that" kept 3/3, no invented caveat, the
  causal verb kept, throat-clearing removed, the em dash converted to a comma
  pair, `2,341` intact).
- `./venv/bin/python tests/corpus_live.py` → `CORPUS_RESULT failures=0`.
- Live preview on the overclaiming paragraph: **0 edits in 3/3 runs for both
  strict and polish** (before: "causes" → "is associated with", "proves" →
  "suggests", "should adopt" → "may benefit from").
- Live preview on a deliberately faulty sentence: strict produced 5 edits,
  polish 3, all correct spelling/grammar fixes.
- Polish path (`load_polish_prompt` for the journal context) now carries the
  context line, keeps "important", and keeps `0.042`, `3.18`, `4.2%`.

## Automated tests

- `./scripts/run_tests.sh` → `test_hardening.py 161 passed`,
  `test_live_preview.py 137 passed`, `test_smoke.py 108 passed`,
  "All test files passed."
- New tests: the editing contract is stated once; the context block sits above
  the scope rules and cannot widen them; polish and live preview both carry the
  context; every prompt file protects claims and measurements; the temperature
  control's copy names the Creative floor.
- New manual harness: `tests/prompt_contract_live.py` (runs only when a
  provider is configured, like `tests/corpus_live.py`).

## Shipped build

- `tools/bump_version.py 2.2.1-beta.7` (pre-release: the public feed keeps
  advertising the last release), `./build_macos.sh arm64` → signed, notarised,
  stapled.
- `scripts/embed_prompts.py` runs inside the build, and a test asserts the
  embedded `PROMPT_FILES` match `prompt/*.txt` exactly, so the packaged app
  carries the text verified here.
- Installed to `/Applications`; bundle version and prompt payload checked after
  install.
