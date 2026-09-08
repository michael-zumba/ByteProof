"""Tests for the live proofread preview feature."""

import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from PyQt6.QtWidgets import QApplication

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


def _mock_completion(returned):
    def fake(*args, **kwargs):
        fake.calls.append((args, kwargs))
        return returned

    fake.calls = []
    return fake


def test_load_preview_prompt_has_json_contract():
    from src import logic

    prompt = logic.load_preview_prompt()
    assert '"edits"' in prompt and '"before"' in prompt


def test_preview_edits_once_uses_local_model_and_parses_edits(monkeypatch):
    from src import logic

    fake = _mock_completion(
        '{"edits":[{"before":"teh","after":"the","reason":"Spelling"}]}'
    )
    monkeypatch.setattr(logic, "_request_completion", fake)
    monkeypatch.setattr(
        logic,
        "resolve_provider_connection",
        lambda settings: (
            logic.LOCAL_MODEL_PROVIDER,
            "",
            "http://127.0.0.1:9999/v1",
            "phi4-mini",
        ),
    )
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {
            "enabled": True,
            "use_local_model": True,
            "max_chars": 1500,
        },
    }
    status, edits, meta = logic.preview_edits_once(
        settings,
        {"bundle_id": "com.apple.TextEdit", "name": "TextEdit", "pid": 1},
        "teh quick brown fox",
        "ctx before",
        "ctx after",
    )
    assert status == "ok"
    assert edits == [Edit("teh", "the", "Spelling")]
    assert meta["provider"] == logic.LOCAL_MODEL_PROVIDER
    assert "teh quick brown fox" in fake.calls[0][0][1]


def test_preview_edits_once_prefers_active_provider_when_configured(monkeypatch):
    from src import logic

    fake = _mock_completion("[]")
    monkeypatch.setattr(logic, "_request_completion", fake)
    monkeypatch.setattr(
        logic,
        "resolve_provider_connection",
        lambda settings: (
            "OpenAI",
            "sk-test",
            "https://api.openai.com/v1",
            "gpt-4o",
        ),
    )
    monkeypatch.setattr(logic, "get_access_status", lambda: {"tier": "licensed"})
    monkeypatch.setattr(logic, "provider_requires_api_key", lambda name: True)
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {
            "enabled": True,
            "use_local_model": False,
            "max_chars": 1500,
        },
    }
    status, edits, meta = logic.preview_edits_once(
        settings,
        {"bundle_id": "com.apple.TextEdit"},
        "hello world",
        "",
        "",
    )
    assert status == "ok"
    assert edits == []
    assert meta["provider"] == "OpenAI"


def test_preview_edits_once_parses_empty_reply_as_no_edits(monkeypatch):
    from src import logic

    monkeypatch.setattr(
        logic, "_request_completion", lambda *a, **k: "no issues"
    )
    monkeypatch.setattr(
        logic,
        "resolve_provider_connection",
        lambda settings: (
            logic.LOCAL_MODEL_PROVIDER,
            "",
            "http://x/v1",
            "m",
        ),
    )
    status, edits, _ = logic.preview_edits_once(
        {
            "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
            "live_preview": {
                "enabled": True,
                "use_local_model": True,
                "max_chars": 1500,
            },
        },
        {"bundle_id": "com.apple.TextEdit"},
        "hello world",
        "",
        "",
    )
    assert status == "ok"
    assert edits == []


def test_request_completion_survives_refactor(monkeypatch):
    """The extracted helper keeps the same OpenAI-compatible call shape."""
    from src import logic

    captured = {}

    response = mock.MagicMock()
    response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": "ok"}}]}
    ).encode("utf-8")
    urlopen = mock.MagicMock()
    urlopen.return_value.__enter__.return_value = response

    def fake_request(*args, **kwargs):
        captured["url"] = kwargs["url"]
        captured["payload"] = json.loads(kwargs["data"].decode("utf-8"))
        request = mock.MagicMock()
        request.full_url = kwargs["url"]
        request.data = kwargs["data"]
        return request

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("urllib.request.Request", fake_request)
    result = logic._request_completion(
        "sys", "user", "key", 64, "https://api.example.com/v1",
        "gpt-test", "FakeProvider", 0.2, None,
    )
    assert result == "ok"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["payload"]["messages"][0]["content"] == "sys"


def test_parse_ax_range_shapes():
    from src import generic_editing as ge

    assert ge._parse_ax_range((3, 4)) == (3, 4)
    assert ge._parse_ax_range(SimpleNamespace(location=3, length=4)) == (3, 4)
    assert ge._parse_ax_range(None) == (None, None)


def test_parse_ax_range_unwraps_ax_value(monkeypatch):
    from src import generic_editing as ge

    class FakeAS:
        kAXValueCFRangeType = "cfrange"

        @staticmethod
        def AXValueGetValue(value, kind, out):
            return True, (5, 6)

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    assert ge._parse_ax_range(SimpleNamespace()) == (5, 6)


def test_parse_ax_rect_struct_and_tuple_shapes():
    from src import generic_editing as ge

    struct = SimpleNamespace(
        origin=SimpleNamespace(x=1.0, y=2.0),
        size=SimpleNamespace(width=30.0, height=16.0),
    )
    assert ge._parse_ax_rect(struct) == (1.0, 2.0, 30.0, 16.0)
    assert ge._parse_ax_rect((5.0, 6.0, 7.0, 8.0)) == (
        5.0,
        6.0,
        7.0,
        8.0,
    )
    assert ge._parse_ax_rect(None) is None


def test_ax_bounds_for_range_guards_non_darwin(monkeypatch):
    from src.generic_editing import GenericTextEditor

    monkeypatch.setattr("src.generic_editing.SYSTEM", "Windows")
    editor = GenericTextEditor()
    assert editor.ax_bounds_for_range({"pid": 1}, 0, 5) == []


def test_ax_replace_range_guards_non_darwin(monkeypatch):
    from src.generic_editing import GenericTextEditor

    monkeypatch.setattr("src.generic_editing.SYSTEM", "Windows")
    editor = GenericTextEditor()
    ok, message = editor.ax_replace_range({"pid": 1}, 0, 5, "x")
    assert ok is False
    assert "macOS" in message


def test_span_at_point_hits_inside_rect():
    from PyQt6.QtCore import QPoint, QRect

    from src.live_overlay import OverlaySpan, span_at_point

    spans = [OverlaySpan("a", "b", "x", QRect(10, 10, 100, 20))]
    assert span_at_point(spans, QPoint(50, 15)) == 0
    assert span_at_point(spans, QPoint(200, 15)) is None


def test_popup_rows_track_changes_styles():
    from PyQt6.QtCore import QRect

    from src.live_overlay import OverlaySpan, popup_rows

    rows = popup_rows(
        OverlaySpan("teh", "the", "Spelling", QRect(0, 0, 10, 10))
    )
    assert ("old", "teh") in rows
    assert ("new", "the") in rows
    assert ("reason", "Spelling") in rows


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeEditor:
    def __init__(self, bundle_id: str, text: str):
        self.bundle_id = bundle_id
        self.text = text

    def frontmost_app(self):
        return {"bundle_id": self.bundle_id, "pid": 1, "name": "FakeApp"}

    def permission_status(self):
        return True, ""

    def selection_details(self, target):
        return {
            "text": self.text,
            "range": (0, len(self.text)),
            "context_before": "",
            "context_after": "",
        }


def test_service_decision_flow_skips_unchanged(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {
            "live_preview": {
                "enabled": True,
                "delay_ms": 900,
                "max_chars": 1500,
                "use_local_model": True,
            }
        }
    )
    monkeypatch.setattr(
        service, "_editor", _FakeEditor("com.apple.TextEdit", "this is a test sentence")
    )
    calls = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: calls.append(a))
    service._sample(now=1000.0)
    assert len(calls) == 0  # debounce: selection just changed
    service._sample(now=2000.0)
    assert len(calls) == 1
    service._sample(now=3000.0)
    assert len(calls) == 1


def test_service_applies_span_through_ax(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {
            "live_preview": {
                "enabled": True,
                "delay_ms": 900,
                "max_chars": 1500,
                "use_local_model": True,
            }
        }
    )
    applied = []

    class FakeEditor:
        def frontmost_app(self):
            return {
                "bundle_id": "com.apple.TextEdit",
                "pid": 9,
                "name": "TextEdit",
            }

        def ax_replace_range(self, target, start, length, text):
            applied.append((start, length, text))
            return True, "Applied."

    service._editor = FakeEditor()
    service._selection_start = 100
    service._apply_span(EditSpan("teh", "the", "Spelling", 0, 3))
    assert applied == [(100, 3, "the")]
