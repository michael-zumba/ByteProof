# Comment insertion and the Proposed Changes pane — Checkpoint

## What was wrong

1. **The comment guard used the wrong signal.** Word for Mac cannot create a
   comment through AppleScript, so the app opens the comment box through the
   Insert menu and fills it from the clipboard. The 2.0.2-beta.1 hardening
   pass added a safety check that waited for `count of comments` to rise
   before pasting. Current Word opens the box as a *draft*: the comment does
   not join the document (and the count) until it is posted with Cmd+Return.
   The count therefore never rose, the guard fired, and the box was left open
   and empty. That is exactly what the owner saw, and it explains why comment
   insertion worked in earlier builds.
2. **The box is the signal, and it is visible.** Word exposes the open draft
   through Accessibility: a "Post comment" button, a "Cancel new comment
   draft" button, and the composer text area beside them (read at ~100-160 ms
   per check, measured).
3. **The two keystroke fallbacks were guesses.** `⌥⌘A` and `⇧⌘A` are not
   Word's comment shortcuts on this machine (the Insert ▸ Comment menu item
   carries no key equivalent at all), so they could never have fired, and
   they were never reached because the first method always reported success.
4. **The review diff lost its alignment.** `diff_html` used difflib's default
   junk heuristic. Past ~200 word tokens it treats the spaces between words
   as "popular" and stops matches there, so a paragraph that changed by one
   clause rendered as the whole paragraph struck through plus the whole
   replacement: measured 96.5% of the text marked as changed for a seven-word
   deletion, against 4.9% once the heuristic is off. The same call in the live
   apply path (`_split_span_word_level`) could pair unrelated text and return
   it as one "word-level" edit, which is how a whole paragraph reaches Word as
   a replacement. Every other diff in the codebase already passed
   `autojunk=False`.

## What changed

- **Comment trigger**: the menu click is verified by the box itself (count
  rise or composer present, polled for up to 8 s), with the Review ribbon's
  New Comment button as a second door for builds that ignore the menu item.
  The guessed keystroke fallbacks are gone.
- **An open box is used, never toggled**: Word's Insert ▸ Comment *cancels* an
  open draft, so a box that is already on screen is filled as it is. This is
  what defeated the owner's retry: the box ByteProof had left open was closed
  again by the next trigger.
- **Comment text**: the composer is given keyboard focus, the note is pasted
  into it, the text is read back out of the box, and Word's own Post comment
  button is pressed. (Accessibility cannot write the box's value — it is a web
  view — and `Cmd+Return` empties the box on this build instead of posting.)
  A paste only ever happens while the box holds focus, so the note cannot be
  typed into the manuscript.
- **Verification**: the card Word draws for a posted comment
  ("Comment from <author>. <note>. On <date>") is the evidence, with
  `count of comments` accepted as well. Modern Word comments never appear in
  that collection at all, which is what the old guard tripped over; the pane
  is the only place they are visible.
- **The comment box belongs to the active document's window only**: a draft
  left open in another document can never receive this document's note.
- **Visible failure**: a comment that cannot be added now says so in the
  status line and a toast, and the note is deliberately left on the
  clipboard instead of vanishing into a `print`.
- **Diff**: `autojunk=False` in `src/ui_theme.diff_html` and in
  `src/live_preview._split_span_word_level`.

## Deliberately left alone

- The comment text is still pasted via the clipboard when Accessibility
  cannot write the box; a focus check gates it.
- `_split_span_word_level` still drops a large span whose alignment cannot be
  verified (safe, and the documented contract). Pure insertions inside a
  large span remain dropped rather than spliced.
- Prompt wording, the 12-edit preview cap and the apply path are untouched.
