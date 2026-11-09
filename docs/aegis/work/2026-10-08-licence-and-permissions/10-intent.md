# Safari editing, and the licence/permission re-entry — Intent

## The owner's report

"check again the log. Now I can't edit in safari. Why? fix bugs for me. Also,
everytime with the update, I need to re-enter the licence key and need to
regrant the permission. You should already fix that."

## What each sentence turned out to mean

1. **"I can't edit in Safari."** ByteProof's apply in Safari failed on every
   attempt after the 14:50/15:00 installs (`mac_replace: paste was not
   observed in the target`, `ax_replace_range ... mismatch`, "Could not apply
   the edit in this app"), while the same path had succeeded four times that
   morning. Not Safari: the app was running with an Accessibility grant that
   macOS had already taken away.
2. **"regrant the permission" every update.** Two mechanisms, both measured
   on this machine: copying a build *over* the installed bundle drops the
   grant (an atomic replacement keeps it), and a grant that arrives while the
   app is running only half-works until the app is started again.
3. **"re-enter the licence key" every update.** The licence fingerprint hashed
   the machine's *first network interface* and *hostname* together with the
   hardware UUID, so a VPN, a new adapter, a virtual machine's regenerated MAC
   or a DHCP-supplied hostname changed it: the stored licence stopped
   matching, the app fell back to free mode, and each re-activation took a
   fresh seat (the service showed **four activations for one computer**).

## Goal

Updates leave the licence and the Accessibility grant alone; if the grant does
go missing, the app asks for it the way macOS remembers and then finishes the
job itself; and a licence already on this computer never has to be typed in
again because the network identity changed.

## Success evidence

- The fingerprint is stable across network changes, and a licence bound to the
  old value is accepted and moved to the stable one, with the old seat
  released (tests + the owner's own machine, live).
- Installing a beta by atomic swap keeps the Accessibility grant; the merge
  copy that drops it is identified and replaced by a script.
- The Safari apply that failed at 15:04 succeeds again on the same machine,
  with the app reporting "Applied to Safari".
- `./scripts/run_tests.sh` passes; the beta is built, notarized and installed.

## Stop condition

- `done`: the three symptoms are explained by measurements, the fixes are in
  the installed beta, and the owner has what they need to test them.

## Non-goals

- Changing what a released slot means: a licence the customer deliberately
  released (portal or another computer) still stops working here; the
  migration only moves a licence that is *this* machine's, in its old form.
- Weakening the machine binding: the new fingerprint is still a hash of
  hardware identity, just a stable one.
