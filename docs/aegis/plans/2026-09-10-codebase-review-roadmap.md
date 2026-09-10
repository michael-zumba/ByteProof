# ByteProof full-app review & improvement roadmap

**Status (2026-09-10):** implemented in `2.0.2-beta.1` — see the checkpoint at
`docs/aegis/work/2026-09-09-live-proofread-preview/20-checkpoint.md` and the
section *Implementation status* at the end of this document for what shipped
and what is deliberately deferred.

**Date:** 2026-09-10 · **Version reviewed:** `2.0.1-beta.2` · **Method:** full read-only
sweep of `src/` (~18k lines) by the owner's agent plus five parallel subsystem
reviewers (engine, live preview, UI/UX, Word/platform, business/security), with
independent verification of every headline claim. No files were changed; the app,
tests, Word and the network were not exercised.

Release-policy reminder (`AGENTS.md`): every change ships as a beta first;
official releases only on the owner's explicit instruction.

---

## Executive summary — the twelve things worth doing

| # | Action | Impact | Effort | Refs |
| --- | --- | --- | --- | --- |
| 1 | Remove the developer-email license path from shipped builds (public support address = free permanent license, and a web page can trigger it) | **Critical** | S | §1.1 |
| 2 | Fix beta version parsing so testers are offered the next release | High | S | §2.1 |
| 3 | Verify updates before auto-installing (feed SHA-256 + signature/host checks) | High | M | §1.2 |
| 4 | Make the live panel respect the paywall | High | S | §1.3 |
| 5 | Word: exception-safe Track-Changes suspend/restore + AppleScript timeout + real error logging | High | S/M | §2.2–2.4 |
| 6 | Guarded, stacked undo; stop the clipboard-restore race in `_mac_replace` | High | S/M | §2.5–2.6 |
| 7 | Get the 350 ms AX poll and apply sleeps off the GUI thread | High | L | §3.1 |
| 8 | Detect truncated/partial proofreads before auto-applying to Word | High | M | §4.1 |
| 9 | Port live preview to Windows (the flagship feature is macOS-only today) | High | M/L | §6.1 |
| 10 | License + Updates into the Settings sidebar; personal onboarding checklist | High | S/M | §5.1–5.2 |
| 11 | Move secrets to the keychain, stop logging user text, rotate logs | High | M | §1.4 |
| 12 | CI: run the tests, pin dependencies, verify `APP_VERSION` matches the tag | Med | S | §7.1 |

Everything else is detailed below, grouped by theme, with file references so each
item can be picked up independently.

---

## 1. Security, licensing and privacy

### 1.1 CRITICAL — the public support address is a master license key

- `SUPPORT_EMAIL = "bytemind.nz@gmail.com"` (`src/settings.py:20`) and
  `DEVELOPER_EMAILS = ("bytemind.nz@gmail.com",)` (`src/settings.py:37-39`) are
  the same string.
- `activate_with_key()` treats any email-shaped input as a license: if it matches
  `DEVELOPER_EMAILS` it calls `activate_dev_license()` and writes a permanent
  `provider: "dev"` license (`src/activation.py:135-144`, `src/licensing.py:397-414`).
- That address is public: printed in the Store listing
  (`packaging/windows/store-listing.md:43`) and shown in the app's own purchase
  help text (`src/gui.py:293`).
- `byteproof://activate?email=…` is parsed by `activate_from_url()`
  (`src/activation.py:257-275`), the scheme is registered on Windows
  (`src/activation.py:287-299`), and `main.py` processes such URLs automatically
  at launch (`src/main.py:101-106`).

Net effect: anyone who knows the support email can license ByteProof permanently
for free — by typing it, or just by visiting a page that links
`byteproof://activate?email=bytemind.nz@gmail.com`.

Fix (small): delete the email branch from release builds (gate on an env var or a
build-time constant), keep developer access in a local-only build, and make URL
activation require an explicit confirmation dialog showing what is being activated.
Add a test asserting that shipped settings contain no developer emails.

### 1.2 Update installs are unverified

- The feed names download URLs; `download_update()` fetches whatever it is told,
  with no checksum, size or host validation (`src/app_version.py:49-86`). The live
  feed has no `sha256` field.
- The in-app installer attaches the DMG with `hdiutil … -noverify`
  (`src/gui.py:5864-5868`), copies the app over `/Applications` with `ditto`
  (`src/gui.py:5911`), and relaunches — no signature, notarisation or hash check
  (`src/gui.py:5855-5925`).

macOS is partially protected by Developer-ID notarisation, but the Windows
artifact is an unsigned ZIP. Fix: publish per-artifact `sha256` (and ideally an
Ed25519 signature over the feed itself), verify before installing, allowlist
`https://github.com/michael-zumba/ByteProof/` and `https://www.bytemind.co.nz/`,
and sign the Windows build.

### 1.3 The live panel bypasses the paywall

`src/live_service.py` never calls `get_access_status()`; usage is only counted on
the manual hotkey path (`src/gui.py:5391` → `src/licensing.py:572`). Live preview
defaults to on (`src/settings.py:208-214`) and the free tier silently falls back to
the local model rather than blocking (`src/logic.py:2089-2094`). After the trial
expires, a user can keep getting unlimited local proofreads through the panel.

Fix: one access check in `_sample`/`_spawn_preview` mirroring the manual path, with
the same free-mode counter and messaging.

### 1.4 Secrets and user text in plaintext

- Provider API keys live in `settings.json` (`providers.<name>.api_keys`), confirmed
  in clear text on this machine. No `chmod` is applied anywhere except the model
  downloader (`src/local_model.py:540`).
- `capture.log` records full before/after user spans (`src/live_service.py:836`, plus
  fragments in `src/generic_editing.py:101-123`), and `citation-mapping.log` stores a
  200-character "redacted" preview of document text (`src/logic.py:498-521`).
- `error.log` (`src/main.py:29-33`) and `citation-mapping.log` are **not** in
  `LOG_PATHS` (`src/cache_cleanup.py:26-33`), so they grow unbounded.
- The license payload is already written to the macOS Keychain — but the secret is
  passed in `argv` (`src/licensing.py:47-62`), visible to `ps` for a moment.

Fix: reuse the existing secure-store helpers for API keys (file fallback kept), log
lengths/hashes instead of text, `chmod 0600` on settings/license writes, add the two
missing logs to cleanup, and pass the keychain secret via stdin (`security -i`).
Also document honestly that activation sends hostname/fingerprint to Polar
(`src/polar.py:62-74`) while the Store listing implies nothing leaves the machine
(`packaging/windows/store-listing.md:19`).

### 1.5 Revoked licenses are never detected (dead code)

`validate_license_remote()` returns `{"ok": False, "error": …}` for revoked or
disabled keys (`src/activation.py:222-231`), but the GUI handler tests
`result.get("valid") is False` (`src/gui.py:4588`) — a key that never exists — so the
warning never fires and refunded keys keep working. Fix the contract on one side
(`not result.get("ok")`) and decide on a grace-period policy.

### 1.6 Lower-priority licensing notes

- `license.json` is self-asserting: the `polar` branch needs only a truthy
  `activation_id`, and omitting `machine_fp` skips the device check
  (`src/licensing.py:438-447`). Python is patchable anyway; the pragmatic hardening
  is periodic online re-validation plus a signed payload.
- Trial/clock rollback is user-resettable (`.trial_start`, `usage.json`); the
  duplicated defaults/registry copy already helps (`src/licensing.py:697-727`).
  Time-box this — it is not the revenue leak that §1.1 is.
- No refund/trial-policy text exists anywhere in the repo; add it to the README and
  the License tab.

---

## 2. Document safety and correctness

Prime directive: never corrupt the user's document; refusing beats a bad edit.

### 2.1 Beta versions are invisible to the updater (verified by execution)

`_parse_version()` keeps only digit-only dot segments (`src/app_version.py:20-22`):

| string | parsed |
| --- | --- |
| `2.0.1` | `(2, 0, 1)` |
| `2.0.1-beta.2` | `(2, 0, 2)` |
| `2.0.2` | `(2, 0, 2)` |

So on the current beta, `_parse_version("2.0.2") > _parse_version("2.0.1-beta.2")`
is `False`: **the owner's machine and every tester on a beta will never be offered
2.0.2.** Additionally `tools/bump_version.py:83` rejects `-beta.N`, so beta versions
are hand-edited and `version_info.txt` can silently drift from `src/settings.py:16`.

Fix: parse with `packaging.version.Version` (add `packaging` to `requirements.txt`
**and** to `hiddenimports` in both `.spec` files — it is not bundled today), or write
an explicit parser that treats `2.0.1-beta.2 < 2.0.1`. Accept betas in the bumper and
add a test asserting `APP_VERSION` matches `version_info.txt`.

### 2.2 Word: Track Changes can be left OFF

`apply_live_edit` disables revisions, performs the write, and restores the flag in a
*separate* `try` block (`src/word_integration.py:396-418`). If `set content of r`
fails — read-only document, Protected View, Word busy — the script aborts before the
restore, silently leaving the document with Track Changes disabled. The same shape
appears at `:433-444` and `:493-503`.

Fix: one `try … on error … restore … end try` around the whole body, persist the
original flag, and restore it on next launch if a crash intervened.

### 2.3 Word: no timeout on AppleScript, and failures are invisible

`_run_applescript` calls `subprocess.run` with no `timeout` (`src/word_integration.py:356-380`).
A Word modal dialog (file in use, Protected View, password, consent) blocks the
worker forever with no way to cancel. Meanwhile `word_integration.py` has 22
`print()` sites and no logging, and both builds ship `console=False`
(`ByteProof.spec:56`, `ByteProof_win.spec:59`) — so "Error deleting range",
"Write denied" and friends go nowhere, and the Windows path swallows every exception
so callers see success (`src/logic.py:770-797` returns `True`).

Fix: bounded timeout plus kill and a "Word is busy" message; route Word failures into
`capture.log`/`error.log`; return structured failures instead of `True`.

### 2.4 Word: the flagship path is the least guarded

The live AX path now confirms ranges and refuses when the text changed; the Word path
has none of that. `_apply_abs` ignores `before_text` for Word
(`src/live_service.py:1062-1072`), `apply_live_edit` writes
`set content of r to (the clipboard as text)` and returns success on exit code 0 with
no read-back (`src/word_integration.py:396-418`). `insert_at_position` rewrites the
1-character range at the insertion point (`src/word_integration.py:707-716`), so a
footnote reference, inline image or cell mark there can be destroyed. Nothing checks
that the active document is still the one the offsets came from — the document-name
helpers exist but have no production caller (`src/word_integration.py:512-530`).

Fix: read the range before writing and skip when it does not hold the expected text;
read back after writing; capture the document name at preview time and refuse if the
active document changed. This is the same "refusal-first" discipline the AX path got
in `2.0.1-beta.1/2` — port it to Word.

### 2.5 Undo can overwrite shifted text

`_perform_undo` replays stored absolute offsets with `allow_direct_paste=True` and no
`before_text` (`src/live_service.py:1033-1039`). Scenario: apply at offset 100, type
five characters earlier in the document, press Undo within the 10-second window — the
original text is pasted over whatever now sits at those offsets. There is one undo
slot and a 10 s window (`src/live_service.py:52,982-994`).

Fix: pass `before_text=<the text that was applied>` and drop `allow_direct_paste=True`
in that branch, refuse with an honest message on mismatch, and keep a small stack of
undo steps.

### 2.6 The clipboard-restore race (full-selection paste)

`_mac_replace` copies the replacement to the clipboard, activates the target, posts
⌘V, sleeps 0.4 s, then restores the saved clipboard in a `finally`
(`src/generic_editing.py:1333-1346`). Nothing verifies the target actually consumed
the paste. On a slow app the pasteboard can be restored first, so the app pastes the
user's **old clipboard contents** into the document — while the UI reports
"Applied — please check" (`src/live_service.py:1361-1376`) and undo then refuses
because the selection no longer matches (`src/live_service.py:1013-1018`). This is the
clearest remaining corruption path. The same code path also destroys non-text
clipboard flavours (images, files, rich text) permanently
(`src/generic_editing.py:186-206`).

Fix: after ⌘V, poll the AX value/selection until the paste is observed (bounded, e.g.
1.5 s) before restoring; if unverified, report failure rather than success; preserve
or warn about rich clipboard flavours.

### 2.7 Other correctness items

- **Windows live apply is unimplemented but currently unreachable.** `LivePreviewService`
  is constructed only under `platform.system() == "Darwin"` (`src/gui.py:3894-3897`), and
  `WindowsWordIntegration` never implements `apply_live_edit` (base raises at
  `src/word_integration.py:62-65`). So this is *latent*, not a shipped crash — but it is
  the first thing a Windows port must implement. (Reviewer claim corrected here after
  verification.)
- **`_apply_all` delta poisoning** — offsets advance only on `ok` (`src/live_service.py:1255-1267`);
  an app that applied the write but failed verification leaves later spans shifted. Abort
  and re-sync on first failure.
- **Latent wrong-offset AX write** — the parameterized replacement passes raw code points
  while every safe path converts to UTF-16 (`src/generic_editing.py:1080-1081` vs
  `:1049-1052`) and returns success unverified. It is inert on the shipped PyObjC 12.2.1
  (the symbols are absent) but would activate silently on a future PyObjC. Convert, and
  verify before trusting.
- **Secure fields** — no `AXSubrole`/`AXSecureTextField` check; `EDITABLE_ROLES` includes
  `axtextfield` (`src/generic_editing.py:31-33`), so a selection in a password field
  could be sent to the provider.
- **Comment posting is blind** — macOS posts via a localized menu click with keystroke
  fallbacks that "succeed" even when Word ignores them, after which ⌘V types the comment
  text into the document body (`src/word_integration.py:1140-1210`). Verify a comment
  exists at the range before pasting.
- **Partial apply reported as success** — the classic loop returns `True`
  (`src/logic.py:770-797`); macOS downgrades some Word errors to a printed warning
  (`src/word_integration.py:1212-1218`).

---

## 3. Reliability and responsiveness

### 3.1 The poll, and the apply, run on the GUI thread

`_poll` is a `QTimer` owned by the main window (`src/live_service.py:185-188`, parented
at `src/gui.py:3897`). Every 350 ms (800 ms in Word) it performs an AX subtree search
(`src/generic_editing.py:535-585`) and, in Word, a synchronous `osascript` with no
timeout. `_mail_is_composing` adds two more 2-second AppleScripts
(`src/live_service.py:520-544`). The apply path adds 0.12×6 s of range-confirmation
waits plus 0.35/0.3/0.4 s sleeps (`src/generic_editing.py:1146,1261,1271,1287`), and
Word "Apply all" performs an AppleScript read-back per span. A busy Word therefore
freezes ByteProof.

Fix: move selection reading and AppleScript into a worker with a request/response
queue (or a bounded `QThreadPool` job per poll with results marshalled back by signal),
add a timeout, and skip the next tick after a slow read. This also fixes the manual
flow's `time.sleep(0.5)` × 2 on the UI thread (`src/gui.py:5716-5729`) and the
worst-case probe of every running app at ~0.25 s each (`src/gui.py:4075-4103`).

### 3.2 Thread-crossing widget calls (Windows)

`pynput` invokes hotkey callbacks on its listener thread (`src/hotkeys.py:61-63`); the
proofread hotkey calls `self._on_proofread_hotkey()` directly (`src/gui.py:4237`) and the
Esc monitor writes `self.status_label.setText(...)` directly (`src/gui.py:4503-4506`).
Qt requires widget access on the GUI thread; only the final `emit` (`src/gui.py:3978`)
is safe. Route every callback through a signal or `QTimer.singleShot(0, …)`.

### 3.3 A second global hotkey manager for Esc

`src/gui.py:4514` builds `HotkeyManager({"<esc>": on_escape})` while the primary manager
is live (`src/gui.py:4247`), producing duplicate global monitors; bare Esc then fires on
every Escape press in every app. Use one manager and add/remove the binding.

### 3.4 Local-model server has no concurrency guard

`start_local_server()` has no lock (`src/local_model.py:757-769`) and there is no
`threading.Lock` in the module. The live-preview worker and the manual proofread worker
can both call `resolve_provider_connection()`, which calls it — spawning two
`llama-server` processes where the second overwrites `self.process` and orphans the
first (only `self.process` is ever stopped), and both writing the same `.part` download
file (a SHA-256 mismatch then deletes a multi-GB download). Fix: a module lock around
server start/stop and a download lockfile.

Related: local startup ignores cancellation and reports no progress
(`src/logic.py:65` passes neither `cancel_event` nor `progress_callback`), and the local
provider's 8192-token output cap cannot fit alongside the 8192-token total context
(`src/settings.py:70` vs `src/local_model.py:639`).

---

## 4. AI quality, cost and latency

### 4.1 Truncated replies can be auto-applied

`_request_completion` never inspects `finish_reason` or `usage`
(`src/logic.py:1130-1140`; no occurrences anywhere), and `auto_apply` defaults to
`True` (`src/settings.py:180`). A reply cut off at the token limit yields a
partially-corrected text whose similarity score lands mid-range — which currently only
appends a warning yet still applies (`src/logic.py:1834-1856`). Fix: treat
`finish_reason == "length"` as a hard failure, compare output/input length ratios, and
refuse auto-apply when the output is materially shorter. Add a similarity floor to
segment reassembly (`src/logic.py:576-605`) for the same reason.

### 4.2 Two full LLM calls per proofread

Every non-free proofread makes a second full-text call to generate a "Language review"
(`src/logic.py:1703-1724` → `generate_comment` at `:1263-1351`) purely to inject as
guidance into the proofread prompt (`src/logic.py:1175-1181`) — even when the user has
set comment type to "None" (`src/settings.py:186`). Fix: skip when no comment will be
inserted, or merge the instruction into the single proofread call. Also apply
`_clean_local_model_output` in `generate_comment` (`src/logic.py:1347`) so local-model
`<think>` artifacts cannot leak into the prompt.

### 4.3 Cost and latency of the live path

Pre-LLM latency is roughly `max(2 × poll, delay_ms)` — about 1.05 s normally and 1.6 s
in Word — because of the two-read selection confirmation
(`src/live_service.py:338-359`) and the 900 ms delay (`src/live_preview.py:30`). The
preview cache key includes 200-char contexts (`src/live_preview.py:35,370-378`), so the
same sentence in two places re-calls the model. Fix: key on bundle + text with context
as a hint, cache negative ("no edits") results, and consider trimming one confirmation
read.

Also: no token accounting anywhere, prompts re-read from disk on every call
(`src/logic.py:883-895`), and multiple API keys are not rotated on HTTP 429
(`select_api_key` picks once per call, `src/utils.py:35-43`; the retry reuses the same
key, `src/logic.py:832-871`).

### 4.4 Quality opportunities

- **Structured edits**: the preview prompt asks for free-text `reason` only
  (`prompt/preview_edits.txt:10`); no severity/category/confidence field exists. A fixed
  enum plus severity would allow "errors only" filtering, ranking, and per-category
  reporting (`src/live_preview.py:37,124` caps at 12 edits in model order).
- **Deterministic post-checks**: the prompt bans em dashes and filler
  (`prompt/phd_proofreader.txt:6`) but nothing enforces it in code.
- **Context routing**: for Word, `load_proofreading_prompt` always uses the PhD/academic
  base prompt (`src/logic.py:897-900`) and appends a context overlay
  (`:912`) — so Email/General settings in Word still get an academic persona. Adding
  non-academic base prompts would match the UI's own context selector. (Reviewer claim
  softened after verification: the overlay *is* applied.)
- **Terminology/glossary**: "consistent terminology" is currently only a prompt wish
  (`prompt/phd_proofreader.txt:19`); a per-project glossary plus a document-level
  consistency pass would be genuinely differentiated.
- **Learn from the user**: accept/reject decisions are discarded
  (`src/live_service.py:172,818-843`). Persisting `(before, after, reason, accepted)`
  locally enables a personal style profile and suppression of repeatedly rejected rules.

---

## 5. User experience

1. **The main window is behind its own live panel.** The review surface is a read-only
   `QTextEdit` (`src/gui.py:3833`) with one batch "Apply Changes"
   (`src/gui.py:6069-6080`) and Cmd+Return (`src/gui.py:4669-4673`). Add a suggestion
   queue with per-row Accept/Reject/Skip, J/K navigation and Accept-all (High, L).
2. **License and Updates are hidden** behind two unlabelled 34×34 icon buttons
   (`src/gui.py:1129-1155`); the sidebar lists only General/Automation/Connect/Local AI
   (`src/gui.py:1119-1121`), and activation is a bare `QInputDialog.getText`
   (`src/gui.py:3452-3460`). This is the paying-user moment (High, S).
3. **First run interrupts five times in three seconds** — permission (800 ms), welcome
   and API key (500 ms), trial (1200 ms), license validation (1800 ms), update check
   (3000 ms), each modal (`src/gui.py:3942-3947`). Replace with one onboarding checklist
   that turns green as prerequisites are met (High, M).
4. **Slow operations show one static string and no cancel.** The floating pill already
   does elapsed time, provider and cancel well (`src/gui.py:428-498`); the main window
   should mirror it (Med, S).
5. **Diff rendering dims the document.** `display_diff` passes `context=100000`
   (`src/gui.py:6746`), so nothing truncates, yet unchanged text keeps
   `CONTEXT_STYLE = #5F6368` (`src/ui_theme.py:32`) — the whole manuscript renders in
   low-contrast grey with only edits coloured. Use full-contrast text for context, add a
   colour-blind-safe variant, and consider side-by-side (Med, S/M).
6. **No dark mode, no accessibility metadata, no i18n.** The palette is deliberately
   forced light (`src/main.py:78-92`); `src/ui_theme.py:12-36` tokens are only used by
   the diff renderer; there is no `setAccessibleName`/`setBuddy`/`setTabOrder`, no
   `tr()`, 12 `setFixedSize` calls, and window geometry is hardcoded
   (`src/gui.py:3659`) with no `saveGeometry`/`restoreGeometry`. Cheap subset now:
   persist geometry, `setMinimumSize`, accessible names on primary controls.
7. **Windows polish gaps**: the live-preview checkbox and "waiting…" row remain visible
   although the service is never built (`src/gui.py:1386-1398,3767`); hotkey failure
   reports "Ready" because `has_permission()` is hardcoded `True`
   (`src/hotkeys.py:78-79`); tray "Open Log Folder" shells out to macOS `open`
   (`src/gui.py:6606-6611`); a downloaded Windows ZIP is handed to
   `webbrowser.open("file://…")` (`src/gui.py:5948`).

---

## 6. Product strategy and growth

### 6.1 Windows live preview is the biggest product-parity gap

`src/gui.py:3894-3896` builds the live service only on macOS, and
`selection_details` returns `{}` off Darwin (`src/generic_editing.py:818-819`), so on
Windows the flagship feature does not exist outside the manual flow. `uiautomation` is
already a dependency and the Windows read/replace primitives exist for the manual path,
so a port is tractable — and it is the highest-value feature investment available.

### 6.2 Do not fight the incumbents on prose polish — own manuscript-level QA

A survey of the academic AI-writing market (Grammarly, Paperpal, Writefull, Trinka,
SciSpace) shows all of them compete on grammar/style/paraphrase, and none does
claim-level editorial work — "does this citation support this claim", "what will a
reviewer object to", "is the argument coherent paragraph to paragraph" ([survey](https://seandavi.github.io/scriptorium/concepts/knowledge/prior-art/ai-writing-tools-survey/)).
Writefull is owned by Digital Science (which also owns Dimensions) and still ships
prose-level editing only. Browser extensions structurally cannot do document-level
work; ByteProof's Word integration can. Highest-differentiation additions:

- **Pre-submission compliance check** (the Penelope.ai model): word limits, required
  sections, unresolved cross-references, heading-numbering gaps, reference-list vs
  in-text citation mismatches, duplicate references — transparent, linked to the text,
  which the existing diff UI already supports.
- **Citation–claim alignment**: flag sentences whose cited source does not appear to
  support the assertion (cheap: sentence + reference entry only).
- **Document-level consistency audit**: terminology drift, abbreviation first-use,
  undefined acronyms, tense/voice drift, unit and number formatting.

### 6.3 Sell authenticity and inspectability

A Cactus survey of 1,440 academics found over half worry AI "might compromise
authenticity or alter their unique perspectives" ([Paperpal/Cactus](https://cactusglobal.com/paperpal/)).
ByteProof's conservative, tracked-changes posture is the right answer but is currently
implicit. Make it explicit: an **exportable edit report** (what changed, why, by
category, plus what was deliberately left alone) that an author can show a supervisor
or co-author, and a conservative/balanced/polish control with a preview before applying.

### 6.4 Remove the bring-your-own-key wall

`BYTEMIND_SETUP.md:53` states a managed cloud/credits option is intentionally absent.
Today a buyer must create a DeepSeek account, add credit and paste a key
(`src/logic.py:47-65`). For the target user that is the largest drop-off between
download and first success. A **ByteProof Cloud** tier with bundled credits (metered,
capped) alongside BYOK and the local model converts one-time purchases into recurring
revenue and gives visibility into real usage. The provider layer already abstracts the
endpoint (`resolve_provider_connection`), so this is mostly a server-side addition plus
a pricing decision.

### 6.5 Institutions, and instrumenting the funnel

- University labs, libraries and thesis offices buy per seat and care about the local
  model/privacy story: prepare an education/institutional price sheet, a seat-count
  license, and a one-page "your text never leaves your machine with Local AI" summary
  for ethics committees.
- Nothing is measured today: `usage.json` holds only `{date, count, total, trial_count}`
  locally (`src/licensing.py:568-583`) and never leaves the machine; there is no
  analytics or email capture anywhere. Add **opt-in**, consented counters (version, OS,
  provider type, success/failure reason, app-bundle bucket — never text) plus a review
  prompt after N successful applies. Even a few dozen data points would replace guesswork
  in prioritisation.
- Price is hardcoded in the UI (`src/gui.py:3133`) while Polar owns pricing
  (`BYTEMIND_SETUP.md:94-96`) — a mismatch waiting to confuse customers.

---

## 7. Engineering process and maintainability

1. **CI does not test anything.** `.github/workflows/build-release.yml` only
   pip-installs and runs PyInstaller on tag — no `pytest`, no lint, no
   `APP_VERSION == tag` check. Add a test job on push/PR, gate the build on it, and
   assert the version constants match the tag.
2. **Dependencies are unpinned** — `requirements.txt` has bare names (only
   `cryptography>=41.0.0`) and mixes runtime with build tooling (PyInstaller). Pin
   versions or add a lock file, and split `requirements-dev.txt`.
3. **`src/gui.py` is 6,772 lines** with 28 classes, 11 `QThread` subclasses and ~117
   `.connect()` calls; worker lifetime is managed ad hoc (`_find_owner_window`,
   `src/gui.py:6759-6772`), `update_check_worker` is overwritten each run
   (`src/gui.py:5783`), and `ToastNotification` is created parentless
   (`src/gui.py:3891`). Extract `ui/workers.py` and `ui/dialogs/` incrementally and add
   a small worker registry.
4. **Test coverage is real but narrow.** 229 tests pass, concentrated on the live path.
   Uncovered and risky: the undo-after-further-edits case, `_apply_all` delta poisoning,
   clipboard-flavour preservation, off-screen remembered panel position, Esc/close
   stickiness while a preview is in flight, and the parameterized AX branch.
5. **Log hygiene**: `error.log` and `citation-mapping.log` are not rotated; `print()`
   in a windowed build goes nowhere (see §2.3).
6. **Housekeeping**: the MSIX version takes all four components of
   `filevers=(2,0,1,2)` (`packaging/windows/build-msix.ps1:82-84`) while the Microsoft
   Store requires the fourth to be 0 — verify before the next Store submission. Add
   `CHANGELOG.md` generated from the feed's `release_notes`. Back up `settings.json` and
   add `settings_version` before future migrations (`src/settings.py:244-294`).

---

## 8. What is already strong (protect these)

- **Refusal-first AX apply** — range confirmation, `before_text` slice check, no retry
  after the document changed (`src/generic_editing.py:1137-1160,1220-1296`).
- **UTF-16 ↔ code-point conversion** at read and write boundaries
  (`src/live_preview.py:181-206`, `src/generic_editing.py:867-888,1043-1052`).
- **Citation, field-code and math protection** with bounded, cancellable tracked-deletion
  scanning (`src/logic.py:88-140,267-322,399-449`) and a table guard (`:1558`).
- **Word's own `result range` preferred over DisplayText parsing**, with nested-field
  dedupe (`src/word_integration.py:33-56,832-923`).
- **Preview cache and two-read confirmation** that make re-selection instant and stale
  results safe (`src/live_service.py:338-359,431-441,708-734`).
- **Local model downloads** with resume, SHA-256 verification and safe extraction
  (`src/local_model.py:329-400,472-486`).
- **License payload in the OS credential store** with file fallback and self-heal, plus
  RSA-PSS-4096 legacy keys (`src/licensing.py:42-170,246-321`).
- **Release discipline**: clean-tree guard, tag-exists resume, sign→notarize→staple
  order (`scripts/release.sh:71-98`, `build_macos.sh:190-205`), and the beta-first policy
  in `AGENTS.md`.
- **Defensive failure handling**: `sys.excepthook` keeps the app alive
  (`src/main.py:18-40`), diff rendering falls back to plain text
  (`src/gui.py:6753-6756`), sound cannot break proofreading (`src/sound.py:34-36`).

---

## 9. Suggested sequencing

**Now (this week, all small):** §1.1 dev-email removal · §2.1 version parsing · §7.1 CI
tests + pinned deps · §5.2 sidebar entries · §1.5 revocation check · §2.3 AppleScript
timeout and logging · §2.2 Track-Changes restore.

**Next (2–4 weeks):** §2.5 guarded undo · §2.6 clipboard race · §3.3 Esc monitor ·
§1.4 keychain + log redaction · §1.2 update verification · §2.4 Word hardening ·
§4.1 truncation guard · §5.5 diff contrast · §3.4 local-server lock.

**Then (1–2 months):** §3.1 off-thread polling · §5.1 suggestion review queue ·
§5.3 onboarding checklist · §4.2 single-call proofread · §6.5 instrumentation ·
§6.4 managed credits decision.

**Later (strategic):** §6.1 Windows live preview · §6.2 document-level compliance and
citation checks · §6.3 edit reports · §5.6 dark mode and accessibility.

---

## Appendix — review method and verification notes

- Five independent reviewers covered the engine (`logic.py`, `local_model.py`,
  prompts), the live-preview subsystem, the GUI/UX, Word/platform/packaging, and
  business/security/licensing. Each worked read-only from the code.
- Every headline finding in this document was re-checked by the owner's agent against
  the source. Where a reviewer claim did not survive verification it was corrected
  rather than dropped:
  - the Windows `NotImplementedError` in `apply_live_edit` is unreachable in shipped
    builds because the live service is Darwin-only (§2.7);
  - the claim that Word ignores the context setting is inaccurate — a context overlay
    *is* appended (`src/logic.py:912`); the real gap is the academic base persona (§4.4);
  - the missing `com.apple.security.automation.apple-events` entitlement is not an issue
    for a non-sandboxed Developer-ID build where Word automation demonstrably works, so
    no action is proposed.
- Two findings were verified by executing the code in the review environment: the
  version-comparison table in §2.1 and the plaintext API keys in §1.4.


---

## Implementation status (2.0.2-beta.1)

Shipped:

- §1.1 developer-email path removed (local configuration only), §1.2 update
  download verification (host allowlist + SHA-256), §1.3 live panel respects
  the paywall, §1.4 keychain-ready secret handling (0600 + atomic writes, log
  redaction), §1.5 revoked-licence fix.
- §2.1 pre-release-aware version comparison (beta builds now see releases),
  §2.2 Track-Changes restore on error paths, §2.3 AppleScript timeout and real
  Word diagnostics, §2.4 Word live-edit guards + Windows implementation,
  §2.5 guarded stacked undo, §2.6 clipboard-restore race fixed, §2.7 secure
  fields, parameterized AX write hardened.
- §4.1 truncated replies refused, §4.2 reviewer-comment cleaning, §4.3 prompt
  loading and similarity measurement.
- §5.2 License/Updates sidebar pages, §5.5 full-contrast review diff, plus
  window-geometry persistence and honest Windows live-settings state.
- §7.1 CI runs tests/lint/version checks, pinned dependencies.

Deferred (each needs a decision or a larger change):

- §3.1 moving the 350 ms AX poll off the GUI thread (timeouts and bounded
  waits remove the worst freezes in the meantime).
- §5.1 per-suggestion review queue, §5.3 onboarding checklist, §5.6 dark mode
  and accessibility metadata.
- §6.1 Windows live preview (now unblocked: `apply_live_edit` exists for
  Windows), §6.2 document-level compliance/citation checks, §6.4 managed cloud
  credits, §6.5 opt-in telemetry.
