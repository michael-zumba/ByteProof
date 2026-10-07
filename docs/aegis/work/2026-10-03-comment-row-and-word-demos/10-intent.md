# Reviewer comments unlock, and the Word films — Intent

## The owner's report

"Why is Add Reviewer Comment disabled? Can you fix that?" and then "Actually,
now I can see it is there. Strangely, it was inactive before."

After that: record short demonstration films that use ByteProof to proofread
page two of `manuscript_immersive_tech_disclosure.docx` in real Word, one film
per setting (Document Context, Preferred Spelling, Editing freedom, and the two
reviewer-comment kinds), with the tracked changes visible as they land, and
narration. Save them where they can be posted to LinkedIn.

## What was true when the work started

- The reviewer-comment dropdown is gated on the licence tier: `free` mode
  shows it disabled with "Reviewer comments require a ByteProof license."
  The machine had been unlicensed since the trial ran out, and the licence was
  activated at 14:19 the same day, which is why the row appeared.
- The row is built from the tier the Settings dialog opened with, so activating
  from the License page in the same dialog left it disabled until Settings was
  reopened. That is a real bug: it is exactly when a new customer looks for the
  setting.
- Inserting a reviewer comment into Word was broken on this machine: the
  composer opened and was never found, so the app refused with "Word did not
  open a comment box" and the note was left on the clipboard.

## Done when

- The comment row unlocks the moment the licence is activated, without closing
  Settings, and locks again when a licence is released.
- A reviewer comment lands in Word from the shipped build, proven on this
  machine against the owner's manuscript.
- Five narrated films exist in `demos/word/`, recorded from the screen, with
  real tracked changes, an upload sheet, subtitles and audit sheets.
- The picture in those films keeps time with the narration, and a busy Word
  cannot silently cost a proofread.
- The suite passes, a beta is built and installed, and the owner is told what
  to test.
