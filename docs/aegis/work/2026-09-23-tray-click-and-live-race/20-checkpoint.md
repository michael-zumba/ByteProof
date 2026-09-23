# Menu bar click and the live preview race — Checkpoint

## What was wrong

1. **A click on the menu bar icon opened the menu, never the window.** The
   macOS 27 crash workaround stopped AppKit owning the status item menu and
   made the app open it itself, on *every* click. The documented "double click
   shows the window" could not arrive either: the first click had already put
   a menu under the pointer. The owner's `Show Window`-via-hotkey habit was
   the only route back.
2. **The manual proofread and the live preview raced over one selection.**
   The poll loop stands down for the live apply (`_poll_paused_for_apply`) but
   knew nothing about a manual proofread. The owner's log shows the result: a
   preview started while the manual task was rewriting the same text, spent a
   provider call, then died on `LIVE DONE SYNC FAIL: selection changed` and
   took its card away with it.
3. **The pill left no trace**, so a report of "I cannot see the capsule" could
   not be checked against the log.

## What changed

- **Tray click**: a plain left click raises the main window again; the menu is
  one right click (or Control-click) away. Verified by hand on the installed
  build: left click → window, menu closed; right click → menu, window hidden.
- **Live preview holds for manual tasks**: `hold_for_manual_task()` stops the
  poll loop, clears the preview bookkeeping and closes the panel;
  `release_after_manual_task()` resumes it. The manual flow calls them at the
  start of a task, when a task cannot start, and when it finishes.
- **Pill diagnostics**: every pill now logs what it shows
  (`APP: pill shown (processing|success|warning|error): …`).

## What was checked and found working

Both capsules do appear on this machine, captured during the investigation: a
manual proofread shows the black "Proofreading… 0:01" pill, and a live preview
shows the "Suggested changes (4)" card with "Checking…" while it works. The
missing capsule the owner saw is most likely the live card dying with the race
above (its card was shown, then hidden by the sync failure), which is what the
hold fixes.
