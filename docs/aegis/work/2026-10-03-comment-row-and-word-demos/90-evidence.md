# Reviewer comments unlock, and the Word films — Evidence

## 1. The comment row, on the running app

The machine is licensed (`license.json`, provider `stripe`), so the row is
enabled; the bug was the stale state after activating in the same dialog.

```
$ osascript -e 'tell application "System Events" to tell process "ByteProof" \
    to get {name, enabled} of every menu button of group 1 of window "ByteProof Settings"'
UK/AU/NZ enabled=true
Creative (Rewrite) enabled=true
None enabled=true
Academic Journal (Top-Tier) enabled=true
```

The regression test builds the dialog in free mode, flips the tier to licensed
and calls the refresh:

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_hardening.py \
    -k comment_row_unlocks -q
1 passed
```

## 2. The comment composer, on the running app

Before the fix, against a real Word document with a paragraph selected:

```
$ venv/bin/python - <<'PY'   # src.word_integration.get_word_integration().add_comment(...)
add_comment failed: Word did not open a comment box, so the comment text was
not inserted ... Last error: review_ribbon: the command is not on screen
PY
```

The Accessibility tree showed the composer open the whole time
(`AXButton "Post comment"`, `AXButton "Cancel new comment draft"`), and the
window title was `manuscript_immersive_tech_disclosure` while AppleScript
reported the document as `manuscript_immersive_tech_disclosure.docx`.

After the fix, on the same document, at the two window widths the films use:

```
width 800: add_comment ok=True comments 0 -> 1
width 1000: add_comment ok=True comments 0 -> 1
```

And inside a take:

```
· revisions=18 comments=1  (34.1s)     # 04-language-comment rehearsal
· revisions=26 comments=1  (49.8s)     # 05-technical-comment take
```

## 3. The films

```
$ ls demos/word/clips/*.mp4
01-academic-journal.mp4   1:25   (re-recorded: voice af_heart, lighter narration)
02-preferred-spelling.mp4 1:12
03-editing-freedom.mp4    1:06   (re-recorded: voice af_heart, lighter narration)
04-language-comment.mp4   1:09
05-technical-comment.mp4  1:06

$ python3 -c "import json; [print(c['name'], c['voice']) for c in json.load(open('demos/word/manifest.json'))['clips']]"
01-academic-journal  af_heart
02-preferred-spelling bm_george
03-editing-freedom   af_heart
04-language-comment  am_michael
05-technical-comment af_heart

$ grep -c "the model" demos/word/clips/*.txt
01-academic-journal.txt:0
02-preferred-spelling.txt:0
03-editing-freedom.txt:0
04-language-comment.txt:0
05-technical-comment.txt:0

$ ffmpeg -i demos/word/clips/04-language-comment.mp4 2>&1 | grep Stream
Stream #0:0 Video: h264 ... 1600x900, 25 fps
Stream #0:1 Audio: aac ... 44100 Hz, mono
```

Every clip has a poster (`.jpg`), subtitles (`.srt`), a read-along transcript
(`.txt`) and an audit sheet (`-audit.jpg`). The audit sheets were read: the
settings row is ringed on the row that changes, the selection is on page two
before the line that describes it, and the tracked changes and comment cards
are in the picture when the narration says they are.

The picture's clock was checked against the narration by taking frames at the
times the transcript gives, with output seeking (input seeking lands on the
nearest keyframe and can be several seconds early, which is what made the first
pass look misaligned):

```
$ ffmpeg -i demos/word/clips/01-academic-journal.mp4 -ss 26.5 -frames:v 1 frame.jpg
   -> page two, the 10-K paragraph selected, caption "One paragraph of page two"
$ ffmpeg -i demos/word/clips/04-language-comment.mp4 -ss 53.6 -frames:v 1 frame.jpg
   -> tracked changes in the text, the Language comment card in the margin
$ ffmpeg -i demos/word/clips/05-technical-comment.mp4 -ss 49.5 -frames:v 1 frame.jpg
   -> tracked changes in the text, caption "A reviewer, reading along"
```

The recorder reports the measured pre-roll for each take, e.g.
`the recording began 61.1s of picture before the title card (wall clock 56.2s)`.

## 4. Suite and build

```
$ ./scripts/run_tests.sh
=== test_hardening.py   200 passed
=== test_license_cycle.py 2 passed
=== test_live_preview.py 155 passed
=== test_smoke.py       110 passed
All test files passed.
```

```
$ venv/bin/python scripts/check_version.py
version markers agree: 2.3.1-beta.4 (tuple (2, 3, 1, 4))
$ ./build_macos.sh
notarytool: status: Accepted
The staple and validate action worked!
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg
```

Three betas were built along the way: beta.2 carried the comment and settings
fixes and recorded the first films; beta.3 was the same code after a lint pass;
beta.4 adds the busy-Word retry and is the build the films were finally
recorded on. Installed to `/Applications` (beta.1 to beta.3 moved to
`previous-versions/`); the installed bundle reports `2.3.1-beta.4`.

## 5. The manuscript was not touched

```
$ ls -la "testing document/"
-rw-r--r--@ 1 zhangy6j staff 839203 Jul 17 10:34 manuscript_immersive_tech_disclosure.docx
```

Size and mtime are the ones from before the session (17 July). Every take
opened a copy under the take folder, and Word's documents were closed without
saving. Word was left with the original open, Track Changes off, saved, and no
revisions.
