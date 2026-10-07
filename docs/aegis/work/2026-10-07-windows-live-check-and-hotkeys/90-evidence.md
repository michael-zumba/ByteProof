# Windows Live Check and global hotkeys — Evidence

Date: 2026-10-07 (Pacific/Auckland). Host: macOS (the Windows reader was
exercised through fakes; the real build comes from the Windows CI job).

## Root-cause evidence

- Released 2.3.0 had `win_str = hk_str.replace("<cmd>", "<ctrl>")`
  (`git show 043dbb1:src/hotkeys.py`), so the default
  `<cmd>+<shift>+<return>` reached pynput as `<ctrl>+<shift>+<return>`.
- pynput 1.8.2 `HotKey.parse` resolves `<name>` via `Key[name]`; the key is
  `Key.enter`, and `Key['return']` raises `ValueError("invalid key")`.
  `GlobalHotKeys.__init__` parses the whole mapping before starting, so one
  bad token aborts every hotkey.
- A local simulation of the old path produced:
  `old behaviour raises: invalid key: <ctrl>+<shift>+<return> -> start()
  returned False, every hotkey dead`.
- Released 2.3.0 had `if platform.system() == "Darwin" and not offscreen:`
  around the `LivePreviewService` creation, and the settings page disabled
  the Live Check controls for every non-Darwin platform.

## Regression tests

Added (tests/test_hardening.py):

- `test_windows_hotkeys_spell_special_keys_the_pynput_way` — `<return>` →
  `<enter>`, `<cmd>` → `<ctrl>`, Qt names (PgUp, Space, Insert, F5, F24)
  map correctly, Backtab and unknown names are dropped.
- `test_windows_hotkey_manager_keeps_the_good_bindings` — with a fake pynput
  that rejects `<return>` exactly like the real parser, the mapping still
  registers the other shortcuts; nothing registrable sets `last_error`.
- `test_windows_canonical_hotkey_equates_cmd_and_ctrl` — conflict detection
  treats a shipped `<cmd>` default and a recorded `<ctrl>` as one shortcut
  on Windows.
- `test_windows_word_selection_reaches_the_live_preview` — a Windows Word
  target (title/exe, no bundle id, empty AX details) is read through the
  Word integration and reaches `_spawn_preview` with the selected text.
- `test_live_check_is_offered_on_windows_for_word` — the platform gate is
  on for Windows/Darwin, off elsewhere, and the window uses it.

Added (tests/test_smoke.py):

- `test_windows_active_document_name` — the Windows integration reports the
  active document and degrades to "" when no document is open.

Red-green check: with the old one-line translation restored temporarily,
`test_windows_hotkey_manager_keeps_the_good_bindings` failed
(`assert {'<ctrl>+<shift>+;'} == {'<ctrl>+<shift>+<enter>', ...}`);
after restoring `windows_hotkey_string()` it passes.

## Gate results (fresh, this checkout)

```
ruff check src/ tests/            -> All checks passed!
pytest tests/test_hardening.py    -> 214 passed
pytest tests/test_smoke.py        -> 111 passed
pytest tests/test_live_preview.py -> 155 passed
python3 scripts/check_version.py  -> version markers agree: 2.3.1-beta.7
```

Combined run (`hardening + smoke + live_preview + license_cycle`) reports
`481 passed, 1 failed`: `test_card_pop_in_animation_completes` fails only in
combined runs because the offscreen Qt platform does not support window
opacity. The same failure reproduces at `HEAD` in a scratch worktree (199
passed, 1 failed with the same assertion), so it is pre-existing and
load/order-dependent, not part of this change. The test passes in isolation.

## Not verified here

- A real Windows desktop: the COM read, pynput hook and panel behaviour are
  covered by unit tests and the existing Windows integration, but the owner
  must confirm on hardware.
- Non-Word Windows apps: Live Check has no reader there yet; the manual
  proofread hotkey is the documented path.

## Release state

Version bumped to `2.3.1-beta.7` in `src/settings.py` and
`version_info.txt`; the public update feed is untouched (pre-release).
Nothing pushed; the Windows beta is produced by the existing CI packaging
job when the owner approves a push.
