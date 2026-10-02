# Track-changes refusal — Checkpoint

State as of 2026-10-02 10:35.

## Done

- Root-caused the "DeepSeek is not connecting, app not working for editing"
  report: DeepSeek was connected (capture.log `LIVE DONE … provider=DeepSeek`
  at 10:07; the provider test passes). The blocker is the Word proofread
  aborting at 10:21:50 on a Track-Changes write.
- Tests first: 4 new hardening tests, watched failing against the old code.
- Repair in `src/word_integration.py` (macOS Word):
  - a Track-Changes write is skipped when Word is already in the wanted
    state — the redundant set is what Word refused;
  - a refusal is tolerated when the state is (still) the wanted one;
  - a genuine refusal reaches the owner as "Microsoft Word would not turn
    Track Changes off. Close any dialog Word is showing (or change it on
    Word's Review tab), then try again.";
  - the invalid fallback property (`track changes of active document`,
    which Word's dictionary does not have) is gone.
- Full gate green: version check, ruff, all three test files (464 collected).
- **2.2.3-beta.2** built, signed, installed to `/Applications`, running
  (pid 74460); previous beta saved to
  `previous-versions/ByteProof_2.2.3-beta.1.app`.

## Blocked (owner action) — resolved the same day

- Apple notarization returned HTTP 403 — "a required agreement is missing or
  has expired".
- **Resolved 2026-10-02.** The gate was the team's *Free Apps Agreement*:
  Business → Agreements showed it becoming effective 2 Oct 2026 – 15 Aug
  2027. Once the owner accepted it, `notarytool history` answered again and
  the **2.3.0-beta.1** build notarised — app and DMG accepted and stapled
  (DMG submission `543676fd-c82d-4a54-999f-ef75825f73d4`), `spctl` reports
  "accepted, source=Notarized Developer ID".
- Lesson for future local betas: `BYTEPROOF_SKIP_NOTARIZE=1` still signs with
  the Developer ID, because a differently-signed build makes macOS drop
  ByteProof's Accessibility permission.

## Next

- Owner: select text in Word and press the proofread hotkey; confirm the pill
  completes and the edits land.
- Official release only when the owner explicitly asks; the update feed still
  advertises 2.2.2.
