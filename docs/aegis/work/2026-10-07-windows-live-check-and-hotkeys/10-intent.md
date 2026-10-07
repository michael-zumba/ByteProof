# Windows Live Check and global hotkeys — Task Intent

## Requested outcome

The owner reported two problems in the Windows build of ByteProof:

1. The live suggestion function does not work.
2. The global shortcuts do not work.

## Findings (before any fix)

Both reports reproduce in the released 2.3.0 source:

1. **No Live Check on Windows.** `ProofreaderApp` created
   `LivePreviewService` only `if platform.system() == "Darwin"`, and the
   Live Check settings page disabled its controls on Windows. The Windows
   build had no selection poll at all.
2. **Every Windows hotkey was dead.** The stored apply-all default is
   `<cmd>+<shift>+<return>`. The Windows manager only rewrote `<cmd>` to
   `<ctrl>`, so pynput's `HotKey.parse` received `<return>` — a name it does
   not have (pynput spells Return `<enter>`). `GlobalHotKeys` parses every
   entry up front, so that one token raised and `start()` returned False for
   the *whole* mapping. The GUI then consulted `has_permission()`, which is
   always True on Windows, and reported "Ready" while nothing was listening.

## Scope and authority

- Fix both defects; no release, no push (owner approval required for main).
- Windows Live Check is enabled where the selection reader already exists:
  Microsoft Word, through the same COM integration the manual flow uses.
  Other Windows apps keep the proofread hotkey, and the settings page says
  so. A cross-app Windows reader (UIA/clipboard) is left for a later change.
- The proofread, open, live-toggle and apply-all hotkeys must all work on
  Windows, using the shipped defaults without the user re-recording them.
- Ship as the next beta (`2.3.1-beta.7`) per the release policy.

## Success evidence

- A regression test that a Windows hotkey mapping containing the default
  `<return>` binding still registers the other shortcuts.
- A regression test that a Windows Word selection is read through the Word
  integration and reaches the preview worker.
- A test that the app's Live Check gate is on for Windows and off for
  platforms without a reader.
- macOS gate: `ruff` clean, full test suite green, `check_version.py` agrees.
