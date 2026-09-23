# Menu bar click and the live preview race — Task Intent

## Requested outcome

The owner reported that the proofreading capsule no longer appears, and that
clicking the app does not bring the main window back (only the hotkey does).
They asked for the log to be checked for bugs or conflicts as well.

## Goal

A click on the menu bar icon shows the window again, and the manual proofread
and the live preview stop fighting over the same selection.

## Success evidence

- Both clicks verified on the installed build: left click raises the window,
  right click opens the menu.
- The log's preview-versus-manual race is gone in the code path and covered by
  a test.
- `./scripts/run_tests.sh` passes; the beta is installed.

## Stop condition

- `done`: suite passes, beta installed, the owner knows what to watch for.

## Non-goals

- Changing when the live preview may run at all (the pointer rule stays).
- Shipping this as a release: these changes go out as a beta first.
