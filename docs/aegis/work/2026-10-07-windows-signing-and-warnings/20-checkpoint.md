# Windows "potential virus" warning — Checkpoint

## What to do on the PC right now

1. **Unblock the download.** Right-click `ByteProof_Windows.zip` → Properties
   → tick **Unblock** → OK, then extract it again. (PowerShell alternative:
   `Unblock-File .\ByteProof_Windows.zip`.)
2. **If Defender already quarantined `ByteProof.exe`:**
   Windows Security → **Virus & threat protection** → **Protection history**
   → select the ByteProof detection → **Actions → Allow on device**. This
   restores and allows that exact file; it does not weaken protection for
   anything else. Prefer this over adding a folder exclusion.
3. **If "Windows protected your PC" appears:** click **More info → Run
   anyway**. The app is unsigned, so Windows cannot name a publisher.

## Free permanent fix: Microsoft Store (recommended)

The Store identity is already configured; only the submission is missing.

1. Partner Center (https://partner.microsoft.com/dashboard) → **Apps and
   games** → **ByteProof**.
2. **Packages** → upload
   `ByteProof_Installer_x64.msix` from the 2.4.0 release.
3. Complete the listing (text and assets are in
   `packaging/windows/store-listing.md` and `packaging/windows/listing/`;
   privacy policy `https://www.bytemind.co.nz/privacy.html`).
4. **Submit for certification.** Review is typically 1–3 business days.
5. After it is live, Windows users install from
   `https://apps.microsoft.com/detail/9NHQWLVWCFTX` with no warning at all.

## Free fix for the current ZIP: false-positive report

Submit at https://www.microsoft.com/en-us/wdsi/filesubmission (choose
"Software developer" → false positive). Values to paste:

- Product: ByteProof 2.4.0 (academic proofreading app for Microsoft Word)
- Publisher: ByteMind Ltd, New Zealand — https://www.bytemind.co.nz
- Download: https://github.com/michael-zumba/ByteProof/releases/tag/v2.4.0
- File: `ByteProof.exe` (inside `ByteProof_Windows.zip`)
  SHA-256 `b420ec226cf81a4495102c4c514946660a00bf3e19deb52e1b5bdc8ccdf281e6`
- ZIP SHA-256
  `bc3138eafe450c663f5420d55404148e2f29b563621e843f753d03b49a9c1113`
- Detection name: copy the exact one from Protection history (usually looks
  like `Trojan:Win32/Wacatac.B!ml` or `Program:Win32/Wacapew.C!ml`)

Microsoft whitelists the exact file hash, typically within 1–3 days. It does
not cover the next build, which is why signing is the real fix below.

## Paid permanent fix for the ZIP path

- **Azure Trusted Signing** — about US$10/month, identity validation, signs
  directly in GitHub Actions (no certificate files to hold). Best fit for the
  automated release.
- **EV code-signing certificate** — roughly US$300–500/year; SmartScreen
  trusts the publisher immediately.
- **OV code-signing certificate** — cheaper, but SmartScreen reputation has
  to build up over downloads.

Once a certificate or Trusted Signing account exists, the Windows build job
gets a signing step before the ZIP and MSIX are produced; nothing else about
the release changes.

## Verified facts

```
ByteProof.exe (from the 2.4.0 ZIP)
  PE certificate table: rva=0 size=0  →  UNSIGNED
  SHA-256 b420ec226cf81a4495102c4c514946660a00bf3e19deb52e1b5bdc8ccdf281e6

ByteProof_Installer_x64.msix
  Identity Name="ByteMind.ByteProof"
           Publisher="CN=4B25323B-AC94-4609-8F92-3AB67A94C5FF"
           Version="2.4.0.0" x64
  self-signed (Store submission package; not for direct install)
  SHA-256 3b46f5e307d71fee8b4a6e25946606d670fc48a7b85249138bb6d538b4064d3a

Store product 9NHQWLVWCFTX: not found in the Store catalogue → not live yet.
```
