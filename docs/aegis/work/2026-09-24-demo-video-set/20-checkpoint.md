# ByteProof demonstration videos — Checkpoint

## What was built

`scripts/record_demo_videos.py` is a recording engine for a desktop product,
and `scripts/video_clips.py` is the plan that says what the films are. Eighteen
films live in `demos/`, twelve minutes and forty seconds in total.

    scripts/record_demo_videos.py   records, narrates, encodes, audits
    scripts/video_clips.py          the films: screens, words, timings
    scripts/kokoro_narrate.py       reads one clip's lines in a single run
    demos/index.html                plays the set
    demos/README.md                 what is real and what is staged
    demos/<name>-audit.jpg          one frame per spoken line, to be read

ByteProof is a Qt application with no page to drive, so the picture is
assembled instead of steered: the shipped widgets are built offscreen, rendered
at twice their size, and composited onto a canvas with a cursor, a caption, a
title card and notes. The narration is read before the frames are drawn, so the
clock comes from the voice and the captions, subtitles and audio cannot drift.

## The set

Getting started, then one group per job: reading the changes in Word,
reviewer comments, citations, style and context, English and strictness, Live
Check (the panel, take-backs, which apps, when it waits), polishing a selection
anywhere, where the proofreading runs (offline and bring-your-own-key), and the
licence and updates.

## Decisions worth knowing

- **What is real.** Every screen is the application's own widget, laid out by
  its own code, and every change of state calls the same method the app's own
  event handler calls.
- **What is staged.** The timing, and any answer that would have come from a
  language model. No clip makes a network request, so a clip shows what the
  software does, not how fast it does it.
- **Screens that belong to other products.** Word and a mail window are drawn
  plainly and named in the caption. Their toolbars are deliberately absent: a
  neutral page in a neutral window says "your document" without pretending to
  be Microsoft Word.
- **Nothing personal is filmed.** The settings dialog is built from a copy with
  the API keys, the configured apps and the active provider stripped out. One
  early probe did show the owner's key fields, which is what prompted the rule.
- **No version bump.** Nothing in `src/` changed, so there is nothing for a
  tester to re-test and no beta is owed. The set is on disk, unpublished.

## Two copies

The owner asked for one version for the website and one for YouTube, so the set
is rendered twice from the same narration.

| | `demos/` | `demos/youtube/` |
|---|---|---|
| size | 1600x900 | 1920x1080 |
| quality | CRF 20 | CRF 18 |
| audio | mono, 160k | stereo, 192k |
| size on disk | 25 MB of MP4, 44 MB with posters and audits | 32 MB of MP4, 50 MB |
| extras | gallery, README, transcripts | the same, plus `upload-sheet.md` |

The narration is read once and reused, so both copies are the same take: the
words, the pacing and the subtitles are identical and only the picture is drawn
again. Plan coordinates are untouched by this. A plan is written in the 1600x900
design space and the painter is scaled before anything is drawn, so type is
rendered at the output size rather than enlarged afterwards.

`demos/youtube/upload-sheet.md` holds a title, a description and YouTube chapter
marks per clip, built from the captions that are already on screen.

## Open

- The owner decides whether `demos/` is committed, built on demand, or
  published to the product page and YouTube.
