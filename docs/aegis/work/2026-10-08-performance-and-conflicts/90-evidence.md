# App slowdown and internal conflicts — Evidence

All measurements on the owner's machine, 2026-10-08, against the running app
and a real Microsoft Word document (`/tmp/byteproof-perf-test.docx`).

## 1. What one tick costs, per app

```
$ venv/bin/python /tmp/byteproof_tick_cost.py
=== ByteProof       selection_details() cold 1.0 ms   warm 1.5 ms
=== Safari          selection_details() cold 3.5 ms   warm 1.6 ms
=== Microsoft Word  get_selection_info() 214-229 ms   selection_scope() 196 ms
```

The Word scripts, timed separately (`/tmp/byteproof_word_scripts.py`):

```
minimal (selection text only):        145 ms
no context (text + start/end):        162 ms
full (what the poll read):            228 ms
```

After the change, through the real integration:

```
new span read (per tick):             187 ms
old full read (per tick, before):     229 ms
context read (once per new selection): 164 ms
```

and while the user is typing the poll now reads **not at all** (see the
typing-stand-down test).

## 2. The app's own CPU, before/after the poll changes

```
$ ps -o %cpu= -p <ByteProof pid>      # sampled every 2 s, idle machine
0.8 0.9 0.8 1.0 1.1 0.9               # frontmost = ByteProof, before
1.6 1.6 1.8 1.7 1.8 1.6               # frontmost = ChatGPT, after
$ wc -l capture.log                   # 10 seconds of idle
0 lines added
```

The higher figure is the cost of reading a *larger* accessibility tree
(ChatGPT/Electron) rather than ByteProof's own; the per-tick cost for that app
is 2 ms median, and the periodic spikes are what item 4 below removed.

## 3. Where the tick's spikes came from

```
$ # 40 ticks against the frontmost Electron app, 250 ms apart
$ (old cache) tick ms: min 1.03  median 2.01  max 192.33
$ (focus-validated cache) slow ticks (>10 ms): [(0, 104.5, cache-miss)]
```

One cold discovery when the app is first looked at, and nothing periodic after
that. Before, the 2.5-second cache expiry made the walk happen all day - in a
heavy app that is a ~190 ms stall every few seconds.

## 4. The Word storm when no document is open

```
$ grep -c "Error getting text with context" capture.log
328
$ grep -c "Selection read failed while Word was busy" capture.log
7
```

328 log lines and as many `osascript` spawns in the retained window, one per
poll tick, because "No active Word document is open" was treated as "no
selection" and retried forever. The read now raises, the poll backs off for
5 s and logs once per 30 s (the existing `_log_word_busy_once`).

## 5. The cancelled-preview bug

```
$ venv/bin/python -m pytest tests/test_live_preview.py -k cancelled_preview
# with the fix removed:  1 failed   (the new worker inherits a set cancel flag)
# with the fix:          1 passed
```

`preview_edits_once` raises `TaskCancelledError` the moment the event is set
(`src/logic.py`, line ~654), and the event was only cleared in `start()`.

## 6. Settings dialog

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python ...   # build the dialog, timed
first open  309 ms   (Applications scan + 16 app icons)
second open  75 ms   (both cached)
```

## 7. Tests

New or updated:

```
tests/test_live_preview.py::test_the_word_poll_stands_down_while_the_user_types
tests/test_live_preview.py::test_word_context_is_read_once_per_selection
tests/test_live_preview.py::test_a_cancelled_preview_does_not_kill_the_next_one
tests/test_smoke.py::test_a_second_copy_of_the_app_refuses_to_run
```

```
$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
=== test_hardening.py       215 passed
=== test_license_cycle.py     3 passed
=== test_live_preview.py    164 passed
=== test_smoke.py           113 passed
All test files passed.
```

## 8. Beta

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.4 "Performance: ..."
$ ./build_macos.sh                     # notarised, stapled
$ ./scripts/install_local_beta.sh
Installed ByteProof 2.4.1-beta.4 (signature verified).
ByteProof is running. Its Accessibility permission was left in place.
$ /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" /Applications/ByteProof.app/Contents/Info.plist
2.4.1-beta.4
```
