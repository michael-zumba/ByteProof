"""Tests for the live proofread preview feature."""

import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

import pytest
from PyQt6.QtWidgets import QApplication

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.live_preview import (
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


def test_diff_html_replacement_shows_arrow():
    from src.live_overlay import diff_html

    rendered = diff_html("teh cat", "the cat")
    assert "→" in rendered


def test_diff_html_dims_unchanged_context():
    from src.live_overlay import diff_html
    from src.ui_theme import CONTEXT_STYLE

    rendered = diff_html("the cat sat", "the cat sat")
    assert CONTEXT_STYLE.split(":")[1].split(";")[0].strip() in rendered


def test_card_drags_by_header(monkeypatch):
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    from src.live_overlay import WordSuggestionCard

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    card.move(100, 100)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(40, 20),  # inside the header zone
        QPointF(140, 120),  # global position
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mousePressEvent(press)
    assert card._dragging is True
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(40, 20),
        QPointF(160, 140),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mouseMoveEvent(move)
    assert card.pos() == QPoint(120, 120)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(40, 20),
        QPointF(160, 140),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mouseReleaseEvent(release)
    assert card._dragging is False


def test_card_pop_in_animation_completes():
    from PyQt6.QtTest import QTest

    from src.live_overlay import WordSuggestionCard

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    card.move(200, 200)
    card.show()
    target = card.geometry()
    card.pop_in()
    assert card.windowOpacity() < 1.0  # animation started dimmed
    QTest.qWait(350)
    assert card.windowOpacity() == 1.0
    assert card.geometry() == target


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
            "found": True,
            "editable": True,
            "role": "AXTextArea",
        }


class _MutableEditor(_FakeEditor):
    """Like _FakeEditor, but the selected text and range can change."""

    def __init__(self, bundle_id: str, text: str):
        super().__init__(bundle_id, text)
        self.range = (0, len(text))
        self.applied: list[tuple[int, int, str]] = []

    def selection_details(self, target):
        return {
            "text": self.text,
            "range": self.range,
            "context_before": "",
            "context_after": "",
            "found": True,
            "editable": True,
            "role": "AXTextArea",
        }

    def ax_replace_range(self, target, start, length, new, allow_direct_paste=False):
        self.applied.append((start, length, new))
        return True, "Applied."


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


def test_service_applies_one_suggestion_through_ax(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    applied = []

    class FakeEditor:
        def selection_details(self, target):
            return {
                "text": "teh",
                "range": (100, 3),
                "context_before": "",
                "context_after": "",
            }

        def ax_replace_range(self, target, start, length, text, allow_direct_paste=False):
            applied.append((start, length, text))
            return True, "Applied."

    service._editor = FakeEditor()
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_start = 100
    service._selection_is_word = False
    service._selection_text = "teh"
    service._seen_text = "teh"
    service._apply_one(0)
    assert applied == [(100, 3, "the")]


def test_service_apply_all_applies_each_suggestion_with_delta(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    text = "teh quick brown fox jumps over where"
    applied = []

    class FakeEditor:
        def selection_details(self, target):
            return {
                "text": text,
                "range": (0, len(text)),
                "context_before": "",
                "context_after": "",
            }

        def ax_replace_range(self, target, start, length, new, allow_direct_paste=False):
            applied.append((start, length, new))
            return True, "Applied."

    service._editor = FakeEditor()
    service._pending = [
        EditSpan("teh", "there", "Spelling", 0, 3),
        EditSpan("where", "was", "Grammar", 31, 36),
    ]
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_start = 0
    service._selection_is_word = False
    service._selection_text = text
    service._seen_text = text
    service._apply_all()
    assert applied == [(0, 3, "there"), (33, 5, "was")]


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
    assert "track revisions of active document to false" in script


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
        "style": "strict",
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

    def replace(target, start, length, text, allow_direct_paste=False):
        editor.applied.append((start, length, text))
        return True, "Applied."

    editor.ax_replace_range = replace
    monkeypatch.setattr(service, "_editor", editor)
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key):
        service._on_done(result, key, text)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=10.0)
    service._sample(now=11.0)
    assert service._pending and service._pending[0].before == "teh"
    service._apply_one(0)
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

    def fake_spawn(target, text, details, key):
        calls.append(text)
        service._on_done(result, key, text)

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

    def fake_spawn(target, text, details, key):
        service._on_done(result, key, text)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert service._panel is not None
    assert len(service._pending) == 1


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

    def fake_spawn(target, text, details, key):
        calls.append(text)
        service._on_done(result, key, text)

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
    assert service._pending and service._pending[0].before == "teh"


def test_card_and_popup_titles_are_suggested_changes():
    from PyQt6.QtWidgets import QLabel

    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

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

    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    spans = [EditSpan("teh", "the", "Spelling", 0, 3)]
    card = WordSuggestionCard()
    card.set_spans(spans)
    card.place_near(QPoint(100, 100))
    first_position = card.pos()
    card.set_spans(spans)
    assert card.pos() == first_position


def test_service_shows_panel_on_result_and_hides_on_selection_change(
    monkeypatch,
):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _MutableEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    result = {
        "status": "ok",
        "edits": [lp.Edit("teh", "the", "Spelling")],
        "meta": {"provider": "fake"},
    }

    def fake_spawn(target, text, details, key):
        service._on_done(result, key, text)

    monkeypatch.setattr(service, "_spawn_preview", fake_spawn)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert service._panel is not None
    assert service._panel.isVisible()
    editor.text = ""
    service._sample(now=3.0)
    assert service._panel.isVisible() is True  # one read: not yet trusted
    service._sample(now=3.5)
    assert service._panel.isVisible() is False  # two agreeing reads: hide


def test_apply_one_shifts_and_keeps_remaining(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    text = "teh quick brown fox jumps over where"

    class FakeEditor:
        def __init__(self):
            self.text = text
            self.applied = []

        def selection_details(self, target):
            return {
                "text": self.text,
                "range": (0, len(self.text)),
                "context_before": "",
                "context_after": "",
            }

        def ax_replace_range(self, target, start, length, new, allow_direct_paste=False):
            self.applied.append((start, length, new))
            self.text = self.text[:start] + new + self.text[start + length :]
            return True, "Applied."

    editor = FakeEditor()
    service._editor = editor
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_start = 0
    service._selection_is_word = False
    service._selection_text = text
    service._seen_text = text
    service._show_result(
        [
            EditSpan("teh", "there", "Spelling", 0, 3),
            EditSpan("where", "was", "Grammar", 31, 36),
        ]
    )
    service._apply_one(0)
    assert editor.applied == [(0, 3, "there")]
    # The document now holds the expected post-edit text under the same
    # selection, so the panel stays open with the remaining suggestion
    # shifted by the length delta (len("there") - len("teh") == 2).
    assert [(s.before, s.start, s.end) for s in service._pending] == [
        ("where", 33, 38)
    ]
    assert service._panel is not None and service._panel.isVisible()


def test_card_rebuild_leaves_exactly_one_of_each_control():
    from PyQt6.QtWidgets import QLabel, QPushButton

    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

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
        if label.text() and label.text().startswith("Suggested changes")
    ]
    assert buttons.count("Apply") == 2
    assert buttons.count("Apply all") == 1
    assert buttons.count("×") == 1 + len(spans)  # close + row dismissals
    assert len(titles) == 1
    assert titles[0] == "Suggested changes (2)"


# --- fixes after the 1.9.0-beta.3 suggestion-panel pivot ---


def test_word_visible_to_doc_without_spans_is_identity():
    from src.live_preview import word_visible_to_doc

    assert word_visible_to_doc(5, 100, 200, [], []) == 105


def test_word_visible_to_doc_shifts_past_hidden_deletion():
    from src.live_preview import word_visible_to_doc

    # 3 tracked-deleted characters at doc 110..113 (visible length 0).
    assert word_visible_to_doc(5, 100, 200, [(110, 113)], []) == 105
    assert word_visible_to_doc(12, 100, 200, [(110, 113)], []) == 115


def test_word_visible_to_doc_accounts_for_field_codes():
    from src.live_preview import word_visible_to_doc

    # Field doc 110..118 whose visible result is "c1" (2 of 8 chars visible).
    assert word_visible_to_doc(12, 100, 200, [], [(110, 118, "c1")]) == 118
    assert word_visible_to_doc(11, 100, 200, [], [(110, 118, "c1")]) is None


def test_word_visible_to_doc_combines_hidden_and_fields():
    from src.live_preview import word_visible_to_doc

    hidden = [(105, 107)]  # 2 hidden chars
    fields = [(110, 118, "x")]  # 7 extra chars
    assert word_visible_to_doc(20, 100, 200, hidden, fields) == 129
    assert word_visible_to_doc(5, 100, 200, hidden, fields) == 107


def test_word_visible_to_doc_clamps_spans_to_selection():
    from src.live_preview import word_visible_to_doc

    assert word_visible_to_doc(5, 100, 120, [(90, 110)], [(120, 140, "x")]) == 115


def test_map_fuzzy_rejects_length_mismatched_lookalikes():
    # "goes" must not fuzzy-match the shorter word "go": replacing the
    # needle-length span would eat the following word ("go t" -> "goes").
    spans = map_edits_to_ranges(
        "He go to school", [Edit("goes", "go", "Grammar")]
    )
    assert spans == []


def test_service_retries_after_failure_then_gives_up(monkeypatch):
    from src import live_service as ls
    from src.live_preview import RETRY_COOLDOWN_S

    service = ls.LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    spawns = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawns.append(a))

    errors = []
    service.preview_error.connect(errors.append)
    service._sample(now=1.0)  # records the selection (debounce window)
    service._sample(now=2.0)  # spawn #1
    assert len(spawns) == 1

    service._on_failed("boom")
    assert len(errors) == 1  # burst-start toast only
    service._sample(now=2.5)  # still cooling down
    assert len(spawns) == 1
    service._sample(now=2.0 + RETRY_COOLDOWN_S + 0.1)
    assert len(spawns) == 2

    service._on_failed("boom")
    service._sample(now=2.0 + 2 * (RETRY_COOLDOWN_S + 0.1))
    assert len(spawns) == 3

    service._on_failed("boom")  # RETRY_MAX_FAILURES reached -> give up
    service._sample(now=2.0 + 3 * (RETRY_COOLDOWN_S + 0.1))
    assert len(spawns) == 3
    assert len(errors) == 1  # no toast spam across retries

    editor.text = "a brand new selection"
    service._sample(now=100.0)  # selection change resets the burst
    service._sample(now=101.0)
    assert len(spawns) == 4


def test_service_reanchors_when_selection_moved_during_preview(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _MutableEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    spawns = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawns.append(a))
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert len(spawns) == 1
    # While the provider call runs, the user re-selects the same phrase at a
    # different position in the document. Applying at the stale position
    # would corrupt the document.
    editor.range = (500, 11)
    _target, text, _details, key = spawns[0]
    service._on_done(
        {
            "status": "ok",
            "edits": [lp.Edit("teh", "the", "Spelling")],
            "meta": {},
        },
        key,
        text,
    )
    assert service._selection_start == 500
    service._apply_one(0)
    assert editor.applied == [(500, 3, "the")]


def test_service_apply_all_reports_partial_failure(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    text = "teh cat sat"

    class FakeEditor:
        def __init__(self):
            self.fail_next = False

        def selection_details(self, target):
            return {
                "text": text,
                "range": (0, len(text)),
                "context_before": "",
                "context_after": "",
            }

        def ax_replace_range(self, target, start, length, new, allow_direct_paste=False):
            if self.fail_next:
                self.fail_next = False
                return False, "Could not apply."
            return True, "Applied."

    editor = FakeEditor()
    editor.fail_next = True
    service._editor = editor
    service._pending = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("cat", "dog", "Word choice", 4, 7),
    ]
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_start = 0
    service._selection_is_word = False
    service._selection_text = text
    service._seen_text = text
    messages = []
    service.apply_done.connect(messages.append)
    service._apply_all()
    assert messages == ["Applied 1 of 2 suggestions."]


def test_preview_worker_cancel_event_aborts_and_signals(monkeypatch):
    from src import live_service as ls
    from src.logic import TaskCancelledError

    cancel = threading.Event()
    cancel.set()
    captured = {}

    def fake_preview(settings, target, selected, before, after, cancel_event=None):
        captured["cancel_event"] = cancel_event
        raise TaskCancelledError()

    monkeypatch.setattr("src.logic.preview_edits_once", fake_preview)
    worker = ls.PreviewWorker({}, {}, "hello world", "", "", cancel)
    outcomes = []
    worker.done.connect(lambda result: outcomes.append(("done", result)))
    worker.failed.connect(lambda msg: outcomes.append(("failed", msg)))
    worker.cancelled.connect(lambda: outcomes.append(("cancelled", None)))
    worker.run()  # synchronous: direct connections deliver immediately
    assert outcomes == [("cancelled", None)]
    assert captured["cancel_event"] is cancel


def test_service_word_apply_compensates_hidden_characters(monkeypatch):
    from src import word_integration as wi
    from src.live_service import LivePreviewService

    class FakeWord:
        def __init__(self):
            self.applied = []
            self.text = "teh cat sat"
            self.start = 100
            self.end = 120
            self.hidden_calls = 0

        def get_selection_info(self):
            return self.text, self.start, self.end, "", ""

        def selection_has_fields(self):
            return False

        def get_selection_field_spans(self):
            return []

        def get_selection_hidden_spans(
            self, start, end, exclude_spans=None, max_hidden=0
        ):
            self.hidden_calls += 1
            return [(104, 107)]

        def apply_live_edit(self, selection_start, rel_start, rel_end, replacement):
            self.applied.append((rel_start, rel_end, replacement))
            return True, "Applied."

    word = FakeWord()
    monkeypatch.setattr(wi, "get_word_integration", lambda: word)
    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    service._selection_is_word = True
    service._selection_target = {
        "bundle_id": "com.microsoft.Word",
        "pid": 9,
        "name": "Microsoft Word",
    }
    service._selection_text = "teh cat sat"
    service._seen_text = "teh cat sat"
    service._selection_start = 100
    service._selection_end = 120
    # Visible "teh" is doc 100..103 (before the hidden span).
    ok, _, _ = service._apply_abs(0, 3, "the")
    assert ok is True
    # Visible "sat" (8..11) lies after the 3 hidden chars -> doc 111..114.
    ok, _, _ = service._apply_abs(8, 3, "sat2")
    assert ok is True
    assert word.applied == [(100, 103, "the"), (111, 114, "sat2")]


def test_service_word_apply_skips_compensation_without_evidence(monkeypatch):
    from src import word_integration as wi
    from src.live_service import LivePreviewService

    class FakeWord:
        def __init__(self):
            self.applied = []
            self.text = "teh cat sat"
            self.start = 100
            self.end = 111  # exactly start + len(text): nothing hidden
            self.hidden_calls = 0

        def get_selection_info(self):
            return self.text, self.start, self.end, "", ""

        def selection_has_fields(self):
            return False

        def get_selection_hidden_spans(
            self, start, end, exclude_spans=None, max_hidden=0
        ):
            self.hidden_calls += 1
            return []

        def apply_live_edit(self, selection_start, rel_start, rel_end, replacement):
            self.applied.append((rel_start, rel_end, replacement))
            return True, "Applied."

    word = FakeWord()
    monkeypatch.setattr(wi, "get_word_integration", lambda: word)
    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    service._selection_is_word = True
    service._selection_target = {"bundle_id": "com.microsoft.Word", "pid": 9}
    service._selection_text = "teh cat sat"
    service._seen_text = "teh cat sat"
    service._selection_start = 100
    service._selection_end = 111
    service._apply_abs(0, 3, "the")
    assert word.applied == [(100, 103, "the")]
    assert word.hidden_calls == 0  # fast path: no expensive scan


def test_on_done_limit_reached_reports_error_and_does_not_retry(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    spawns = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawns.append(a))
    errors = []
    service.preview_error.connect(errors.append)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert len(spawns) == 1
    _target, text, _details, key = spawns[0]
    service._on_done(
        {"status": "limit_reached", "edits": [], "meta": {}}, key, text
    )
    assert errors and "free proofreads" in errors[0]
    assert service._panel is None or not service._panel.isVisible()
    # The same selection must not hammer the provider with retries.
    service._sample(now=3.0)
    service._sample(now=4.0)
    assert len(spawns) == 1


# --- fixes for non-Word apps (Pages / Mail / Gmail / Outlook) ---


def test_apply_one_reports_sync_failure_instead_of_silent_close(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class DriftEditor:
        def selection_details(self, target):
            return {
                "text": "something else entirely",
                "range": (0, 20),
                "context_before": "",
                "context_after": "",
                "found": True,
            }

    service._editor = DriftEditor()
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_text = "teh cat sat"
    service._seen_text = "teh cat sat"
    service._selection_start = 0
    service._selection_has_range = True
    messages = []
    service.apply_done.connect(messages.append)
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    service._apply_one(0)
    assert messages == [
        "Could not verify the selection — please try again."
    ]


def test_apply_edits_to_text_applies_spans_right_to_left():
    from src.live_preview import EditSpan, apply_edits_to_text

    spans = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("sat", "was sitting", "Word choice", 8, 11),
    ]
    assert apply_edits_to_text("teh cat sat", spans) == "the cat was sitting"


def test_service_clipboard_fallback_reads_mail_selection(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class MailEditor(_FakeEditor):
        def selection_details(self, target):
            return {
                "text": "",
                "range": None,
                "context_before": "",
                "context_after": "",
                "found": False,
            }

        def _mac_copy_selection(self, pid=0, app_name="", max_attempts=3):
            return self.text

    editor = MailEditor("com.apple.mail", "teh cat sat on the mat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    monkeypatch.setattr(service, "_mail_is_composing", lambda target: True)
    spawns = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawns.append(a))
    service._sample(now=100.0)
    assert len(spawns) == 0  # debounce window
    service._sample(now=101.0)
    assert len(spawns) == 1
    assert spawns[0][1] == "teh cat sat on the mat"
    assert service._selection_has_range is False
    # The throttled read must not repeat for an unchanged selection.
    service._sample(now=102.0)
    assert len(spawns) == 1


def test_service_full_paste_when_no_range(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    text = "teh cat sat on the mat"
    replaced = []

    class MailEditor:
        def __init__(self):
            self.text = text

        def get_selection_light(self, target):
            return self.text

        def replace_selection(self, target, new_text):
            replaced.append(new_text)
            self.text = new_text
            return True, "Applied."

    service._editor = MailEditor()
    service._pending = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("sat", "was sitting", "Word choice", 8, 11),
    ]
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 9,
        "name": "Mail",
    }
    service._selection_text = text
    service._seen_text = text
    service._selection_has_range = False
    messages = []
    service.apply_done.connect(messages.append)
    service._apply_one(0)
    assert replaced == ["the cat was sitting on the mat"]
    assert messages == ["Applied all suggestions to the selection."]


def test_ax_replace_range_browser_uses_paste_not_ax_write(monkeypatch):
    from typing import ClassVar

    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"
        kAXValueAttribute = "value"
        value = "the cat sat"
        set_attr_calls: ClassVar[list[str]] = []
        last_range: ClassVar[tuple] = (0, 0)

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, v):
            FakeAS.last_range = v
            return v

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, v):
            FakeAS.set_attr_calls.append(attr)
            return 0

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXValueAttribute:
                return 0, FakeAS.value
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, FakeAS.last_range
            return 0, ""

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_activate", lambda target: True
    )
    monkeypatch.setattr("src.generic_editing._mac_set_clipboard", lambda t: None)
    monkeypatch.setattr(
        "src.generic_editing._mac_restore_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_clipboard_string", lambda: "saved"
    )
    posted = []
    monkeypatch.setattr(
        "src.generic_editing._post_mac_key", lambda code, pid: posted.append(code)
    )
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)

    editor = GenericTextEditor()
    ok, message = editor.ax_replace_range(
        {
            "pid": 9,
            "name": "Google Chrome",
            "bundle_id": "com.google.chrome",
        },
        0,
        3,
        "the",
        allow_direct_paste=False,
    )
    assert ok is True and message == "Applied."
    assert posted == [9]  # real paste, not the fake-success AX text write
    assert FakeAS.kAXSelectedTextAttribute not in FakeAS.set_attr_calls


def test_ax_replace_range_falls_back_to_paste_when_write_unverified(monkeypatch):
    from typing import ClassVar

    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"
        kAXValueAttribute = "value"
        value = "zzz cat sat"  # the AX write never reaches the value
        set_attr_calls: ClassVar[list[str]] = []
        last_range: ClassVar[tuple] = (0, 0)

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, v):
            FakeAS.last_range = v
            return v

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, v):
            FakeAS.set_attr_calls.append(attr)
            return 0  # writes claim success (Outlook-style)

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXValueAttribute:
                return 0, FakeAS.value
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, FakeAS.last_range
            return 0, ""

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_activate", lambda target: True
    )
    monkeypatch.setattr("src.generic_editing._mac_set_clipboard", lambda t: None)
    monkeypatch.setattr(
        "src.generic_editing._mac_restore_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_clipboard_string", lambda: "saved"
    )
    posted = []

    def fake_post(code, pid):
        posted.append(code)
        FakeAS.value = "the cat sat"  # the paste lands

    monkeypatch.setattr("src.generic_editing._post_mac_key", fake_post)
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)

    editor = GenericTextEditor()
    ok, message = editor.ax_replace_range(
        {"pid": 9, "name": "Outlook", "bundle_id": "com.microsoft.outlook"},
        0,
        3,
        "the",
        allow_direct_paste=False,
    )
    assert ok is True and message == "Applied."
    assert FakeAS.kAXSelectedTextAttribute in FakeAS.set_attr_calls
    assert posted == [9]  # verification failed -> real paste followed


def test_ax_replace_range_pastes_when_attributes_fail(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, value):
            return value

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, value):
            return 1  # every attribute write fails

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_activate", lambda target: True
    )
    monkeypatch.setattr("src.generic_editing._mac_set_clipboard", lambda t: None)
    monkeypatch.setattr(
        "src.generic_editing._mac_restore_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_clipboard_string", lambda: "saved"
    )
    posted = []
    monkeypatch.setattr(
        "src.generic_editing._post_mac_key", lambda code, pid: posted.append(code)
    )
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)

    editor = GenericTextEditor()
    ok, message = editor.ax_replace_range(
        {"pid": 9, "name": "App"}, 0, 3, "the", allow_direct_paste=True
    )
    assert ok is True and "Applied" in message
    assert posted == [9]  # kVK_ANSI_V

    posted.clear()
    ok, message = editor.ax_replace_range(
        {"pid": 9, "name": "App"}, 2, 3, "the", allow_direct_paste=False
    )
    assert ok is False
    assert posted == []  # refused to paste without a selected range


# --- editable-context gate ---


def test_selection_details_reports_editable_via_settable_probe(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXRoleAttribute = "role"
        kAXValueAttribute = "value"
        kAXSelectedTextAttribute = "seltext"
        kAXSelectedTextRangeAttribute = "range"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementIsAttributeSettable(el, attr, out):
            # PyObjC signature: (error, settable) via an out-parameter.
            return (0, attr == FakeAS.kAXValueAttribute)

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXRoleAttribute:
                return 0, "AXGroup"  # role alone does not imply editable
            if attr == FakeAS.kAXValueAttribute:
                return 0, "editable text"
            if attr == FakeAS.kAXSelectedTextAttribute:
                return 0, "some text"
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, (0, 9)
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor,
        "_mac_ax_text_element",
        lambda pid: (FakeAS, "el"),
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    editor = GenericTextEditor()
    details = editor.selection_details(
        {"pid": 9, "bundle_id": "com.apple.TextEdit"}
    )
    assert details["editable"] is True
    assert details["role"] == "AXGroup"


def test_selection_details_editable_role_even_when_probe_fails(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXRoleAttribute = "role"
        kAXValueAttribute = "value"
        kAXSelectedTextAttribute = "seltext"
        kAXSelectedTextRangeAttribute = "range"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementIsAttributeSettable(el, attr, out):
            return (0, False)  # probe says not settable

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXRoleAttribute:
                return 0, "AXTextArea"
            if attr == FakeAS.kAXValueAttribute:
                return 0, "text"
            if attr == FakeAS.kAXSelectedTextAttribute:
                return 0, "some text"
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, (0, 9)
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor,
        "_mac_ax_text_element",
        lambda pid: (FakeAS, "el"),
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    editor = GenericTextEditor()
    details = editor.selection_details(
        {"pid": 9, "bundle_id": "com.apple.TextEdit"}
    )
    assert details["editable"] is True  # editable role is enough


def test_selection_details_reports_read_only_static_text(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXRoleAttribute = "role"
        kAXValueAttribute = "value"
        kAXSelectedTextAttribute = "seltext"
        kAXSelectedTextRangeAttribute = "range"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementIsAttributeSettable(el, attr, out):
            return (0, False)  # nothing settable: read-only static text

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXRoleAttribute:
                return 0, "AXStaticText"
            if attr == FakeAS.kAXValueAttribute:
                return 0, "read only"
            if attr == FakeAS.kAXSelectedTextAttribute:
                return 0, "some text"
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, (0, 9)
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor,
        "_mac_ax_text_element",
        lambda pid: (FakeAS, "el"),
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    editor = GenericTextEditor()
    details = editor.selection_details(
        {"pid": 9, "bundle_id": "com.apple.Preview"}
    )
    assert details["editable"] is False
    assert details["role"] == "AXStaticText"


def test_service_skips_read_only_selection(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class ReadOnlyEditor(_FakeEditor):
        def selection_details(self, target):
            details = super().selection_details(target)
            details["editable"] = False
            details["role"] = "AXStaticText"
            return details

    editor = ReadOnlyEditor("com.apple.Preview", "a paragraph of pdf text")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    spawns = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawns.append(a))
    for tick in (1.0, 2.0, 3.0, 4.0):
        service._sample(now=tick)
    assert len(spawns) == 0  # reading a PDF must never trigger a preview


def test_mail_compose_detection_distinguishes_viewer(monkeypatch):
    import subprocess

    from src.live_service import LivePreviewService

    service = LivePreviewService()
    responses = []

    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        responses.append(args)
        if "front window" in args[-1]:
            return FakeResult(FRONT_NAME)
        return FakeResult(DRAFT_SUBJECTS)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("src.live_service.subprocess.run", fake_run)
    monkeypatch.setattr(
        "src.live_service.MAIL_COMPOSE_CHECK_INTERVAL_S", 0.0
    )

    DRAFT_SUBJECTS = ""
    FRONT_NAME = "Inbox — Personal Gmail"
    assert service._mail_is_composing({}) is False  # viewer: read-only

    DRAFT_SUBJECTS = "Meeting notes"
    FRONT_NAME = "Meeting notes"
    assert service._mail_is_composing({}) is True  # compose window

    DRAFT_SUBJECTS = "Meeting notes"
    FRONT_NAME = "Inbox — Personal Gmail"
    assert service._mail_is_composing({}) is False  # draft in background


# --- suggestion style (strict vs polish) ---


def test_load_preview_prompt_variants():
    from src import logic

    strict = logic.load_preview_prompt("strict")
    polish = logic.load_preview_prompt("polish")
    assert "conservative and minimal" in strict
    assert "polish the language" in polish
    assert '"edits"' in polish and '"before"' in polish


def test_preview_edits_once_uses_polish_style_and_temperature(monkeypatch):
    from src import logic

    fake = _mock_completion('{"edits":[]}')
    monkeypatch.setattr(logic, "_request_completion", fake)
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
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {
            "enabled": True,
            "use_local_model": True,
            "max_chars": 1500,
            "style": "polish",
        },
    }
    status, _edits, meta = logic.preview_edits_once(
        settings,
        {"bundle_id": "com.apple.TextEdit"},
        "this sentence could be nicer",
        "",
        "",
    )
    assert status == "ok"
    assert meta["style"] == "polish"
    system_prompt = fake.calls[0][0][0]
    assert "polish the language" in system_prompt
    assert fake.calls[0][0][7] == 0.2  # temperature argument


def test_preview_edits_once_defaults_to_strict(monkeypatch):
    from src import logic

    fake = _mock_completion('{"edits":[]}')
    monkeypatch.setattr(logic, "_request_completion", fake)
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
    settings = {
        "general": {"spelling": "UK/AU/NZ", "context": "General Editing"},
        "live_preview": {
            "enabled": True,
            "use_local_model": True,
            "max_chars": 1500,
        },
    }
    _status, _edits, meta = logic.preview_edits_once(
        settings,
        {"bundle_id": "com.apple.TextEdit"},
        "hello world",
        "",
        "",
    )
    assert meta["style"] == "strict"
    assert fake.calls[0][0][7] == 0.1


# --- loading pill + undo ---


def test_undo_pill_emits_on_click():
    from PyQt6.QtWidgets import QPushButton

    from src.live_overlay import UndoPill

    pill = UndoPill()
    clicks = []
    pill.undo_requested.connect(lambda: clicks.append(True))
    buttons = pill.findChildren(QPushButton)
    buttons[0].click()
    assert clicks == [True]


def test_checking_panel_shows_on_spawn_and_updates_on_done(monkeypatch):
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

    class _StubSignal:
        def connect(self, *args, **kwargs):
            pass

    class FakeWorker:
        done = _StubSignal()
        failed = _StubSignal()
        cancelled = _StubSignal()
        finished = _StubSignal()

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr("src.live_service.PreviewWorker", FakeWorker)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert service._panel is not None
    assert service._panel.isVisible()  # checking state visible immediately
    assert service._pending == []  # no suggestions yet
    service._on_done(result, "key", "teh cat sat")
    assert service._pending[0].before == "teh"  # rebuilt with real results


def test_apply_one_arms_undo_and_undo_restores(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    applied = []

    class FakeEditor:
        def selection_details(self, target):
            return {
                "text": "teh",
                "range": (100, 3),
                "context_before": "",
                "context_after": "",
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

        def ax_replace_range(
            self, target, start, length, text, allow_direct_paste=False
        ):
            applied.append((start, length, text))
            return True, "Applied."

    service._editor = FakeEditor()
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_target = {
        "bundle_id": "com.apple.TextEdit",
        "pid": 9,
        "name": "TextEdit",
    }
    service._selection_start = 100
    service._selection_is_word = False
    service._selection_has_range = True
    service._selection_text = "teh"
    service._seen_text = "teh"
    service._apply_one(0)
    assert applied == [(100, 3, "the")]
    assert service._undo_state is not None
    assert service._undo_state["steps"] == [(100, 3, "teh")]
    service._perform_undo()
    assert applied[-1] == (100, 3, "teh")  # restored the original


def test_full_apply_undo_requires_matching_selection(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    text = "teh cat sat on the mat"
    replaced = []

    class MailEditor:
        def __init__(self):
            self.current = text  # the selection still holds the original

        def get_selection_light(self, target):
            return self.current

        def replace_selection(self, target, new_text):
            replaced.append(new_text)
            self.current = new_text
            return True, "Applied."

    editor = MailEditor()
    service._editor = editor
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 9,
        "name": "Mail",
    }
    service._selection_text = text
    service._seen_text = text
    service._selection_has_range = False
    service._apply_one(0)
    assert service._undo_state is not None
    service._perform_undo()
    assert replaced == ["the cat sat on the mat", text]

    # If the selection has moved on, undo must refuse to paste blindly.
    replaced.clear()
    service._undo_state = {
        "mode": "full",
        "target": {"bundle_id": "com.apple.mail", "pid": 9},
        "original": text,
        "corrected": "the cat sat on the mat",
    }
    editor.current = "something else now"
    messages = []
    service.apply_done.connect(messages.append)
    service._perform_undo()
    assert replaced == []
    assert messages == ["Selection changed — could not undo."]


# --- granular edits + Word missing-value ---


def test_map_edits_drops_whole_selection_span():
    long_text = "x" * 229
    spans = map_edits_to_ranges(
        long_text,
        [Edit(long_text, "y" * 229, "Rewrite")],
    )
    assert spans == []  # a whole-selection rewrite must never apply

    # A short selection may legitimately be one edit.
    spans = map_edits_to_ranges("teh cat", [Edit("teh cat", "the cat", "Grammar")])
    assert [(s.start, s.end) for s in spans] == [(0, 7)]


def test_word_selection_info_maps_missing_value_to_empty(monkeypatch):
    import subprocess

    from src import word_integration as wi

    def fake_run(args, **kwargs):
        completed = mock.Mock()
        completed.returncode = 0
        completed.stdout = (
            b"10###PROOF_SEP###missing value###PROOF_SEP###10"
            b"###PROOF_SEP######PROOF_SEP###"
        )
        completed.stderr = b""
        return completed

    monkeypatch.setattr(subprocess, "run", fake_run)
    integration = wi.MacOSWordIntegration()
    text, start, end, _before, _after = integration.get_selection_info()
    assert text == ""  # collapsed selection, not the literal string
    assert (start, end) == (10, 10)


# --- word-level splits, double-Esc, busy-cache, Word poll ---


def test_map_edits_splits_large_span_into_word_edits():
    original = (
        "the quik brown fox jumps over the lazy dog while sleeping soundly "
        "under the tree near the river"
    )
    fixed = original.replace("quik", "quick").replace("soundly", "deeply")
    spans = map_edits_to_ranges(
        original, [Edit(original, fixed, "Rewrite")]
    )
    assert [(s.before, s.after) for s in spans] == [
        ("quik", "quick"),
        ("soundly", "deeply"),
    ]
    for span in spans:
        assert original[span.start : span.end] == span.before


def test_double_escape_cancels_inflight_preview():
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    service._worker = object()  # marks a preview as in flight
    messages = []
    service.apply_done.connect(messages.append)
    service._on_escape_pressed()
    assert not service._cancel_event.is_set()  # first Esc: just dismiss
    service._on_escape_pressed()
    assert service._cancel_event.is_set()  # second Esc: cancel the call
    assert messages == ["Preview cancelled."]


def test_busy_decision_serves_cached_result(monkeypatch):
    from src import live_preview as lp
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "teh cat sat")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    service._worker = object()  # another preview is still in flight
    spans = [lp.EditSpan("teh", "the", "Spelling", 0, 3)]
    key = lp.preview_cache_key(
        "com.apple.textedit", "teh cat sat", "", "", service._fingerprint
    )
    service._cache.put(key, spans)
    service._seen_text = "teh cat sat"
    service._changed_at = 0.0
    service._sample(now=5.0)
    assert service._pending == spans
    assert service._panel is not None and service._panel.isVisible()
    service._worker = None


def test_word_poll_interval_relaxes(monkeypatch):
    from src import live_service as ls
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class WordEditor:
        @staticmethod
        def frontmost_app():
            return {
                "bundle_id": "com.microsoft.word",
                "pid": 7,
                "name": "Microsoft Word",
            }

        @staticmethod
        def is_word(target):
            return True

        @staticmethod
        def permission_status():
            return True, ""

    monkeypatch.setattr(service, "_editor", WordEditor())

    class FakeWord:
        def get_selection_info(self):
            return "teh cat sat", 100, 111, "", ""

    monkeypatch.setattr(
        "src.word_integration.get_word_integration", lambda: FakeWord()
    )
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: None)
    service.start()
    try:
        assert service._timer.interval() == ls.POLL_INTERVAL_MS
        service._sample(now=1.0)
        assert service._timer.interval() == ls.WORD_POLL_INTERVAL_MS
    finally:
        service.stop()


def test_mail_compose_detection_empty_subject_counts_as_composing(monkeypatch):
    import subprocess

    from src.live_service import LivePreviewService

    service = LivePreviewService()

    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        if "front window" in args[-1]:
            return FakeResult("")  # a fresh compose window has no title
        return FakeResult("")  # and no drafts carry subjects yet

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "src.live_service.MAIL_COMPOSE_CHECK_INTERVAL_S", 0.0
    )
    assert service._mail_is_composing({}) is True


# --- panel display polish ---


def test_card_reshapes_to_fit_long_edit():
    from src.live_overlay import PANEL_MAX_WIDTH, PANEL_MIN_WIDTH, WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    assert PANEL_MIN_WIDTH <= card.width() <= PANEL_MAX_WIDTH

    long_before = "the " + "word " * 40  # ~200 chars of changed text
    long_after = "the " + "phrase " * 40
    card.set_spans([EditSpan(long_before, long_after, "Rewrite", 0, 1)])
    assert card.width() == PANEL_MAX_WIDTH  # widened to fit the change


def test_card_has_no_window_mask():
    # The rounded-corner window mask was rejected in beta.17; the card must
    # stay a plain opaque window (the stylesheet still rounds the surface).
    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    assert card.mask() is None or card.mask().isEmpty()


def test_card_diff_labels_get_true_wrapped_heights():
    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    card.set_spans(
        [
            EditSpan(
                "a sentence with " + "many words " * 30 + "to fix",
                "a sentence with " + "several words " * 30 + "improved",
                "Clarity",
                0,
                1,
            )
        ]
    )
    for label in card._diff_labels:
        assert label.minimumHeight() > label.fontMetrics().height() * 1.5


def test_card_resize_reflows_heights():
    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    card.set_spans(
        [
            EditSpan(
                "some words " * 40,
                "some other words " * 40,
                "Rewrite",
                0,
                1,
            )
        ]
    )
    card.show()
    label = card._diff_labels[0]
    before = label.minimumHeight()
    card.resize(card.width() - 80, card.height() + 40)
    # Narrower window -> the wrapped label needs more lines.
    assert label.minimumHeight() >= before


# --- P1: onboarding & trust ---


def test_note_launch_version_detects_updates():
    from src import settings as settings_mod

    settings = {"app_version": "1.0.0"}
    assert settings_mod.note_launch_version(settings) is False  # first run
    assert settings["last_run_version"] == "1.0.0"
    assert settings_mod.note_launch_version(settings) is False  # same build
    settings["app_version"] = "2.0.0"
    assert settings_mod.note_launch_version(settings) is True  # updated
    assert settings["last_run_version"] == "2.0.0"


def test_live_status_emits_on_permission_transitions(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class FlipEditor(_FakeEditor):
        def __init__(self):
            super().__init__("com.apple.TextEdit", "teh cat sat")
            self.trusted = True

        def permission_status(self):
            return self.trusted, ""

    editor = FlipEditor()
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: None)
    states = []
    service.live_status.connect(states.append)
    service._sample(now=1.0)
    service._sample(now=2.0)
    assert states == ["ready"]
    editor.trusted = False
    service._sample(now=3.0)
    assert states[-1] == "no_permission"
    editor.trusted = True
    service._sample(now=4.0)
    assert states[-1] == "ready"


def test_refresh_settings_emits_disabled_status():
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    states = []
    service.live_status.connect(states.append)
    service.refresh_settings(
        {"live_preview": {"enabled": False, "delay_ms": 900}}
    )
    assert states == ["disabled"]


def test_readiness_row_updates_per_state():
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    QApplication.instance() or QApplication([])
    loaded = settings_mod.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    try:
        window._apply_live_status("ready")
        assert "ready" in window.live_status_label.text().lower()
        assert window.live_action_btn.text() == "Test now"
        # Working fine: the whole row retires so the window stays clean.
        assert window.live_container.isHidden() is True

        window._apply_live_status("no_permission")
        assert "accessibility" in window.live_status_label.text().lower()
        assert window.live_action_btn.text() == "Open System Settings"
        assert window.live_container.isHidden() is False

        window._apply_live_status("disabled")
        assert window.live_action_btn.text() == "Open Settings"
        assert window.live_container.isHidden() is False
    finally:
        window.close()


def test_live_test_now_probes_last_user_app(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    QApplication.instance() or QApplication([])
    loaded = settings_mod.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    try:
        service = window.live_service

        class FakeEditor:
            @staticmethod
            def frontmost_app():
                return {
                    "bundle_id": "nz.co.bytemind.byteproof",
                    "pid": 999,
                    "name": "ByteProof",
                }

            @staticmethod
            def permission_status():
                return True, ""

            @staticmethod
            def selection_details(target):
                return {
                    "text": "a paragraph of pdf text",
                    "range": (0, 23),
                    "context_before": "",
                    "context_after": "",
                    "found": True,
                    "editable": False,
                    "role": "AXStaticText",
                }

            @staticmethod
            def is_word(target):
                return False

        monkeypatch.setattr(service, "_editor", FakeEditor())
        # Word produced the last preview, but the user most recently worked
        # in a PDF — the probe must follow the user, not the preview cache.
        service._selection_target = {
            "bundle_id": "com.microsoft.word",
            "pid": 5,
            "name": "Microsoft Word",
        }
        service._last_user_app = {
            "bundle_id": "com.apple.Preview",
            "pid": 6,
            "name": "Preview",
        }
        toasts = []
        monkeypatch.setattr(
            window,
            "_show_toast",
            lambda msg, kind="success": toasts.append((msg, kind)),
        )
        window._test_live_now()
        assert toasts
        message, kind = toasts[0]
        assert "Preview" in message  # the last active app, not Word
        assert "read-only" in message
        assert kind == "warning"
    finally:
        window.close()


def test_sample_records_last_user_app(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())

    class MutableFront:
        def __init__(self):
            self.current = {
                "bundle_id": "com.apple.TextEdit",
                "pid": 1,
                "name": "TextEdit",
            }

        def frontmost_app(self):
            return self.current

        def permission_status(self):
            return True, ""

        def selection_details(self, target):
            return {
                "text": "",
                "range": None,
                "context_before": "",
                "context_after": "",
                "found": False,
                "editable": False,
                "role": "",
            }

    editor = MutableFront()
    monkeypatch.setattr(service, "_editor", editor)
    service._sample(now=1.0)
    assert service._last_user_app.get("name") == "TextEdit"
    # ByteProof becomes frontmost: the remembered app must not change.
    editor.current = {
        "bundle_id": "nz.co.bytemind.byteproof",
        "pid": 2,
        "name": "ByteProof",
    }
    service._sample(now=2.0)
    assert service._last_user_app.get("name") == "TextEdit"
    # The user moves to a PDF: the remembered app follows them.
    editor.current = {
        "bundle_id": "com.apple.Preview",
        "pid": 3,
        "name": "Preview",
    }
    service._sample(now=3.0)
    assert service._last_user_app.get("name") == "Preview"


# --- P3: shared diff renderer in the main window ---


def test_main_window_display_diff_uses_word_level_renderer():
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    QApplication.instance() or QApplication([])
    loaded = settings_mod.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    try:
        window.diff_text.clear()
        window.display_diff(
            "teh cat sat on the mat", "the cat sat on the mat"
        )
        text = window.diff_text.toPlainText()
        assert "teh" in text
        assert "the" in text
        assert "→" in text  # the shared renderer's replacement arrow
    finally:
        window.close()


def test_diff_html_preserves_newlines_when_requested():
    from src.ui_theme import diff_html

    rendered = diff_html(
        "first line\ntecond line",
        "first line\nsecond line",
        preserve_newlines=True,
    )
    assert "\n" in rendered
    plain = diff_html(
        "first line\ntecond line", "first line\nsecond line"
    )
    assert "\n" not in plain  # panel default still flattens newlines


# --- P4.1 / P4.3 / P2.4 ---


def test_reason_color_maps_categories():
    from src.ui_theme import DOT_AMBER, DOT_BLUE, DOT_RED, reason_color

    assert reason_color("Spelling") == DOT_RED
    assert reason_color("Capitalization") == DOT_RED
    assert reason_color("Grammar") == DOT_AMBER
    assert reason_color("Punctuation") == DOT_AMBER
    assert reason_color("Word choice") == DOT_BLUE
    assert reason_color("Clarity") == DOT_BLUE
    assert reason_color("") == DOT_BLUE


def test_card_rows_include_reason_dots():
    from PyQt6.QtWidgets import QLabel

    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    spans = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("go", "goes", "Grammar", 10, 12),
        EditSpan("nice", "elegant", "Word choice", 20, 24),
    ]
    card.set_spans(spans)
    dots = [
        label
        for label in card.findChildren(QLabel)
        if label.width() == 8 and label.height() == 8
    ]
    assert len(dots) == len(spans)


def test_tray_tooltip_mirrors_live_status():
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    QApplication.instance() or QApplication([])
    loaded = settings_mod.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    try:
        window._apply_live_status("ready")
        assert "ready" in window.tray_icon.toolTip()
        window._apply_live_status("no_permission")
        assert "Accessibility" in window.tray_icon.toolTip()
        window._apply_live_status("disabled")
        assert "off" in window.tray_icon.toolTip()
    finally:
        window.close()


def test_close_hides_to_tray_with_first_time_hint(monkeypatch):
    from PyQt6.QtWidgets import QSystemTrayIcon

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    loaded = settings_mod.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    try:
        monkeypatch.setattr(
            QSystemTrayIcon,
            "isSystemTrayAvailable",
            staticmethod(lambda: True),
        )
        toasts = []
        monkeypatch.setattr(
            window,
            "_show_toast",
            lambda msg, kind="success": toasts.append(msg),
        )
        window.show()
        window.close()
        assert window.isHidden() is True
        assert len(toasts) == 1
        assert "menu bar" in toasts[0]
        window.show()
        window.close()
        assert len(toasts) == 1  # hint only on the first close
    finally:
        window.close()


# --- clean panel, dismiss, drag smoothness, position memory ---


def test_service_shows_clean_panel_when_no_changes(monkeypatch):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    editor = _FakeEditor("com.apple.TextEdit", "a perfect sentence")
    editor.ax_bounds_for_range = lambda *a: []
    monkeypatch.setattr(service, "_editor", editor)
    service._sample(now=1.0)
    service._sample(now=2.0)
    # Simulate the worker replying with no edits.
    result = {"status": "ok", "edits": [], "meta": {"provider": "fake"}}
    service._on_done(result, "key", "a perfect sentence")
    assert service._panel is not None and service._panel.isVisible()
    from PyQt6.QtWidgets import QLabel

    labels = [
        label.text()
        for label in service._panel.findChildren(QLabel)
    ]
    assert any("No changes needed" in text for text in labels)
    assert service._clean_timer is not None
    assert service._clean_timer.isActive()  # auto-fades after a moment


def test_service_dismiss_removes_suggestion_and_persists(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    spans = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("go", "goes", "Grammar", 10, 12),
    ]
    service._pending = list(spans)
    service._on_dismiss(0)
    assert [(s.before, s.after) for s in service._pending] == [
        ("go", "goes")
    ]
    # The dismissed pair must never come back, even via the cache.
    service._show_result(list(spans), clean_when_empty=False)
    assert [(s.before, s.after) for s in service._pending] == [
        ("go", "goes")
    ]


def test_card_emits_drag_signals():
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    from src.live_overlay import WordSuggestionCard
    from src.live_preview import EditSpan

    card = WordSuggestionCard()
    card.set_spans([EditSpan("teh", "the", "Spelling", 0, 3)])
    card.move(100, 100)
    recorded = []
    card.dragging_started.connect(lambda: recorded.append("start"))
    card.dragging_finished.connect(lambda: recorded.append("end"))
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(40, 20),
        QPointF(140, 120),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mousePressEvent(press)
    assert recorded == ["start"]
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(40, 20),
        QPointF(140, 120),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mouseReleaseEvent(release)
    assert recorded == ["start", "end"]


def test_service_pauses_polling_while_dragging():
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    service.start()
    assert service._timer.isActive()
    service._pause_polling()
    assert not service._timer.isActive()
    service._resume_polling()
    assert service._timer.isActive()
    # When the poll was already stopped, resuming must not restart it.
    service._timer.stop()
    service._pause_polling()
    service._resume_polling()
    assert not service._timer.isActive()
    service.stop()


def test_service_remembers_panel_position_after_drag():
    from PyQt6.QtCore import QPoint

    from src.live_overlay import WordSuggestionCard
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    card = WordSuggestionCard()
    card.set_spans([])
    service._panel = card
    card.move(200, 200)
    service._on_panel_dragged()
    assert service._remembered_pos == QPoint(200, 200)
    service._place_panel(card)
    # The remembered spot is used, clamped to the screen's usable area.
    from PyQt6.QtCore import QRect

    from src.live_overlay import _clamp_rect

    expected = _clamp_rect(
        QRect(200, 200, card.width(), card.height()), QPoint(200, 200)
    ).topLeft()
    # pop_in animates the geometry from a shrunk rect; the remembered
    # position drives the animation's final target.
    assert card._pop_target is not None
    assert card._pop_target.topLeft() == expected


# --- UTF-16 range conversion + drag-pause recovery ---


def test_utf16_codepoint_index_roundtrip():
    from src.live_preview import (
        codepoint_to_utf16_index,
        utf16_to_codepoint_index,
    )

    text = "a😀b🎉c"
    # UTF-16 units: a(0) 😀(1-2) b(3) 🎉(4-5) c(6)
    assert utf16_to_codepoint_index(text, 0) == 0
    assert utf16_to_codepoint_index(text, 1) == 1
    assert utf16_to_codepoint_index(text, 3) == 2
    assert utf16_to_codepoint_index(text, 6) == 4
    assert utf16_to_codepoint_index(text, 99) == len(text)
    for cp in range(len(text) + 1):
        assert utf16_to_codepoint_index(
            text, codepoint_to_utf16_index(text, cp)
        ) == cp


def test_selection_details_converts_utf16_range_to_codepoints(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXRoleAttribute = "role"
        kAXValueAttribute = "value"
        kAXSelectedTextAttribute = "seltext"
        kAXSelectedTextRangeAttribute = "range"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementIsAttributeSettable(el, attr, out):
            return (0, False)

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXRoleAttribute:
                return 0, "AXTextArea"
            if attr == FakeAS.kAXValueAttribute:
                return 0, "a😀b cat"
            if attr == FakeAS.kAXSelectedTextAttribute:
                return 1, None  # force the range-slice fallback
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, (3, 5)  # UTF-16 units: "b cat"
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor,
        "_mac_ax_text_element",
        lambda pid: (FakeAS, "el"),
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    editor = GenericTextEditor()
    details = editor.selection_details(
        {"pid": 9, "bundle_id": "com.apple.TextEdit"}
    )
    assert details["text"] == "b cat"
    assert details["range"] == (2, 5)  # code points, not UTF-16 units


def test_ax_replace_range_sets_range_in_utf16_units(monkeypatch):
    from typing import ClassVar

    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"
        kAXValueAttribute = "value"
        created: ClassVar[list] = []

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, value):
            FakeAS.created.append(value)
            return value

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, value):
            return 0

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXValueAttribute:
                return 0, "a😀xx yy"
            if attr == FakeAS.kAXSelectedTextAttribute:
                return 0, "xx"
            return 1, None

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_activate", lambda target: True
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_set_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_restore_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_clipboard_string", lambda: "saved"
    )
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)

    editor = GenericTextEditor()
    ok, message, _ = None, "", 0
    ok, message = editor.ax_replace_range(
        {"pid": 9, "name": "App", "bundle_id": "com.example.app"},
        3,  # code-point start: "xx"
        2,
        "xx",
        allow_direct_paste=False,
    )
    assert ok is True and message == "Applied."
    # The range passed to the app must be UTF-16 units (4, 2), because the
    # emoji before the edit occupies two units.
    assert FakeAS.created[0] == (4, 2)


def test_hide_panel_resumes_polling_after_lost_drag():
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(_live_settings())
    service.start()
    service._pause_polling()
    assert not service._timer.isActive()
    # A drag whose release event was lost (panel hidden mid-drag) must not
    # leave the poll frozen.
    service._hide_panel()
    assert service._timer.isActive()
    service.stop()


# --- apply safety for apps that ignore range writes (Codex/ChatGPT) ---


def test_ax_replace_range_refuses_paste_when_range_not_confirmed(monkeypatch):
    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"
        kAXValueAttribute = "value"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, v):
            return v

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, v):
            return 0  # claims success

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXValueAttribute:
                return 0, "the cat sat"
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, (999, 1)  # the app ignores the range write
            return 0, ""

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    posted = []
    monkeypatch.setattr(
        "src.generic_editing._post_mac_key", lambda code, pid: posted.append(code)
    )
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)

    editor = GenericTextEditor()
    ok, _message = editor.ax_replace_range(
        {"pid": 9, "name": "ChatGPT", "bundle_id": "com.openai.chat"},
        0,
        3,
        "da",  # differs from the existing text at the range
        allow_direct_paste=False,
    )
    assert ok is False  # never paste into an unconfirmed range
    assert posted == []


def test_ax_replace_range_does_not_retry_after_document_changed(monkeypatch):
    from typing import ClassVar

    from src.generic_editing import GenericTextEditor

    class FakeAS:
        kAXValueTypeCFRange = "cfrange"
        kAXSelectedTextRangeAttribute = "range"
        kAXSelectedTextAttribute = "seltext"
        kAXValueAttribute = "value"
        value = "the cat sat"
        last_range: ClassVar[tuple] = (0, 0)
        paste_count = 0

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXValueCreate(kind, v):
            FakeAS.last_range = v
            return v

        @staticmethod
        def AXUIElementSetAttributeValue(el, attr, v):
            return 0

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXValueAttribute:
                return 0, FakeAS.value
            if attr == FakeAS.kAXSelectedTextRangeAttribute:
                return 0, FakeAS.last_range
            return 0, ""

    monkeypatch.setitem(sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_text_element", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_ax_focused", lambda pid: (FakeAS, "el")
    )
    monkeypatch.setattr(
        GenericTextEditor, "_mac_activate", lambda target: True
    )
    monkeypatch.setattr("src.generic_editing._mac_set_clipboard", lambda t: None)
    monkeypatch.setattr(
        "src.generic_editing._mac_restore_clipboard", lambda t: None
    )
    monkeypatch.setattr(
        "src.generic_editing._mac_clipboard_string", lambda: "saved"
    )

    def fake_post(code, pid):
        FakeAS.paste_count += 1
        # The paste lands somewhere unexpected: the range itself changed.
        FakeAS.value = "xhe cat sat"

    monkeypatch.setattr("src.generic_editing._post_mac_key", fake_post)
    monkeypatch.setattr("src.generic_editing.time.sleep", lambda s: None)
    system_events = []
    monkeypatch.setattr(
        "src.generic_editing._mac_system_events_key",
        lambda key, name: system_events.append(key),
    )

    editor = GenericTextEditor()
    ok, _message = editor.ax_replace_range(
        {"pid": 9, "name": "ChatGPT", "bundle_id": "com.openai.chat"},
        0,
        3,
        "changed",
        allow_direct_paste=False,
    )
    assert ok is False
    assert FakeAS.paste_count == 1
    assert system_events == []  # a second paste could only corrupt further
