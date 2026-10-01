# ByteProof — Operator's Setup Guide

ByteProof is the ByteMind Ltd edition of the proofreading app (formerly
Research Pathway / ProofPath). It is fully rebranded, Windows-ready, and
includes a fresh ByteMind license keypair.

## What has already been done

- Copied the app into `ByteMind Project/ByteProof` with a new name (**ByteProof**).
- Rebranded all code, docs, build scripts, bundle identifiers, and the license
  file to ByteMind Ltd / `bytemind.co.nz`.
- Replaced the logo with the ByteProof brain mark on a white background
  (text-free), including `logo.svg`, `logo.png`, `logo.ico`, and `logo.icns`.
- Added real Windows support:
  - pynput global hotkeys (no Accessibility permission needed)
  - COM thread initialisation for Word integration
  - cross-platform settings/license/log paths
  - launch-at-login via LaunchAgent (macOS) and registry (Windows)
  - Windows updater support (`windows_url` in the version feed)
  - `ByteProof_win.spec`, `build_windows.bat`, and Windows file-version info
- Fixed bugs found during review:
  - provider Connect page no longer duplicates itself after configuring keys
  - hotkey display now matches each platform (⌘ on macOS, Ctrl on Windows)
  - update check/download no longer freeze the UI
  - per-provider `max_tokens` caps (avoids API errors on OpenAI/Groq/etc.)
  - "Launch at login" now actually works
  - hotkeys retry cleanly after Accessibility permission is granted
  - missing segment markers can no longer delete user text (falls back to original)
  - temp-file cleanup on macOS no longer leaks files (missing `os` import fixed)
- Added UX improvements:
  - "Test Connection" button in each provider's settings dialog
  - "Copy" button for the proposed corrected text
  - friendly dialogs when Word is not running / no document is open
  - status line shows which provider and model is processing
- Added "any app" editing:
  - ByteProof now detects the frontmost app automatically: Microsoft Word gets
    tracked-changes proofreading, every other app (email, browser, editor) gets
    a clean final-text polish.
  - Selections are read and replaced through the Accessibility API on macOS and
    the clipboard on Windows, with before/after verification so a changed
    selection is never overwritten.
  - A dedicated general-writing prompt (`prompt/polish_general.txt`) is used
    outside Word; the academic prompts remain for Word.
  - Context-aware: outside Word, ByteProof reads the surrounding text before and
    after the selection (Accessibility API on macOS, UI Automation on Windows)
    and feeds it to the model, matching the context behaviour Word already had.
- Type-checked with Pyright (0 errors) and covered by expanded smoke tests.
- Added **Local AI** as the default engine: ByteProof downloads a small local
  model (Phi-4 Mini by default for English grammar, with Qwen3 1.7B/4B/8B/14B
  and a proofreading-tuned 4B as options) and runs it privately through
  llama.cpp, with in-app progress, resume, and SHA-256 verification.
  Bring-your-own-key providers (DeepSeek, OpenAI, Anthropic, Google, xAI,
  Groq, Perplexity, Ollama) remain fully supported. A managed cloud/credits
  option is intentionally not included in this version.
- Stable code signing: builds are signed with a persistent self-signed identity
  (`tools/certs/`), so macOS no longer treats every reinstall as a new app and
  Accessibility permissions survive updates. See the note below.

## macOS permission persistence (read this)

macOS remembers Accessibility permission by the app's code signature. Older
ByteProof builds used a fresh ad-hoc signature each time, which is why you had
to remove and re-add ByteProof in System Settings after every reinstall.

The build scripts now sign with a stable identity: `ByteMind Code Signing`
(certificate stored in `tools/certs/byteproof_codesign.p12`, keychain created
automatically by `tools/sign_byteproof.sh`).

**One-time step:** install this newly signed build, then remove and re-add
ByteProof in System Settings > Privacy & Security > Accessibility **once**.
From then on, reinstalling or updating keeps the permission.

**Do not regenerate or delete `tools/certs/`.** The certificate is the app's
identity; losing it means granting Accessibility once more.
- Generated a new 4096-bit RSA license keypair:
  - public key embedded in `src/licensing.py`
  - private key generator in `tools/generate_license.py` (gitignored)
- Verified with an automated smoke test (`tests/test_smoke.py`) and a working
  macOS PyInstaller build (`dist/ByteProof.app`, ~90 MB).

## Still to do before selling

### 1. Licensing (Stripe + ByteProof licence service)

Stripe Checkout takes the payment and the ByteProof licence service in
`server/` issues one key per purchase and enforces the 2-computer limit. The
price (NZ$49 incl. GST) and the Payment Link live in Stripe; the app points at
the website buy section (`PURCHASE_URL` in `src/settings.py`) and the licence
service URL (`LICENSE_API_URL`, default `https://api.bytemind.co.nz`), both
overridable with `BYTEPROOF_PURCHASE_URL` / `BYTEPROOF_LICENSE_API_URL`.

If you change the price in Stripe, also update the button label in
`src/gui.py` (`Purchase License ($49)`) and the trial-expired copy to match.

#### How activation works (Stripe)

1. The customer pays on Stripe Checkout (card, Apple Pay, Google Pay, Alipay).
   Stripe emails the key, and the thank-you page shows it too.
2. In the app, Settings → License → "Already Paid? Activate with License Key",
   paste the key (or click the button in the email). The app registers this
   computer's fingerprint with the licence service.
3. The service enforces the 2-computer limit and returns a signed,
   machine-bound licence the app stores. To switch machines, "Deactivate This
   Computer" or release a slot in the licence portal ("Manage My Licences" in
   the app emails a portal link).
4. Refunds and chargebacks revoke the key; the app shows a blocking notice on
   the next online check.

Owner machines: the two `BYTEPROOF_-...` keys are configured with
`BYTEPROOF_INTERNAL_KEYS` and have no device limit.

Pre-Polar buyers: their original signed keys still activate locally (the app
verifies the signature and machine fingerprint without any server).

**Developer access:** developer emails are handled locally via
`scripts/dev_access.py` / `BYTEPROOF_DEV_EMAILS`; no public address unlocks a
shipped build.

#### Deploying the licence service

See `STRIPE_LICENSING_SETUP.md` for the full step-by-step guide (Stripe, tax,
Render, DNS, email, the website switch and day-2 support), and
`server/README.md` for the service's own reference. The short version:

1. `python scripts/stripe_setup.py --base-url https://api.bytemind.co.nz` with
   a live Stripe key (creates product, price, Payment Link and webhook).
2. Deploy `server/` to Render with a persistent disk at `/data` and the env
   vars from the README, plus a restricted Stripe key for the service itself.
3. Point `api.bytemind.co.nz` at the service and confirm `/health`.

### 2. Update feed

Host `byteproof-version.json` at `https://www.bytemind.co.nz/byteproof-version.json`.
The format is documented in `GITHUB_DISTRIBUTION_GUIDE.md`. Until it is live,
the app silently skips update checks.

## Trial enforcement

- The 7-day free trial starts on first launch (stored in the ByteProof support
  folder and in a second system location, so deleting the support folder does
  not reset the trial).
- During the trial, proofreading works normally and a warning toast appears
  when 3 days or fewer remain.
- After the trial expires, ByteProof enters a limited free mode: Local AI only,
  3 proofreads per day, and no reviewer comments. Cloud providers and unlimited
  use require the $49 license.
- Hitting the daily cap or trying a cloud provider shows a purchase dialog with
  a recap of how many selections were proofread during the trial, plus
  **Purchase**, **Activate License**, and **I've Paid — Activate Automatically**.
- Completed proofreads are counted in `usage.json` in the support folder (daily
  count, lifetime total, and trial total).

### 3. Website / GitHub release

- Add a ByteProof page to the ByteMind website (link target used by the app:
  `https://www.bytemind.co.nz/byteproof`).
- Create the GitHub repository `michael-zumba/ByteProof` (already referenced in
  `README.md`, `GITHUB_DISTRIBUTION_GUIDE.md`, and the website download links).
- Build on a Windows machine (this Mac cannot produce Windows binaries) with
  `build_windows.bat`, then upload the three release artifacts.

### 4. Licensing keys

Generate customer keys with:

```bash
python tools/generate_license.py customer@email.com unlimited
python tools/generate_license.py customer@email.com 2027-06-30
```

Keep `tools/` private — it contains the ByteMind license private key and is
already excluded from git.

## Release checklist (always bump the version)

Every shipped change — major feature, minor polish, or bugfix — must bump the
version so installed apps can see the update notification.

```bash
python tools/bump_version.py 1.6.0 "Short release notes for users..."
```

The script updates `APP_VERSION` in `src/settings.py`, the Windows
`version_info.txt` metadata, the website update feed
(`../ByteMind_Website/byteproof-version.json`), and the local example feed.
Then rebuild and distribute:

```bash
./build_macos.sh
```

Rules of thumb:

- Bug fixes and UI polish → patch bump (`1.5.1` → `1.5.2`).
- New features or behaviour changes → minor bump (`1.5.2` → `1.6.0`).
- Never ship a rebuilt app whose `APP_VERSION` matches the version already on
  the website feed; users would never be offered the update.

## Rebuilding

```bash
./build_macos.sh arm64     # Apple Silicon DMG
./build_macos.sh x86_64    # Intel DMG (needs .venv_x86, see README)
build_windows.bat         # on a Windows machine
```

## Running the tests

```bash
QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_smoke.py
```

## Verifying app/text capture

ByteProof includes a capture diagnostic. With a text selection made in the app
you want to proofread (e.g. Mail), run:

```bash
./venv/bin/python run.py --capture-test
```

(or the packaged app: `ByteProof.app/Contents/MacOS/ByteProof --capture-test`).
It prints JSON with the detected frontmost app, Accessibility permission
status, the selected text (with a preview), context lengths, and whether the
clipboard fallback was used. This is the fastest way to confirm ByteProof sees
the right app and the right text before applying anything.
