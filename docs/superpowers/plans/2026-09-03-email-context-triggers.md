# Email Context Triggers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically apply ByteProof's Email Editing context to selected email/webmail text and let users customise app-to-context triggers.

**Architecture:** A small resolver module reads frontmost app metadata (and, on macOS, the active browser URL), matches it against persisted rules, and returns a context. The generic polishing path then selects the email prompt and passes the resolved context to the provider.

**Tech Stack:** Python 3.13, PyQt6, pyobjc, pytest.

**Spec:** docs/superpowers/specs/2026-09-03-email-context-triggers-design.md

## Global Constraints

- Python 3.13 and existing dependencies only; no new runtime packages.
- Prompt files in `prompt/` are canonical; after editing run
  `python scripts/embed_prompts.py`.
- Follow existing GUI and defensive exception-handling style.
- Do not change Word proofreading behaviour.

---

### Task 1: Automation resolver module

**Files:**
- Create: `src/automation.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces:
  - `EMAIL_CONTEXT: str`
  - `DEFAULT_AUTOMATION_RULES: list[dict[str, str]]`
  - `resolve_automation_context(target: dict[str, Any], settings: dict[str, Any]) -> str | None`
  - `source_matches(target: dict[str, Any], source: str, browser_url: str = "") -> bool`
  - `source_display_label(source: str) -> str`

- [ ] **Step 1: Write failing tests**

Add tests for explicit bundle, URL, name, plain inference, disabled state, and
default rules.

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_smoke.py -q`
Expected: new tests fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/automation.py`**

Implement constants, source inference, rule matching, and
`resolve_automation_context`. Lazy-import `get_generic_editor` for browser URL.

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_smoke.py -q`
Expected: new resolver tests pass; existing tests unchanged except the known
pre-existing context-manager failure, which is fixed in Task 6.

- [ ] **Step 5: Commit**

```bash
git add src/automation.py tests/test_smoke.py
git commit -m "feat: add email automation context resolver"
```

---

### Task 2: Browser URL detection

**Files:**
- Modify: `src/generic_editing.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: existing `GenericTextEditor` class.
- Produces:
  - `GenericTextEditor.browser_url(self, target: dict[str, Any]) -> str`
  - `GenericTextEditor._mac_browser_url(bundle_id: str) -> str`

- [ ] **Step 1: Write failing tests**

Test that `browser_url` returns an empty string on unsupported platforms and
that the macOS AppleScript map contains expected bundles. Avoid live network.

- [ ] **Step 2: Run tests to verify they fail**

Expected: `GenericTextEditor` has no `browser_url`.

- [ ] **Step 3: Implement URL detection**

Add bundle-to-AppleScript mapping and a time-bounded subprocess call.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/generic_editing.py tests/test_smoke.py
git commit -m "feat: add browser URL detection for webmail"
```

---

### Task 3: Email prompt assets and loader

**Files:**
- Add: `prompt/context_email.txt`
- Add: `prompt/polish_email.txt`
- Add: `prompt/polish_email_creative.txt`
- Modify: `src/logic.py`
- Regenerate: `src/prompt_data.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces:
  - `logic.load_polish_prompt(style: str, context: str = "General Editing") -> str`
  - `logic.load_context_overlay("Email Editing") -> str`

- [ ] **Step 1: Write failing tests**

Assert email prompts are selected for `Email Editing` and embedded prompt
files match their source files.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add prompt files**

Draft concise, professional email editing prompts that preserve intent, use
surrounding context, and follow ByteProof output rules.

- [ ] **Step 4: Extend loaders**

Map `Email Editing` to `context_email.txt` and add email branches to
`load_polish_prompt`.

- [ ] **Step 5: Regenerate embedded prompts**

Run: `python scripts/embed_prompts.py`

- [ ] **Step 6: Run tests to verify they pass**

- [ ] **Step 7: Commit**

```bash
git add prompt src/logic.py src/prompt_data.py tests/test_smoke.py
git commit -m "feat: add email editing prompt templates"
```

---

### Task 4: Automatic context selection in generic polish

**Files:**
- Modify: `src/logic.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `resolve_automation_context`.
- Produces: `polish_selection_once` now passes a resolved `context` and email
  `system_prompt_override` to `proofread_with_provider`.

- [ ] **Step 1: Write a mocked end-to-end test**

Fake a Mail target and provider; assert the provider receives
`context == "Email Editing"` and an email system prompt.

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement selection**

Resolve automation context inside `polish_selection_once` and use it.

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/logic.py tests/test_smoke.py
git commit -m "feat: auto-select email context in generic polishing"
```

---

### Task 5: Settings Automation page

**Files:**
- Modify: `src/gui.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces:
  - `SettingsDialog.init_automation_tab()`
  - `SettingsDialog.get_settings()` persists `automation.enabled` and
    `automation.rules`.

- [ ] **Step 1: Write failing GUI tests**

Assert sidebar count is 6, the Automation page contains default rules, and
get_settings returns edited rules.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add page and widgets**

Add imports, class attributes, `init_automation_tab`, add/remove dialogs, and
settings persistence.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/gui.py tests/test_smoke.py
git commit -m "feat: add automation trigger settings page"
```

---

### Task 6: Settings defaults and test harness fix

**Files:**
- Modify: `src/settings.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Produces: `load_runtime_settings` includes a safe `automation` default.

- [ ] **Step 1: Add automation default**

Default `enabled=True` and rules from `DEFAULT_AUTOMATION_RULES`.

- [ ] **Step 2: Fix the existing fake response**

Add `__enter__` and `__exit__` to the test's `FakeResponse`.

- [ ] **Step 3: Run full suite**

Run: `QT_QPA_PLATFORM=offscreen python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add src/settings.py tests/test_smoke.py
git commit -m "test: fix urlopen fake and add automation defaults"
```
