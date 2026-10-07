# Licence link, Automation layout, unattended updates, hidden bugs — Checkpoint

## 1. The purchase button pays through Stripe

`PURCHASE_URL` pointed at `www.bytemind.co.nz/byteproof#pricing`, so the
button made the buyer read the website before they could pay. It now defaults
to the live Stripe Payment Link
(`https://buy.stripe.com/fZueV61d99tv52vaOfgYU00`), the same link the licence
service issues keys from. The `BYTEPROOF_PURCHASE_URL` override still wins for
testing, and a regression test asserts the default is a Stripe URL and not the
old pricing page.

## 2. Automation is one card, not a list with floating buttons

The owner's screenshot showed a trigger list clipped mid-card with the three
actions sitting on the page background underneath it. The page now puts the
list and its actions in one bordered card: the list scrolls inside the card
(three whole rows minimum, four before scrolling), a hairline footer holds the
actions at the bottom of the same surface, and "Remove Selected" is disabled
until a trigger is selected. The list snaps to whole rows, so the viewport
never slices a card in half. The page itself gained the scroll area the other
settings pages have, which is what let the old layout overflow.

| Before | After |
|---|---|
| `automation-before.png` | `automation-after.png` |

## 3. macOS updates install themselves

The old flow replaced the bundle from inside the running app, launched a
second copy with `open -n`, and quit. When that failed - macOS refusing the
swap while the app is open, a permission problem, a leftover DMG window - it
fell back to a "Download Complete" dialog and made the owner drag the app to
Applications, where macOS then said ByteProof had to be closed first.

The new flow hands the swap to a detached helper:

- `stage_macos_payload()` attaches the DMG and copies the new bundle next to
  the installed one (`ByteProof.app.update-staging`) **while the app is still
  running**, then clears the quarantine flag on the copy.
- Only once that copy exists does the app stage the helper
  (`macos_update_script` / `stage_macos_update`), show "ByteProof will reopen
  automatically", and quit itself after 900 ms (`_stage_macos_update`,
  `_quit_for_update`).
- The helper waits for the process to exit (up to 60 s), moves the old bundle
  to `ByteProof.app.update-backup`, renames the staged bundle into place,
  removes the backup and the DMG, and reopens the app. If the rename fails it
  retries with administrator privileges (the only path macOS may ask for a
  password); if that fails it restores the old bundle, writes
  `update-result.json`, shows a native alert, and opens the DMG for a manual
  install.
- On the next launch the app reads the result once
  (`take_update_result`, `_check_update_result_marker`): "ok" is a toast,
  "failed" is a plain explanation.
- A bundle running from a mounted DMG or App Translocation is not treated as
  the install; the helper targets `/Applications/ByteProof.app`
  (`installed_app_bundle_path`).

The regression test builds a stub app, runs the real helper against it with a
finished PID, and asserts both directions: the good update replaces the bundle
and reports `ok`, the broken update leaves the old bundle in place and reports
`failed`.

A full mock run lives in `scripts/mock_update_and_install.py`. It builds a
throwaway DMG whose `ByteProof.app` is a stub that writes a "relaunched"
marker, serves it over localhost, and drives the real code end to end:
download with SHA-256 verification, staging, the wait-for-exit helper, the
swap, the quarantine clear, the relaunch, and the result marker - then the
three failure branches (bad checksum, missing payload, vanished staging).
The first full run passed 16/16 checks, including a two-second stand-in
process that proves the swap waits for the app to exit.

`scripts/mock_ui_update.py` covers the glue the owner actually clicks: a real
`ProofreaderApp` calls `_handle_download_finished()` with a mock DMG, stages
it, quits itself, and the detached helper swaps the bundle and relaunches it.
The destination and support folder are redirected into a temp workspace, and
the script asserts the real `/Applications` copy is untouched. It passed 7/7.

A process check after the beta.6 install then caught a separate problem: the
install had moved the *running* beta.5 bundle to `previous-versions/` because
the quit Apple Event was not processed, so `open` re-activated that process
instead of starting beta.6. The install procedure now verifies the process is
gone (and uses SIGTERM/SIGKILL if not), then confirms the new process's
`lsof` executable path is `/Applications/ByteProof.app/...`. The app's own
update path is unaffected (it calls `QApplication.quit()` itself, and the
helper waits for the pid to disappear).

The first cut of this helper copied the bundle *after* the app had quit, and
the owner's screenshot showed what that looks like when macOS refuses the
copy: "ByteProof could not install the update automatically". The redesign
moves the copy to the moment the app is still on screen, so a refusal falls
back to the manual installer without quitting, and the headless step is only
two atomic renames. A rehearsal against `/Applications` with the real
notarized DMG (attach → copy → rename → quarantine clear → result marker)
passed, with no staging or backup left behind.

## 4. The suggestion panel wears ByteProof's palette

The panel kept its structure, its one-filled-primary hierarchy, and the
meaningful red/green diff colours, but its chrome moved off Google blue and
cool greys onto the app's own tokens: cream surface, hairline borders, deep
green "Apply all", soft green tonal per-row "Apply", warm grey chips, muted
steel-blue for style/clarity dots. The single-edit hover popup, the
"Checking…" state, the "No changes needed" state, and the Undo pill use the
same tokens, so the panel no longer looks like a different product.

| Before | After |
|---|---|
| `panel-before.png` | `panel-after.png` |

## 5. Hidden bugs found and fixed

- **A wrong-shaped settings file could stop the app from starting.**
  `load_runtime_settings()` handled invalid JSON but not valid JSON of the
  wrong shape: `{"general": {"temperature": "hot"}}` raised at `max()`,
  `"providers": {"DeepSeek": 5}` raised on `.items()` values, and a
  non-string `active_provider` raised as an unhashable key. Every sub-document
  is now read through `_as_mapping()`, string fields are type-checked, and the
  temperature is coerced with a fallback. A regression test loads a
  deliberately mangled file and asserts the defaults survive.
- **The Windows "open installer" fallback used an invalid file URI.**
  `"file://" + "C:\\Users\\...\\ByteProof_Windows.zip"` is not a URI; the
  fallback now uses `Path(download_path).resolve().as_uri()`.
- **The Automation show/hide flag could go stale.** Rebuilding the page
  (Restore Defaults) left the "triggers shown" flag pointing at deleted
  widgets, so the first toggle did nothing. The flag is reset when the page is
  built.

The wider pass also covered the helpers the owner uses daily: the licence
client, activation, autostart, hotkey lifecycle, cache cleanup, and the model
downloader. No further defects were found there; the extended ruff sets
(`F`, `B`, `SIM`, `C4`, `PERF`, `RUF`) reported only style-level suggestions.

## Deliberate limits

- The panel redesign is my recommendation, not a signed-off design; it ships
  in the beta so the owner can react. The diff red/green and all strings,
  signals, and layout order are unchanged.
- The macOS update flow is proven against a stub bundle and shell-level roll
  back; the first real DMG update will be the live proof, which needs a newer
  version than the feed currently offers.
- Windows keeps its existing open-the-archive flow; only its URI spelling was
  fixed.
- No public release, no version feed, no push.
