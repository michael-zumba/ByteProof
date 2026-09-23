# Live edits that keep their place — Evidence

## 1. The log, quoted

Word, every live apply in the file (57 occurrences of this line):

```
[11:24:35] LIVE APPLY ALL: app='Microsoft Word' has_range=True count=3
[11:24:36] WORD: live edit read-back differs; reporting for review
```

ChatGPT, one Apply All of six suggestions, then the selection churn:

```
[10:52:41] LIVE APPLY ALL: app='ChatGPT' has_range=True count=6
[10:52:41] ax_replace_range: pid=74984 start=2158 length=39 …
[10:52:41] ax_replace_range verify: value slice <len=40 …> expected <len=40 …>
[10:52:41] ax_replace_range: AX write unverified (mismatch); falling back to paste
[10:52:42] ax_replace_range: range changed after paste (<len=39 …>); refusing retry
[10:52:42] LIVE APPLY ALL: skipping a span after failure … at rel=(230,269)
… five more spans, the same way …
[10:52:53] LIVE PREVIEW: app='ChatGPT' chars=29
[10:52:55] LIVE DONE SYNC FAIL: selection changed: previewed=<len=29 …> now=<len=73 …>
[10:52:55] LIVE PREVIEW: app='ChatGPT' chars=73
[10:52:56] LIVE DONE SYNC FAIL: selection changed: previewed=<len=73 …> now=<len=36 …>
[10:52:57] LIVE PREVIEW: app='ChatGPT' chars=36
[10:52:58] LIVE DONE SYNC FAIL: selection changed: previewed=<len=36 …> now=<len=39 …>
```

The "now" values are the texts of the spans whose ranges had just been written
into the app: its own selection was still walking through them.

## 2. Word probes (real documents, scratch, closed without saving)

`/tmp/byteproof-word-test/probe.applescript` — what a range object does after a
content write:

```
range-before=[uick ]            <- create range d start 5 end 10
range-after=[quick]             <- r kept its old extent
clipboard=[quickly brown]
matches=false
doc=[The qquickly brownbrown fox jumps over the lazy dog.]   <- the write landed
```

`/tmp/byteproof-word-test/probe2.applescript` — a fresh range is the honest
read-back:

```
fresh=[quickly brown]
fresh-matches-clipboard=true
```

`/tmp/byteproof-word-test/live_edit_probe.py` — the shipped function, before
and after the change (same scratch document):

```
document: Document3
ok: True | message: Applied.                              <- was: "Applied — please check the document."
doc after: The quickly brown brown fox jumps over the lazy dog.
```

`/tmp/byteproof-word-test/live_edit_probe2.py`:

```
A. wrong before_text (the range holds something else):
   (False, 'The text moved or changed — please try again.')
B. replacement with a line break:
   (True, 'Applied.')
   doc: 'one\ntwo beta gamma delta epsilon.'
```

`/tmp/byteproof-word-test/mapping_probe.py` — the position mapping the user's
workflow depends on:

```
--- emoji before the span
    selection: 26 code points, doc 0..27
    visible 13..18 -> doc {13: 14, 18: 19}
    write: ok=True 'Applied.'
    doc: '🙂 alpha beta gamut delta.'
--- tracked deletion before the span
    selection: 19 code points, doc 0..24
    visible 6..11 -> doc {6: 6, 11: 16}
    write: ok=True 'Applied.'
    doc: 'Alpha gamut delta.'
```

## 3. Tests that fail against the old code

Each file was reverted to `HEAD` in place, the new tests run, then restored:

```
FAILED test_ax_replace_range_puts_the_selection_back_when_nothing_was_written
FAILED test_ax_replace_range_refuses_when_the_span_text_has_two_homes
FAILED test_apply_all_stops_when_the_app_changed_the_text_unconfirmed
FAILED test_apply_one_reselects_the_edited_text_and_keeps_the_panel
FAILED test_word_live_edit_verifies_the_extent_it_wrote
5 failed, 1 passed
```

The one that passes both ways is `…accepts_a_lagging_range_when_the_span_is_unique`:
it pins behaviour that must *not* regress (lagging apps with provable text).

## 4. Suite and lint

```
tests/test_live_preview.py   146 passed
tests/test_hardening.py      173 passed
tests/test_smoke.py          108 passed
ruff check src tests         All checks passed!
scripts/check_version.py     version markers agree: 2.2.2-beta.2
```

## 5. Shipped

Beta **2.2.2-beta.3** (beta.2 was built before the last Apply All change, so the
rebuilt installer carries a new number):

```
ByteProof_Installer_AppleSilicon.dmg
sha256 ab86e6fa1ab468ad394339e85e110779570cf54e2f9d233d9beecbb3793f1da9
stapler validate: The validate action worked!
spctl: accepted, source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
```

Installed to `/Applications` (2.2.2-beta.2 moved to the Trash, and beta.1
before it) and started; the app is running as 2.2.2-beta.3.

### What the owner can watch in the log

* Word applies now report `ok=True "Applied."`; `WORD: live edit read-back
  differs` should appear only when Word really normalised the text.
* A refused apply leaves `ax_replace_range: put the previous selection back`
  and never a `LIVE DONE SYNC FAIL … now=<a different span>` right after.
* A write the app cannot explain stops the batch:
  `LIVE APPLY ALL: stopping at rel=(…); the app did not confirm the edit …`.
* The ambiguity rule announces itself once it fires:
  `ax_replace_range: the selection holds the span's text but that text is not
  unique in the field; refusing to treat the range as confirmed`.
