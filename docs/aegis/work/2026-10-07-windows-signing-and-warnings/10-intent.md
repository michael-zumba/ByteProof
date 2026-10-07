# Windows "potential virus" warning — Task Intent

## Requested outcome

After installing ByteProof 2.4.0 on Windows, the owner saw Windows report the
file/app as potentially containing a virus. They asked how to fix it and to
make the build acceptable to Windows.

## Findings (verified 2026-10-07)

The Windows ZIP's `ByteProof.exe` carries **no Authenticode signature** — the
PE certificate table is empty. Windows therefore has no publisher to trust:

- SmartScreen shows "Windows protected your PC" for the downloaded app.
- Defender's heuristic engine can quarantine an unsigned Python/PyInstaller
  build as a false positive (the common `…!ml` machine-learning detections
  that blanket PyInstaller executables).

The MSIX in the release is signed with a **self-signed** certificate matching
the Partner Center identity, so it is a Store submission package only; users
cannot install it directly until Microsoft signs it during certification.

The Microsoft Store app identity is already reserved
(`ByteProof`, Store ID `9NHQWLVWCFTX`, publisher
`CN=4B25323B-AC94-4609-8F92-3AB67A94C5FF`) and the 2.4.0 MSIX matches it. The
Store product is not live yet (product lookup returns "not found").

## Fix paths

1. **Immediate, on the owner's PC:** unblock the ZIP, and if Defender
   quarantined the exe, restore it from Protection history ("Allow on
   device"); at the SmartScreen prompt choose More info → Run anyway.
2. **Free and permanent: Microsoft Store.** Submit the 2.4.0 MSIX in Partner
   Center. Microsoft signs the package; Store installs carry no warning. This
   needs the owner's Microsoft account (agents cannot do it).
3. **Free, covers the current ZIP for everyone:** submit `ByteProof.exe` to
   Microsoft's false-positive portal. Whitelisting is per file hash, so it has
   to be repeated for each new build until the binaries are signed.
4. **Paid and permanent for the ZIP path:** sign with a Windows
   code-signing certificate (EV gives SmartScreen trust immediately; Azure
   Trusted Signing is the CI-friendly monthly option). Requires the owner's
   purchase and identity verification. The release workflow can be wired to
   sign once a certificate exists.

Paths 2 and 4 need the owner's account or money; the agent prepares
everything up to the click.
