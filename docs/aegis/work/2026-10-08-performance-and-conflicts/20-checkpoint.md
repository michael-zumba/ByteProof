# App slowdown and internal conflicts — Checkpoint

## What was actually slow

Everything below was measured on the owner's machine (2026-10-08); the numbers
are in the evidence file.

1. **The Word poll made Word and ByteProof stutter.** One Word selection read
   costs 145 ms at its cheapest and 229 ms in the shape the poll used (the two
   250-character context ranges are a third of it), and the poll ran every
   800 ms **on the UI thread** - while Word was doing the same work on *its*
   main thread. Typing in Word was the worst case: exactly when the user feels
   a stutter, and exactly when there is nothing to preview (Word reports a
   caret, not a selection).
2. **A busy Word could freeze the app for seconds.** The poll's read retried
   three times, each with a 1.5-second timeout plus a sleep, and the retries
   swallowed the error the poll's own back-off was waiting for - so the
   designed "back off from a busy Word" never ran.
3. **A Word with no document open was polled four times a second, forever.**
   Each tick spawned an `osascript` and wrote a log line ("Error getting text
   with context", 328 of them in the retained log window); the 1 MB log cap
   then threw away the history the owner needs for support.
4. **A cancelled preview silenced Live Check.** Double-Escape sets the
   *shared* cancel event for the running worker; nothing cleared it, and
   `preview_edits_once` raises the moment it sees a set event - so every later
   preview aborted at birth. No card, no error: Live Check simply stopped
   doing anything until the app was restarted. (Found by reading the cancel
   path while auditing; the new test fails against the old code.)
5. **The Accessibility element cache re-walked the tree every 2.5 seconds.**
   In a heavy app (an Electron window) that walk costs ~100-190 ms, blocking
   the UI thread, for every app, all day.
6. **Settings re-scanned every installed app and loaded sixteen icons every
   time it opened** (~309 ms), even though neither changes while the app runs.
7. **Two copies of the app could run at once.** LaunchServices stops a second
   start of the *same* bundle, but a copy elsewhere - and the app itself keeps
   `previous-versions/ByteProof_*.app` around after updates - would be a
   second watcher that could paste over the same selection.

## What changed

* Word's poll reads the **selection span** (187 ms) and fetches the
  surrounding text **once per new selection**, remembered so the preview cache
  key stays stable. The poll's read is **fail-fast**: one attempt, then the
  existing 5-second back-off (and one log line per 30 s) instead of up to
  ~5.6 s of retries and sleeps on the UI thread.
* The Word poll **stands down while the user is typing** (a plain key press
  within the last 0.9 s, no panel visible, nothing captured). A captured
  selection or a visible panel keeps the read running, so the panel still
  disappears the moment the user types over it. A chord (Shift/Cmd) is not
  "typing" - keyboard selection still reads.
* New previews start with a **fresh cancel state**, so a cancelled preview can
  no longer silence later ones.
* The AX element cache is **validated by the focus chain** (is the cached
  element still the focused one or inside it?) instead of expiring on a timer:
  ~1-3 ms per tick, and a full re-walk only when focus really moved or the
  element stopped answering.
* The installed-app list and app icons are **cached** (list 5 minutes, icons
  for the session), so Settings opens in ~70 ms after the first time; the
  "Choose App…" picker still rescans because that is a deliberate ask.
* A **second bundled copy refuses to run** and brings the running one forward.

## Checked and found healthy (no change needed)

* Idle cost: ~1-1.6 % CPU, no measurable memory growth over the session
  (145 MB resident), and the log does not grow while nothing happens.
* Widgets and timers are reused, not recreated: one suggestion panel, one undo
  pill, one timer each for detach/clean/undo; worker threads are `deleteLater`d.
* Queues are bounded: preview cache 64, AX caches per pid, undo stack capped,
  app history capped, suppressed selections pruned by age.
* The three global event monitors (hotkeys, input watcher, Escape bridge) are
  observe-only and cheap; the local hotkey monitor returns the event untouched.
* The manual proofread's hold on the live preview is released on every path
  (success, error, cancel).

## Known, deliberately not fixed here

* The poll is still a poll. An Accessibility *observer* (AXObserver
  notifications) would remove the Word read entirely, but that is a
  project-sized change and Word's AX tree does not report the selection.
* Copy-keystroke reads for Pages/Mail remain (that is how those apps expose a
  selection at all).
