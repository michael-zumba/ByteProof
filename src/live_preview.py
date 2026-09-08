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

_FUZZY_RATIO_FLOOR = 0.6


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
    brace = cleaned.find("{")
    bracket = cleaned.find("[")
    if brace == -1:
        start, closer = bracket, "]"
    elif bracket == -1 or brace < bracket:
        start, closer = brace, "}"
    else:
        start, closer = bracket, "]"
    if start == -1:
        return json.loads(cleaned)
    end = cleaned.rfind(closer)
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
    for item in items:
        if not isinstance(item, dict):
            continue
        before = str(item.get("before") or "").strip()
        after = str(item.get("after") or "").strip()
        if not before or not after or before == after:
            continue
        reason = str(item.get("reason") or "").strip()
        edits.append(Edit(before, after, reason))
        if len(edits) >= PREVIEW_MAX_EDITS:
            break
    return edits


def _fuzzy_locate(text: str, needle: str) -> int | None:
    """Return the best word-boundary match for needle, or None below floor."""
    needle_words = re.findall(r"\w+", needle)
    if not needle_words:
        return None
    words = re.findall(r"\w+", text)
    best_ratio = _FUZZY_RATIO_FLOOR
    best: int | None = None
    for i in range(len(words) - len(needle_words) + 1):
        candidate = " ".join(words[i : i + len(needle_words)])
        ratio = difflib.SequenceMatcher(
            None, " ".join(needle_words), candidate
        ).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = i
    if best is None:
        return None
    prefix = " ".join(words[:best])
    if prefix:
        prefix += " "
    index = text.find(prefix)
    if index >= 0:
        return len(prefix)
    found = text.find(words[best])
    return found if found >= 0 else None


def _locate_all(text: str, needle: str) -> list[tuple[int, int]]:
    """Return every exact occurrence; fall back to one fuzzy match."""
    occurrences: list[tuple[int, int]] = []
    cursor = 0
    while True:
        index = text.find(needle, cursor)
        if index < 0:
            break
        occurrences.append((index, index + len(needle)))
        cursor = index + 1
    if occurrences:
        return occurrences
    fuzzy = _fuzzy_locate(text, needle)
    if fuzzy is None:
        return []
    return [(fuzzy, max(fuzzy + 1, fuzzy + len(needle)))]


def map_edits_to_ranges(
    original: str, edits: Sequence[Edit]
) -> list[EditSpan]:
    """Map edits to character offsets.

    Each edit is assigned a distinct occurrence of its "before" text. When
    spans overlap, the longest span wins and the loser is dropped.
    """
    candidates: list[tuple[int, Edit, int, int]] = []
    for edit_index, edit in enumerate(edits):
        for located in _locate_all(original, edit.before):
            start, end = located
            candidates.append((edit_index, edit, start, end))
    candidates.sort(key=lambda c: (c[3] - c[2], -c[2]), reverse=True)
    used_edits: set[int] = set()
    kept: list[tuple[int, int, Edit]] = []
    for edit_index, edit, start, end in candidates:
        if edit_index in used_edits:
            continue
        if any(start < k_end and end > k_start for k_start, k_end, _ in kept):
            continue
        kept.append((start, end, edit))
        used_edits.add(edit_index)
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
    payload = "\x1f".join(
        [bundle_id, text, context_before, context_after, fingerprint]
    )
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
        return "too_short", (
            f"Selection is shorter than {MIN_PREVIEW_CHARS} characters."
        )
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
