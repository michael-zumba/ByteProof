# Reviewer comments unlock, and the Word films — Checkpoint

## The two product fixes

**The comment row followed the licence the dialog opened with.** In Settings,
"Add Reviewer Comment" is disabled in free mode. It is built once, from
`get_access_status()`, so a customer who activates a licence from the License
page in the same dialog kept seeing the row locked, with the "requires a
license" hint, until they closed and reopened Settings. That is the worst
moment for it: activation is when they go looking for the setting.

- `SettingsDialog._apply_comment_row_state()` is now the single owner of that
  row's state, and `refresh_license_gated_rows()` re-reads the tier after a
  successful activation or deactivation, rebuilds the cloud-provider buttons
  with it, and refreshes the sidebar badge. Only the gated state is rewritten,
  so unsaved edits on other rows survive.
- The three paths that change a licence call it: the in-dialog activation, the
  `byteproof://` activation, and deactivation.

**Word's comment composer was never found.** `_comment_box_window()` matched
the AppleScript document name (`paper.docx`) against Word's window title
(`paper`). They never matched, so `_comment_box_open()` always answered "no
box", `_trigger_comment_and_paste()` refused to type, and every reviewer
comment failed with "Word did not open a comment box" while the composer sat
open on screen. Matching now accepts the name with or without its extension,
and still refuses a window it cannot identify (a draft in another document
must never receive this document's note).

**A busy Word lost the proofread.** The selection read runs on every poll and
carries a 1.5 second timeout. When Word was still repainting, or a plugin held
it for a moment, the read failed and the app answered "Selection is empty." and
dropped the request. `get_selection_info()` now asks again up to three times on
`WordBusyError` before it gives that answer, which reads like the app refusing
to work rather than Word catching its breath.

## The films

Five films in `demos/word/`, recorded from the screen while the shipped app
proofread page two of `manuscript_immersive_tech_disclosure.docx` in real
Microsoft Word:

| Clip | Setting it changes | What it shows |
|---|---|---|
| `01-academic-journal` | Document Context → Academic Journal (Top-Tier) | tracked changes from a journal-standard edit |
| `02-preferred-spelling` | Preferred Spelling → US English | `visualisation` → `visualization` as a revision |
| `03-editing-freedom` | Editing freedom 0.2 → 0.7 | a freer rewrite, every change tracked |
| `04-language-comment` | Add Reviewer Comment → Language | tracked changes plus a Word comment in the margin |
| `05-technical-comment` | Add Reviewer Comment → Technical | the same edits with a reviewer's note |

## The recorder learned to keep time

The first cut of these films had the picture running several seconds behind the
narration. The cause was in the recorder, not the app: `Capture.start()` notes
when the recording began by waiting for the file to grow past 4 KB, and a still
screen compresses to almost nothing, so on this machine the note could be six
seconds late and the crop started that far into the recording.

`record_word_demo.py` now measures the offset instead of assuming it: a
full-screen magenta frame is shown at a known wall-clock time right after the
capture starts, and the recording is searched for that frame afterwards. The
frame's position in the video gives the true start, so the crop is cut where
the title card went up. A frame taken at 26.5s of `01-academic-journal` shows
page two with the paragraph selected, which is exactly what the narration
says at that moment.

## Two films were re-recorded on request

Films 1 and 3 were re-recorded after the first review:

- both now use `af_heart`, the voice film 5 was cast with, because the owner
  liked it best. `word_demo_clips.py` carries `voice=` on those two clips, and
  `record_word_demo.py` lets a plan's voice win over the seeded casting, so a
  full re-record reproduces every voice in the set;
- the three narration lines that said "the model" are gone, replaced by lines
  about the journal standard, so nothing in the films frames ByteProof as a
  text generator;
- the narration is lighter in the same professional register: "We can all
  dream" while the journal standard is set, "No pressure" when the tracked
  changes land, "That is the difference between tidying and renovating" while
  the freedom slider is turned up, and "Worth the wait" during the edit.

Films 2, 4 and 5 were left as they were, and the index and upload sheet now
show the voices each film actually uses.

    scripts/record_word_demo.py      the recorder: screen, narration, overlay, audit
    scripts/word_demo_clips.py       the films: what each one shows and says
    scripts/word_demo_driver.py      the hands: Word, ByteProof, its Settings window
    demos/word/upload-sheet.md       titles, descriptions and chapters for LinkedIn
    demos/word/clips/<name>-audit.jpg  one frame per spoken line, to be read

## Decisions worth knowing

- **The document keeps the frame.** ByteProof's window is 640 points wide at
  its minimum and Word needs the width for its markup and comment margin, so
  the app's window opens Settings and then steps aside; the black status pill
  reports "Proofreading…" and the result while the document holds the picture.
- **A fresh copy per take.** Each film opens its own copy of the manuscript in
  a take folder; the original is never opened by a film, and the copies are
  discarded without saving. The original's mtime and size were checked
  afterwards.
- **The app is left on known settings.** Temperature 0.2, Precise, General
  Editing, UK/AU/NZ spelling, no comment, Track Changes on, Live Check on with
  Word excluded (its card would draw over the paragraph). The Word rule is put
  back when the set finishes.
- **The model is the real one.** These films are not fixture-driven: the edits
  are what DeepSeek returned for the owner's manuscript, which is why each film
  waits on the pill while the model works. The narration covers the wait
  instead of cutting it out.
- **One clip failed and was re-recorded.** The overlay recording of
  `02-preferred-spelling` started 16.7s into the title card, which the engine
  refuses to write; the re-run was clean.
