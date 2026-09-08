"""Tests for the live proofread preview feature."""

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.live_preview import (  # noqa: E402
    Edit,
    EditSpan,
    PreviewCache,
    evaluate_trigger,
    map_edits_to_ranges,
    parse_preview_response,
    preview_cache_key,
    settings_fingerprint,
)


def _settings(**live):
    base = {
        "live_preview": {
            "enabled": True,
            "delay_ms": 900,
            "max_chars": 1500,
            "use_local_model": True,
        }
    }
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
        _settings(enabled=False),
        {"bundle_id": "com.apple.TextEdit"},
        "hello world",
        True,
        True,
        False,
        False,
    )
    assert decision == "disabled"
    assert "settings" in reason.lower()


def test_evaluate_trigger_unchanged_selection_skips():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.apple.TextEdit"},
        "hello world",
        True,
        True,
        False,
        True,
    )
    assert decision == "unchanged"


def test_evaluate_trigger_unsupported_app_skips():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.example.random"},
        "hello world",
        True,
        True,
        False,
        False,
    )
    assert decision == "unsupported_app"


def test_evaluate_trigger_empty_selection_skips():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.apple.TextEdit"},
        "",
        True,
        True,
        False,
        False,
    )
    assert decision == "empty"


def test_evaluate_trigger_runs():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.apple.TextEdit"},
        "this sentence has a problem",
        True,
        True,
        False,
        False,
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
