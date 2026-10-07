# ByteProof demonstration videos — Evidence

## 1. The set, measured

Every clip, from `ffprobe`, after the final run:

```
01-what-byteproof-does.mp4              50.5s  h264 1600x900 + aac
02-proofread-a-selection.mp4            50.8s
03-review-the-changes.mp4               39.6s
04-comments-for-your-supervisor.mp4     44.6s
05-keep-citations-and-equations.mp4     39.2s
06-choose-a-writing-style.mp4           49.6s
07-english-and-how-much-it-changes.mp4  41.4s
08-turn-on-live-check.mp4               42.3s
09-the-floating-panel.mp4               48.1s
10-undo-a-change.mp4                    30.7s
11-which-apps-it-watches.mp4            39.8s
12-when-it-waits.mp4                    56.9s
13-polish-in-any-app.mp4                43.3s
14-run-it-offline.mp4                   46.2s
15-bring-your-own-key.mp4               40.9s
16-the-free-trial.mp4                   32.2s
17-activate-a-licence.mp4               33.8s
18-stay-up-to-date.mp4                  33.1s
```

12.7 minutes of finished video, 25 MB of MP4 and 43 MB in the folder. Narration
loudness measured with `volumedetect`: mean -16.4 to -18.0 dB, peaks -1.5 dB,
against the same target in every clip.

## 2. What the audit sheets caught

The audit sheet is one frame taken as each spoken line begins, with the line
under it. Four faults were found this way and fixed, none of which was audible:

1. **A card drawn as a black rectangle.** The highlight ring used
   `painter.setBrush(0)`, which is not "no brush" but the colour black, so the
   ring filled its own rectangle. Every frame of the suggestion card was a
   black box.
2. **A film narrating a control nobody could see.** `06-choose-a-writing-style`
   and `04-comments-for-your-supervisor` talked about settings that were below
   the fold of a 620 pixel dialog. The pages now scroll the control into view
   before the step is filmed.
3. **A download that was already finished.** The Local AI film reported the
   model as installed from the first frame, in a film whose subject is the
   download. The staged answer now follows the staged progress.
4. **A step whose result arrived halfway through its own sentence.** Cues sat
   at the midpoint of a step, so the first half of a sentence played over the
   screen the sentence was not about. Result-bearing steps now fire their cue
   near the start; only instruction steps click late, so the caption is on
   screen while the button is pressed.

## 3. Two tools that made the set cheap to correct

`--probe --at 12,27` writes stills and encodes nothing, turning a thirty second
check into a one second one; it fires every cue up to the step being probed so
the still shows the screen the step ends on.

`--rebuild` rewrites the gallery, README and transcripts from
`demos/manifest.json`, so a wording change to a template does not cost another
run. The manifest also means re-recording one clip leaves the other seventeen
in the gallery: an early partial run had rewritten the gallery with two films,
which is what created the need.

## 4. The one bug that cost the most time

Playwright's bundled ffmpeg has a VP8 muxer and almost nothing else: no
libx264, no AAC, and **no WAV demuxer**. The engine found it first, because the
website clips use it, and reported `Invalid data found when processing input`
on Kokoro's perfectly good output. The engine now looks for an ffmpeg with
libx264 and says so in one line if there is none, rather than passing the wrong
binary to a job it cannot do.
