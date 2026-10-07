# ByteProof demonstration videos — Task Intent

## Requested outcome

The owner asked for demonstration video clips for ByteProof, in the same shape
as the ByteBook set: "similar to codex://threads/01a0d1ed… I want you to create
the demo video clips for ByteProof."

## Goal

A narrated, captioned set of short films, one per function, filmed from the
product rather than mocked up, organised in a folder that can be handed to a
video editor or embedded in the product page, and re-recordable after a release
with the same scripts.

## Success evidence

- `demos/` holds the films, their posters, subtitles, scripts and audit sheets,
  and a gallery page that plays them.
- Every clip plays: H.264 1600x900 with an AAC narration track.
- An audit sheet per clip, read before the set is declared finished, showing
  the frame under each spoken line.
- Re-recording one clip works without disturbing the rest of the set.
- The method is written down where the next ByteMind product can use it.

## Stop condition

- `done`: the films exist, each one verified against its audit sheet, and the
  folder explains what is real and what is staged.

## Non-goals

- Not a release. Nothing here changes the application, so no version bump and
  no beta build; the owner decides whether the clips are published.
- Not a person reading the narration. The voice is local, generated speech and
  every published clip has to say so.
- Not a demonstration of speed. The timing is staged and no clip waits on a
  network or a model.
