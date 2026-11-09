# Writing in a Word comment box, and Live Check in the comment — Evidence

## 1. The failure, reproduced on this machine

Scratch document `byteproof-comment-test.docx` (two paragraphs, made for this
test), Word frontmost, a draft comment box opened through Word's own New
Comment button and focused, then words typed with System Events - the same
thing a reader does.

With **ByteProof stopped** (control), every word landed and the box stayed
focused:

```
after word 1 (alpha):   {'value': 'alpha ', 'focused': 'True'}
after word 2 (bravo):   {'value': 'alpha bravo ', 'focused': 'True'}
...
after word 6 (foxtrot): {'value': 'alpha bravo charlie delta echo foxtrot ',
                         'focused': 'True'}
```

With the shipped 2.4.0 running (its live poll active), the first word closed
the box and the text vanished:

```
[14:21:57] comment box open: {'value': '', 'focused': 'True'}
[14:22:00] after word 1 (alpha): None
           composer has closed
```

The document was unchanged afterwards (`content of text object` identical,
`count of comments` 0), so the character was swallowed, not moved.

## 2. Which read does it

Two probes ran the same typing test while a poll of one kind ran in the
background:

```
$ venv/bin/python /tmp/byteproof_poll_probe.py applescript
  [14:23:08] applescript poll: '' start=43     # Word's `selection`
  [14:23:11] after word 1 (alpha): None
             composer has closed

$ venv/bin/python /tmp/byteproof_poll_probe.py ax
  [14:23:17] ax poll: role='AXTextArea' text=''   # Accessibility only
  ... five words, one per second ...
  [14:23:25] final: {'value': 'alpha bravo charlie delta echo ',
                     'focused': 'True'}
```

Polling alone does not close the box - eight seconds of `selection` reads with
no typing left it open (`/tmp/byteproof_poll_only_probe.py selection`) - so the
harm is the interleaving of the read with a keystroke.

## 3. How the two surfaces differ

```
document body focused      -> AXFocusedUIElement role=AXWindow   (10/10 samples)
comment composer focused   -> AXFocusedUIElement role=AXTextArea (6/6 samples)
```

The composer's own attributes on this build:

```
AXRole AXTextArea | AXDescription 'Start a conversation'
ancestor (2 up): AXUnknown desc='1 of 1, Comment thread from Michael Zhang'
sibling: AXButton 'Post comment', AXHelp 'Post comment (Cmd + Enter)'
AXFocused True, AXValue '<the comment text>'
```

`AXDescription` never contains "comment" - the old recognition gate could not
fire. Accessibility writes into the composer are accepted and ignored:

```
AXValue write:          err=0  value now='<unchanged>'
AXSelectedText write:   err=0  value now='<unchanged>'
AXSelectedTextRange:    err=0  selected now=''
```

which is why the apply path has to be keystrokes (select-all + paste), gated on
the box still holding the previewed text and the keyboard.

## 4. Word's own AppleScript is flaky around a draft comment

In the same session, plain reads that had just worked started answering with
Word's terminology errors, then worked again moments later:

```
$ osascript -e 'tell application "Microsoft Word" to get name of every document'
execution error: every document doesn't understand the "count" message (-1708)
$ osascript -e 'tell application "Microsoft Word" to get name of active document'
byteproof-comment-test.docx            # three times in a row, immediately after
$ osascript -e 'tell application "Microsoft Word" to count documents'
3
```

The owner's log carries the same shape from earlier in the day (13:54 and
14:15: repeated `No active Word document is open (-2700)` from the manual
read, then normal service). The app's existing busy back-off already covers
the errors; the new rule is that no read is attempted at all while the
composer has the keyboard.

## 5. The fix, exercised live

The same scratch document, with the working-tree live service running and the
provider stubbed (`preview_edits_once` replaced, nothing spent), the draft box
open and focused:

```
[14:32:30] box after 'alpha':   {'value': 'alpha ', 'focused': 'True'}
[14:32:32] box after 'bravo':   {'value': 'alpha bravo ', 'focused': 'True'}
[14:32:33] box after 'charlie': {'value': 'alpha bravo charlie ', ...}
[14:32:35] box after 'this':    {'value': 'alpha bravo charlie this ', ...}
[14:32:37] box after 'coment':  {'value': '... this coment ', ...}
[14:32:40] box after 'a':       {'value': '... needs a ', ...}
[14:32:41] box after 'fix':     {'value': '... needs a fix ', ...}
[14:32:42] panel showing suggestions: True
[14:32:42] composer while the panel is up: {..., 'focused': 'True'}
```

and the capture log shows the comment text being read through Accessibility
and previewed, and the apply landing:

```
[14:32:30] LIVE WORD COMMENT BOX: reading the open comment box through AX (6 chars)
[14:32:34] LIVE PREVIEW: app='Microsoft Word' chars=20
...
[14:32:42] LIVE APPLY ALL: app='Microsoft Word' has_range=False count=1
[14:32:42] LIVE COMMENT BOX APPLY: suggestions=1 chars=44->45
[14:32:43] LIVE COMMENT BOX APPLY RESULT: ok=True message='Applied to the comment.'
```

Not one Word AppleScript call appears in that window while the box was
focused.

That run exercised the first shape of the fix, which treated the whole draft
as the subject. Two things came out of it:

* **The undo appended instead of replacing** (the box ended up holding the
  corrected text followed by the original): the select-all had not taken
  effect at paste time. The writer was changed so that select-all, its proof
  and the paste happen inside one System Events script, and a box whose text
  is not selected is never pasted into.
* **A whole-draft subject puts a card up while the owner is simply typing**
  (the stub was called at 20, 25, 32, 38 and 44 characters - one preview per
  pause). That is not the contract anywhere else in the app, and it spends
  provider calls on text nobody asked to have checked, so the subject was
  narrowed to the selection inside the box - as the original, unrunnable
  `_word_comment_box_details` intended. The apply still rewrites the box as a
  whole, splicing the corrected span back into the captured comment.

The final writer and the narrowed subject are covered by the tests below; the
live re-measurement of Apply + Undo in the composer is the owner's minute in
Word.

## 6. Tests

New coverage (all fail against the old code):

```
tests/test_live_preview.py::test_word_comment_box_is_read_through_ax_and_never_through_applescript
tests/test_live_preview.py::test_word_document_selection_still_uses_applescript
tests/test_live_preview.py::test_word_comment_box_apply_rewrites_the_box_through_the_writer
tests/test_live_preview.py::test_word_comment_box_apply_refuses_when_the_box_moved_on
tests/test_live_preview.py::test_word_comment_box_undo_writes_the_original_back
tests/test_hardening.py::test_a_manual_proofread_leaves_an_open_comment_box_alone
```

```
$ venv/bin/python -m pytest tests/test_live_preview.py tests/test_hardening.py -q
375 passed in 278.25s

$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
...
111 passed in 14.22s
All test files passed.
```

## 7. Beta

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.1 "Fix: writing in a Word
  comment box no longer loses characters or closes the box, and Live Check
  reviews comment text as it is written"
Bumped ByteProof to 2.4.1-beta.1 (pre-release)
  src/settings.py                      updated
  version_info.txt                     updated
  update feed                          SKIPPED (pre-release)

$ venv/bin/python scripts/check_version.py
version markers agree: 2.4.1-beta.1 (tuple (2, 4, 1, 1))

$ ./build_macos.sh            # arm64
... PyInstaller 6.22.0, BUILD-00.toc completed successfully
Applying stable code signature (preserves Accessibility permissions)...
Signed and verified.
...
Notarizing installer with Apple notary service...
  status: Accepted
Stapling notarization ticket to installer...
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg
Architecture: arm64

$ ditto dist/ByteProof.app /Applications/ByteProof.app
$ /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
      /Applications/ByteProof.app/Contents/Info.plist
2.4.1-beta.1
$ codesign --verify --deep --strict /Applications/ByteProof.app
                                    # silent: verified
$ open -a /Applications/ByteProof.app      # running
$ ls -l ByteProof_Installer_AppleSilicon.dmg
34310013  ByteProof_Installer_AppleSilicon.dmg   # notarized, stapled
```
