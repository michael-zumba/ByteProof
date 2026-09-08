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


def test_parse_keeps_duplicate_pairs_for_repeated_occurrences():
    raw = (
        '[{"before":"teh","after":"the","reason":"S"},'
        '{"before":"teh","after":"the","reason":"S"}]'
    )
    assert len(parse_preview_response(raw)) == 2


def test_map_exact_edits():
    spans = map_edits_to_ranges("teh cat sat", [Edit("teh", "the", "Spelling")])
    assert spans == [EditSpan("teh", "the", "Spelling", 0, 3)]


def test_map_repeated_edits_hit_distinct_occurrences():
    spans = map_edits_to_ranges(
        "teh cat and teh mat",
        [Edit("teh", "the", "S"), Edit("teh", "the", "S")],
    )
    assert [(s.start, s.end) for s in spans] == [(0, 3), (12, 15)]


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


def test_evaluate_trigger_any_app_with_selection_runs():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.example.random"},
        "hello world",
        True,
        True,
        False,
        False,
    )
    assert decision == "run"


def test_evaluate_trigger_skips_byteproof_itself():
    decision, _ = evaluate_trigger(
        _settings(),
        {"bundle_id": "com.bytemind.byteproof", "name": "ByteProof"},
        "hello world",
        True,
        True,
        False,
        False,
    )
    assert decision == "self"


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


def test_evaluate_trigger_supports_target_bundle_ids():
    for bundle_id in (
        "com.microsoft.Word",
        "com.apple.iWork.Pages",
        "com.apple.mail",
        "com.microsoft.Outlook",
        "com.apple.TextEdit",
        "com.apple.Notes",
    ):
        decision, _ = evaluate_trigger(
            _settings(),
            {"bundle_id": bundle_id},
            "this sentence has a problem",
            True,
            True,
            False,
            False,
        )
        assert decision == "run", bundle_id


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


def test_span_at_point_has_wide_hover_target():
    from PyQt6.QtCore import QPoint, QRect

    from src.live_overlay import OverlaySpan, span_at_point

    spans = [OverlaySpan("a", "b", "x", QRect(10, 10, 100, 20))]
    assert span_at_point(spans, QPoint(50, 36)) == 0  # below rect, in pad


def test_diff_html_pinpoints_changed_words():
    from src.live_overlay import diff_html

    rendered = diff_html("He go to school", "He goes to school")
    assert "go</s>" in rendered
    assert "goes</span>" in rendered
    assert "He" in rendered and "to school" in rendered


def test_diff_html_replacement_and_escape():
    from src.live_overlay import diff_html

    rendered = diff_html("teh cat <x>", "the cat <x>")
    assert "teh</s>" in rendered and "the</span>" in rendered
    assert "&lt;x&gt;" in rendered


def test_popup_rows_track_changes_styles():
    from PyQt6.QtCore import QRect

    from src.live_overlay import OverlaySpan, popup_rows

    rows = popup_rows(
        OverlaySpan("teh", "the", "Spelling", QRect(0, 0, 10, 10))
    )
    assert ("old", "teh") in rows
    assert ("new", "the") in rows
    assert ("reason", "Spelling") in rows


def test_overlay_renders_dashed_rose_underline():
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    from src.live_overlay import LiveOverlay, OverlaySpan

    overlay = LiveOverlay()
    overlay.set_spans([OverlaySpan("teh", "the", "S", QRect(100, 100, 60, 20))])
    image = QImage(160, 120, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    overlay.render(painter)
    painter.end()
    rose = QColor("#E23A5B")
    found = any(
        image.pixelColor(x, y) == rose
        for y in range(image.height())
        for x in range(image.width())
    )
    assert found, "no rose-colored underline pixels were rendered"


def test_overlay_refresh_keeps_popup_open():
    from PyQt6.QtCore import QRect

    from src.live_overlay import LiveOverlay, OverlaySpan

    overlay = LiveOverlay()
    overlay.set_spans([OverlaySpan("teh", "the", "S", QRect(100, 100, 60, 20))])
    overlay.show_popup(0)
    assert overlay._popup is not None
    overlay.set_spans([OverlaySpan("teh", "the", "S", QRect(100, 120, 60, 20))])
    assert overlay._popup is not None


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


class _MutableEditor(_FakeEditor):
    """Like _FakeEditor, but the selected text can change between samples."""


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


def test_service_applies_mark_through_ax(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
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
    service._marks = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._mark_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._mark_is_word = False
    service._apply_mark(0)
    assert applied == [(0, 3, "the")]


def test_service_apply_all_applies_each_mark_with_delta(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    applied = []

    class FakeEditor:
        def ax_replace_range(self, target, start, length, text):
            applied.append((start, length, text))
            return True, "Applied."

    service._editor = FakeEditor()
    service._marks = [
        EditSpan("teh", "there", "Spelling", 0, 3),
        EditSpan("where", "was", "Grammar", 30, 35),
    ]
    service._mark_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._mark_is_word = False
    service._apply_all()
    assert applied == [(0, 3, "there"), (32, 5, "was")]


def _fake_subprocess_run(stdout: bytes = b"OK"):
    completed = mock.Mock()
    completed.returncode = 0
    completed.stdout = stdout
    completed.stderr = b""
    return completed


def test_word_apply_live_edit_builds_subrange_script(monkeypatch):
    import subprocess

    from src import word_integration as wi

    captured = []

    def fake_run(args, **kwargs):
        captured.append(kwargs.get("input"))
        return _fake_subprocess_run()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "src.generic_editing._mac_set_clipboard", lambda text: None
    )
    integration = wi.MacOSWordIntegration()
    ok, _ = integration.apply_live_edit(500, 10, 13, "the")
    assert ok is True
    script = captured[-1].decode("utf-8")
    assert "start 510 end 513" in script
    assert "clipboard as text" in script


def test_word_set_live_underline_builds_dotted_script(monkeypatch):
    import subprocess

    from src import word_integration as wi

    captured = []

    def fake_run(args, **kwargs):
        captured.append(kwargs.get("input"))
        return _fake_subprocess_run()

    monkeypatch.setattr(subprocess, "run", fake_run)
    integration = wi.MacOSWordIntegration()
    ok, _ = integration.set_live_underline(500, 10, 13, True)
    assert ok is True
    script = captured[-1].decode("utf-8")
    assert "underline dot dot dash" in script
    assert "start 510 end 513" in script
    assert "set track revisions of active document to false" in script
    assert "58082, 14906, 23387" in script


def test_load_runtime_settings_includes_live_preview_defaults(monkeypatch, tmp_path):
    from src import settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    loaded = settings_mod.load_runtime_settings()
    assert loaded["live_preview"] == {
        "enabled": True,
        "delay_ms": 900,
        "max_chars": 1500,
        "use_local_model": True,
    }


def test_load_runtime_settings_merges_existing_live_preview(monkeypatch, tmp_path):
    from src import settings as settings_mod

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"live_preview": {"enabled": False}}))
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(path))
    loaded = settings_mod.load_runtime_settings()
    assert loaded["live_preview"]["enabled"] is False
    assert loaded["live_preview"]["delay_ms"] == 900


def _live_settings():
    return {
        "live_preview": {
            "enabled": True,
            "delay_ms": 900,
            "max_chars": 1500,
            "use_local_model": True,
        }
    }


def test_service_full_cycle_with_fake_provider(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat on teh mat")
    editor.rects = [(10.0, 10.0, 30.0, 16.0)]
    editor.applied = []
    editor.ax_bounds_for_range = lambda target, start, length: [editor.rects[0]]

    def replace(target, start, length, text):
        editor.applied.append((start, length, text))
        return True, "Applied."

    editor.ax_replace_range = replace
    monkeypatch.setattr(service, "_editor", editor)
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key, selection_start, is_word):
        service._on_done(result, key, text, target, selection_start, is_word)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=10.0)
    service._sample(now=11.0)
    assert service._marks and service._marks[0].before == "teh"
    service._apply_mark(0)
    assert editor.applied == [(0, 3, "the")]


def test_preview_cache_prevents_duplicate_provider_calls(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    calls = []
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key, selection_start, is_word):
        calls.append(text)
        service._on_done(result, key, text, target, selection_start, is_word)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    service._sample(now=3.0)
    service._sample(now=4.0)
    assert len(calls) == 1
    # Even when the unchanged-selection guard is bypassed, the cache key
    # must prevent a second provider call for the same text.
    service._previewed_text = ""
    service._sample(now=5.0)
    assert len(calls) == 1


def test_service_renders_card_when_bounds_unavailable(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key, selection_start, is_word):
        service._on_done(result, key, text, target, selection_start, is_word)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert service._word_card is not None
    assert len(service._marks) == 1


def test_service_repreviews_same_selection_after_deselect(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _MutableEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    calls = []
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key, selection_start, is_word):
        calls.append(text)
        service._on_done(result, key, text, target, selection_start, is_word)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert len(calls) == 1
    editor.text = ""
    service._sample(now=3.0)
    service._sample(now=4.0)
    editor.text = "teh cat sat"
    service._sample(now=5.0)
    service._sample(now=6.0)
    assert len(calls) == 1  # cache, not a new provider call
    assert service._marks and service._marks[0].before == "teh"


def test_service_keeps_marks_after_deselect(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _MutableEditor("com.apple.TextEdit", "teh cat sat")
    editor.rects = [(10.0, 10.0, 30.0, 16.0)]
    editor.ax_bounds_for_range = lambda target, start, length: [editor.rects[0]]
    monkeypatch.setattr(service, "_editor", editor)
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key, selection_start, is_word):
        service._on_done(result, key, text, target, selection_start, is_word)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert service._marks and service._overlay._spans
    editor.text = ""
    service._sample(now=3.0)
    assert service._marks
    assert service._overlay._spans


def test_card_and_popup_titles_are_suggested_changes():
    from PyQt6.QtWidgets import QLabel

    from src.live_preview import EditSpan
    from src.live_overlay import WordSuggestionCard

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    titles = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.text() == "Suggested changes"
    ]
    assert titles


class _FakeCursor:
    def __init__(self, point):
        self.p = point

    def pos(self):
        return self.p


def test_word_card_keeps_position_across_refresh(monkeypatch):
    from PyQt6.QtCore import QPoint

    from src import live_service as ls
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    fake = _FakeCursor(QPoint(100, 100))
    monkeypatch.setattr(ls, "QCursor", fake)
    spans = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._show_card(spans)
    first_position = service._word_card.pos()
    fake.p = QPoint(700, 700)
    service._show_card(spans)
    assert service._word_card.pos() == first_position


def test_card_rebuild_leaves_exactly_one_of_each_control():
    from PyQt6.QtWidgets import QLabel, QPushButton

    from src.live_preview import EditSpan
    from src.live_overlay import WordSuggestionCard

    def walk_items(layout):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            sub = item.layout()
            if sub is not None:
                yield from walk_items(sub)
            widget = item.widget()
            if widget is not None:
                yield widget
                if widget.layout() is not None:
                    yield from walk_items(widget.layout())

    card = WordSuggestionCard()
    spans = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("where", "was", "Grammar", 30, 35),
    ]
    for _ in range(3):
        card.set_spans(spans)
    widgets = list(walk_items(card.layout()))
    buttons = [
        button.text()
        for button in widgets
        if isinstance(button, QPushButton)
    ]
    titles = [
        label.text()
        for label in widgets
        if isinstance(label, QLabel)
        if label.text() == "Suggested changes"
    ]
    assert buttons.count("Apply") == 2
    assert buttons.count("Apply all") == 1
    assert buttons.count("×") == 1
    assert len(titles) == 1


def test_service_accumulates_marks_across_selections(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _MutableEditor("com.apple.TextEdit", "teh cat sat")
    editor.rects = [(10.0, 10.0, 30.0, 16.0)]
    editor.ax_bounds_for_range = lambda target, start, length: [editor.rects[0]]
    monkeypatch.setattr(service, "_editor", editor)
    results = iter(
        [
            {
                "status": "ok",
                "edits": [lp.Edit("teh", "the", "Spelling")],
                "meta": {"provider": "fake"},
            },
            {
                "status": "ok",
                "edits": [lp.Edit("where", "was", "Grammar")],
                "meta": {"provider": "fake"},
            },
        ]
    )

    def fake_spawn(target, text, details, key, selection_start, is_word):
        service._on_done(
            next(results), key, text, target, selection_start, is_word
        )

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert [s.before for s in service._marks] == ["teh"]
    editor.text = "it where fine"
    service._sample(now=3.0)
    service._sample(now=4.0)
    assert [s.before for s in service._marks] == ["teh", "where"]
