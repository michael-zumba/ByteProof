# Email Context Triggers Design

## Goal

When ByteProof reads selected text from an email app (for example Apple Mail,
Microsoft Outlook, or Gmail in a browser), it should automatically use an
email-specific editing prompt. Users should also be able to customise the
app-to-context rules in Settings, following the VoiceInk idea of trigger
groups while keeping ByteProof's existing prompt and GUI patterns.

## Current State

- `generic_editing.GenericTextEditor` already returns the frontmost app with
  `name`, `pid`, and `bundle_id` on macOS, plus `name`, `exe`, and `hwnd` on
  Windows.
- `logic.polish_selection_once` handles non-Word apps and currently loads
  `polish_general*.txt` regardless of the source app.
- `SettingsDialog` has a sidebar and pages, but no automation page.
- Prompt assets are editable in `prompt/*.txt` and baked into
  `src/prompt_data.py` by `scripts/embed_prompts.py`.

## Design

### 1. Context rules

Add `src/automation.py`:

- `EMAIL_CONTEXT = "Email Editing"`.
- `DEFAULT_AUTOMATION_RULES` maps common email sources to `Email Editing`.
- `resolve_automation_context(target, settings)` returns the matching context
  or `None`.

Rule source strings support these forms:

- `bundle:<bundle_id>` matches the macOS bundle identifier.
- `url:<domain>` matches the active browser URL.
- `name:<substring>` matches the app/window name.
- `exe:<substring>` matches the Windows executable path.
- A plain value is inferred as a URL, bundle id, or app name.

Rules are stored in `settings["automation"]["rules"]`, with
`settings["automation"]["enabled"]` controlling whether detection runs.

### 2. Browser/webmail detection

Extend `GenericTextEditor` with `browser_url(target)`. On macOS, use short
AppleScript calls for Chrome, Safari, Brave, Edge, Arc, Opera, and Vivaldi to
return the active tab URL. On Windows, the window title already carries
webmail hints such as "Gmail", so the existing `name` field is sufficient for
the default rules.

### 3. Email prompts

Add:

- `prompt/context_email.txt`, used by Word proofreading when the user selects
  `Email Editing` manually.
- `prompt/polish_email.txt`, used for precise generic polishing in email apps.
- `prompt/polish_email_creative.txt`, used for creative rewriting in email apps.

Extend `logic.load_polish_prompt(style, context)` so `Email Editing` returns
the email-specific prompt.

### 4. Automatic selection path

In `logic.polish_selection_once`, after reading the target and settings:

1. Resolve `effective_context` with `resolve_automation_context`.
2. If an automation rule matched, use that context.
3. Otherwise keep the user's `general.context`.
4. Build the system prompt with `load_polish_prompt(style, effective_context)`.
5. Pass `effective_context` to `proofread_with_provider`.

This keeps Word proofreading unchanged and only affects the non-Word path.

### 5. Settings UI

Add an "Automation" page to `SettingsDialog`, appended after "Updates" so
existing page indices stay stable:

- Checkbox "Detect email apps and webmail automatically".
- A two-column table of rules: "App or URL" and "Context".
- Add/Remove buttons.

Saving the dialog persists `automation.enabled` and `automation.rules`.

## Error Handling

- Browser URL lookups are best-effort and time-bounded.
- If no rule matches, the previous behaviour is preserved.
- If the settings file lacks automation fields, defaults are used and saved on
  the next write.
- Prompt loading falls back to the general prompt when an email prompt file is
  missing, so packaged builds without the new asset remain safe.

## Testing

- Unit tests for rule inference and matching.
- Unit tests for prompt selection.
- An end-to-end mocked generic-polish test proving a Mail target uses the
  email prompt and context automatically.
- A Settings dialog test proving the new page saves rules and enabled state.
- Existing smoke suite must pass.
