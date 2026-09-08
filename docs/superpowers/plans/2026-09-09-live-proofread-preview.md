# Live Proofread Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a token-efficient Grammarly-style live proofread preview to ByteProof on macOS: dashed pink-red underlines on words the AI would change, a track-changes hover popup, one-click apply, and a General-settings toggle.

**Architecture:** A poll-based `LivePreviewService` watches the frontmost app and AX selection, calls a compact structured-edit preview (Local AI by default), maps edits to character ranges, and renders them in a transparent overlay window. Word uses native in-document formatting and AppleScript sub-range apply instead of AX geometry.

**Tech Stack:** Python 3.13, PyQt6, pyobjc (AppKit/ApplicationServices/Quartz), AppleScript for Word, pytest.

**Spec:** docs/superpowers/specs/2026-09-09-live-proofread-preview-design.md

## Global Constraints

- Python 3.13 and existing dependencies only; no new runtime packages.
- macOS-only behavior in this beta; Windows/Linux paths must stay functional but may return "not supported" from new live-preview methods.
- Prompt files in `prompt/` are canonical; after editing run `python scripts/embed_prompts.py`.
- Run tests with `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_smoke.py -q`; keep the existing smoke suite green.
- Follow the existing defensive exception-handling style; new network/provider code must respect `cancel_event`.
- Branch: `codex/live-proofread-preview`.

---

### Task 1: Live-preview pure core (`src/live_preview.py`)

**Files:**
- Create: `src/live_preview.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Produces:
  - `Edit(before: str, after: str, reason: str)` frozen dataclass
  - `EditSpan(before: str, after: str, reason: str, start: int, end: int)`
  - `parse_preview_response(raw: str) -> list[Edit]`
  - `map_edits_to_ranges(original: str, edits: Sequence[Edit]) -> list[EditSpan]`
  - `preview_cache_key(bundle_id: str, text: str, context_before: str, context_after: str, fingerprint: str) -> str`
  - `settings_fingerprint(settings: dict[str, Any]) -> str`
  - `evaluate_trigger(settings, target, selected_text, has_permission, is_stable, in_flight, unchanged) -> tuple[str, str]`
  - `PreviewCache.get(key: str) -> list[EditSpan] | None`
  - `PreviewCache.put(key: str, spans: list[EditSpan]) -> None`
  - Constants: `SUPPORTED_BUNDLE_IDS`, `DEFAULT_DELAY_MS=900`, `MIN_DELAY_MS=400`, `MAX_DELAY_MS=2000`, `DEFAULT_MAX_CHARS=1500`, `MIN_PREVIEW_CHARS=8`, `CONTEXT_CHARS=200`, `PREVIEW_MAX_OUTPUT_TOKENS=512`, `PREVIEW_MAX_EDITS=12`, `POLL_INTERVAL_MS=350`, `CACHE_MAX=64`, `UNDERLINE_COLOR_HEX="#E23A5B"`.

- [ ] **Step 1: Write the failing tests**

```python
import json

from src.live_preview import (
    Edit, PreviewCache, evaluate_trigger, map_edits_to_ranges,
    parse_preview_response, preview_cache_key, settings_fingerprint,
    SUPPORTED_BUNDLE_IDS,
)


def _settings(**live):
    base = {"live_preview": {"enabled": True, "delay_ms": 900,
                             "max_chars": 1500, "use_local_model": True}}
    base["live_preview"].update(live)
    return base


def test_parse_bare_json_array():
    edits = parse_preview_response('[{"before":"teh","after":"the","reason":"Spelling"}]')
    assert edits == [Edit("teh", "the", "Spelling")]


def test_parse_fenced_object_with_garbage():
    raw = 'Here is your answer:\n```json\n{"edits":[{"before":"are","after":"is","reason":"Agreement"}]}\n```'
    assert parse_preview_response(raw) == [Edit("are", "is", "Agreement")]


def test_parse_caps_and_dedupes_edits():
    edits = [{"before": f"a{i}", "after": f"b{i}", "reason": ""} for i in range(20)]
    parsed = parse_preview_response(json.dumps({"edits": edits}))
    assert len(parsed) == 12
    assert parsed[0] == Edit("a0", "b0", "")


def test_map_exact_edits():
    spans = map_edits_to_ranges("teh cat sat", [Edit("teh", "the", "Spelling")])
    assert spans == [EditSpan("teh", "the", "Spelling", 0, 3)]


def test_map_fuzzy_typo():
    spans = map_edits_to_ranges("teh cat sat", [Edit("the", "the", "Spelling")])
    assert [(s.start, s.end) for s in spans] == [(0, 3)]


def test_map_overlap_longer_span_wins():
    edits = [Edit("cat", "dog", "a"), Edit("teh cat", "the dog", "b")]
    spans = map_edits_to_ranges("teh cat sat", edits)
    assert [s.reason for s in spans] == ["b"]


def test_map_unmappable_edit_dropped():
    assert map_edits_to_ranges("hello", [Edit("zzzz", "y", "Noise")]) == []


def test_evaluate_trigger_disabled():
    decision, reason = evaluate_trigger(
        _settings(enabled=False), {"bundle_id": "com.apple.TextEdit"},
        "hello world", True, True, False, False,
    )
    assert decision == "disabled"
    assert "settings" in reason.lower()


def test_evaluate_trigger_unchanged_selection_skips():
    decision, _ = evaluate_trigger(
        _settings(), {"bundle_id": "com.apple.TextEdit"},
        "hello world", True, True, False, True,
    )
    assert decision == "unchanged"


def test_evaluate_trigger_unsupported_app_skips():
    decision, _ = evaluate_trigger(
        _settings(), {"bundle_id": "com.example.random"},
        "hello world", True, True, False, False,
    )
    assert decision == "unsupported_app"


def test_evaluate_trigger_empty_selection_skips():
    decision, _ = evaluate_trigger(
        _settings(), {"bundle_id": "com.apple.TextEdit"},
        "", True, True, False, False,
    )
    assert decision == "empty"


def test_evaluate_trigger_runs():
    decision, _ = evaluate_trigger(
        _settings(), {"bundle_id": "com.apple.TextEdit"},
        "this sentence has a problem", True, True, False, False,
    )
    assert decision == "run"


def test_cache_hit_and_lru():
    cache = PreviewCache()
    spans = [EditSpan("a", "b", "x", 0, 1)]
    key = preview_cache_key("com.apple.TextEdit", "abc", "", "", "fp")
    assert cache.get(key) is None
    cache.put(key, spans)
    assert cache.get(key) == spans
    for i in range(70):
        cache.put(f"k{i}", [])
    assert cache.get(key) is None


def test_settings_fingerprint_only_uses_live_preview():
    a = settings_fingerprint(_settings())
    b = settings_fingerprint(_settings(delay_ms=1200))
    assert a != b
    assert a == settings_fingerprint(_settings())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'src.live_preview'`.

- [ ] **Step 3: Implement `src/live_preview.py`**

```python
"""Pure logic for the live proofread preview feature.

No Qt, no I/O, no network. Parsing, mapping, trigger decisions, and cache
live here so they can be tested without a GUI or live APIs.
"""

import difflib
import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

SUPPORTED_BUNDLE_IDS = frozenset(
    {
        "com.microsoft.word",
        "com.apple.pages",
        "com.apple.mail",
        "com.microsoft.outlook",
        "com.apple.textedit",
        "com.apple.notes",
    }
)

DEFAULT_DELAY_MS = 900
MIN_DELAY_MS = 400
MAX_DELAY_MS = 2000
DEFAULT_MAX_CHARS = 1500
MIN_PREVIEW_CHARS = 8
CONTEXT_CHARS = 200
PREVIEW_MAX_OUTPUT_TOKENS = 512
PREVIEW_MAX_EDITS = 12
POLL_INTERVAL_MS = 350
CACHE_MAX = 64
UNDERLINE_COLOR_HEX = "#E23A5B"


@dataclass(frozen=True)
class Edit:
    before: str
    after: str
    reason: str


@dataclass(frozen=True)
class EditSpan:
    before: str
    after: str
    reason: str
    start: int
    end: int


def _extract_json(raw: str) -> Any:
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    if start >= 0:
        end = cleaned.rfind("}")
        cleaned = cleaned[start : end + 1]
    else:
        start = cleaned.find("[")
        if start >= 0:
            end = cleaned.rfind("]")
            cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def parse_preview_response(raw: str) -> list[Edit]:
    """Parse a provider reply into validated, deduplicated, capped edits."""
    if not raw or not raw.strip():
        return []
    try:
        payload = _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    items = payload.get("edits") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    edits: list[Edit] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        before = str(item.get("before") or "").strip()
        after = str(item.get("after") or "").strip()
        if not before or not after or before == after:
            continue
        reason = str(item.get("reason") or "").strip()
        pair = (before, after)
        if pair in seen:
            continue
        seen.add(pair)
        edits.append(Edit(before, after, reason))
        if len(edits) >= PREVIEW_MAX_EDITS:
            break
    return edits


def _fuzzy_locate(text: str, needle: str) -> int | None:
    """Return the best word-boundary match for needle, or None below 0.82."""
    needle_words = re.findall(r"\w+", needle)
    if not needle_words:
        return None
    words = re.findall(r"\w+", text)
    best_ratio = 0.82
    best: int | None = None
    for i in range(len(words) - len(needle_words) + 1):
        candidate = " ".join(words[i : i + len(needle_words)])
        ratio = difflib.SequenceMatcher(None, " ".join(needle_words), candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = i
    if best is None:
        return None
    prefix = " ".join(words[:best])
    if prefix:
        prefix += " "
    index = text.find(prefix)
    return len(prefix) if index >= 0 else text.find(words[best])


def _locate(text: str, needle: str, cursor: int) -> tuple[int, int] | None:
    index = text.find(needle, cursor)
    if index >= 0:
        return index, index + len(needle)
    fuzzy = _fuzzy_locate(text[cursor:], needle)
    if fuzzy is None:
        return None
    span = fuzzy, fuzzy + max(1, len(needle))
    return cursor + span[0], cursor + span[1]


def map_edits_to_ranges(
    original: str, edits: Sequence[Edit]
) -> list[EditSpan]:
    """Map edits to character offsets; longest non-overlapping spans win."""
    candidates: list[tuple[Edit, int, int]] = []
    cursor = 0
    for edit in edits:
        located = _locate(original, edit.before, cursor)
        if located is None:
            continue
        start, end = located
        candidates.append((edit, start, end))
        cursor = start + 1
    candidates.sort(key=lambda c: (c[2] - c[1], -c[1]), reverse=True)
    kept: list[tuple[int, int, Edit]] = []
    for edit, start, end in candidates:
        if any(start < k_end and end > k_start for k_start, k_end, _ in kept):
            continue
        kept.append((start, end, edit))
    kept.sort(key=lambda k: k[0])
    return [
        EditSpan(edit.before, edit.after, edit.reason, start, end)
        for start, end, edit in kept
    ]


def settings_fingerprint(settings: dict[str, Any]) -> str:
    live = settings.get("live_preview", {})
    payload = json.dumps(
        {
            "enabled": live.get("enabled", True),
            "delay_ms": live.get("delay_ms", DEFAULT_DELAY_MS),
            "max_chars": live.get("max_chars", DEFAULT_MAX_CHARS),
            "use_local_model": live.get("use_local_model", True),
        },
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def preview_cache_key(
    bundle_id: str,
    text: str,
    context_before: str,
    context_after: str,
    fingerprint: str,
) -> str:
    payload = "\x1f".join([bundle_id, text, context_before, context_after, fingerprint])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def evaluate_trigger(
    settings: dict[str, Any],
    target: dict[str, Any],
    selected_text: str,
    has_permission: bool,
    is_stable: bool,
    in_flight: bool,
    unchanged: bool,
) -> tuple[str, str]:
    """Return (decision, reason). Decisions are lowercase snake_case strings."""
    live = settings.get("live_preview", {})
    if not live.get("enabled", True):
        return "disabled", "Live suggestions are off in Settings."
    if not has_permission:
        return "no_permission", "Accessibility permission is required."
    bundle = str(target.get("bundle_id", "")).lower()
    if bundle not in SUPPORTED_BUNDLE_IDS:
        return "unsupported_app", f"App {bundle!r} is not supported for live preview."
    if not selected_text or not selected_text.strip():
        return "empty", "No text selected."
    length = len(selected_text.strip())
    if length < MIN_PREVIEW_CHARS:
        return "too_short", f"Selection is shorter than {MIN_PREVIEW_CHARS} characters."
    max_chars = int(live.get("max_chars", DEFAULT_MAX_CHARS))
    if length > max_chars:
        return "too_long", f"Selection is longer than {max_chars} characters."
    if not is_stable:
        return "not_stable", "Selection is still changing."
    if in_flight:
        return "busy", "A preview is already running."
    if unchanged:
        return "unchanged", "Selection has not changed since the last preview."
    return "run", "Selection ready for preview."


class PreviewCache:
    """LRU cache mapping preview keys to mapped edit spans."""

    def __init__(self, maxsize: int = CACHE_MAX) -> None:
        self._data: OrderedDict[str, list[EditSpan]] = OrderedDict()
        self.maxsize = maxsize

    def get(self, key: str) -> list[EditSpan] | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, spans: list[EditSpan]) -> None:
        self._data[key] = spans
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: all 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/live_preview.py tests/test_live_preview.py
git commit -m "feat: add live-preview pure logic (parse, map, trigger, cache)"
```

---

### Task 2: Preview prompt asset and provider call (`src/logic.py`)

**Files:**
- Add: `prompt/preview_edits.txt`
- Regenerate: `src/prompt_data.py`
- Modify: `src/logic.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Consumes: `Edit`, `parse_preview_response` from `src/live_preview.py`; `PROMPT_FILES`.
- Produces:
  - `_request_completion(system_prompt, user_content, api_key, max_tokens, base_url, model, provider_name, temperature, cancel_event) -> str`
  - `load_preview_prompt() -> str`
  - `preview_edits_once(settings, target, cancel_event=None) -> tuple[str, list[Edit], dict[str, Any]]` returning `(status, edits, meta)` with `meta["provider"]`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest import mock

from src import logic
from src.live_preview import Edit


def _mock_completion(returned):
    def fake(*args, **kwargs):
        fake.calls.append((args, kwargs))
        return returned
    fake.calls = []
    return fake


def test_load_preview_prompt_has_json_contract():
    prompt = logic.load_preview_prompt()
    assert '"edits"' in prompt and '"before"' in prompt


def test_preview_edits_once_uses_local_model_and_parses_edits(monkeypatch):
    fake = _mock_completion('{"edits":[{"before":"teh","after":"the","reason":"Spelling"}]}')
    monkeypatch.setattr(logic, "_request_completion", fake)
    monkeypatch.setattr(
        logic, "resolve_provider_connection",
        lambda settings: (logic.LOCAL_MODEL_PROVIDER, "", "http://127.0.0.1:9999/v1", "phi4-mini"),
    )
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {"enabled": True, "use_local_model": True, "max_chars": 1500},
    }
    status, edits, meta = logic.preview_edits_once(
        settings, {"bundle_id": "com.apple.TextEdit", "name": "TextEdit", "pid": 1},
        "teh quick brown fox",
        "ctx before",
        "ctx after",
    )
    assert status == "ok"
    assert edits == [Edit("teh", "the", "Spelling")]
    assert meta["provider"] == logic.LOCAL_MODEL_PROVIDER
    assert "teh quick brown fox" in fake.calls[0][1]["user_content"]


def test_preview_edits_once_prefers_active_provider_when_configured(monkeypatch):
    fake = _mock_completion("[]")
    monkeypatch.setattr(logic, "_request_completion", fake)
    monkeypatch.setattr(
        logic, "resolve_provider_connection",
        lambda settings: ("OpenAI", "sk-test", "https://api.openai.com/v1", "gpt-4o"),
    )
    monkeypatch.setattr(logic, "get_access_status", lambda: {"tier": "licensed"})
    monkeypatch.setattr(logic, "provider_requires_api_key", lambda name: True)
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {"enabled": True, "use_local_model": False, "max_chars": 1500},
    }
    status, edits, meta = logic.preview_edits_once(
        settings, {"bundle_id": "com.apple.TextEdit"}, "hello world", "", ""
    )
    assert status == "ok" and edits == [] and meta["provider"] == "OpenAI"


def test_preview_edits_once_returns_no_changes_for_empty_reply(monkeypatch):
    monkeypatch.setattr(logic, "_request_completion", lambda *a, **k: "no issues")
    monkeypatch.setattr(
        logic, "resolve_provider_connection",
        lambda settings: (logic.LOCAL_MODEL_PROVIDER, "", "http://x/v1", "m"),
    )
    status, edits, _ = logic.preview_edits_once(
        {"general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
         "live_preview": {"enabled": True, "use_local_model": True, "max_chars": 1500}},
        {"bundle_id": "com.apple.TextEdit"}, "hello world", "", "",
    )
    assert status == "no_changes" and edits == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `AttributeError: module 'src.logic' has no attribute 'load_preview_prompt'`.

- [ ] **Step 3: Add `prompt/preview_edits.txt`**

```text
You are a precise writing editor producing a compact, machine-readable diff of the changes you would make to the author's selected text. This is a live suggestion preview, so be conservative and minimal: only correct real errors or genuinely unclear wording. Do not rewrite text that is already acceptable.

Language rules:
- Correct grammar, spelling, punctuation, and obvious word-choice errors.
- Keep the author's voice and meaning. Never add or remove facts or information.
- Never use em dashes. Never output "delve", "tapestry", "multifaceted", or AI filler.
- Preserve names, URLs, placeholders like {{OBJ_0}}, citations, and exact technical terms.

Return ONLY one JSON object in this exact shape, with no Markdown fences and no commentary:
{"edits":[{"before":"the exact original fragment","after":"the replacement","reason":"short label like Spelling, Grammar, Word choice"}]}

Rules for the edits list:
- "before" must be an exact substring of the selected text.
- "after" must differ from "before".
- List at most 12 edits, ordered by how confident and important each change is.
- If the text needs no changes, return {"edits":[]}.
- Never output conversational replies, explanations, or the full rewritten text.
```

- [ ] **Step 4: Regenerate `src/prompt_data.py`**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && venv/bin/python scripts/embed_prompts.py`
Expected: `Embedded N prompt files -> .../src/prompt_data.py` and `prompt_data.py` now contains `preview_edits.txt`.

- [ ] **Step 5: Refactor the request builder out of `proofread_with_provider`**

In `src/logic.py`, extract the entire `_send` closure body into a module-level function. Cut the `headers = {...}` block through `return _api_call_with_retry(_do_request, cancel_event=cancel_event)` from `proofread_with_provider` verbatim, and parameterise it:

```python
def _request_completion(
    system_prompt: str,
    user_content: str,
    api_key: str,
    max_tokens: int,
    base_url: str,
    model: str,
    provider_name: str,
    temperature: float,
    cancel_event: threading.Event | None = None,
) -> str:
    """Send one chat completion and return the assistant text."""
    headers = {
        "Content-Type": "application/json",
    }
    if provider_name == "Anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        endpoint = f"{base_url}/messages"
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if provider_name == "DeepSeek":
            payload["thinking"] = {"type": "disabled"}
        endpoint = f"{base_url}/chat/completions"

    ssl_context = ssl.create_default_context(cafile=certifi.where())

    def _do_request() -> str:
        try:
            request = urllib.request.Request(
                url=endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120, context=ssl_context) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{provider_name} API HTTP error: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{provider_name} API connection error: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unexpected error connecting to {provider_name} API: {exc}") from exc

        try:
            if provider_name == "Anthropic":
                return response_data["content"][0]["text"].strip()
            content = response_data["choices"][0]["message"]["content"].strip()
            if PROVIDERS.get(provider_name, {}).get("is_local"):
                content = _clean_local_model_output(content)
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected {provider_name} response format: {response_data}") from exc

    return _api_call_with_retry(_do_request, cancel_event=cancel_event)
```

Then replace the deleted closure in `proofread_with_provider` with:

```python
    def _send(system_prompt: str, user_content: str) -> str:
        return _request_completion(
            system_prompt,
            user_content,
            api_key,
            max_tokens,
            base_url,
            model,
            provider_name,
            temperature,
            cancel_event,
        )
```

The conversational-reply retry below it stays exactly as it is.

- [ ] **Step 6: Add `load_preview_prompt` and `preview_edits_once` to `src/logic.py`**

```python
def load_preview_prompt() -> str:
    content = PROMPT_FILES.get("preview_edits.txt")
    if content:
        return content
    return (
        "Return only JSON: {\"edits\":[{\"before\":\"...\",\"after\":\"...\","
        "\"reason\":\"...\"}]}. Correct only real errors in the text between markers."
    )


def preview_edits_once(
    settings: dict[str, Any],
    target: dict[str, Any],
    selected_text: str,
    context_before: str = "",
    context_after: str = "",
    cancel_event: threading.Event | None = None,
) -> tuple[str, list[Any], dict[str, Any]]:
    """Run the compact live-preview call and return (status, edits, meta)."""
    from .live_preview import (
        PREVIEW_MAX_OUTPUT_TOKENS,
        parse_preview_response,
    )

    live = settings.get("live_preview", {})
    use_local = bool(live.get("use_local_model", True))
    active_provider, api_key, base_url, model = resolve_provider_connection(settings)
    provider_name = active_provider
    if use_local and active_provider != LOCAL_MODEL_PROVIDER:
        try:
            provider_name, api_key, base_url, model = resolve_provider_connection(
                {**settings, "active_provider": LOCAL_MODEL_PROVIDER}
            )
        except Exception:
            provider_name = active_provider

    if provider_name != LOCAL_MODEL_PROVIDER:
        access = get_access_status()
        if access.get("tier") == "free" and not access.get("free_mode_allowed"):
            return "limit_reached", [], {"provider": provider_name}
        if provider_requires_api_key(provider_name) and not api_key:
            return "no_api_key", [], {"provider": provider_name}

    spelling = settings.get("general", {}).get("spelling", "UK/AU/NZ")
    system_prompt = load_preview_prompt()
    if spelling == "UK/AU/NZ":
        system_prompt += "\n\nUse British/Australian/New Zealand spelling."
    elif spelling == "US English":
        system_prompt += "\n\nUse American spelling."

    marked = f"<SELECTED>\n{selected_text}\n</SELECTED>"
    parts = [marked]
    if context_before:
        parts.insert(0, f"Context before:\n{context_before}")
    if context_after:
        parts.append(f"Context after:\n{context_after}")
    user_content = "\n\n".join(parts) + (
        "\n\nEdit only the text between <SELECTED> and </SELECTED>. "
        "Return only the JSON edits object."
    )

    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelledError()
    raw = _request_completion(
        system_prompt,
        user_content,
        api_key,
        PREVIEW_MAX_OUTPUT_TOKENS,
        base_url,
        model,
        provider_name,
        0.1,
        cancel_event,
    )
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelledError()
    edits = parse_preview_response(raw)
    status = "ok" if edits else "no_changes"
    return status, edits, {"provider": provider_name, "raw": raw}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py tests/test_smoke.py -q`
Expected: new preview tests pass and the smoke suite stays green (the refactor must not change `proofread_with_provider` behavior).

- [ ] **Step 8: Commit**

```bash
git add prompt/preview_edits.txt src/prompt_data.py src/logic.py tests/test_live_preview.py
git commit -m "feat: add compact edit-json preview call and shared completion request"
```

---

### Task 3: AX geometry and range replacement (`src/generic_editing.py`)

**Files:**
- Modify: `src/generic_editing.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Produces (macOS only; other platforms return safe defaults):
  - `GenericTextEditor.selection_details(self, target) -> dict[str, Any]` with keys `text`, `range` (tuple location,length or None), `context_before`, `context_after`.
  - `GenericTextEditor.ax_bounds_for_range(self, target, start, length) -> list[tuple[float, float, float, float]]`
  - `GenericTextEditor.ax_replace_range(self, target, start, length, new_text) -> tuple[bool, str]`

- [ ] **Step 1: Write the failing tests**

```python
from src.generic_editing import GenericTextEditor


def test_parse_ax_range_dict_shape():
    assert GenericTextEditor._parse_ax_range({"location": 3, "length": 4}) == (3, 4)
    assert GenericTextEditor._parse_ax_range(None) == (None, None)


def test_ax_bounds_for_range_guards_untrusted(monkeypatch):
    editor = GenericTextEditor()
    monkeypatch.setattr(
        "src.generic_editing.SYSTEM", "Windows"
    )
    assert editor.ax_bounds_for_range({"pid": 1}, 0, 5) == []


def test_ax_replace_range_guards_untrusted(monkeypatch):
    editor = GenericTextEditor()
    monkeypatch.setattr("src.generic_editing.SYSTEM", "Windows")
    ok, _ = editor.ax_replace_range({"pid": 1}, 0, 5, "x")
    assert ok is False
```

Note: `_parse_ax_range` already exists; the test locks its behavior. The geometry calls need a trusted AX session, so they are covered by the Task 8 integration fixture; these unit tests only lock the guards.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `AttributeError: 'GenericTextEditor' object has no attribute 'ax_bounds_for_range'`.

- [ ] **Step 3: Add a focused-element helper and refactor the read path**

In `GenericTextEditor`, add:

```python
    @staticmethod
    def _mac_ax_focused(pid: int):
        try:
            import ApplicationServices as AS
            app_el = AS.AXUIElementCreateApplication(pid)
            err, focused = AS.AXUIElementCopyAttributeValue(
                app_el, AS.kAXFocusedUIElementAttribute, None
            )
            if err == 0 and focused is not None:
                return AS, focused
        except Exception:
            pass
        return None, None
```

Rewrite `_mac_ax_selection` and `_mac_selection_info` to use `_mac_ax_focused` instead of their inline copies (behavior must stay identical; the smoke suite guards this).

- [ ] **Step 4: Implement the three new methods**

```python
    def selection_details(self, target: dict[str, Any]) -> dict[str, Any]:
        """Read (text, absolute range, context) through AX only."""
        result: dict[str, Any] = {
            "text": "", "range": None, "context_before": "", "context_after": "",
        }
        if SYSTEM != "Darwin":
            return result
        pid = target.get("pid")
        if not pid:
            return result
        found = GenericTextEditor._mac_ax_focused(pid)
        if found is None or found[0] is None:
            return result
        AS, focused = found
        try:
            err, text = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXSelectedTextAttribute, None
            )
            if err == 0 and text:
                result["text"] = str(text)
            err, range_val = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXSelectedTextRangeAttribute, None
            )
            if err == 0 and range_val is not None:
                result["range"] = _parse_ax_range(range_val)
            err, value = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXValueAttribute, None
            )
            full = str(value) if err == 0 and isinstance(value, str) else ""
            location = (result["range"] or (None, None))[0]
            if result["text"] and full:
                before, after = "", ""
                if location is not None and 0 <= location <= len(full):
                    end = location + len(result["text"])
                    before = full[:location]
                    after = full[end:]
                elif result["text"] in full:
                    idx = full.find(result["text"])
                    before = full[:idx]
                    after = full[idx + len(result["text"]) :]
                from .live_preview import CONTEXT_CHARS
                result["context_before"] = before[-CONTEXT_CHARS:]
                result["context_after"] = after[:CONTEXT_CHARS]
        except Exception:
            pass
        return result

    def ax_bounds_for_range(
        self, target: dict[str, Any], start: int, length: int
    ) -> list[tuple[float, float, float, float]]:
        if SYSTEM != "Darwin" or length <= 0:
            return []
        try:
            import ApplicationServices as AS
            if not AS.AXIsProcessTrusted():
                return []
        except Exception:
            return []
        found = GenericTextEditor._mac_ax_focused(target.get("pid") or 0)
        if found is None or found[0] is None:
            return []
        AS, focused = found
        rects: list[tuple[float, float, float, float]] = []
        for offset in range(start, start + length):
            try:
                param = AS.AXValueCreate(AS.kAXValueTypeCFRange, (offset, 1))
                err, value = AS.AXUIElementCopyParameterizedAttributeValue(
                    focused,
                    AS.kAXBoundsForRangeParameterizedAttribute,
                    param,
                    None,
                )
                if err != 0 or value is None:
                    continue
                raw = AS.AXValueGetValue(value)
                # PyObjC returns the CGRect value as (x, y, w, h) floats.
                rects.append(tuple(float(v) for v in raw))
            except Exception:
                continue
        return rects

    def ax_replace_range(
        self, target: dict[str, Any], start: int, length: int, new_text: str
    ) -> tuple[bool, str]:
        if SYSTEM != "Darwin":
            return False, "Live apply is only supported on macOS in this beta."
        try:
            import ApplicationServices as AS
            if not AS.AXIsProcessTrusted():
                return False, "Accessibility permission is required to apply edits."
        except Exception:
            return False, "Accessibility permission could not be checked."
        found = GenericTextEditor._mac_ax_focused(target.get("pid") or 0)
        if found is None or found[0] is None:
            return False, "Could not read the focused text field."
        AS, focused = found
        try:
            param = AS.AXValueCreate(AS.kAXValueTypeCFRange, (start, length))
            err, _ = AS.AXUIElementCopyParameterizedAttributeValue(
                focused,
                AS.kAXReplaceRangeWithTextParameterizedAttribute,
                param,
                None,
            )
            if err == 0:
                return True, "Applied."
            _debug_log(f"AXReplaceRangeWithText unavailable or failed: {err}")
        except Exception as exc:
            _debug_log(f"ax_replace_range error: {exc}")
        # Fallback: select the sub-range, then write the selected-text attribute.
        try:
            param = AS.AXValueCreate(AS.kAXValueTypeCFRange, (start, length))
            AS.AXUIElementSetAttributeValue(
                focused, AS.kAXSelectedTextRangeAttribute, param
            )
            err = AS.AXUIElementSetAttributeValue(
                focused, AS.kAXSelectedTextAttribute, new_text
            )
            if err == 0:
                return True, "Applied."
        except Exception as exc:
            _debug_log(f"ax_replace_range fallback error: {exc}")
        return False, "Could not apply the edit in this app."
```

Note: the `AXValueGetValue` call shape may differ per PyObjC version; Task 8's integration fixture is the authority. If `AXValueGetValue` requires an extra type argument, the fixture step adjusts this call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py tests/test_smoke.py -q`
Expected: new tests pass; smoke suite unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/generic_editing.py tests/test_live_preview.py
git commit -m "feat: add AX selection details, bounds, and range replacement"
```

---

### Task 4: Overlay window with underlines and hover popup (`src/live_overlay.py`)

**Files:**
- Create: `src/live_overlay.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Produces:
  - `OverlaySpan(before: str, after: str, reason: str, rect: QRect)`
  - `span_at_point(spans: Sequence[OverlaySpan], pos: QPoint) -> int | None`
  - `popup_rows(span: OverlaySpan) -> list[tuple[str, str]]` (style, text)
  - `LiveOverlay(QWidget)` with signals `hovered(int)`, `apply_requested(int)`, `apply_all_requested()`, `dismissed()`, and methods `set_spans(spans)`, `hide_overlay()`.

- [ ] **Step 1: Write the failing tests**

```python
from PyQt6.QtCore import QPoint, QRect

from src.live_overlay import OverlaySpan, popup_rows, span_at_point


def test_span_at_point_hits_inside_rect():
    spans = [OverlaySpan("a", "b", "x", QRect(10, 10, 100, 20))]
    assert span_at_point(spans, QPoint(50, 15)) == 0
    assert span_at_point(spans, QPoint(200, 15)) is None


def test_popup_rows_track_changes_styles():
    rows = popup_rows(OverlaySpan("teh", "the", "Spelling", QRect(0, 0, 10, 10)))
    assert ("old", "teh") in rows
    assert ("new", "the") in rows
    assert ("reason", "Spelling") in rows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `ModuleNotFoundError: No module named 'src.live_overlay'`.

- [ ] **Step 3: Implement `src/live_overlay.py`**

```python
"""Transparent overlay that draws dashed underlines and a hover popup."""

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .live_preview import UNDERLINE_COLOR_HEX


@dataclass(frozen=True)
class OverlaySpan:
    before: str
    after: str
    reason: str
    rect: QRect


def span_at_point(spans: Sequence[OverlaySpan], pos: QPoint) -> int | None:
    for index, span in enumerate(spans):
        if span.rect.adjusted(-3, -4, 3, 4).contains(pos):
            return index
    return None


def popup_rows(span: OverlaySpan) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if span.before:
        rows.append(("old", span.before))
    if span.after:
        rows.append(("new", span.after))
    if span.reason:
        rows.append(("reason", span.reason))
    return rows


class LiveOverlay(QWidget):
    """A frameless, mostly click-through window that hosts underlines."""

    hovered = pyqtSignal(int)
    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._spans: list[OverlaySpan] = []
        self._hovered: int | None = None
        self._popup: QFrame | None = None
        self._popup_span: int | None = None

    def set_spans(self, spans: list[OverlaySpan]) -> None:
        self._spans = spans
        self._hide_popup()
        if not spans:
            self.hide()
            return
        union = spans[0].rect
        for span in spans[1:]:
            union = union.united(span.rect)
        self.setGeometry(union.adjusted(-40, -40, 40, 40))
        region = QRegion()
        for span in spans:
            region = region.united(QRegion(span.rect.adjusted(-3, -4, 3, 4)))
        self.setMask(region)
        self.show()
        self.update()

    def hide_overlay(self) -> None:
        self._hide_popup()
        self._spans = []
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        pen = QPen(QColor(UNDERLINE_COLOR_HEX))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        origin = self.geometry().topLeft()
        for span in self._spans:
            rect = span.rect.translated(-origin)
            painter.drawLine(
                rect.left(), rect.bottom() - 2, rect.right(), rect.bottom() - 2
            )
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        index = span_at_point(self._spans, event.position().toPoint() + self.geometry().topLeft())
        if index != self._hovered:
            self._hovered = index
            if index is not None:
                self._show_popup(index)
                self.hovered.emit(index)
            else:
                self._hide_popup()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = None
        self._hide_popup()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        index = span_at_point(self._spans, event.position().toPoint() + self.geometry().topLeft())
        if index is not None:
            self.apply_requested.emit(index)
        super().mouseReleaseEvent(event)

    def _show_popup(self, index: int) -> None:
        self._hide_popup()
        span = self._spans[index]
        popup = QFrame(self)
        popup.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #E8E4E0;"
            " border-radius: 10px; }"
        )
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        for style, text in popup_rows(span):
            label = QLabel(text)
            label.setWordWrap(True)
            if style == "old":
                label.setText(f"<s style='color:#B91C1C'>{text}</s>")
                label.setStyleSheet("color:#B91C1C; font-size:12px;")
            elif style == "new":
                label.setText(f"<span style='color:#166534'>{text}</span>")
                label.setStyleSheet("color:#166534; font-size:12px; font-weight:600;")
            else:
                label.setStyleSheet("color:#78716C; font-size:11px;")
            layout.addWidget(label)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(lambda: self.apply_requested.emit(index))
        layout.addWidget(apply_btn)
        popup.adjustSize()
        origin = self.geometry().topLeft()
        target = span.rect.translated(-origin)
        x = target.left()
        y = target.bottom() + 6
        if y + popup.height() > self.height():
            y = target.top() - popup.height() - 6
        popup.move(max(0, x), max(0, y))
        popup.show()
        self._popup = popup
        self._popup_span = index

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None
            self._popup_span = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: the two overlay tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/live_overlay.py tests/test_live_preview.py
git commit -m "feat: add live overlay window with dashed underlines and hover popup"
```

---

### Task 5: Live preview orchestrator (`src/live_service.py`)

**Files:**
- Create: `src/live_service.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Consumes: `evaluate_trigger`, `PreviewCache`, `preview_cache_key`, `settings_fingerprint`, `map_edits_to_ranges`, `POLL_INTERVAL_MS`, `DEFAULT_DELAY_MS`; `logic.preview_edits_once`; `GenericTextEditor`; `LiveOverlay`/`OverlaySpan`.
- Produces:
  - `LivePreviewService(QObject)` with `start()`, `stop()`, `refresh_settings(settings)`, signals `apply_done(str)`, `preview_error(str)`.
  - `PreviewWorker(QThread)` running `logic.preview_edits_once` with a `cancel_event`.

- [ ] **Step 1: Write the failing tests**

```python
from src.live_service import LivePreviewService
from src.live_preview import EditSpan


def test_service_decision_flow_skips_unchanged(monkeypatch, qapp):
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True, "delay_ms": 900,
                                               "max_chars": 1500, "use_local_model": True}})
    fake_editor = _FakeEditor("com.apple.TextEdit", "this is a test sentence")
    monkeypatch.setattr(service, "_editor", fake_editor)
    calls = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: calls.append(a))
    service._sample(now=1000.0)
    service._sample(now=2000.0)
    assert len(calls) == 1
    service._sample(now=3000.0)
    assert len(calls) == 1


def test_service_applies_span_through_ax(monkeypatch, qapp):
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True, "delay_ms": 900,
                                               "max_chars": 1500, "use_local_model": True}})
    applied = []
    class FakeEditor:
        def frontmost_app(self): return {"bundle_id": "com.apple.TextEdit", "pid": 9, "name": "TextEdit"}
        def ax_replace_range(self, target, start, length, text):
            applied.append((start, length, text)); return True, "Applied."
    service._editor = FakeEditor()
    service._selection_start = 100
    span = EditSpan("teh", "the", "Spelling", 0, 3)
    service._apply_span(span)
    assert applied == [(100, 3, "the")]
```

Add to the test file a tiny helper at the top:

```python
class _FakeEditor:
    def __init__(self, bundle_id: str, text: str):
        self.bundle_id = bundle_id
        self.text = text
    def frontmost_app(self):
        return {"bundle_id": self.bundle_id, "pid": 1, "name": "FakeApp"}
    def selection_details(self, target):
        return {"text": self.text, "range": (0, len(self.text)),
                "context_before": "", "context_after": ""}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `ModuleNotFoundError: No module named 'src.live_service'`.

- [ ] **Step 3: Implement `src/live_service.py`**

```python
"""Orchestrates live-preview sampling, provider calls, rendering, and apply."""

import threading
import time
from typing import Any

from PyQt6.QtCore import QObject, QRect, QThread, QTimer, pyqtSignal

from .generic_editing import get_generic_editor
from .live_overlay import LiveOverlay, OverlaySpan
from .live_preview import (
    DEFAULT_DELAY_MS,
    POLL_INTERVAL_MS,
    PreviewCache,
    EditSpan,
    evaluate_trigger,
    map_edits_to_ranges,
    preview_cache_key,
    settings_fingerprint,
)


class PreviewWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, settings, target, selected, before, after, cancel_event):
        super().__init__()
        self.settings = settings
        self.target = target
        self.selected = selected
        self.before = before
        self.after = after
        self.cancel_event = cancel_event

    def run(self):
        from . import logic
        try:
            status, edits, meta = logic.preview_edits_once(
                self.settings, self.target, self.selected, self.before, self.after,
                cancel_event=self.cancel_event,
            )
            self.done.emit({"status": status, "edits": edits, "meta": meta})
        except Exception as exc:
            self.failed.emit(str(exc))


class LivePreviewService(QObject):
    apply_done = pyqtSignal(str)
    preview_error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings: dict[str, Any] = {}
        self._editor = get_generic_editor()
        self._overlay = LiveOverlay()
        self._cache = PreviewCache()
        self._fingerprint = ""
        self._selection_start = 0
        self._selection_text = ""
        self._selected_target: dict[str, Any] = {}
        self._spans: list[EditSpan] = []
        self._last_seen = ""
        self._last_change = 0.0
        self._worker: PreviewWorker | None = None
        self._cancel_event = threading.Event()
        self._timer: QTimer | None = None
        self._overlay.apply_requested.connect(self._apply_index)
        self._overlay.dismissed.connect(self._clear_preview)

    def start(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_INTERVAL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._cancel_event.set()
        self._clear_preview()

    def refresh_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._fingerprint = settings_fingerprint(settings)
        if not settings.get("live_preview", {}).get("enabled", True):
            self._clear_preview()

    def _poll(self) -> None:
        try:
            self._sample(now=time.monotonic())
        except Exception:
            pass

    def _sample(self, now: float) -> None:
        if not self._settings.get("live_preview", {}).get("enabled", True):
            return
        target = self._editor.frontmost_app()
        if not target:
            self._clear_preview()
            return
        permission_ok, _ = self._editor.permission_status()
        details = self._editor.selection_details(target)
        text = details.get("text") or ""
        decision, _reason = evaluate_trigger(
            self._settings,
            target,
            text,
            permission_ok,
            now - self._last_change >= self._delay() / 1000.0,
            self._worker is not None,
            text == self._last_seen,
        )
        if decision != "run":
            return
        self._selection_text = text
        self._selected_target = target
        self._last_seen = text
        self._selection_start = (details.get("range") or (0, 0))[0]
        key = preview_cache_key(
            str(target.get("bundle_id", "")),
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._fingerprint,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._spans = cached
            self._render(cached)
            return
        self._spawn_preview(target, text, details, key)

    def _delay(self) -> int:
        return int(self._settings.get("live_preview", {}).get("delay_ms", DEFAULT_DELAY_MS))

    def _spawn_preview(self, target, text, details, key) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._cancel_event.set()
            self._worker.wait(500)
        self._cancel_event = threading.Event()
        self._worker = PreviewWorker(
            self._settings,
            target,
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._cancel_event,
        )
        self._worker.done.connect(lambda result, k=key: self._on_done(result, k))
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, result: dict[str, Any], key: str) -> None:
        self._worker = None
        edits = result.get("edits") or []
        spans = map_edits_to_ranges(self._selection_text, edits)
        self._cache.put(key, spans)
        self._spans = spans
        self._render(spans)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._clear_preview()
        self.preview_error.emit(message)

    def _render(self, spans: list[EditSpan]) -> None:
        if not spans:
            self._clear_preview()
            return
        rects = []
        for span in spans:
            bounds = self._editor.ax_bounds_for_range(
                self._selected_target, self._selection_start + span.start,
                span.end - span.start,
            )
            rects.append(self._rect_from_bounds(bounds))
        overlay_spans = [
            OverlaySpan(span.before, span.after, span.reason, rect)
            for span, rect in zip(spans, rects)
            if not rect.isNull()
        ]
        self._overlay.set_spans(overlay_spans)

    @staticmethod
    def _rect_from_bounds(bounds):
        if not bounds:
            return QRect()
        xs = [b[0] for b in bounds]
        ys = [b[1] for b in bounds]
        rights = [b[0] + b[2] for b in bounds]
        bottoms = [b[1] + b[3] for b in bounds]
        return QRect(
            int(min(xs)), int(min(ys)),
            int(max(rights) - min(xs)), int(max(bottoms) - min(ys)),
        )

    def _apply_index(self, index: int) -> None:
        if not (0 <= index < len(self._spans)):
            return
        self._apply_span(self._spans[index])

    def _apply_span(self, span: EditSpan) -> None:
        target = self._selected_target
        ok, message = self._editor.ax_replace_range(
            target,
            self._selection_start + span.start,
            span.end - span.start,
            span.after,
        )
        self.apply_done.emit(message)
        if ok:
            self._last_seen = ""
            self._clear_preview()

    def _clear_preview(self) -> None:
        self._spans = []
        self._overlay.hide_overlay()
```

The Word-specific apply hook is added in Task 6: when `GenericTextEditor.is_word(target)` is true, `_sample`/`_render`/`_apply_span` branch to the Word integration instead of AX.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: the two service tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/live_service.py tests/test_live_preview.py
git commit -m "feat: add live preview service with sampling, cache, and apply"
```

---

### Task 6: Word native underlines, apply, and rect estimation (`src/word_integration.py`)

**Files:**
- Modify: `src/word_integration.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Produces on the object returned by `get_word_integration()`:
  - `apply_live_edit(selection_start: int, rel_start: int, rel_end: int, replacement: str) -> tuple[bool, str]`
  - `set_live_underline(selection_start: int, rel_start: int, rel_end: int, enable: bool) -> tuple[bool, str]`
  - `clear_live_underlines() -> None`
  - `estimate_range_rect(selection_start: int, rel_start: int, rel_end: int) -> dict[str, float] | None`

- [ ] **Step 1: Spike Word's AppleScript surface (documented, not TDD)**

Run each of these from the project root and record the working form:

```bash
osascript -e 'tell application "Microsoft Word" to get {start of selection, end of selection}'
osascript -e 'tell application "Microsoft Word" to get name of active document'
osascript -e 'tell application "Microsoft Word" to get underline of font object of selection'
osascript -e 'tell application "Microsoft Word" to set underline of font object of selection to underline dot dot dash'
osascript -e 'tell application "Microsoft Word" to get color of font object of selection'
osascript -e 'tell application "Microsoft Word" to get percentage of zoom of active window of active document'
```

If `underline dot dot dash` is not a recognized constant, use `underline dotted`. If RGB color setting fails with a record, use `set color index of font object of selection to red`. If `zoom ... active window` fails, try `zoom of active window`.

- [ ] **Step 2: Write the failing tests**

```python
import subprocess
from unittest import mock

from src import word_integration as wi


def _fake_osascript(script: str, *a, **k):
    completed = mock.Mock()
    completed.returncode = 0
    completed.stdout = "OK"
    completed.stderr = ""
    return completed


def test_word_apply_live_edit_builds_subrange_script(monkeypatch):
    calls = []
    def capture(args, **kwargs):
        calls.append(args)
        return _fake_osascript("")
    monkeypatch.setattr(subprocess, "run", capture)
    integration = wi.MacOSWordIntegration()
    ok, _ = integration.apply_live_edit(500, 10, 13, "the")
    assert ok is True
    joined = " ".join(str(a) for a in calls[-1][0])
    assert "500" in joined or "510" in joined


def test_word_set_live_underline_builds_dotted_script(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda args, **kw: calls.append(args) or _fake_osascript(""))
    integration = wi.MacOSWordIntegration()
    ok, _ = integration.set_live_underline(500, 10, 13, True)
    assert ok is True
    assert any("underline" in str(a).lower() for a in calls[-1][0])
```

Note: if `get_word_integration()` returns a differently named macOS class in this codebase, use that exact class name in the tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `AttributeError: ... has no attribute 'apply_live_edit'`.

- [ ] **Step 4: Implement the methods on the macOS integration class**

```python
    def _subrange_script(self, selection_start: int, rel_start: int,
                         rel_end: int) -> str:
        start = selection_start + rel_start
        end = selection_start + rel_end
        return (
            "tell application \"Microsoft Word\"\n"
            f"set r to create range active document start {start} end {end}\n"
        )

    def apply_live_edit(self, selection_start: int, rel_start: int,
                        rel_end: int, replacement: str) -> tuple[bool, str]:
        escaped = replacement.replace("\\", "\\\\").replace('"', '\\"')
        script = self._subrange_script(selection_start, rel_start, rel_end) + (
            f'set content of text object of r to "{escaped}"\n'
            "end tell"
        )
        return self._run_osascript(script)

    def set_live_underline(self, selection_start: int, rel_start: int,
                           rel_end: int, enable: bool) -> tuple[bool, str]:
        style = "underline dot dot dash" if enable else "underline none"
        script = self._subrange_script(selection_start, rel_start, rel_end) + (
            f"set underline of font object of r to {style}\n"
        )
        if enable:
            script += "set color index of font object of r to red\n"
        script += "end tell"
        return self._run_osascript(script)

    def clear_live_underlines(self) -> None:
        for start, end in getattr(self, "_live_underline_ranges", []):
            self.set_live_underline(self._live_selection_start, start, end, False)
        self._live_underline_ranges = []

    def estimate_range_rect(self, selection_start: int, rel_start: int,
                            rel_end: int) -> dict[str, float] | None:
        """Best-effort page-relative position; None when unavailable."""
        return None  # replaced by the spike result in Task 9 calibration
```

Track underline ranges: in `set_live_underline` with `enable=True`, append `(rel_start, rel_end)` to `self._live_underline_ranges` and store `self._live_selection_start = selection_start`. `_run_osascript` is the existing subprocess wrapper in the class; reuse it.

- [ ] **Step 5: Wire the Word branch into `src/live_service.py`**

In `LivePreviewService`, import `get_word_integration` lazily. In `_sample`, after `target = self._editor.frontmost_app()`, set `self._is_word = self._editor.is_word(target)`; when Word, the apply path becomes:

```python
    def _apply_span(self, span: EditSpan) -> None:
        if getattr(self, "_is_word", False):
            from .word_integration import get_word_integration
            ok, message = get_word_integration().apply_live_edit(
                self._selection_start, span.start, span.end, span.after
            )
            self.apply_done.emit(message)
            if ok:
                self._clear_preview()
            return
        ...  # existing AX path
```

Underline rendering for Word is added in Task 9 after the calibration spike, because it needs the AppleScript constants confirmed live.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py tests/test_smoke.py -q`
Expected: new tests pass; smoke suite green.

- [ ] **Step 7: Commit**

```bash
git add src/word_integration.py src/live_service.py tests/test_live_preview.py
git commit -m "feat: add Word sub-range live apply and underline hooks"
```

---

### Task 7: Settings schema and General-page controls (`src/settings.py`, `src/gui.py`)

**Files:**
- Modify: `src/settings.py`, `src/gui.py`
- Test: `tests/test_live_preview.py`

**Interfaces:**
- Produces: `settings["live_preview"] = {"enabled": True, "delay_ms": 900, "max_chars": 1500, "use_local_model": True}` with load-time merging and save support; General-page controls `chk_live_preview`, `live_delay_slider`, `chk_live_local`; `ProofreaderApp.live_service` started on launch and toggled on settings save.

- [ ] **Step 1: Write the failing tests**

```python
import json

from src import settings as settings_mod


def test_load_runtime_settings_includes_live_preview_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    loaded = settings_mod.load_runtime_settings()
    assert loaded["live_preview"] == {
        "enabled": True, "delay_ms": 900, "max_chars": 1500, "use_local_model": True,
    }


def test_load_runtime_settings_merges_existing_live_preview(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"live_preview": {"enabled": False}}))
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(path))
    loaded = settings_mod.load_runtime_settings()
    assert loaded["live_preview"]["enabled"] is False
    assert loaded["live_preview"]["delay_ms"] == 900
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: `KeyError: 'live_preview'`.

- [ ] **Step 3: Update `src/settings.py`**

In `load_runtime_settings`, add to the default dict:

```python
        "live_preview": {
            "enabled": True,
            "delay_ms": 900,
            "max_chars": 1500,
            "use_local_model": True,
        },
```

And next to the `automation` merge add:

```python
    if "live_preview" in loaded:
        settings["live_preview"].update(loaded["live_preview"])
```

- [ ] **Step 4: Add General-page controls to `SettingsDialog`**

In `src/gui.py`, in the General-page builder, append a `QGroupBox("Live Suggestions (Beta)")` with:

```python
        self.chk_live_preview = QCheckBox("Show live suggestions when text is selected")
        self.chk_live_preview.setChecked(
            self.settings.get("live_preview", {}).get("enabled", True)
        )
        self.chk_live_preview.setToolTip(
            "Underline suggested changes as soon as you select text in Word, "
            "Pages, Mail, or Outlook. Hover a suggestion to preview it and "
            "click to apply."
        )
        group_layout.addWidget(self.chk_live_preview)

        delay_row = QHBoxLayout()
        delay_label = QLabel("Preview delay")
        self.live_delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.live_delay_slider.setRange(400, 2000)
        self.live_delay_slider.setSingleStep(100)
        self.live_delay_slider.setValue(
            int(self.settings.get("live_preview", {}).get("delay_ms", 900))
        )
        self.live_delay_label = QLabel(f"{self.live_delay_slider.value()} ms")
        self.live_delay_slider.valueChanged.connect(
            lambda v: self.live_delay_label.setText(f"{v} ms")
        )
        delay_row.addWidget(delay_label)
        delay_row.addWidget(self.live_delay_slider)
        delay_row.addWidget(self.live_delay_label)
        group_layout.addLayout(delay_row)

        self.chk_live_local = QCheckBox(
            "Prefer Local AI for live suggestions (saves cloud tokens)"
        )
        self.chk_live_local.setChecked(
            self.settings.get("live_preview", {}).get("use_local_model", True)
        )
        group_layout.addWidget(self.chk_live_local)
```

In the method that collects settings into `self.settings` before accept, add:

```python
        self.settings["live_preview"] = {
            "enabled": self.chk_live_preview.isChecked(),
            "delay_ms": int(self.live_delay_slider.value()),
            "max_chars": int(
                self.settings.get("live_preview", {}).get("max_chars", 1500)
            ),
            "use_local_model": self.chk_live_local.isChecked(),
        }
```

- [ ] **Step 5: Start/stop the service from `ProofreaderApp`**

In `ProofreaderApp.__init__`, after the existing worker setup:

```python
        from .live_service import LivePreviewService
        self.live_service = LivePreviewService(self)
        self.live_service.refresh_settings(runtime_settings)
        if runtime_settings.get("live_preview", {}).get("enabled", True):
            self.live_service.start()
        self.live_service.preview_error.connect(self._on_live_preview_error)
```

Add:

```python
    def _on_live_preview_error(self, message: str) -> None:
        self._show_toast(f"Live suggestions: {message}", kind="warning")
```

Where settings are re-read after the dialog accepts (the `open_settings` handlers), call `self.live_service.refresh_settings(self.settings)` and start or stop it based on `enabled`. Add `self.live_service.stop()` before `QApplication.quit()` paths.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py tests/test_smoke.py -q`
Expected: settings tests pass; smoke suite green. Note the smoke suite instantiates `ProofreaderApp`; if the service's AX polling throws in offscreen mode it must degrade silently (its `_poll` already swallows exceptions).

- [ ] **Step 7: Commit**

```bash
git add src/settings.py src/gui.py tests/test_live_preview.py
git commit -m "feat: add live-preview settings and General-page controls"
```

---

### Task 8: Integration fixture and efficiency assertions (`tests/test_live_preview.py`)

**Files:**
- Modify: `tests/test_live_preview.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1-7.

- [ ] **Step 1: Write the integration tests**

```python
def test_service_full_cycle_with_fake_provider(monkeypatch, qapp):
    from src import logic
    monkeypatch.setattr(
        logic, "preview_edits_once",
        lambda settings, target, text, before, after, cancel_event=None: (
            "ok",
            [Edit("teh", "the", "Spelling")],
            {"provider": "fake"},
        ),
    )
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True, "delay_ms": 900,
                                               "max_chars": 1500, "use_local_model": True}})
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat on teh mat")
    editor.rects = [(10.0, 10.0, 30.0, 16.0), (40.0, 10.0, 30.0, 16.0)]
    editor.applied = []
    def bounds(target, start, length):
        return [editor.rects[0]]
    editor.ax_bounds_for_range = bounds
    def replace(target, start, length, text):
        editor.applied.append((start, length, text))
        return True, "Applied."
    editor.ax_replace_range = replace
    monkeypatch.setattr(service, "_editor", editor)
    service._sample(now=10.0)
    assert service._spans and service._spans[0].before == "teh"
    service._apply_span(service._spans[0])
    assert editor.applied == [(0, 3, "the")]


def test_preview_cache_prevents_duplicate_provider_calls(monkeypatch, qapp):
    from src import logic
    calls = []
    def fake(settings, target, text, before, after, cancel_event=None):
        calls.append(text)
        return "ok", [Edit("teh", "the", "Spelling")], {"provider": "fake"}
    monkeypatch.setattr(logic, "preview_edits_once", fake)
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True, "delay_ms": 900,
                                               "max_chars": 1500, "use_local_model": True}})
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    service._sample(now=1.0)
    service._sample(now=2.0)
    service._sample(now=3.0)
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/zhangy6j/Python Projects/Personal/ByteMind Project/ByteProof && QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q`
Expected: both tests pass. The duplicate-call test is the machine-checked efficiency assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_preview.py
git commit -m "test: cover live-preview end-to-end cycle and cache efficiency"
```

---

### Task 9: Live calibration and test campaign (manual + scripted)

**Files:**
- Modify: `src/word_integration.py`, `src/live_service.py` as calibration demands
- Add: `tests/corpus_live.py`

**Steps:**

- [ ] **Step 1: TextEdit live run.** Launch the dev app (`venv/bin/python run.py`), enable Live suggestions, select a defective sentence in TextEdit, and verify: dashed pink-red underlines appear after the delay; hover opens the popup with struck-through old and green new text; clicking applies the edit in place; re-selecting the same text does not trigger a second provider call (watch the local server log).

- [ ] **Step 2: Mail and Pages live runs.** Repeat in a Mail compose window and a Pages document. Record which surface each uses (overlay underlines vs fallback card).

- [ ] **Step 3: Word calibration.** With a scratch Word document, run the Task 6 spike scripts, then calibrate `estimate_range_rect` using `selection.Information(wdHorizontalPositionRelativeToPage)` (constant 4) and `wdVerticalPositionRelativeToPage` (5), the AX window frame, and the zoom percentage. If the estimate is within one line height, use it for popup anchoring; otherwise anchor the popup to the mouse position when hovering the host window and list all edits.

- [ ] **Step 4: Word underline rendering.** Implement `_render` for Word: call `set_live_underline(selection_start, span.start, span.end, True)` for each mapped span, storing the ranges; clear on preview change via `clear_live_underlines()`. Verify visually that underlines are pink-red dashed (or dotted, per the spike constant) and are removed on selection change.

- [ ] **Step 5: Accuracy corpus.** Add `tests/corpus_live.py` with a fixed list like:

```python
CORPUS = [
    ("teh cat sat on teh mat", {"teh": "the"}),
    ("He go to school every day", {"go": "goes"}),
    ("The data is important for are analysis", {"are": "our"}),
]
```

Mark it `@pytest.mark.skipif(not os.environ.get("BYTEPROOF_LIVE_TESTS"), reason="live model")`. Run it at least three times with the Local AI model and record that every expected edit appears each run.

- [ ] **Step 6: Packaged build.** Run `./build_macos.sh`, install/open the built app, repeat the TextEdit run, and confirm the General toggle turns the feature on and off live.

- [ ] **Step 7: Commit any calibration changes**

```bash
git add src/word_integration.py src/live_service.py tests/corpus_live.py
git commit -m "feat: calibrate Word underline and popup anchoring for live preview"
```

---

### Task 10: Full verification and finish

**Steps:**

- [ ] **Step 1:** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/ -q` and confirm everything is green.
- [ ] **Step 2:** Rerun the Task 9 live campaign once more from a clean app start.
- [ ] **Step 3:** Announce "I'm using the finishing-a-development-branch skill to complete this work" and follow it: verify tests, detect environment, present the merge/PR/keep menu to the user.
- [ ] **Step 4:** Update `docs/aegis/work/2026-09-09-live-proofread-preview/90-evidence.md` with the test outputs, token counts, and corpus results; write `99-reflection.md`.
