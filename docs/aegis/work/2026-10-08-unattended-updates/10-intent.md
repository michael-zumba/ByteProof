# Unattended updates that stay unattended — Intent

## The owner's report

"Can you fix that? whenever new installation required, it will show this. My
requirement is to be able to autoinstall the updates. User do not need to
manually install." — with the dialog "ByteProof could not install the update
automatically. The installer has been opened - drag ByteProof to Applications
to finish."

## Goal

Clicking Install finishes the update by itself: ByteProof downloads, replaces
its own bundle, reopens, and reports success — no dragging, no dialog, and no
false failure after a successful install.

## Success evidence

- The mechanism is shown to work end to end *on this machine, on the real
  /Applications copy*: beta.4 replaced by beta.5 unattended, relaunched, no
  leftovers.
- The reason the owner saw the dialog is named from the logs, and the
  situation that produced it is covered by a test that fails without the fix.
- The rehearsal script (`scripts/mock_update_and_install.py`) still passes all
  of its checks, including the new "lost race" ones.
- `./scripts/run_tests.sh` passes and the beta is installed.

## Stop condition

- `done`: the fix is in an installed beta, and the owner's next update needs
  one click (Install) and nothing else.

## Non-goals

- Changing the download/verification rules (host allowlist, SHA-256).
- Removing the manual fallback: if macOS genuinely refuses the swap, the DMG
  path is still the honest last resort.
