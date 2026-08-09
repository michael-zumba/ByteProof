# ByteProof

**ByteMind Ltd** — Dr Yuqian Zhang, 2026

## Overview

ByteProof is a desktop proofreading app that uses AI models (DeepSeek, OpenAI,
Anthropic, Google, xAI, Groq, Perplexity, and local models). It works in two
seamless modes:

- **Microsoft Word** — academic proofreading with tracked changes, reviewer
  comments, citation/math protection, and context-aware editing styles.
- **Any other app** (email drafts, browsers, editors, chat windows, etc.) —
  writing polish that replaces the selected text with the final polished
  version, no tracked changes. ByteProof reads the surrounding context
  (before/after the selection) and includes it in the prompt, so tone,
  references, and agreement are resolved accurately.

Mode is detected automatically: if Word is in front you get tracked changes;
in any other app you get the polished final text. Global hotkeys work
everywhere on macOS and Windows.

## Two ways to power ByteProof

ByteProof ships with a hybrid model so almost every buyer can use it without
touching an API key:

1. **Local AI (default)** — ByteProof downloads a small Qwen3 model (Apache
   2.0) and runs it privately on the user's computer through llama.cpp.
   Offline, no account, and still available in the limited free mode after the
   7-day trial (3 proofreads/day). The app picks a model from the user's RAM:
   Phi-4 Mini (~2.3 GB, MIT) for 8–16 GB machines — the strongest small
   English grammar model tested — Qwen3 8B (~5 GB) for 16 GB+, and Qwen3 14B
   (~9 GB) for large machines. An ultra-light Qwen3 1.7B (~1.2 GB) covers
   older 6 GB machines, with Qwen3 4B as a general/multilingual alternative.
   Downloads resume, verify SHA-256, and can be managed in Settings → Local AI.
2. **Bring your own key (advanced)** — DeepSeek, OpenAI, Anthropic, Google,
   xAI, Groq, Perplexity, or the user's own Ollama server for maximum quality
   or context.

The one-time $35 license remains the core purchase. A subscription is
deliberately not part of the core model (a managed cloud option can be added
later if needed).

### Local model engine

- `src/local_model.py` — model catalog (Qwen3 + Phi-4 Mini GGUF files with
  pinned SHA-256), hardware detection and recommendation, resumable/verified
  downloads, llama.cpp runtime bootstrap, and server lifecycle.
- The first proofread with Local AI starts the engine automatically; users can
  also pre-download from Settings → Local AI with progress bars.
- To ship a ByteProof fine-tune later, publish a GGUF and update the model
  manifest; see `training/README.md`.

## Installation for Users

### macOS

1. Download the appropriate installer from the [Releases](https://github.com/michael-zumba/ByteProof/releases) page:
   - **Apple Silicon (M1/M2/M3):** `ByteProof_Installer_AppleSilicon.dmg`
   - **Intel Mac:** `ByteProof_Installer_Intel.dmg`
2. Open the `.dmg` file.
3. Drag **ByteProof** to the Applications folder.
4. Launch the application from your Applications folder.

**Important:** On first launch, grant Accessibility permission when prompted
(System Settings > Privacy & Security > Accessibility). This is required for
hotkey support and Word integration.

### Windows

1. Download `ByteProof_Windows.zip` from the [Releases](https://github.com/michael-zumba/ByteProof/releases) page.
2. Extract the zip file.
3. Run `ByteProof.exe`.

## Development Setup

### Prerequisites

- Python 3.11+
- macOS (tested on Sequoia/Sonoma) or Windows 10/11
- `create-dmg` (`brew install create-dmg`) for macOS installers

### Installation

```bash
git clone https://github.com/michael-zumba/ByteProof.git
cd ByteProof
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Running in development

```bash
python run.py
```

## Building for Distribution

### macOS — Apple Silicon (arm64)

```bash
./build_macos.sh
```

Output: `ByteProof_Installer_AppleSilicon.dmg`

### macOS — Intel (x86_64) — cross-compile from Apple Silicon

One-time setup:

```bash
# Use a universal2 Python 3.11+ (e.g. python.org installer); the system
# /usr/bin/python3 on macOS is 3.9 and cannot run this codebase.
arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv_x86
source .venv_x86/bin/activate
pip install -r requirements.txt
deactivate
```

Then build:

```bash
./build_macos_intel.sh
```

Output: `ByteProof_Installer_Intel.dmg`

### Windows

1. Copy the project to a Windows machine with Python 3.11+.
2. Run `build_windows.bat` (or manually `pip install -r requirements.txt` then `pyinstaller --noconfirm ByteProof_win.spec`).
3. Output: `dist\ByteProof\ByteProof.exe` and `ByteProof_Windows.zip`.

## Licensing & Payments

ByteProof uses **Polar** for checkout and license keys — the same approach the
VoiceInk app uses. Polar owns the license records on its durable servers, so
there is no custom activation server whose data can be lost on redeploy.

### Customer flow

1. 7-day free trial from first launch (every feature, no credit card).
2. After the trial: limited free mode — Local AI only, 3 proofreads/day.
3. One-time **$35 license**, works on up to 2 computers.
4. Customer buys on Polar's checkout page; Polar emails them a license key and
   keeps it in their customer portal.
5. Customer opens Settings → License → "Already Paid? Activate with License
   Key" and pastes the key. The app activates with this computer's
   fingerprint; Polar enforces the 2-device limit server-side.
6. To switch machines, the customer uses "Deactivate This Computer" to free a
   slot, then pastes the key on the new machine.

### Legacy fallback (before Polar is configured)

Until `POLAR_ORGANIZATION_ID` is set in `src/settings.py`, the app keeps the
older Stripe-era behavior so existing buyers are not stranded:

- Entering the email used at checkout activates through the ByteMind server
  (`byteproof-api.onrender.com`).
- Support-issued signed keys (`tools/generate_license.py`) still work.

As soon as Polar is configured, the app switches to the license-key flow and
the old server is no longer used.

### One-time Polar setup (app owner)

1. Create an organization at [polar.sh](https://polar.sh).
2. **Benefits → New Benefit → License Keys**: brand prefix `BYTEPROOF_`, set
   **activation limit = 2**.
3. **Products → New Product**: "ByteProof License", one-time $35 NZD, attach
   the license-key benefit.
4. **Checkout Links → New Link**: select the product; set the Success URL to
   `https://www.bytemind.co.nz/byteproof` (optional).
5. Copy your organization ID (Polar → Settings) into `POLAR_ORGANIZATION_ID`
   in `src/settings.py`, and paste the checkout link into
   `POLAR_CHECKOUT_URL`.
6. Rebuild and release. Update the website buy button and FAQ to the Polar
   flow (`ByteMind_Website/byteproof.html`).

Developer-only emails that unlock full access without a key are listed in
`DEVELOPER_EMAILS` in `src/settings.py`.

## Distribution

See [GITHUB_DISTRIBUTION_GUIDE.md](GITHUB_DISTRIBUTION_GUIDE.md) for the full
release workflow.

### Accessibility Permissions (macOS)

The app needs Accessibility and Automation permissions in macOS to:
- Register global hotkeys (Cmd+Shift+; to open, Cmd+Shift+' to proofread)
- Send AppleScript commands to Microsoft Word

The app will prompt users to grant these permissions on first use. If hotkeys
stop working after an update, remove the app from System Settings > Privacy &
Security > Accessibility, then re-add it.

**Why updates ask again (and the fix):** every rebuilt app previously got a new
ad-hoc signature, so macOS treated it as a different app and forgot the
Accessibility grant. ByteProof now signs each build with a stable
self-signed identity stored in `tools/certs/` (gitignored). As long as that
certificate and the bundle ID stay the same, macOS remembers the permission
across reinstalls. The first install of a certificate-signed build may still
need one remove-and-re-add; after that it persists. Keep `tools/certs/`
backed up — regenerating the certificate resets the permission once more.

## License

This software is proprietary. See [LICENSE](LICENSE) for details.
