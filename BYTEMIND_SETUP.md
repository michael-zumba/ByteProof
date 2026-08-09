# ByteProof — Operator's Setup Guide

ByteProof is the ByteMind Ltd edition of the proofreading app (formerly
Research Pathway / ProofPath). It is fully rebranded, Windows-ready, and
includes a fresh ByteMind license keypair.

## What has already been done

- Copied the app into `ByteMind Project/ByteProof` with a new name (**ByteProof**).
- Rebranded all code, docs, build scripts, bundle identifiers, and the license
  file to ByteMind Ltd / `bytemind.co.nz`.
- Replaced the logo with the official ByteMind mark (green monogram), including
  `logo.svg`, `logo.png`, `logo.ico`, and `logo.icns`.
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

### 1. Stripe

Done. The ByteMind Stripe payment link is live in `src/settings.py`:

```python
STRIPE_PAYMENT_URL = "https://buy.stripe.com/3cIcN50KZfX1bP3dN73Nm05"
```

The purchase button and trial-expired flow open this link directly. If you
later want to change the price, update the button label in `src/gui.py`
(`Purchase License ($35)` and the trial-expired copy).

#### Automatic activation after payment

ByteProof supports two automatic activation paths:

1. **Stripe session deep link** (recommended): set the Stripe Payment Link's
   success URL to `byteproof://activate?session={CHECKOUT_SESSION_ID}`. After
   checkout, ByteProof opens, verifies the paid session with the ByteMind
   server, and activates this computer automatically.
2. **Email/auto lookup** (needs the ByteMind server): the app's
   "I've Paid — Activate Automatically" button (also in the trial-expired
   dialog) asks for the checkout email, then POSTs it with the machine
   fingerprint to `ACTIVATION_API_URL` in `src/activation.py`:
   `https://byteproof-api.onrender.com/api/byteproof/activate`. The server must verify
   the Stripe payment and return `{"license_key": "..."}`. Send buyers a
   fallback link `byteproof://activate?email=<email>` in the fulfilment email.

**To complete path 2**, deploy the ready-made service in `server/`
(FastAPI + Stripe webhook + license generation). Use the included
`render.yaml` blueprint on Render, set the three secrets (`STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `BYTEPROOF_LICENSE_PRIVATE_KEY`), and point
the app at the Render service URL (currently
`https://byteproof-api.onrender.com`). Follow `server/README.md`.
Until that endpoint is live, path 1 (emailed key link) works fully and path 2
shows a clear "server not reachable" message.

**Activation email:** the webhook also sends a backup activation email with a
one-click link. Add SMTP secrets in Render (`BYTEPROOF_SMTP_HOST`,
`BYTEPROOF_SMTP_USER`, `BYTEPROOF_SMTP_PASSWORD`, optionally
`BYTEPROOF_SMTP_PORT`, `BYTEPROOF_SMTP_FROM`, `BYTEPROOF_SMTP_TLS`) — see
`server/README.md`. If they are missing, payments still activate in-app; the
email is skipped.

**Developer access:** set `BYTEPROOF_DEV_EMAILS` in Render to a
comma-separated list of your own email addresses. Those addresses can
activate on any number of computers with full access and no payment or
device-limit checks.

**Device limit:** each license allows **2 computers**. The server registry
(`licenses.json`) is the source of truth; a third computer is rejected until
one is deactivated. The app's Settings → License → "Deactivate This Computer"
calls `POST /api/byteproof/deactivate`, frees the slot, and removes the local
key. The license payload is also stored in the macOS Keychain / Windows
Credential Manager and re-verified (signature + machine fingerprint) on every
app launch, so editing `license.json` cannot bypass licensing.

**User-facing UX:** activation is email-based. The License tab offers
"Already Paid? Activate with Email" and never asks users for a key. Keys exist
only internally (generated server-side, stored in the app). The manual key
deep link (`byteproof://activate?key=...`) still works as a hidden support
path for ByteMind staff to recover lost activations.

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
  use require the $35 license.
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

## Rebuilding

```bash
./build_macos.sh          # Apple Silicon DMG
./build_macos_intel.sh    # Intel DMG (needs .venv_x86, see README)
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
