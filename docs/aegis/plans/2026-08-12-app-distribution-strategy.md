# ByteProof: App Distribution & Trust Strategy

**Date:** 2026-08-12 · **Status:** Draft for review · **Owner:** ByteMind

## 1. Goal

Make it easy for users on macOS and Windows to install ByteProof without
frightening security warnings, at the lowest possible cost while the app is
still pre-revenue.

## 2. Why users see warnings today

- **macOS:** Gatekeeper trusts apps signed with an Apple-issued *Developer ID*
  certificate and notarized by Apple. ByteProof is currently signed only with
  a local self-signed identity ("ByteMind Code Signing"), which exists to keep
  Accessibility permissions stable — it is not Apple-trusted, so macOS shows
  "Apple could not verify ByteProof.app is free of malware."
- **Windows:** ByteProof.exe has no code-signing certificate and no SmartScreen
  reputation, so Windows shows "Windows protected your PC." Antivirus
  heuristics can also flag Python/PyInstaller-built apps even when harmless.
  On managed/work laptops, admin approval and corporate antivirus add extra
  friction regardless — signing reduces false positives, but IT policy may
  still require approval for any new desktop software.

## 3. The facts (verified August 2026)

| Option | Cost | What it does | Verdict |
|---|---|---|---|
| Apple Developer Program | US$99 per year (local equivalent where available) | Enables the Developer ID certificate + Apple notarization that removes the macOS warning. One membership also covers iOS/iPadOS later. | Required for a no-warning Mac install |
| Microsoft Store developer account | Free (individual fees removed 2025; company fees removed May 2026) | Publish ByteProof as an MSIX package; Microsoft signs it; Store installs get no SmartScreen warning. | Best Windows fix — no recurring cost |
| Azure Artifact Signing (formerly Trusted Signing) | ~US$9.99/month | Microsoft-managed cloud signing for non-Store distribution | Not currently available in New Zealand (individuals: US/Canada only) — skip for now |
| OV code-signing certificate | US$150–300/year | Signs the .exe for direct downloads from GitHub/your site | Optional later; SmartScreen warnings still appear until reputation builds |
| EV certificate | US$400+/year | Historically gave instant SmartScreen trust | No longer instant trust since 2024 — not worth the premium |
| SignPath Foundation free signing | Free | Signs Windows builds for qualifying projects | Open-source projects only — ByteProof is proprietary, so not eligible |

**Bottom line:** the macOS fix costs US$99/year. The Windows fix can be free
via the Microsoft Store.

## 4. Recommended path

### Phase 0 — now, $0

1. Keep GitHub Releases as the current channel.
2. Add one clear "install help" section (website FAQ or README):
   - macOS: right-click ByteProof in Applications → **Open** → **Open** again.
   - Windows: on the SmartScreen prompt, click **More info** → **Run anyway**.
   - Reassure users the warning is expected for a new, small developer and does
     not mean the app is a virus.
3. Keep releasing consistently from the same identity so future signing and
   reputation work is clean.
4. Agree on a spending trigger — suggestion: **first paid license, OR ~100
   downloads/month, OR 5+ install-support questions**. When triggered, move to
   Phase 1.

### Phase 1 — first revenue/usage signal (~US$99/year total)

**macOS (~US$99/year):**

1. Enroll in the Apple Developer Program (individual is fastest; organization
   enrollment requires a D-U-N-S number).
2. Create a "Developer ID Application" certificate in Xcode → Settings →
   Accounts → Manage Certificates.
3. Store notary credentials once (`xcrun notarytool store-credentials`), then
   set `BYTEPROOF_DEV_ID` — the existing `scripts/notarize.sh` and release
   pipeline already do the rest (sign, notarize, staple).
4. Verify on a clean Mac: a fresh download opens with **no** "cannot verify"
   warning.

> Note: the first Developer ID build replaces the current self-signed identity,
> so existing users may need to re-grant Accessibility permission once. New
> users after that point are unaffected.

**Windows ($0):**

1. Create a free Microsoft Store developer account.
2. Package ByteProof as MSIX (wrap the existing PyInstaller output with the
   MSIX Packaging Tool or a GitHub Actions step). This is a one-time packaging
   effort plus a Store review.
3. Microsoft signs the package during certification, so Store installs get no
   SmartScreen warning.
4. Keep the GitHub zip as a fallback channel with the "Run anyway"
   instructions.
5. Update the website download buttons: Microsoft Store recommended on
   Windows; DMG on macOS.

> **Status (2026-08-14):** the MSIX packaging pipeline is now in the repo
> (`packaging/windows/`, workflow builds `ByteProof_Installer_x64.msix`).
> Remaining: create the free Store account, reserve the app name, copy the
> Store identity into `msix-config.json`, and submit — see
> `MICROSOFT_STORE_GUIDE.md`.

### Phase 2 — only when growth justifies it

- If corporate/enterprise users need a signed direct-download .exe, buy an OV
  certificate (US$150–300/year). Warnings will still appear for new files
  until SmartScreen reputation builds.
- Consider the macOS App Store later for extra trust and automatic updates
  (15% commission under the US$1M small-business program). PyInstaller +
  sandbox + Word integration makes this a real project — not a priority now.
- Keep the update feed and GitHub Releases; consistent signing lets Windows
  reputation accumulate over time.

## 5. What NOT to do

- Don't buy an EV certificate just for SmartScreen (no longer instant trust).
- Don't open-source ByteProof just to qualify for SignPath's free signing.
- Don't tell users to disable security software — it destroys trust and gets
  flagged.
- Don't treat the current self-signed certificate as a distribution trust
  signal; it exists only to preserve Accessibility permissions.

## 6. How we'll know it worked

- **macOS:** fresh download on a clean machine opens without warning;
  `spctl --assess --type execute -v /Applications/ByteProof.app` reports
  `accepted`; notary log is clean.
- **Windows (Store):** clean machine installs from the Microsoft Store with no
  SmartScreen warning; Defender reports no detection; GitHub zip still works
  with the documented fallback.
- **Business:** install-related support messages drop; trial-to-license
  conversion improves.

## 7. Decisions needed

1. **Individual or organization Apple enrollment?** If ByteMind Ltd is
   registered, organization enrollment needs a D-U-N-S number; individual is
   faster and costs the same.
2. **Commit to the Microsoft Store as the main Windows channel?** Recommended;
   keep GitHub as the fallback.
3. **Agree on the spending trigger** (suggested: first paid license OR ~100
   downloads/month).

---

## Plan notes

- **Type:** strategy / docs-only plan — no source-code changes in this plan.
- **TDD route:** not applicable (no code changes).
- **Baseline references:** `README.md`, `GITHUB_DISTRIBUTION_GUIDE.md`,
  `scripts/notarize.sh`, `tools/sign_byteproof.sh`; Apple Developer membership
  pricing page; Microsoft Learn "Code signing options for Windows app
  developers"; Microsoft announcements on free Store registration (2025/2026).
- **Compatibility boundary:** macOS Apple Silicon + Intel DMGs; Windows
  10/11 zip; in-app update feed and licensing/activation stay unchanged.
