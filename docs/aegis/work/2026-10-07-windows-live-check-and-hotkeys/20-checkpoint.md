# Windows Live Check and global hotkeys — Checkpoint

## 1. Windows shortcuts work again

`src/hotkeys.py` gained `windows_hotkey_string()`, which translates a stored
shortcut into the spelling pynput parses on Windows:

- `<cmd>` (and `<win>`/`<super>`) become `<ctrl>`; Windows has no Command key.
- `<return>`/`<enter>` become `<enter>`, `<esc>` stays, and the Qt names a
  user can record in Settings (Space, PgUp/PgDn, Insert, arrows, F1–F24…)
  are mapped to their pynput names. Backtab is deliberately dropped rather
  than renamed to Tab, which would misfire on a bare Tab press.

`_WindowsHotkeyManager.start()` now validates each binding separately and
skips only the unreadable ones, so one bad shortcut can never take the rest
down. It records `last_error` when nothing could be registered, and
`HotkeyManager.last_error` exposes that to the UI. `ProofreaderApp` no longer
prints "Ready" when the manager failed to start without a permission cause;
it says "Hotkeys unavailable — see ByteProof's debug_hotkeys.log."
`canonical_hotkey()` also
treats `<cmd>` and `<ctrl>` as the same shortcut on Windows, so the shipped
defaults and re-recorded bindings conflict-check correctly.

## 2. Live Check runs on Windows, in Word

`ProofreaderApp` now creates `LivePreviewService` on Windows as well
(offscreen test runs still skip it), and the helper
`live_check_platform_supported()` is the single gate for that decision and
for the settings page.

The Windows poll reads the selection through the existing
`WindowsWordIntegration` COM path: `get_selection_info()` for text and
context, `selection_scope()` for body/comment/table handling, and
`apply_live_edit()` for per-suggestion writes with its Track Changes
suspension and before-text guard. `GenericTextEditor.is_word()` now also
recognises a Windows title ending in " - Word", and
`WindowsWordIntegration.active_document_name()` was added so a live edit can
refuse when the user switched documents (the same guard macOS has always
had).

The Live Check settings page is enabled on Windows, with a note that Live
Check watches Microsoft Word there and other apps use the proofread hotkey.
The app list on Windows shows Word instead of the macOS-only rows.

## 3. Left for a later change

Live Check in non-Word Windows apps (browsers, email, chat) would need a new
reader — UIA TextPattern or the throttled clipboard-copy path — plus its own
safety work. Until then the manual proofread hotkey covers those apps, and
the settings page says so.

## Files

- `src/hotkeys.py` — spelling table, per-binding validation, `last_error`.
- `src/gui.py` — platform gate helper, live service on Windows, settings
  note and app list, honest hotkey status.
- `src/generic_editing.py` — Word detection from the window title.
- `src/word_integration.py` — Windows `active_document_name()`.
- `tests/test_hardening.py`, `tests/test_smoke.py` — regression tests.
- `src/settings.py`, `version_info.txt` — `2.3.1-beta.7`.
