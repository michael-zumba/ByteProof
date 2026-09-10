"""Hardening tests for the 2.0.1-beta.3 review fixes.

Each group exercises a specific defect found in the 2026-09-10 full-app review:

* update/version semantics (beta builds must still see the next release)
* download integrity (host allowlist + published SHA-256)
* licensing hardening (no public master key, confirmed URL activation)
* Word document safety (Track-Changes restore, timeouts, range guards)
* runtime hygiene (log rotation list, secret file permissions)
"""

import hashlib
import http.server
import os
import socketserver
import tempfile
import threading
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _qt_app():
    """Live-preview tests build widgets, which need a QApplication."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app

# --- update version semantics ------------------------------------------------


def test_prerelease_sorts_below_its_release() -> None:
    from src.app_version import _parse_version

    assert _parse_version("2.0.1-beta.2") < _parse_version("2.0.1")
    assert _parse_version("2.0.1-rc.1") < _parse_version("2.0.1")
    assert _parse_version("2.0.1-beta.2") < _parse_version("2.0.1-rc.1")
    assert _parse_version("2.0.1-beta.2") < _parse_version("2.0.1-beta.10")
    assert _parse_version("2.0.1-beta.2") > _parse_version("2.0.0")
    assert _parse_version("2.0.1") < _parse_version("2.0.2")


def test_beta_build_is_offered_the_next_release() -> None:
    """The exact regression: a tester on a beta never saw the next version."""
    from src.app_version import is_newer

    # Running 2.0.1-beta.2, the feed announces 2.0.2 -> must be offered.
    assert is_newer("2.0.2", "2.0.1-beta.2")
    # ...and 2.0.1 final, which supersedes the beta, must be offered too.
    assert is_newer("2.0.1", "2.0.1-beta.2")
    # The beta itself is not an upgrade from the release it precedes.
    assert not is_newer("2.0.1-beta.2", "2.0.1")
    # Same version, no update.
    assert not is_newer("2.0.2", "2.0.2")


def test_version_parsing_tolerates_prefixes_and_junk() -> None:
    from src.app_version import _parse_version

    assert _parse_version("v2.1.0") == _parse_version("2.1.0")
    assert _parse_version(" 2.1 ") == _parse_version("2.1.0")
    assert _parse_version("2.0.1+build.9") == _parse_version("2.0.1")
    for bad in ("", "abc", "-"):
        try:
            _parse_version(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


def test_check_for_updates_uses_prerelease_ordering(monkeypatch) -> None:
    from src import app_version

    monkeypatch.setattr(
        app_version, "_fetch_version_info", lambda: {"version": "2.0.2"}
    )
    available, info = app_version.check_for_updates("2.0.1-beta.2")
    assert available and info is not None and info["version"] == "2.0.2"

    monkeypatch.setattr(
        app_version, "_fetch_version_info", lambda: {"version": "2.0.1-beta.2"}
    )
    available, _ = app_version.check_for_updates("2.0.1-beta.2")
    assert not available


# --- download integrity ------------------------------------------------------


def test_update_url_allowlist() -> None:
    from src.app_version import _is_allowed_url

    assert _is_allowed_url(
        "https://github.com/michael-zumba/ByteProof/releases/latest/download/x.dmg"
    )
    assert _is_allowed_url("https://www.bytemind.co.nz/byteproof-version.json")
    assert _is_allowed_url("https://objects.githubusercontent.com/asset/1")
    # Plain http is refused even on a trusted host.
    assert not _is_allowed_url("http://github.com/x/y.dmg")
    # Lookalike hosts must not pass.
    assert not _is_allowed_url("https://github.com.evil.example/x.dmg")
    assert not _is_allowed_url("https://evil.example/github.com")
    assert not _is_allowed_url("https://notgithub.com/x.dmg")
    assert not _is_allowed_url("file:///tmp/ByteProof.dmg")
    assert not _is_allowed_url("")


def test_expected_sha256_supports_both_feed_shapes() -> None:
    from src.app_version import _expected_sha256

    mapped = {"sha256": {"windows_url": "ABC123"}}
    assert _expected_sha256(mapped, "windows_url") == "abc123"
    mapped_short = {"sha256": {"windows": "DEF456"}}
    assert _expected_sha256(mapped_short, "windows_url") == "def456"
    flat = {"sha256_windows_url": "0F0F"}
    assert _expected_sha256(flat, "windows_url") == "0f0f"
    assert _expected_sha256({}, "windows_url") is None


def test_verify_file_sha256() -> None:
    from src.app_version import verify_file_sha256

    path = os.path.join(tempfile.mkdtemp(), "artifact.bin")
    with open(path, "wb") as handle:
        handle.write(b"byteproof")
    digest = hashlib.sha256(b"byteproof").hexdigest()
    assert verify_file_sha256(path, digest)
    assert verify_file_sha256(path, digest.upper())
    assert not verify_file_sha256(path, "0" * 64)
    # No published checksum means nothing to verify (feed compatibility).
    assert verify_file_sha256(path, None)


def _serve(payload: bytes, headers: dict[str, str] | None = None) -> Any:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_download_update_verifies_published_checksum(monkeypatch) -> None:
    from src import app_version

    payload = b"installer-bytes"
    server = _serve(payload)
    port = server.server_address[1]
    monkeypatch.setattr(app_version, "_is_allowed_url", lambda url: True)
    try:
        good = hashlib.sha256(payload).hexdigest()
        target = os.path.join(tempfile.mkdtemp(), "ByteProof.dmg")
        url = f"http://127.0.0.1:{port}/ByteProof.dmg"

        # Matching checksum: the file is kept.
        path = app_version.download_update(
            {"macos_apple_silicon_url": url, "sha256": {"macos_apple_silicon_url": good}},
            os.path.dirname(target),
        )
        assert path and os.path.exists(path)
        with open(path, "rb") as handle:
            assert handle.read() == payload

        # Mismatching checksum: the download is discarded.
        path = app_version.download_update(
            {"macos_apple_silicon_url": url, "sha256": {"macos_apple_silicon_url": "0" * 64}},
            tempfile.mkdtemp(),
        )
        assert path is None
    finally:
        server.shutdown()


def test_download_update_refuses_untrusted_host(monkeypatch) -> None:
    from src import app_version

    called = []
    monkeypatch.setattr(
        app_version.urllib.request, "urlopen", lambda *a, **k: called.append(a)
    )
    path = app_version.download_update(
        {"macos_apple_silicon_url": "https://evil.example/ByteProof.dmg"},
        tempfile.mkdtemp(),
    )
    assert path is None
    assert called == []  # refused before any network access


# --- licensing hardening -----------------------------------------------------


def _isolate_licensing(monkeypatch, tmpdir: str):
    from src import licensing

    monkeypatch.setattr(
        licensing, "_get_license_path", lambda: os.path.join(tmpdir, "license.json")
    )
    monkeypatch.setattr(licensing, "_secure_store_set", lambda value: None)
    monkeypatch.setattr(licensing, "_secure_store_get", lambda: None)
    monkeypatch.setattr(licensing, "_secure_store_delete", lambda: None)
    return licensing


def test_support_email_is_not_a_license_key(monkeypatch) -> None:
    """Typing the public support address must never unlock the app."""
    from src import activation, settings

    tmpdir = tempfile.mkdtemp()
    licensing = _isolate_licensing(monkeypatch, tmpdir)
    # Simulate a shipped build: no developer identities configured.
    monkeypatch.setattr(activation, "developer_emails", lambda: ())
    monkeypatch.setattr(licensing, "developer_emails", lambda: ())

    result = activation.activate_with_key(settings.SUPPORT_EMAIL)
    assert result.get("ok") is False
    assert not licensing.is_licensed()
    assert not os.path.exists(os.path.join(tmpdir, "license.json"))


def test_activation_url_requires_a_key_not_an_email(monkeypatch) -> None:
    from src import activation, settings

    tmpdir = tempfile.mkdtemp()
    licensing = _isolate_licensing(monkeypatch, tmpdir)
    monkeypatch.setattr(activation, "developer_emails", lambda: ())
    monkeypatch.setattr(licensing, "developer_emails", lambda: ())

    result = activation.activate_from_url(
        f"byteproof://activate?email={settings.SUPPORT_EMAIL}"
    )
    assert result.get("ok") is False
    assert not licensing.is_licensed()


def test_developer_access_requires_local_configuration(monkeypatch) -> None:
    """A configured developer identity still works, but only locally."""
    from src import activation, licensing

    tmpdir = tempfile.mkdtemp()
    monkeypatch.setattr(
        licensing, "_get_license_path", lambda: os.path.join(tmpdir, "license.json")
    )
    monkeypatch.setattr(licensing, "_secure_store_set", lambda value: None)
    monkeypatch.setattr(licensing, "_secure_store_get", lambda: None)
    monkeypatch.setattr(licensing, "_secure_store_delete", lambda: None)
    monkeypatch.setattr(
        licensing, "developer_emails", lambda: ("owner@example.test",)
    )
    monkeypatch.setattr(
        activation, "developer_emails", lambda: ("owner@example.test",)
    )

    assert licensing.is_licensed() is False
    result = activation.activate_with_key("owner@example.test")
    assert result.get("ok") is True
    assert licensing.is_licensed()
    assert licensing.get_access_status()["tier"] == "licensed"


def test_developer_emails_are_empty_in_shipped_defaults() -> None:
    """The shipped source must not carry a public master identity."""
    from src import settings

    assert settings.DEVELOPER_EMAILS == ()


# --- Word document safety ----------------------------------------------------


def test_track_changes_restore_is_exception_safe() -> None:
    """Every suspend of revision tracking must be restored on the error path."""
    import re

    from src import word_integration

    with open(word_integration.__file__, encoding="utf-8") as handle:
        source = handle.read()
    # Look at each AppleScript block individually: a block that saves and
    # disables revision tracking must also carry an `on error` handler that
    # restores it, otherwise a failed write leaves tracking switched off.
    blocks = re.findall(r'"""(.*?)"""', source, flags=re.DOTALL)
    suspend_blocks = [
        block
        for block in blocks
        if "set track revisions of active document to false" in block
        and "set oldTrack to track revisions of active document" in block
    ]
    assert suspend_blocks, "expected at least one suspend/restore script"
    for block in suspend_blocks:
        assert "on error" in block, "suspend without an error handler"
        assert (
            block.count("set track revisions of active document to oldTrack") >= 2
        ), "suspend must restore on both the success and the error path"


def test_applescript_calls_have_a_timeout() -> None:
    from src import word_integration

    with open(word_integration.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "timeout=" in source


# --- runtime hygiene ---------------------------------------------------------


def test_log_paths_include_every_written_log() -> None:
    from src import cache_cleanup, settings

    names = {os.path.basename(path) for path in cache_cleanup.LOG_PATHS}
    assert "capture.log" in names
    assert "error.log" in names
    assert "citation-mapping.log" in names
    assert settings is not None


# --- live-preview safety (2.0.1-beta.3) --------------------------------------


def test_undo_passes_before_text_and_refuses_when_text_changed(monkeypatch):
    """Undo must verify the applied text is still there before overwriting."""
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    calls = []

    class FakeEditor:
        def ax_replace_range(
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            calls.append((start, length, new, before_text, allow_direct_paste))
            return True, "Applied."

    from PyQt6.QtCore import QPoint

    service._editor = FakeEditor()
    service._last_anchor = QPoint(50, 50)
    messages = []
    service.apply_done.connect(messages.append)
    service._undo_state = {
        "mode": "range",
        "target": {"pid": 7, "bundle_id": "com.example.app"},
        "is_word": False,
        "steps": [(100, "the", "teh")],
    }
    service._perform_undo()

    assert calls == [(100, 3, "teh", "the", False)]
    assert messages == ["Undone."]
    service.stop()


def test_undo_stack_keeps_older_steps(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 900, "max_chars": 1500}}
    )
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
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            applied.append((start, length, new))
            return True, "Applied."

    from PyQt6.QtCore import QPoint

    service._editor = FakeEditor()
    service._last_anchor = QPoint(50, 50)
    service._selection_target = {"bundle_id": "com.example.app", "pid": 9}
    service._selection_start = 100
    service._selection_is_word = False
    service._selection_has_range = True

    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_text = "teh"
    service._seen_text = "teh"
    service._apply_one(0)
    first_state = service._undo_state
    assert first_state is not None

    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._selection_text = "teh"
    service._seen_text = "teh"
    service._apply_one(0)
    # The earlier apply is still undoable.
    assert service._undo_state is not first_state
    assert service._undo_previous
    service.stop()


def test_live_preview_respects_licence_limit(monkeypatch):
    """The live panel must not spend provider credits past the free limit."""
    from src import licensing
    from src import live_service as ls
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 900, "max_chars": 1500}}
    )
    monkeypatch.setattr(
        licensing,
        "get_access_status",
        lambda: {"tier": "free", "free_mode_allowed": False},
    )
    started = []

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            started.append(args)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    monkeypatch.setattr(ls, "PreviewWorker", FakeWorker)
    errors = []
    service.preview_error.connect(errors.append)

    service._spawn_preview({"name": "TextEdit", "pid": 1}, "hello there", {}, "k")
    assert started == []  # no provider call was started
    assert errors and "limit" in errors[0].lower()
    service.stop()


def test_live_preview_allows_licensed_users(monkeypatch):
    from src import licensing
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 900, "max_chars": 1500}}
    )
    monkeypatch.setattr(
        licensing,
        "get_access_status",
        lambda: {"tier": "licensed", "free_mode_allowed": False},
    )
    assert service._access_allows_preview() is True
    service.stop()


def test_dismissed_panel_is_not_reopened_by_late_result(monkeypatch):
    from src import live_service as ls
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 900, "max_chars": 1500}}
    )
    service._spawned_at = 100.0
    service._dismissed_at = 101.0  # user pressed Escape after the spawn
    shown = []
    monkeypatch.setattr(service, "_show_result", lambda spans: shown.append(spans))
    service._selection_target = {"name": "TextEdit", "pid": 1, "bundle_id": "x"}
    monkeypatch.setattr(
        service._editor,
        "frontmost_app",
        lambda: {"pid": 1, "bundle_id": "x"},
    )
    monkeypatch.setattr(service, "_sync_selection", lambda: (True, ""))
    service._on_done({"status": "ok", "edits": [], "meta": {}}, "key", "text")
    assert shown == []
    assert ls is not None
    service.stop()


def test_capture_log_redacts_user_text(monkeypatch, tmp_path):
    """capture.log must not accumulate the user's document text."""
    from src import generic_editing

    log_path = tmp_path / "capture.log"
    monkeypatch.setattr(
        generic_editing, "get_app_support_dir", lambda: str(tmp_path)
    )
    generic_editing._last_logged.clear()
    secret = "Confidential thesis paragraph about patient outcomes"
    generic_editing._debug_log(f"range holds {generic_editing._redact(secret)}")
    content = log_path.read_text(encoding="utf-8")
    assert secret not in content
    assert "len=" in content and "sha=" in content


def test_settings_file_is_user_private(tmp_path, monkeypatch):
    import stat

    from src import settings

    monkeypatch.setattr(settings, "APP_SUPPORT_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.save_runtime_settings({"providers": {"DeepSeek": {"api_keys": ["k"]}}})
    mode = stat.S_IMODE(os.stat(tmp_path / "settings.json").st_mode)
    assert mode == 0o600


# --- truncated model replies must never be auto-applied ----------------------


def test_truncated_response_is_detected() -> None:
    from src import logic

    logic._record_finish_reason("length")
    assert logic.last_response_was_truncated() is True
    logic._record_finish_reason("stop")
    assert logic.last_response_was_truncated() is False
    logic._record_finish_reason("max_tokens")
    assert logic.last_response_was_truncated() is True
    logic._record_finish_reason("")


def test_truncation_heuristic_only_flags_drastic_shortening() -> None:
    from src.logic import _looks_truncated

    long_text = "word " * 200  # 1000 characters
    # A normal edit keeps most of the text.
    assert not _looks_truncated(long_text, long_text[:-20])
    # A conciseness rewrite still keeps the bulk of it.
    assert not _looks_truncated(long_text, long_text[:600])
    # A reply cut off halfway is caught.
    assert _looks_truncated(long_text, long_text[:200])
    # Short selections are exempt from the heuristic.
    assert not _looks_truncated("teh cat sat", "the cat")


def test_request_completion_resets_stop_reason(monkeypatch) -> None:
    """A previous truncated reply must not mark later ones as truncated."""
    from src import logic

    logic._record_finish_reason("length")

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            import json

            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    payload = {
        "choices": [
            {"message": {"content": "corrected"}, "finish_reason": "stop"}
        ]
    }
    monkeypatch.setattr(
        logic.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload)
    )
    text = logic._request_completion(
        "system", "user", "key", 100, "https://example.test", "m", "DeepSeek", 0.1
    )
    assert text == "corrected"
    assert logic.last_response_was_truncated() is False


# --- applies must survive focus changes --------------------------------------


def _apply_service(monkeypatch, frontmost: dict[str, Any]):
    """A service whose captured target is NOT the frontmost app."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    applied: list[tuple[int, int, str]] = []
    frontmost_calls: list[int] = []

    class FakeEditor:
        def frontmost_app(self):
            frontmost_calls.append(1)
            return dict(frontmost)

        def selection_details(self, target):
            assert target.get("pid") == 4242  # the captured target, not front
            return {
                "text": "teh cat",
                "range": (10, 6),
                "context_before": "",
                "context_after": "",
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

        def ax_replace_range(
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            assert target.get("pid") == 4242
            applied.append((start, length, new))
            return True, "Applied."

        def activate(self, target):
            return True

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.x", "pid": 4242, "name": "X"}
    service._selection_start = 10
    service._selection_is_word = False
    service._selection_has_range = True
    service._selection_text = "teh cat"
    service._seen_text = "teh cat"
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    return service, applied, frontmost_calls


def test_apply_one_works_while_another_app_is_frontmost(monkeypatch):
    service, applied, _ = _apply_service(
        monkeypatch, {"bundle_id": "com.other", "pid": 99, "name": "Other"}
    )
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == [(10, 3, "the")]  # the captured selection was edited
    assert messages and messages[0] == "Applied."
    service.stop()


def test_apply_all_works_while_another_app_is_frontmost(monkeypatch):
    from src.live_preview import EditSpan

    service, applied, _ = _apply_service(
        monkeypatch, {"bundle_id": "com.other", "pid": 99, "name": "Other"}
    )
    service._pending = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("cat", "dog", "Word choice", 4, 3),
    ]
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    assert applied[0] == (10, 3, "the")
    assert len(applied) == 2
    assert messages and messages[0].startswith("Applied")
    service.stop()


def test_poll_is_silent_while_an_apply_runs(monkeypatch):
    """A new selection mid-apply must not spawn a preview or hide the panel."""
    service, _applied, frontmost_calls = _apply_service(
        monkeypatch, {"bundle_id": "com.other", "pid": 99, "name": "Other"}
    )
    spawned: list[Any] = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a: spawned.append(a))
    hidden: list[int] = []
    monkeypatch.setattr(service, "_hide_panel", lambda: hidden.append(1))

    service._applying = True
    service._sample(now=1000.0)
    service._sample(now=2000.0)

    assert spawned == []
    assert hidden == []
    assert frontmost_calls == []  # the poll did not even read the screen
    service._applying = False
    service.stop()


def test_selection_made_during_apply_is_not_auto_previewed(monkeypatch):
    """Text selected while edits were being written is marked as seen.

    The user asked for it explicitly: selecting something else to read during
    an apply must neither interrupt the apply nor trigger a fresh preview.
    """
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class FakeEditor:
        def frontmost_app(self):
            return {"bundle_id": "com.example", "pid": 5, "name": "App"}

        def selection_details(self, target):
            # The selection the user made while the apply was running.
            return {
                "text": "other text being read",
                "range": (0, 21),
                "context_before": "",
                "context_after": "",
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

        def activate(self, target):
            return True

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.example", "pid": 5, "name": "App"}
    service._selection_has_range = True
    service._seen_text = "the text that was just edited"
    service._previewed_text = "the text that was just edited"
    service._applying = True

    service._end_apply()

    assert service._seen_text == "other text being read"
    assert service._previewed_text == "other text being read"
    assert service._candidate_text == ""
    assert service._applying is False
    service.stop()


def test_selection_in_another_app_during_apply_is_suppressed(monkeypatch):
    """Text selected elsewhere while an apply runs is not auto-previewed."""
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class FakeEditor:
        def frontmost_app(self):
            return {"bundle_id": "com.slack", "pid": 77, "name": "Slack"}

        def get_selection_ax_only(self, target):
            return "a message the user is reading"

        def activate(self, target):
            return True

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.apple.TextEdit", "pid": 5}
    service._applying = True

    service._end_apply()

    assert service._selection_is_suppressed(
        {"pid": 77}, "a message the user is reading"
    )
    # A different selection in that app previews normally.
    assert not service._selection_is_suppressed({"pid": 77}, "something else")
    # So does the same text in another app.
    assert not service._selection_is_suppressed(
        {"pid": 78}, "a message the user is reading"
    )
    service.stop()


def test_panel_survives_app_switch_while_suggestions_wait(monkeypatch):
    """Switching apps must not destroy suggestions the user can still apply."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class FakeEditor:
        def running_apps(self):
            return [{"pid": 5, "bundle_id": "com.apple.TextEdit"}]

        def selection_details(self, target):
            return {
                "text": "teh cat",
                "range": (0, 6),
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.apple.TextEdit", "pid": 5}
    service._selection_text = "teh cat"
    service._seen_text = "teh cat"
    service._selection_has_range = True
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]

    keep = service._keep_panel_for_detached_target(
        {"bundle_id": "com.other", "pid": 99}
    )
    assert keep is True

    # If the captured selection no longer matches, the panel is dismissed.
    service._selection_text = "teh cat"
    service._editor.selection_details = lambda target: {
        "text": "different text now",
        "range": (0, 18),
        "found": True,
        "editable": True,
        "role": "AXTextArea",
    }
    service._detach_checked_at = 0.0
    assert (
        service._keep_panel_for_detached_target(
            {"bundle_id": "com.other", "pid": 99}
        )
        is False
    )
    service.stop()


def test_panel_is_dismissed_when_captured_app_closed(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class FakeEditor:
        def running_apps(self):
            return [{"pid": 42, "bundle_id": "com.other"}]

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.apple.TextEdit", "pid": 5}
    service._selection_text = "teh cat"
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    assert (
        service._keep_panel_for_detached_target(
            {"bundle_id": "com.other", "pid": 42}
        )
        is False
    )
    service.stop()
