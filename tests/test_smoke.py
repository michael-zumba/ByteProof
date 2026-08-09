"""Offline smoke tests for ByteProof.

Run from the project root:
    QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_smoke.py
"""

import hashlib
import importlib.util
import os
import platform
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def test_updater_url_selection() -> None:
    from src.app_version import _get_download_url

    info = {
        "macos_apple_silicon_url": "https://example.com/arm.dmg",
        "macos_intel_url": "https://example.com/intel.dmg",
        "windows_url": "https://example.com/win.zip",
    }
    real_system = platform.system
    real_machine = platform.machine
    try:
        platform.system = lambda: "Windows"
        assert _get_download_url(info) == "https://example.com/win.zip"
        platform.system = lambda: "Darwin"
        platform.machine = lambda: "arm64"
        assert _get_download_url(info) == "https://example.com/arm.dmg"
        platform.machine = lambda: "x86_64"
        assert _get_download_url(info) == "https://example.com/intel.dmg"
    finally:
        platform.system = real_system
        platform.machine = real_machine


def test_settings_branding() -> None:
    from src import settings

    assert settings.APP_NAME == "ByteProof"
    assert "ByteMind" in settings.APP_SUPPORT_DIR
    assert "bytemind" in settings.PRODUCT_URL
    assert settings.PRODUCT_URL == "https://www.bytemind.co.nz/byteproof"
    assert settings.STRIPE_PAYMENT_URL == "https://buy.stripe.com/00w7sLeBP3af6uJ24p3Nm03"
    assert "ByteProof Local (Qwen3)" in settings.PROVIDERS


def test_open_purchase_url_uses_live_link() -> None:
    import webbrowser

    from src import settings
    from src.gui import open_purchase_url

    opened: list[str] = []
    original_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    try:
        open_purchase_url(None)
    finally:
        webbrowser.open = original_open
    assert opened == [settings.STRIPE_PAYMENT_URL]


def test_licensing_roundtrip() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")

    spec = importlib.util.spec_from_file_location(
        "generate_license", os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    key = generator.generate_license_key("buyer@example.com", "unlimited", "")
    result = licensing.activate_license(key)
    assert result["valid"], result
    assert licensing.is_licensed()
    assert licensing.get_license_info()["status"] == "licensed"


def test_activation_from_url() -> None:
    from src import activation, licensing

    tmpdir = tempfile.mkdtemp()
    licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")

    spec = importlib.util.spec_from_file_location(
        "generate_license", os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    key = generator.generate_license_key("paid@example.com", "unlimited", "")

    result = activation.activate_from_url(f"byteproof://activate?key={key}")
    assert result["ok"], result
    assert result["email"] == "paid@example.com"
    assert licensing.is_licensed()

    bad = activation.activate_from_url("https://example.com/not-byteproof")
    assert not bad["ok"]

    original_url = activation.ACTIVATION_API_URL
    activation.ACTIVATION_API_URL = "http://127.0.0.1:9/api"
    try:
        unreachable = activation.activate_with_email("someone@example.com")
    finally:
        activation.ACTIVATION_API_URL = original_url
    assert not unreachable["ok"]
    assert unreachable["error"]


def test_trial_expired_disables_proofreading() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import gui, settings

    window = gui.ProofreaderApp(1024, settings.load_runtime_settings())
    original_licensed = gui.is_licensed
    original_ensure = gui.ensure_trial_started
    original_status = gui.get_trial_status
    try:
        gui.is_licensed = lambda: False
        gui.ensure_trial_started = lambda: 1000000000.0
        gui.get_trial_status = lambda _ts: {
            "in_trial": False,
            "days_left": 0,
            "trial_expired": True,
        }
        window._update_proofread_button()
        assert not window.run_btn.isEnabled()
        assert "Trial Expired" in window.run_btn.text()
    finally:
        gui.is_licensed = original_licensed
        gui.ensure_trial_started = original_ensure
        gui.get_trial_status = original_status
        window.close()


def test_hotkey_conversion() -> None:
    from src.gui import SettingsDialog

    if platform.system() == "Darwin":
        assert SettingsDialog.pynput_to_qt(None, "<cmd>+<shift>+;") == "Meta+Shift+;"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.qt_to_pynput(None, "Meta+Shift+;") == "<cmd>+<shift>+;"  # pyright: ignore[reportArgumentType]
    else:
        assert SettingsDialog.pynput_to_qt(None, "<cmd>+<shift>+;") == "Ctrl+Shift+;"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.qt_to_pynput(None, "Ctrl+Shift+;") == "<ctrl>+<shift>+;"  # pyright: ignore[reportArgumentType]


def test_gui_constructs() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import settings
    from src.gui import ProofreaderApp, SettingsDialog

    loaded = settings.load_runtime_settings()
    window = ProofreaderApp(1024, loaded)
    window.show()
    app.processEvents()
    assert window.windowTitle() == "ByteProof"

    dialog = SettingsDialog(loaded, window)
    dialog.set_active_provider("Ollama (Local)")
    assert any("Runs locally" in lbl.text() for lbl in dialog.provider_status_labels.values())
    dialog.set_active_provider("DeepSeek")
    dialog.close()
    window.close()


def test_segment_reassembly_safety() -> None:
    from src.logic import _parse_segmented_response, _reassemble_segments

    text = "one two three"
    spans = [(4, 7)]  # "two"
    parsed = _parse_segmented_response("the model forgot all markers", 2)
    assert parsed == ["", ""]
    # Missing markers must never delete the user's original text.
    assert _reassemble_segments(text, spans, parsed) == text
    # One good segment, one missing segment: only the good one is changed.
    assert _reassemble_segments(text, spans, ["ONE!", ""]) == "ONE!two three"
    # Valid markers are parsed normally.
    response = "===SEGMENT_0===\nAlpha\n\n===SEGMENT_1===\nBeta"
    assert _parse_segmented_response(response, 2) == ["Alpha", "Beta"]


def test_provider_connection_tester() -> None:
    from src.logic import test_provider_connection

    ok, message = test_provider_connection(
        api_key="",
        base_url="http://127.0.0.1:9/v1",
        model="",
        provider_name="Ollama (Local)",
    )
    assert ok is False
    assert message


def test_strict_editing_rules() -> None:
    from src.logic import (
        load_polish_prompt,
        load_proofreading_prompt,
        with_strict_editing_rules,
    )

    base = "You are an editor."
    enriched = with_strict_editing_rules(base)
    assert "STRICT LANGUAGE-EDITING MODE" in enriched
    assert "never a question" in enriched
    assert with_strict_editing_rules(enriched) == enriched

    assert "STRICT LANGUAGE-EDITING MODE" in load_polish_prompt()
    assert "STRICT LANGUAGE-EDITING MODE" in load_proofreading_prompt("Precise (Minimal Changes)")
    assert "STRICT LANGUAGE-EDITING MODE" in load_proofreading_prompt("Creative (Rewrite)")


def test_polish_prompt_and_flow() -> None:
    from src import generic_editing, logic

    prompt = logic.load_polish_prompt("Precise (Minimal Changes)")
    assert "professional writing editor" in prompt.lower()

    class FakeEditor:
        def __init__(self, text):
            self.text = text

        def frontmost_app(self):
            return {"pid": 1, "name": "FakeMail", "bundle_id": "fake.mail"}

        def get_selection(self, target):
            return self.text

        def activate(self, target):
            return True

        def get_selection_info(self, target):
            return (self.text, "Dear team, ", " Best regards, Alice")

        def is_word(self, target):
            return False

        @staticmethod
        def permission_status():
            return True, ""

    original_get_editor = generic_editing.get_generic_editor
    original_provider = logic.proofread_with_provider
    try:
        # No selection -> friendly status.
        generic_editing.get_generic_editor = lambda: FakeEditor("")
        status, *_ = logic.polish_selection_once(1024, {}, None)
        assert status == "No text selected in FakeMail.", status

        # Selection but no API keys -> clear message (explicit settings, no network).
        generic_editing.get_generic_editor = lambda: FakeEditor("Hello world this is a draft email.")
        settings_no_keys = {
            "active_provider": "DeepSeek",
            "providers": {"DeepSeek": {"api_keys": [], "base_url": "http://x", "model": "m"}},
            "general": {"temperature": 0.7, "spelling": "UK/AU/NZ", "style": "Precise (Minimal Changes)", "context": "General Editing"},
        }
        status, *_ = logic.polish_selection_once(1024, settings_no_keys, None)
        assert "No API keys configured" in status, status

        # Successful polish: verify the polish prompt was used.
        captured = {}

        def fake_provider(
            source_text, api_key, max_tokens, base_url, model,
            provider_name, temperature=0.7, context_before="", context_after="",
            spelling="UK/AU/NZ", style="Precise (Minimal Changes)",
            context="General Editing", system_prompt_override=None,
            text_section_label="Text to proofread", reviewer_comment="",
        ):
            captured["prompt"] = system_prompt_override
            captured["context_before"] = context_before
            captured["context_after"] = context_after
            captured["section_label"] = text_section_label
            return "Hello world. This is a polished draft email."

        logic.proofread_with_provider = fake_provider
        settings = {
            "active_provider": "DeepSeek",
            "providers": {"DeepSeek": {"api_keys": ["sk-test"], "base_url": "http://x", "model": "m"}},
            "general": {"temperature": 0.7, "spelling": "UK/AU/NZ", "style": "Precise (Minimal Changes)", "context": "General Editing"},
        }
        messages: list = []
        status, original, corrected, _, _ = logic.polish_selection_once(
            1024,
            settings,
            None,
            status_callback=messages.append,
        )
        assert status == "Polished.", status
        assert original == "Hello world this is a draft email."
        assert corrected is not None
        assert "polished" in corrected.lower()
        prompt = str(captured.get("prompt") or "")
        assert "professional writing editor" in prompt.lower()
        assert "STRICT LANGUAGE-EDITING MODE" in prompt
        assert captured["context_before"] == "Dear team, "
        assert captured["context_after"] == " Best regards, Alice"
        assert captured["section_label"] == "Text to polish"
        assert messages and "Polishing" in messages[0] and "characters" in messages[0]
    finally:
        generic_editing.get_generic_editor = original_get_editor
        logic.proofread_with_provider = original_provider


def test_generic_gui_flow() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src import settings
    from src.gui import ProofreaderApp

    loaded = settings.load_runtime_settings()
    loaded["general"]["auto_apply"] = False
    window = ProofreaderApp(1024, loaded)
    window.show()
    app.processEvents()

    fake_worker = SimpleNamespace(
        mode="generic",
        generic_target={"pid": 1, "name": "FakeMail", "bundle_id": "fake.mail"},
    )
    window.worker = fake_worker  # type: ignore[assignment]
    window._handle_generic_result(
        "Polished.",
        "Hello world.",
        "Hello, world.",
        "",
    )
    assert window.apply_btn.isVisible()
    assert window.apply_btn.text() == "Apply to FakeMail"
    assert window.pending_generic_apply is not None
    assert window.last_generic_target == {
        "pid": 1,
        "name": "FakeMail",
        "bundle_id": "fake.mail",
    }

    window._handle_generic_result("No text selected in FakeMail.", "", "", "")
    assert "No text selected" in window.status_label.text()
    window.close()


def test_generic_editor_classification() -> None:
    from src.generic_editing import GenericTextEditor

    assert GenericTextEditor.is_word({"bundle_id": "com.microsoft.Word", "name": "Microsoft Word"})
    assert GenericTextEditor.is_word({"exe": "C:\\Program Files\\Microsoft Office\\WINWORD.EXE"})
    assert not GenericTextEditor.is_word({"bundle_id": "com.apple.mail", "name": "Mail"})


def test_generic_selection_info_and_range_parser() -> None:
    from src.generic_editing import _parse_ax_range, get_generic_editor

    assert _parse_ax_range((10, 5)) == (10, 5)
    assert _parse_ax_range([3, 2]) == (3, 2)
    assert _parse_ax_range("not-a-range") == (None, None)

    editor = get_generic_editor()
    target = editor.frontmost_app()
    info = editor.get_selection_info(target)
    assert isinstance(info, tuple) and len(info) == 3
    assert all(isinstance(part, str) for part in info)


def test_mac_selection_fallback_safe() -> None:
    from src.generic_editing import GenericTextEditor

    # Without Accessibility permission these must return empty, never crash,
    # and never post keystrokes.
    assert GenericTextEditor._mac_selection({}) == ""
    assert isinstance(GenericTextEditor._mac_copy_selection(), str)


def test_generic_apply_preview_shows_diff() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src import settings
    from src.gui import ProofreaderApp

    loaded = settings.load_runtime_settings()
    loaded["general"]["auto_apply"] = True
    window = ProofreaderApp(1024, loaded)
    window.show()
    app.processEvents()
    window.worker = SimpleNamespace(  # type: ignore[assignment]
        mode="generic",
        generic_target={"pid": 1, "name": "FakeMail", "bundle_id": "fake.mail"},
    )
    window.pending_generic_preview = {
        "original": "Hello world.",
        "corrected": "Hello, world.",
        "comment": "",
        "app_name": "FakeMail",
    }
    window._on_generic_apply_done(True, "Applied to FakeMail.")
    assert "Hello, world." in window.diff_text.toPlainText()
    assert not window.apply_btn.isVisible()
    window.close()


def test_apply_verification_tolerant() -> None:
    from src.gui import evaluate_apply_verification

    # Smart-quote reformatting by the target app is not a failure.
    ok, state = evaluate_apply_verification(
        'Hello "there".',
        "Hello \u201cthere\u201d.",
        "Hello \u201cthere\u201d.",
    )
    assert ok and state == ""

    # Original text still selected means the paste did not land.
    ok, state = evaluate_apply_verification("Old text", "New text", "Old text")
    assert not ok and state == "ORIGINAL_STILL_SELECTED"

    # Anything else that changed is treated as applied.
    ok, _ = evaluate_apply_verification("Old text", "New text", "Something else")
    assert ok


def test_toast_notification_shows() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src.gui import ToastNotification

    toast = ToastNotification()
    toast.show_message("Applied to FakeMail.", kind="success", duration_ms=500)
    app.processEvents()
    assert toast.isVisible()
    toast.close()


def test_toast_processing_states() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src.gui import ToastNotification

    toast = ToastNotification()
    toast.show_processing("Proofreading…")
    app.processEvents()
    assert toast.is_processing()
    assert toast.isVisible()
    assert toast.waveform.isVisible()
    assert not toast.dot.isVisible()
    toast.update_message("Polishing in FakeMail…")
    assert "FakeMail" in toast.label.text()
    toast.complete("Applied to FakeMail.", kind="success")
    assert not toast.is_processing()
    assert "Applied" in toast.label.text()
    assert not toast.waveform.isVisible()
    assert toast.dot.isVisible()
    toast.close()


def test_worker_mode_passthrough() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src.gui import SingleProofreadWorker

    worker = SingleProofreadWorker(
        1024,
        {},
        mode="generic",
        generic_target={"pid": 1, "name": "FakeMail"},
        activate_target=False,
    )
    assert worker.mode == "generic"
    assert worker.generic_target == {"pid": 1, "name": "FakeMail"}
    assert worker.activate_target is False


def test_worker_status_starts_toast() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import settings
    from src.gui import ProofreaderApp

    window = ProofreaderApp(1024, settings.load_runtime_settings())
    window.toast.complete("Done", kind="success")
    window._on_worker_status("Polishing 42 characters from Mail…")
    assert window.toast.is_processing()
    assert "Mail" in window.toast.label.text()
    window.toast.complete("Applied ✓", kind="success")
    window.close()


def test_capture_diagnostics_shape() -> None:
    from src.generic_editing import capture_diagnostics

    result = capture_diagnostics()
    for key in (
        "platform",
        "frontmost_app",
        "permission_ok",
        "ax_text",
        "selected_text",
        "selected_text_preview",
        "context_before_len",
        "context_after_len",
        "is_word",
        "mode",
        "clipboard_fallback_used",
        "errors",
    ):
        assert key in result, key
    assert isinstance(result["errors"], list)


def test_find_target_with_selection() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import settings
    from src.gui import ProofreaderApp

    window = ProofreaderApp(1024, settings.load_runtime_settings())
    window._last_other_app = {"pid": 2, "name": "Mail", "bundle_id": "com.apple.mail"}
    window._app_history = [
        {"pid": 2, "name": "Mail", "bundle_id": "com.apple.mail"},
        {"pid": 3, "name": "Finder", "bundle_id": "com.apple.finder"},
    ]

    class FakeEditor:
        def is_word(self, target):
            return False

        def running_apps(self):
            return []

        def get_selection_ax_only(self, target):
            return "Selected text" if target.get("pid") == 2 else ""

        def activate(self, target):
            return True

        def get_selection_info(self, target):
            return ("Selected text", "", "") if target.get("pid") == 2 else ("", "", "")

    editor = FakeEditor()
    result = window._find_target_with_selection(editor)
    assert result is not None and result["pid"] == 2

    # No app with text -> no target (must not fall back to Word).
    class EmptyEditor:
        def is_word(self, target):
            return False

        def running_apps(self):
            return []

        def get_selection_ax_only(self, target):
            return ""

        def activate(self, target):
            return True

        def get_selection_info(self, target):
            return ("", "", "")

    assert window._find_target_with_selection(EmptyEditor()) is None

    # Empty history but a running app has text -> found via the app scan.
    class ScanEditor:
        def is_word(self, target):
            return False

        def running_apps(self):
            return [{"pid": 5, "name": "Mail", "bundle_id": "com.apple.mail"}]

        def get_selection_ax_only(self, target):
            return "Mail selection" if target.get("pid") == 5 else ""

        def activate(self, target):
            return True

        def get_selection_info(self, target):
            return ("Mail selection", "", "") if target.get("pid") == 5 else ("", "", "")

    window._app_history = []
    window._last_other_app = None
    scan_result = window._find_target_with_selection(ScanEditor())
    assert scan_result is not None and scan_result["pid"] == 5
    window.close()


def test_machine_fingerprint_stable() -> None:
    from src import licensing

    first = licensing._get_machine_fingerprint()
    second = licensing._get_machine_fingerprint()
    assert first and second and first == second
    assert len(first) > 10


def test_dock_activation_restores_window() -> None:
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src import settings
    from src.gui import ProofreaderApp

    window = ProofreaderApp(1024, settings.load_runtime_settings())
    window.show()
    app.processEvents()
    window.hide()
    app.processEvents()
    assert window.isHidden()

    activation = QEvent(QEvent.Type.ApplicationActivate)
    window.eventFilter(window, activation)
    app.processEvents()
    assert not window.isHidden()
    window.close()


def test_settings_dialog_fits_screen() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src import settings
    from src.gui import SettingsDialog

    dialog = SettingsDialog(settings.load_runtime_settings())
    dialog.show()
    app.processEvents()
    assert dialog.button_box.isVisible()
    assert dialog.button_box.geometry().bottom() <= dialog.height()
    dialog.close()


def test_update_dismissal() -> None:
    from src.gui import is_update_dismissed

    settings = {"general": {"skipped_update_version": "1.0.2"}}
    assert is_update_dismissed("1.0.2", settings)
    assert not is_update_dismissed("1.0.3", settings)
    assert not is_update_dismissed("", settings)


def test_download_update_progress() -> None:
    import http.server
    import socketserver
    import threading

    from src.app_version import download_update

    payload = b"fake-installer-content"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            tmpdir = tempfile.mkdtemp()
            calls = []
            path = download_update(
                {"macos_apple_silicon_url": f"http://127.0.0.1:{port}/ByteProof.dmg"},
                tmpdir,
                progress_callback=lambda done, total: calls.append((done, total)),
            )
            assert path and os.path.exists(path)
            with open(path, "rb") as f:
                assert f.read() == payload
            assert calls and calls[-1][0] == len(payload)
        finally:
            server.shutdown()


def test_sound_module() -> None:
    from src import sound

    path = sound._sound_path()
    assert path and os.path.exists(path)
    assert path.endswith("proofread_start.wav")

    # Verify the play call is safe without actually making a sound.
    original_popen = sound.subprocess.Popen
    sound.subprocess.Popen = lambda *args, **kwargs: None
    try:
        sound.play_start_sound()
    finally:
        sound.subprocess.Popen = original_popen


def test_local_model_catalog_and_recommendation() -> None:
    from src import local_model

    original_dir = local_model.LOCAL_MODEL_DIR
    local_model.LOCAL_MODEL_DIR = tempfile.mkdtemp(prefix="byteproof-test-models-")
    try:
        catalog = local_model.get_catalog()
        assert len(catalog) >= 3
        model = local_model.get_model("qwen3-4b")
        assert model["sha256"]
        assert model["size_bytes"] > 1_000_000_000
        assert local_model.model_path("qwen3-4b").endswith(".gguf")
        assert local_model.is_model_installed("qwen3-4b") is False
        assert local_model.resolve_model_id("qwen3-4b") == "qwen3-4b"
        assert local_model.resolve_model_id("not-a-model") == local_model.recommend_model()["id"]
        assert local_model.resolve_model_id(None) == local_model.recommend_model()["id"]

        assert local_model.recommend_model({"total_ram_gb": 8.0})["id"] == "qwen3-4b"
        assert local_model.recommend_model({"total_ram_gb": 16.0})["id"] == "qwen3-8b"
        assert local_model.recommend_model({"total_ram_gb": 24.0})["id"] == "qwen3-14b"
    finally:
        local_model.LOCAL_MODEL_DIR = original_dir


def test_local_model_download_with_checksum_and_resume() -> None:
    import http.server
    import socketserver
    import threading

    from src import local_model

    payload = b"byteproof-local-model-test" * 200
    sha = hashlib.sha256(payload).hexdigest()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            range_header = self.headers.get("Range")
            if range_header:
                start = int(range_header.split("=")[1].split("-")[0])
                body = payload[start:]
                self.send_response(206)
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{len(payload) - 1}/{len(payload)}",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            tmpdir = tempfile.mkdtemp()
            dest = os.path.join(tmpdir, "qwen-test.gguf")

            calls = []
            local_model.download_file(
                f"http://127.0.0.1:{port}/model.gguf",
                dest,
                expected_size=len(payload),
                expected_sha256=sha,
                progress_callback=lambda done, total, stage: calls.append((done, total)),
            )
            with open(dest, "rb") as f:
                assert f.read() == payload
            assert calls and calls[-1][0] == len(payload)

            # Resume: seed the .part file, then re-download and verify.
            os.remove(dest)
            with open(dest + ".part", "wb") as f:
                f.write(payload[: len(payload) // 3])
            local_model.download_file(
                f"http://127.0.0.1:{port}/model.gguf",
                dest,
                expected_sha256=sha,
            )
            with open(dest, "rb") as f:
                assert f.read() == payload

            # Corrupt checksum must fail and leave nothing behind.
            bad_dest = os.path.join(tmpdir, "bad.gguf")
            try:
                local_model.download_file(
                    f"http://127.0.0.1:{port}/model.gguf",
                    bad_dest,
                    expected_sha256="0" * 64,
                )
                raise AssertionError("Expected checksum failure")
            except RuntimeError:
                assert not os.path.exists(bad_dest)
                assert not os.path.exists(bad_dest + ".part")
        finally:
            server.shutdown()


def test_settings_new_pages() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import settings
    from src.gui import SettingsDialog

    dialog = SettingsDialog(settings.load_runtime_settings())
    dialog.show()
    app.processEvents()

    assert dialog.sidebar.count() == 4
    local_item = dialog.sidebar.item(2)
    assert local_item is not None and local_item.text() == "Local AI"
    license_item = dialog.sidebar.item(3)
    assert license_item is not None and license_item.text() == "License"

    dialog.sidebar.setCurrentRow(2)
    dialog.change_page(2)
    app.processEvents()
    assert dialog.local_page is not None
    assert "Recommended" in dialog.local_recommend_label.text()

    dialog._use_local_model("qwen3-4b")
    assert dialog.settings["active_provider"] == "ByteProof Local (Qwen3)"
    assert dialog.settings["local_model"]["active_model"] == "qwen3-4b"
    assert dialog.settings["providers"]["ByteProof Local (Qwen3)"]["model"] == "qwen3-4b"
    dialog.close()


def test_resolve_local_provider_without_api_key() -> None:
    from src import logic, settings

    original_start = logic.start_local_server
    try:
        logic.start_local_server = lambda model_id=None, progress_callback=None: (
            "http://127.0.0.1:17999/v1"
        )
        runtime = settings.load_runtime_settings()
        runtime["active_provider"] = settings.LOCAL_MODEL_PROVIDER
        runtime["providers"][settings.LOCAL_MODEL_PROVIDER] = {
            "api_keys": [],
            "base_url": "",
            "model": "qwen3-4b",
        }

        assert logic.provider_requires_api_key(settings.LOCAL_MODEL_PROVIDER) is False
        assert logic.provider_requires_api_key("DeepSeek") is True

        name, api_key, base_url, model = logic.resolve_provider_connection(runtime)
        assert name == settings.LOCAL_MODEL_PROVIDER
        assert api_key == ""
        assert base_url == "http://127.0.0.1:17999/v1"
        assert model == "qwen3-4b"
    finally:
        logic.start_local_server = original_start


def test_local_download_worker_anchored_to_main_window() -> None:
    """The QThread must outlive the Settings dialog or Qt aborts."""
    import time

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import gui, settings

    window = gui.ProofreaderApp(1024, settings.load_runtime_settings())
    dialog = gui.SettingsDialog(settings.load_runtime_settings(), window)
    dialog.show()
    app.processEvents()

    assert gui._find_owner_window(dialog) is window

    class FakeDownloadWorker(gui.LocalModelDownloadWorker):
        def run(self) -> None:
            time.sleep(0.2)
            self.done.emit(self.model_id)

    original_worker = gui.LocalModelDownloadWorker
    gui.LocalModelDownloadWorker = FakeDownloadWorker
    try:
        dialog._download_local_model("qwen3-4b")
        worker = getattr(window, "_local_download_worker", None)
        assert worker is not None, "worker must be stored on the main window"
        assert worker.isRunning()
        assert worker.wait(3000)
        assert not worker.isRunning()
    finally:
        gui.LocalModelDownloadWorker = original_worker
        dialog.close()
        window.close()


def test_format_bytes() -> None:
    from src.gui import _format_bytes

    assert _format_bytes(0) == "0 B"
    assert _format_bytes(1023) == "1023 B"
    assert _format_bytes(1024) == "1.00 KB"
    assert _format_bytes(2497280256) == "2.33 GB"


def test_local_model_use_button_active_state() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import gui, settings

    window = gui.ProofreaderApp(1024, settings.load_runtime_settings())
    dialog = gui.SettingsDialog(settings.load_runtime_settings(), window)
    dialog.show()
    app.processEvents()

    original_installed = gui.is_model_installed
    gui.is_model_installed = lambda _model_id: True
    try:
        dialog._use_local_model("qwen3-4b")
        active_btn = dialog.local_model_cards["qwen3-4b"]["use"]
        other_btn = dialog.local_model_cards["qwen3-8b"]["use"]
        assert active_btn.text() == "Active"
        assert not active_btn.isEnabled()
        assert other_btn.text() == "Use"
        assert other_btn.isEnabled()
    finally:
        gui.is_model_installed = original_installed
        dialog.close()
        window.close()


def test_provider_free_badges_only_local_and_ollama() -> None:
    from src import settings

    assert settings.PROVIDERS["ByteProof Local (Qwen3)"].get("is_local")
    assert settings.PROVIDERS["Ollama (Local)"].get("is_free")
    for name in (
        "DeepSeek",
        "Google Gemini",
        "Groq",
        "OpenAI",
        "Anthropic",
        "xAI",
        "Perplexity",
    ):
        assert not settings.PROVIDERS[name].get("is_free"), name


def test_download_file_cancellation_keeps_partial_file() -> None:
    import http.server
    import socketserver
    import threading
    import time

    from src import local_model

    payload = b"cancellable-download-payload-" * 5000

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for i in range(0, len(payload), 4096):
                self.wfile.write(payload[i : i + 4096])
                self.wfile.flush()
                time.sleep(0.02)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            tmpdir = tempfile.mkdtemp()
            dest = os.path.join(tmpdir, "model.gguf")
            cancel_event = threading.Event()
            result: dict[str, Any] = {}

            def run_download() -> None:
                try:
                    local_model.download_file(
                        f"http://127.0.0.1:{port}/model.gguf",
                        dest,
                        expected_size=len(payload),
                        cancel_event=cancel_event,
                    )
                    result["ok"] = True
                except Exception as exc:
                    result["error"] = exc

            worker = threading.Thread(target=run_download, daemon=True)
            worker.start()
            time.sleep(0.15)
            cancel_event.set()
            worker.join(5)

            assert "ok" not in result
            assert isinstance(result.get("error"), local_model.DownloadCancelledError)
            assert os.path.exists(dest + ".part")
        finally:
            server.shutdown()


def test_double_escape_cancels_download() -> None:

    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import gui, settings

    window = gui.ProofreaderApp(1024, settings.load_runtime_settings())
    dialog = gui.SettingsDialog(settings.load_runtime_settings(), window)
    dialog.show()
    app.processEvents()

    class FakeDownloadWorker(gui.LocalModelDownloadWorker):
        def run(self) -> None:
            while not self.cancel_event.wait(0.05):
                pass
            self.cancelled.emit()

    original_worker = gui.LocalModelDownloadWorker
    gui.LocalModelDownloadWorker = FakeDownloadWorker
    try:
        dialog._download_local_model("qwen3-4b")
        worker = getattr(window, "_local_download_worker", None)
        assert worker is not None and worker.isRunning()

        esc = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        assert window.eventFilter(None, esc) is True
        assert not worker.cancel_event.is_set()
        assert window.eventFilter(None, esc) is True
        assert worker.cancel_event.is_set()
        assert worker.wait(3000)
    finally:
        gui.LocalModelDownloadWorker = original_worker
        dialog.close()
        window.close()


def main() -> None:
    test_updater_url_selection()
    print("PASS updater URL selection")
    test_settings_branding()
    print("PASS settings branding")
    test_open_purchase_url_uses_live_link()
    print("PASS open purchase URL uses live link")
    test_licensing_roundtrip()
    print("PASS licensing roundtrip")
    test_activation_from_url()
    print("PASS activation from URL")
    test_trial_expired_disables_proofreading()
    print("PASS trial expiry blocks proofreading")
    test_hotkey_conversion()
    print("PASS hotkey conversion")
    test_gui_constructs()
    print("PASS GUI construction")
    test_segment_reassembly_safety()
    print("PASS segment reassembly safety")
    test_provider_connection_tester()
    print("PASS provider connection tester")
    test_strict_editing_rules()
    print("PASS strict editing rules")
    test_polish_prompt_and_flow()
    print("PASS polish prompt + flow")
    test_generic_gui_flow()
    print("PASS generic GUI flow")
    test_generic_editor_classification()
    print("PASS generic editor classification")
    test_generic_selection_info_and_range_parser()
    print("PASS generic selection info + range parser")
    test_mac_selection_fallback_safe()
    print("PASS mac selection fallback (safe without permission)")
    test_generic_apply_preview_shows_diff()
    print("PASS generic apply preview (diff shown after apply)")
    test_apply_verification_tolerant()
    print("PASS apply verification tolerant")
    test_toast_notification_shows()
    print("PASS toast notification shows")
    test_toast_processing_states()
    print("PASS toast processing states")
    test_worker_mode_passthrough()
    print("PASS worker mode passthrough")
    test_worker_status_starts_toast()
    print("PASS worker status starts toast")
    test_capture_diagnostics_shape()
    print("PASS capture diagnostics shape")
    test_find_target_with_selection()
    print("PASS find target with selection")
    test_machine_fingerprint_stable()
    print("PASS machine fingerprint stable")
    test_dock_activation_restores_window()
    print("PASS dock activation restores window")
    test_settings_dialog_fits_screen()
    print("PASS settings dialog fits screen")
    test_update_dismissal()
    print("PASS update dismissal")
    test_download_update_progress()
    print("PASS update download progress")
    test_sound_module()
    print("PASS sound module")
    test_local_model_catalog_and_recommendation()
    print("PASS local model catalog + recommendation")
    test_local_model_download_with_checksum_and_resume()
    print("PASS local model download (checksum + resume)")
    test_settings_new_pages()
    print("PASS settings new pages")
    test_resolve_local_provider_without_api_key()
    print("PASS resolve local provider without API key")
    test_local_download_worker_anchored_to_main_window()
    print("PASS local download worker anchored to main window")
    test_format_bytes()
    print("PASS format bytes")
    test_local_model_use_button_active_state()
    print("PASS local model use button active state")
    test_provider_free_badges_only_local_and_ollama()
    print("PASS provider free badges only local + Ollama")
    test_download_file_cancellation_keeps_partial_file()
    print("PASS download file cancellation keeps partial file")
    test_double_escape_cancels_download()
    print("PASS double escape cancels download")
    print("\nALL_SMOKE_TESTS_PASSED")


if __name__ == "__main__":
    main()
