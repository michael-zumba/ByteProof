# Writing in a Word comment box, and Live Check in the comment — Checkpoint

## What was wrong

1. **The live poll read Word's selection through AppleScript while the comment
   box held the keyboard.** Measured on this machine: the read alone does not
   close the box, but a read that lands between two keystrokes does - Word
   closes the draft comment and the character being typed disappears. That is
   the owner's "the editing is not able to achieve" and "the comment ... always
   disappears". The read is also useless: AppleScript's `selection` describes
   the manuscript, never a draft comment (a draft is not part of the document
   until it is posted).
2. **The composer was never recognised as a comment box.** The old check
   required the focused text area's `AXDescription` to contain "comment"; on
   this Word build that description is "Start a conversation". So even a
   selection inside the box could not become a Live Check subject.
3. **The manual proofread (hotkey) started with the same AppleScript read**, so
   pressing it while writing a comment closed the box and swallowed the draft,
   and would have proofread the manuscript text instead of the comment.
4. **A preview that finished while the user had moved into the comment box**
   still re-read the selection through AppleScript (`_sync_selection`), the
   same hazard from a different door.

## What changed

- **Focus is probed before every Word read.** Word reports its *window* as the
  focused Accessibility element while the manuscript has the keyboard (10 of
  10 samples); the comment composer reports a text element (6 of 6 samples).
  `word_integration.focused_text_field()` answers None in the ordinary case -
  the AppleScript path is untouched - and answers with the surface when the
  keyboard is inside one of Word's own text fields. The composer is recognised
  from the focused element's ancestors plus Word's own "Post comment" button,
  with a short cache so the walk is not repeated per tick, and with no
  AppleScript at all (the old `_comment_draft_box` needs a document-name read,
  which is exactly the round trip that must not happen while the box is
  focused).
- **A selection inside the draft comment is read through Accessibility.**
  `LivePreviewService._word_field_details` reads the composer with AX (the
  same `selection_details` other apps use), and the selection is the subject -
  exactly as in the manuscript, in Mail or in Pages. Nothing about the trigger
  rules changed: the panel still waits for stable text, a few words and the
  pointer staying with it, so no panel appears while the owner is simply
  typing a comment. The box's whole text travels with the capture, because
  that is what the write rewrites: Word offers no writable sub-range inside a
  draft comment.
- **Apply rewrites the box the way a reader would.** Word's composer accepts
  Accessibility writes with a success code and ignores them (measured: value
  and selection unchanged), so `replace_comment_box_text()` focuses the box,
  gates on the box still holding exactly the previewed text, and then does
  select-all + paste **inside one System Events script** with a length proof
  between them: if the select-all did not cover the box, the paste is never
  sent. The text pasted is the captured comment with the corrected span
  spliced back in, so the rest of the comment is carried across untouched, and
  the panel closes afterwards (the rewrite consumed the selection). Undo is
  the same write in reverse, gated the same way.
- **A manual proofread stands down** while one of Word's text fields has the
  keyboard (`COMMENT_BOX_SKIPPED_STATUS`, shown as an information message),
  instead of reading the manuscript and closing the draft.
- **`_sync_selection` and the document-content fallback** skip their
  AppleScript reads while Word's keyboard is in a text field, and a draft
  comment is no longer treated as a clipboard-only target (a Copy keystroke
  must never be posted into an open composer).

## Deliberately left alone

- The trigger contract (selection/stable/pointer) and the 12-edit cap.
- Posted-comment proofreading (`replace_comment_selection`, scope `comments`).
- `_comment_draft_box()` and the comment-insertion flow that uses it: that flow
  is user-initiated and its paste is already gated on the box holding focus.
- Pages. Its live path only posts a Copy keystroke after a real selection
  gesture, so typing in a Pages comment is not exposed to any of this; a
  selection inside a Pages comment uses the same clipboard read and paste as
  the Pages canvas, which is unchanged and still to be confirmed by the owner
  on a comment.

## What the owner should watch

1. Type a comment with ByteProof running: the characters must land and the box
   must stay open (this is the part measured live, see the evidence).
2. Select a few words inside the draft comment: the card should appear, and
   Apply should correct just those words and leave the rest of the comment
   alone; Undo should put the comment back.
3. Press the proofread hotkey while the comment box is open: it should say the
   box is open instead of touching the document.

Apply and Undo inside the composer have not been re-measured end to end since
the writer was hardened and the subject was narrowed to the selection: the
first live run (whole-comment variant) applied correctly but its undo landed
after the written text instead of replacing it, i.e. the select-all had not
taken effect at paste time. Select-all, its proof and the paste now happen
inside one System Events script, and a box whose text is not selected is never
pasted into - which closes that gap by construction, but it is the piece of
this work that still wants the owner's own minute in Word.
