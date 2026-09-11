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
        "com.apple.iwork.pages",
        "com.apple.mail",
        "com.microsoft.outlook",
        "com.apple.textedit",
        "com.apple.notes",
    }
)

SELF_BUNDLE_MARKERS = ("bytemind", "byteproof")

# How long the selection must be stable before the preview is requested.
# 600 ms still filters out in-progress drags while making the panel feel
# noticeably quicker; the two-read confirmation is unchanged.
DEFAULT_DELAY_MS = 600
MIN_DELAY_MS = 400
MAX_DELAY_MS = 2000
DEFAULT_MAX_CHARS = 1500
MIN_PREVIEW_CHARS = 8
# A selection must also be a few words long before suggestions appear:
# a stray word or two is usually just a cursor placement, and pinging the
# model for it wastes tokens and interrupts the user.
MIN_PREVIEW_WORDS = 3
CONTEXT_CHARS = 200
PREVIEW_MAX_OUTPUT_TOKENS = 512
PREVIEW_MAX_EDITS = 12
# Selection poll cadence. The Accessibility element lookup is cached, so
# a faster tick costs little and cuts detection latency.
POLL_INTERVAL_MS = 250
CACHE_MAX = 64
UNDERLINE_COLOR_HEX = "#E23A5B"

# Failed previews retry silently a few times so a transient provider or
# local-model error does not require the user to reselect; after that the
# burst gives up until the selection changes.
RETRY_COOLDOWN_S = 5.0
RETRY_MAX_FAILURES = 3

# Apps whose editors never expose the selection through AX (Mail's WebKit
# compose view, Pages' canvas). For these the service falls back to a
# throttled, clipboard-preserving Cmd+C read; apply then pastes the fully
# corrected selection because no absolute range is available.
CLIPBOARD_FALLBACK_BUNDLE_IDS = frozenset(
    {"com.apple.mail", "com.apple.pages", "com.apple.iwork.pages"}
)
CLIPBOARD_READ_INTERVAL_S = 2.5
CLIPBOARD_READ_BACKOFF_S = 8.0

_FUZZY_RATIO_FLOOR = 0.6

# A single edit may never swallow the whole selection: spans larger than
# 70% of the selection (or 80 chars, whichever is larger) are whole-text
# rewrites in disguise and are dropped, so apply stays word/phrase-level.
MAX_EDIT_SPAN_FRACTION = 0.7
MIN_EDIT_SPAN_CHARS = 80


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


def _fuzzy_locate(text: str, needle: str) -> tuple[int, int] | None:
    """Return the real span of the best same-length word-boundary match.

    Fuzzy matching only runs when the exact "before" text is absent, so it
    must stay conservative: candidates whose words total a different length
    than the needle are ignored (otherwise a lookalike such as "go" could be
    matched for "goes" and apply would replace the wrong span), and the
    matched words' actual extents are used rather than the needle's length.
    """
    needle_words = re.findall(r"\w+", needle)
    if not needle_words:
        return None
    word_matches = list(re.finditer(r"\w+", text))
    if not word_matches:
        return None
    needle_joined = " ".join(needle_words)
    best_ratio = _FUZZY_RATIO_FLOOR
    best: int | None = None
    for i in range(len(word_matches) - len(needle_words) + 1):
        group = word_matches[i : i + len(needle_words)]
        candidate = " ".join(match.group(0) for match in group)
        if len(candidate) != len(needle_joined):
            continue
        ratio = difflib.SequenceMatcher(None, needle_joined, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = i
    if best is None:
        return None
    start = word_matches[best].start()
    end = word_matches[best + len(needle_words) - 1].end()
    return start, end


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
    return [fuzzy]


def utf16_to_codepoint_index(text: str, utf16_index: int) -> int:
    """Map a UTF-16 code-unit index to a Python code-point index.

    macOS text ranges are UTF-16 based (NSString lengths), while Python
    strings index by code points. Any emoji or astral character before an
    offset makes the two drift apart and every edit lands in the wrong
    place, so all AX ranges must be converted at read time and back again
    when writing.
    """
    if utf16_index <= 0:
        return 0
    encoded = text.encode("utf-16-le")
    total_units = len(encoded) // 2
    if utf16_index >= total_units:
        return len(text)
    prefix = encoded[: utf16_index * 2].decode("utf-16-le")
    return len(prefix)


def codepoint_to_utf16_index(text: str, cp_index: int) -> int:
    """Map a Python code-point index to a UTF-16 code-unit index."""
    if cp_index <= 0:
        return 0
    if cp_index >= len(text):
        return len(text.encode("utf-16-le")) // 2
    return len(text[:cp_index].encode("utf-16-le")) // 2


def word_visible_to_doc(
    rel: int,
    selection_start: int,
    selection_end: int,
    hidden_spans: Sequence[tuple[int, int]],
    field_spans: Sequence[tuple[int, int, str]],
) -> int | None:
    """Map a visible-text offset to an absolute Word document position.

    Word's ``content`` string omits tracked deletions and field-code
    characters, but document positions count them, so a visible offset after
    such a span must be shifted by ``doc_len - visible_len``. Spans are
    walked in document order; every span before the offset contributes its
    extra characters. Returns None when the offset lands inside a span's
    visible text, where the mapping is unreliable.
    """
    items: list[tuple[int, int, int]] = []
    for start, end in hidden_spans:
        if start >= end:
            continue
        start = max(start, selection_start)
        end = min(end, selection_end)
        if end <= start:
            continue
        items.append((start, end, 0))
    for start, end, text in field_spans:
        if start >= end:
            continue
        start = max(start, selection_start)
        end = min(end, selection_end)
        if end <= start:
            continue
        items.append((start, end, len(text)))
    extra = 0
    for start, end, visible_len in sorted(items, key=lambda item: item[0]):
        visible_start = (start - selection_start) - extra
        if rel < visible_start:
            break
        if rel < visible_start + visible_len:
            return None
        extra += (end - start) - visible_len
    return selection_start + rel + extra


def _split_span_word_level(
    original: str, start: int, edit: Edit, max_parts: int = 8
) -> list[tuple[int, int, str, str]]:
    """Split a large edit into word-level sub-edits aligned to the text.

    Uses a token diff between the edit's before/after text and maps each
    changed fragment back to its exact character offset inside the original
    span. Returns [] when the alignment cannot be verified — the caller then
    drops the span entirely, so a whole-text rewrite can never apply.
    """
    before_tokens = re.findall(r"\S+|\s+", edit.before)
    after_tokens = re.findall(r"\S+|\s+", edit.after)
    if not before_tokens or not after_tokens:
        return []
    matcher = difflib.SequenceMatcher(None, before_tokens, after_tokens)
    offsets: list[int] = []
    total = 0
    for token in before_tokens:
        offsets.append(total)
        total += len(token)
    subs: list[tuple[int, int, str, str]] = []
    span_end = start + len(edit.before)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_text = "".join(before_tokens[i1:i2])
        new_text = "".join(after_tokens[j1:j2])
        if not old_text.strip() or not new_text.strip() or old_text == new_text:
            continue
        sub_start = start + offsets[i1]
        sub_end = sub_start + len(old_text)
        if sub_end > span_end or sub_start >= sub_end:
            return []
        if original[sub_start:sub_end] != old_text:
            return []
        subs.append((sub_start, sub_end, old_text, new_text))
    if not subs or len(subs) > max_parts:
        return []
    return subs


def map_edits_to_ranges(
    original: str, edits: Sequence[Edit]
) -> list[EditSpan]:
    """Map edits to character offsets.

    Each edit is assigned a distinct occurrence of its "before" text. When
    spans overlap, the longest span wins and the loser is dropped. Spans
    larger than the granularity cap are split into word-level sub-edits;
    if a split cannot be verified it is dropped, so a whole-text rewrite
    never reaches the document.
    """
    max_span = max(
        MIN_EDIT_SPAN_CHARS, int(len(original) * MAX_EDIT_SPAN_FRACTION)
    )
    candidates: list[tuple[bool, int, Edit, int, int]] = []
    for edit_index, edit in enumerate(edits):
        for located in _locate_all(original, edit.before):
            start, end = located
            if end - start <= max_span:
                candidates.append((True, edit_index, edit, start, end))
            else:
                for sub in _split_span_word_level(original, start, edit):
                    sub_start, sub_end, sub_before, sub_after = sub
                    if sub_end - sub_start <= max_span:
                        candidates.append(
                            (
                                False,
                                edit_index,
                                Edit(sub_before, sub_after, edit.reason),
                                sub_start,
                                sub_end,
                            )
                        )
    candidates.sort(key=lambda c: (c[4] - c[3], -c[3]), reverse=True)
    used_edits: set[int] = set()
    kept: list[tuple[int, int, Edit]] = []
    for is_whole, edit_index, edit, start, end in candidates:
        if is_whole and edit_index in used_edits:
            continue
        if any(start < k_end and end > k_start for k_start, k_end, _ in kept):
            continue
        kept.append((start, end, edit))
        if is_whole:
            used_edits.add(edit_index)
    kept.sort(key=lambda k: k[0])
    return [
        EditSpan(edit.before, edit.after, edit.reason, start, end)
        for start, end, edit in kept
    ]


def apply_edits_to_text(original: str, spans: Sequence[EditSpan]) -> str:
    """Apply mapped spans to the original text (right-to-left, no overlap)."""
    text = original
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        if span.start < 0 or span.end < span.start or span.end > len(text):
            continue
        text = text[: span.start] + span.after + text[span.end :]
    return text


def settings_fingerprint(settings: dict[str, Any]) -> str:
    live = settings.get("live_preview", {})
    payload = json.dumps(
        {
            "enabled": live.get("enabled", True),
            "delay_ms": live.get("delay_ms", DEFAULT_DELAY_MS),
            "max_chars": live.get("max_chars", DEFAULT_MAX_CHARS),
            "min_words": int(live.get("min_words", MIN_PREVIEW_WORDS)),
            "use_local_model": live.get("use_local_model", True),
            "style": live.get("style", "strict"),
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
    payload = f"{bundle_id}\x1f{text}\x1f{context_before}\x1f{context_after}\x1f{fingerprint}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def app_allowed(live: dict[str, Any], target: dict[str, Any]) -> bool:
    """Whether this app may trigger suggestions.

    ``live_preview.app_rules`` maps an app identifier (a bundle id, an app-name
    fragment, or ``"*"`` for everything else) to enabled/disabled. An empty
    map means every app is allowed, which is the default.
    """
    rules = live.get("app_rules")
    if not isinstance(rules, dict) or not rules:
        return True
    bundle = str(target.get("bundle_id", "")).lower()
    name = str(target.get("name", "")).lower()
    normalised = {
        str(key).strip().lower(): bool(value) for key, value in rules.items()
    }
    # The bundle id is authoritative and is checked across *all* rules before
    # any name fragment: returning on the first key that matched in dict order
    # let a stale name-based rule ("mail") shadow the app's own switch
    # ("com.apple.mail"), so unchecking an app in Live Check did nothing.
    if bundle and bundle in normalised:
        return normalised[bundle]
    for marker, enabled in normalised.items():
        if not marker or marker == "*":
            continue
        # A bundle id only ever matches exactly: letting one take part in the
        # name test let "com.apple.mail" decide an unrelated app whose name
        # merely contains a fragment of it.
        if "." in marker:
            continue
        if len(marker) > 3 and marker in name:
            return enabled
    return bool(normalised.get("*", True))


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
        return "disabled", "Live Check is off in Settings."
    if not has_permission:
        return "no_permission", "Accessibility permission is required."
    bundle = str(target.get("bundle_id", "")).lower()
    name = str(target.get("name", "")).lower()
    if any(marker in bundle or marker in name for marker in SELF_BUNDLE_MARKERS):
        return "self", "ByteProof itself is excluded from live check."
    if not app_allowed(live, target):
        return "app_disabled", "Live Check is turned off for this app."
    if not selected_text or not selected_text.strip():
        return "empty", "No text selected."
    length = len(selected_text.strip())
    if length < MIN_PREVIEW_CHARS:
        return "too_short", (
            f"Selection is shorter than {MIN_PREVIEW_CHARS} characters."
        )
    min_words = int(live.get("min_words", MIN_PREVIEW_WORDS))
    words = len([word for word in selected_text.split() if word.strip()])
    if words < min_words:
        return "too_short", (
            f"Selection is shorter than {min_words} words."
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
