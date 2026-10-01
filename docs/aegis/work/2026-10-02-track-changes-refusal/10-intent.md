# A refused Track-Changes write must not stop a proofread — Task Intent

## Requested outcome

The owner asked why DeepSeek "is not connecting" and said the app shows the
provider as connected but "is not working for editing". The provider was
reaching DeepSeek (capture.log shows `LIVE DONE … provider=DeepSeek` at
10:07), and the last action in the log is the real failure:

```
[10:21:49] APP: pill shown (processing): Proofreading…
[10:21:50] WORD: AppleScript error: 45:111: execution error: Microsoft Word got an error: Can’t set track revisions of active document to false. (-10006)
[10:21:50] WORD: AppleScript error: 67:80: syntax error: A identifier can’t go after this identifier. (-2740)
[10:21:50] APP: pill shown (error): Error: Unable to disable Track Changes in Microsoft Word.
```

Track Changes was already **off** in the document. The proofread aborted on a
redundant "set it off again" that Word refused, and the second script it fell
back to (`track changes of active document`) is not a property Word's
AppleScript dictionary has — it can only ever produce a syntax error.

## Goal

Proofreading in Word works when Track Changes already sits where the settings
need it, and a genuine refusal says what to do instead of the bare "Unable to
disable Track Changes".

## Success evidence

- Failing tests first: an already-off document gets no redundant write; a
  refused write is tolerated when the state is already what the proofread
  needs; a refusal that leaves Track Changes on reaches the owner with a next
  step in the message.
- The invalid `track changes of active document` fallback is gone from the
  source.
- Full gate green (version check, ruff, all three test files).
- **2.2.3-beta.2** built, signed, notarized, installed to `/Applications`, and
  running for the owner to test.

## Stop condition

- `done`: the beta is installed and the owner can proofread in Word.
- Official release only when the owner explicitly says so; the public update
  feed keeps advertising 2.2.2.

## Non-goals

- No change to which provider paths call Track Changes, and no change to the
  live-edit suspend/restore scripts (they already restore on both paths).
- No Windows change: the Windows path sets `TrackRevisions` directly and does
  not hard-fail on an already-correct state.
