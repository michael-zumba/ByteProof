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

The one-time $20 license remains the core purchase. A subscription is
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
arch -x86_64 /usr/bin/python3 -m venv .venv_x86
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

## Stripe / Payments

The purchase button points to ByteMind's live Stripe payment link, defined in
`src/settings.py` as `STRIPE_PAYMENT_URL`
(`https://buy.stripe.com/00w7sLeBP3af6uJ24p3Nm03`).

After payment, licenses can be activated automatically:

- **Deep link**: customers click `byteproof://activate?key=...` (e.g. from a
  fulfilment email) and ByteProof activates itself.
- **In-app**: "I've Paid — Activate Automatically" verifies the checkout email
  with the ByteMind activation API (`src/activation.py`) and applies the
  returned license key.

The app enforces a 7-day free trial from first launch. When it expires,
proofreading is blocked and the user is prompted to purchase or activate.

License keys are generated with `tools/generate_license.py` using ByteMind's
private key (kept out of version control — see `.gitignore`).

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
