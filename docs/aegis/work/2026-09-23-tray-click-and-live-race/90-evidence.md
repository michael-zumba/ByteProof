# Menu bar click and the live preview race — Evidence

## The race, from the owner's log

```
[15:32:26] LIVE PREVIEW: app='Microsoft Word' chars=1419
[15:32:26] WORD:   Comment trigger 'menu_insert' sent      <- the manual proofread ran too
[15:32:28] LIVE DONE: edits=0 provider_ms=2417 provider=DeepSeek
[15:32:29] LIVE DONE SYNC FAIL: selection changed: previewed=<len=1419 sha=…> now=<len=0 …>
```

The manual task's apply had collapsed the selection under the preview, so the
card appeared and vanished and the provider call was spent for nothing.

## The click, before and after

Before (installed 2.2.1, measured with the status item at (995, 14)):

```
LEFT  click -> menu opens, no window
RIGHT click -> menu opens
```

After (2.2.2-beta.1, window closed first):

```
windows after close:      ['']
LEFT  click -> windows:   ['', 'ByteProof']   menu open: False
RIGHT click -> windows:   ['']                menu open: True
```

## The capsules, captured

- Manual proofread: the black pill read `Proofreading… 0:01` with the orange
  waveform, at the bottom centre of the 1920x1080 screen.
- Live preview: the card `Suggested changes (4)` with the first row
  `This are → is`, placed near the pointer while the preview ran.

## Automated tests

- `tests/test_hardening.py`: the tray test now asserts a plain click raises the
  window and a right click opens the menu (and that AppKit still does not own
  the status item menu on macOS); a new test drives a real
  `LivePreviewService` timer through `hold_for_manual_task()` /
  `release_after_manual_task()` from the app's own methods.
- `./scripts/run_tests.sh` → 173 + 137 + 108 passed, "All test files passed."

## Shipped

- Official release **2.2.1** published from this session: GitHub release with
  `ByteProof_Installer_AppleSilicon.dmg` (sha256 `a1414168…`),
  `ByteProof_Windows.zip` (`620b06d9…`) and `ByteProof_Installer_x64.msix`
  (`d921011f…`), and the website feed serves 2.2.1 with those checksums.
- Beta **2.2.2-beta.1** built, signed, notarized, stapled, installed to
  `/Applications` (2.2.1 moved to the Trash).
