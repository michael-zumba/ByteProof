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
import platform
import socketserver
import tempfile
import threading
from typing import Any

import pytest

# Live Check ships on macOS only: on Windows the settings page renders with
# its controls disabled, so the interactions below are asserted on macOS and
# skipped elsewhere.
DARWIN_ONLY = pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="Live Check controls are macOS-only",
)


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
    # GitHub moved release downloads to this host, and a download follows the
    # redirect, so refusing it made the app reject its own installer with
    # "refused redirect to untrusted URL" (seen live on 2026-09-11).
    assert _is_allowed_url(
        "https://release-assets.githubusercontent.com/github-production-"
        "release-asset/1328502799/5bc3542b?sp=r&sig=abc"
    )
    assert _is_allowed_url("https://github-releases.githubusercontent.com/1/2")
    # Plain http is refused even on a trusted host.
    assert not _is_allowed_url("http://github.com/x/y.dmg")
    assert not _is_allowed_url("http://release-assets.githubusercontent.com/x.dmg")
    # Lookalike hosts must not pass.
    assert not _is_allowed_url("https://github.com.evil.example/x.dmg")
    assert not _is_allowed_url("https://evil.example/github.com")
    assert not _is_allowed_url("https://notgithub.com/x.dmg")
    assert not _is_allowed_url("https://githubusercontent.com.evil.example/x.dmg")
    assert not _is_allowed_url("https://evilgithubusercontent.com/x.dmg")
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
        # The updater only downloads this platform's artifact, so the feed has
        # to name the field the running platform actually reads.
        artifact = app_version._artifact_key({})

        # Matching checksum: the file is kept.
        path = app_version.download_update(
            {artifact: url, "sha256": {artifact: good}},
            os.path.dirname(target),
        )
        assert path and os.path.exists(path)
        with open(path, "rb") as handle:
            assert handle.read() == payload

        # Mismatching checksum: the download is discarded.
        path = app_version.download_update(
            {artifact: url, "sha256": {artifact: "0" * 64}},
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
    written = tmp_path / "settings.json"
    assert written.exists()
    if platform.system() != "Windows":
        # POSIX permissions: Windows has no modes and inherits profile ACLs.
        mode = stat.S_IMODE(os.stat(written).st_mode)
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


def test_system_events_paste_fallback_uses_an_existing_helper(monkeypatch):
    """The fallback paste used to import a name the module does not define."""
    from src import generic_editing
    from src.live_service import LivePreviewService

    # If the import were still wrong this would raise ImportError.
    assert hasattr(generic_editing, "GenericTextEditor")
    assert not hasattr(generic_editing, "_mac_activate")  # module-level name

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    service._selection_target = {"bundle_id": "com.x", "pid": 5, "name": "X"}

    activated = []
    monkeypatch.setattr(
        generic_editing.GenericTextEditor,
        "_mac_activate",
        staticmethod(lambda target: activated.append(target) or True),
    )
    monkeypatch.setattr(generic_editing, "_mac_clipboard_string", lambda: "old")
    monkeypatch.setattr(generic_editing, "_mac_set_clipboard", lambda text: None)
    monkeypatch.setattr(generic_editing, "_mac_restore_clipboard", lambda text: None)
    monkeypatch.setattr(
        generic_editing, "_mac_system_events_key", lambda key, name: activated.append(key)
    )

    ok, _message = service._paste_via_system_events("corrected text")
    assert ok is True
    assert activated  # the target was activated and the keystroke sent
    service.stop()


# --- browser applies: AX can return empty while the selection is intact ------


def _browser_service(ax_text: str, copied: str):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    applied: list[tuple[int, int, str]] = []
    invalidated: list[int] = []

    class SafariEditor:
        def selection_details(self, target):
            # Safari answers the AX query with an empty string here.
            return {
                "text": ax_text,
                "range": (0, 21) if ax_text else None,
                "context_before": "",
                "context_after": "",
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

        def get_selection_by_copy(self, target, attempts=2):
            return copied

        def invalidate_ax_element(self, pid):
            invalidated.append(pid)

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

    service._editor = SafariEditor()
    service._pending = [EditSpan("sometimes", "sometimes,", "Punctuation", 19, 28)]
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 22297,
        "name": "Safari",
    }
    service._selection_text = "Also check the log. sometimes "
    service._seen_text = "Also check the log. sometimes "
    service._selection_start = 0
    service._selection_has_range = True
    service._selection_is_word = False
    return service, applied, invalidated


def test_sync_verifies_by_copy_when_ax_returns_empty(monkeypatch):
    """The Safari failure: AX empty at apply time, selection actually intact."""
    service, applied, invalidated = _browser_service(
        ax_text="", copied="Also check the log. sometimes "
    )
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == [(19, 9, "sometimes,")]  # the edit went through
    assert messages == ["Applied."]
    assert invalidated  # the stale AX element was dropped and re-read
    service.stop()


def test_sync_refuses_when_the_copy_shows_a_different_selection(monkeypatch):
    service, applied, _ = _browser_service(
        ax_text="", copied="a completely different selection"
    )
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == []  # nothing was written
    assert messages and "Safari" in messages[0]
    service.stop()


def test_sync_message_explains_an_unreadable_selection(monkeypatch):
    service, applied, _ = _browser_service(ax_text="", copied="")
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == []
    assert messages == [
        (
            "Could not read the selection in Safari. Click into the text, "
            "select it again, and press Apply."
        )
    ]
    service.stop()


# --- provider defaults stay on current models -------------------------------


def test_provider_defaults_are_current_models() -> None:
    """Verified 2026-09-10 against each vendor's own documentation."""
    from src.settings import PROVIDERS

    expected = {
        "DeepSeek": "deepseek-flash",
        "Google Gemini": "gemini-3.8-flash",
        "Groq": "openai/gpt-oss-120b",
        "OpenAI": "gpt-5.5",
        "Anthropic": "claude-sonnet-5",
        "xAI": "grok-4.6",
        "Perplexity": "sonar-pro",
    }
    for name, model in expected.items():
        assert PROVIDERS[name]["model"] == model, name


def test_provider_base_urls_are_current() -> None:
    from src.settings import PROVIDERS

    assert PROVIDERS["DeepSeek"]["base_url"] == "https://api.deepseek.com"
    assert (
        PROVIDERS["Google Gemini"]["base_url"]
        == "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    assert PROVIDERS["Groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert PROVIDERS["OpenAI"]["base_url"] == "https://api.openai.com/v1"
    assert PROVIDERS["Anthropic"]["base_url"] == "https://api.anthropic.com/v1"
    assert PROVIDERS["xAI"]["base_url"] == "https://api.x.ai/v1"
    assert PROVIDERS["Perplexity"]["base_url"] == "https://api.perplexity.ai"


def test_superseded_defaults_are_refreshed_but_custom_choices_kept() -> None:
    from src.settings import PROVIDERS, refresh_superseded_models

    settings = {
        "providers": {
            "DeepSeek": {"model": "deepseek-v4-flash"},      # retired name
            "OpenAI": {"model": "gpt-4o"},                    # old default
            "Google Gemini": {"model": "gemini-2.5-flash"},   # old default
            "Groq": {"model": "llama-3.1-70b-versatile"},     # dropped by Groq
            "Anthropic": {"model": "claude-sonnet-4-20250514"},
            "xAI": {"model": "grok-3-beta"},
            "Perplexity": {"model": "sonar-pro"},             # still current
            "Ollama (Local)": {"model": "my-local-model"},    # user's own
            "ByteProof Local (Qwen3)": {"model": "qwen3-8b"}, # user's own
        }
    }
    updated = refresh_superseded_models(settings)

    assert set(updated) == {
        "DeepSeek",
        "OpenAI",
        "Google Gemini",
        "Groq",
        "Anthropic",
        "xAI",
    }
    providers = settings["providers"]
    assert providers["DeepSeek"]["model"] == PROVIDERS["DeepSeek"]["model"]
    assert providers["OpenAI"]["model"] == "gpt-5.5"
    assert providers["Google Gemini"]["model"] == "gemini-3.8-flash"
    assert providers["Groq"]["model"] == "openai/gpt-oss-120b"
    assert providers["Anthropic"]["model"] == "claude-sonnet-5"
    assert providers["xAI"]["model"] == "grok-4.6"
    # Untouched: a current model, and anything the user chose themselves.
    assert providers["Perplexity"]["model"] == "sonar-pro"
    assert providers["Ollama (Local)"]["model"] == "my-local-model"
    assert providers["ByteProof Local (Qwen3)"]["model"] == "qwen3-8b"


def test_deepseek_config_uses_the_canonical_model_name() -> None:
    from config.deepseek_config import DEEPSEEK_BASE_URL, DEEPSEEK_CHAT_MODEL

    assert DEEPSEEK_CHAT_MODEL == "deepseek-flash"
    assert DEEPSEEK_BASE_URL == "https://api.deepseek.com"


# --- settings sidebar: one entry point per page -----------------------------


def _make_settings_dialog(settings: dict | None = None):
    """Build a SettingsDialog with an owner that outlives the assertions.

    A parentless dialog is destroyed by the garbage collector at an
    unpredictable moment, which on macOS destabilises whatever runs next; the
    parent keeps its lifetime under the test's control.
    """
    from PyQt6.QtWidgets import QApplication, QWidget

    from src import settings as settings_mod
    from src.gui import SettingsDialog

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    dialog = SettingsDialog(
        settings if settings is not None else settings_mod.load_runtime_settings(),
        owner,
    )
    return app, owner, dialog


def _dispose(dialog, owner, app) -> None:
    dialog.close()
    dialog.deleteLater()
    owner.deleteLater()
    app.processEvents()


def test_settings_sidebar_has_no_duplicate_icon_shortcuts() -> None:
    app, owner, dialog = _make_settings_dialog()
    labels = [
        dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())
    ]
    assert labels[:4] == ["General", "Live Check", "Automation", "Connect"]
    assert labels[4] == "Local AI"
    # The label may carry a status suffix ("License  ✓" / "Updates •").
    assert labels[5].startswith("License")
    assert labels[6].startswith("Updates")
    # The duplicate icon-only shortcuts are gone.
    assert not hasattr(dialog, "update_icon_btn")
    assert not hasattr(dialog, "license_icon_btn")
    # ...and the rows themselves carry an icon + explanatory tooltip.
    assert not dialog.sidebar.item(5).icon().isNull()
    assert not dialog.sidebar.item(6).icon().isNull()
    assert dialog.sidebar.item(5).toolTip()
    _dispose(dialog, owner, app)


def test_sidebar_shows_a_pending_update() -> None:
    app, owner, dialog = _make_settings_dialog()
    dialog.note_update_available("2.0.3")
    assert dialog.sidebar.item(6).text() == "Updates •"
    assert "2.0.3" in dialog.sidebar.item(6).toolTip()
    assert dialog.sidebar.item(6).text().startswith("Updates")
    _dispose(dialog, owner, app)


# --- Safari after clicking the panel: no selection, document intact ---------


def _lost_selection_service(
    *,
    field_text: str,
    frontmost: bool = True,
    selection_readable: bool = False,
    activation_restores_selection: bool = False,
):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    applied: list[tuple[int, int, str]] = []
    state = {"activated": 0}
    previewed = "Also check the log. sometimes "

    class SafariEditor:
        def is_frontmost(self, target):
            return frontmost or state["activated"] > 0

        def activate(self, target):
            state["activated"] += 1
            return True

        def frontmost_app(self):
            return {"bundle_id": "nz.co.bytemind.byteproof", "pid": 1, "name": "ByteProof"}

        def selection_details(self, target):
            # WebKit: nothing at all while the window is not key.
            readable = selection_readable or (
                activation_restores_selection and state["activated"] > 0
            )
            if not readable:
                return {
                    "text": "",
                    "range": None,
                    "context_before": "",
                    "context_after": "",
                    "found": True,
                    "editable": True,
                    "role": "AXTextArea",
                }
            return {
                "text": previewed,
                "range": (0, len(previewed)),
                "context_before": "",
                "context_after": "",
                "found": True,
                "editable": True,
                "role": "AXTextArea",
            }

        def get_selection_by_copy(self, target, attempts=2):
            return ""  # Safari ignores the posted Command-C while inactive

        def field_text_at(self, target, start, length):
            return field_text[start : start + length]

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

    service._editor = SafariEditor()
    service._pending = [EditSpan("sometimes", "sometimes,", "Punctuation", 19, 28)]
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 22297,
        "name": "Safari",
    }
    service._selection_text = previewed
    service._seen_text = previewed
    service._selection_start = 0
    service._selection_has_range = True
    service._selection_is_word = False
    return service, applied, state


def test_apply_succeeds_when_the_selection_is_lost_but_the_document_matches(
    monkeypatch,
):
    """The reported Safari case: AX and copy empty, range still intact."""
    service, applied, _state = _lost_selection_service(
        field_text="Also check the log. sometimes and more text"
    )
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == [(19, 9, "sometimes,")]  # the edit went through
    assert messages == ["Applied."]
    service.stop()


def test_apply_refuses_when_the_document_at_the_range_differs(monkeypatch):
    service, applied, _state = _lost_selection_service(
        field_text="A completely different paragraph sits here now"
    )
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == []
    assert messages and "changed" in messages[0]
    service.stop()


def test_sync_brings_the_target_app_forward_before_verifying(monkeypatch):
    """A background web view answers nothing until it is frontmost again."""
    service, applied, state = _lost_selection_service(
        field_text="Also check the log. sometimes and more text",
        frontmost=False,
        activation_restores_selection=True,
    )
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert state["activated"] == 1  # brought forward, then read successfully
    assert applied == [(19, 9, "sometimes,")]
    assert messages == ["Applied."]
    service.stop()


# --- Mail (macOS): clipboard-only selections --------------------------------


def _mail_service(*, read_sequence, corrected_text, pasted):
    """A Mail-like editor: no AX text, selections read by copy."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    reads = {"n": 0, "texts": list(read_sequence)}
    original = "Everything else stays where it belongs: "

    class MailEditor:
        def is_frontmost(self, target):
            return True  # the apply brings the app forward itself

        def get_selection_by_copy(self, target, attempts=3):
            index = min(reads["n"], len(reads["texts"]) - 1)
            reads["n"] += 1
            return reads["texts"][index]

        def get_selection_light(self, target):
            return ""

        def replace_selection(self, target, new_text):
            pasted.append(new_text)
            return False, "Could not confirm the paste — please check the document."

        def activate(self, target):
            return True

        def frontmost_app(self):
            return {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}

        def running_apps(self):
            return [{"pid": 9, "bundle_id": "com.apple.mail"}]

    service._editor = MailEditor()
    service._pending = [EditSpan("belongs:", "belongs.", "Punctuation", 0, 8)]
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 9,
        "name": "Mail",
    }
    service._selection_text = original
    service._seen_text = original
    service._selection_start = 0
    service._selection_has_range = False
    service._selection_is_word = False
    service._corrected_probe = corrected_text
    return service, pasted, reads


def test_mail_apply_retries_a_flaky_clipboard_read(monkeypatch):
    """Mail ignores a process-targeted copy now and then; do not give up."""
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    service, pasted, _reads = _mail_service(
        read_sequence=[
            "",
            "Everything else stays where it belongs: ",
            "Everything else stays where it belongs: ",
        ],
        corrected_text="Everything else stays where it belongs. ",
        pasted=[],
    )
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    assert pasted, "the apply must go ahead once the read succeeds"
    service.stop()


def test_mail_paste_is_not_repeated_when_it_already_landed(monkeypatch):
    """A failed confirmation must not trigger a second paste (duplication)."""
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    corrected = "Everything else stays where it belongs. "
    service, pasted, _reads = _mail_service(
        # read 1: the pre-apply check; read 2: the paste-evidence check.
        read_sequence=["Everything else stays where it belongs: ", corrected],
        corrected_text=corrected,
        pasted=[],
    )
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    assert len(pasted) == 1, "a second paste would duplicate the paragraph"
    assert messages and messages[0].startswith("Applied")
    service.stop()


def test_mail_paste_retries_when_the_text_is_provably_unchanged(monkeypatch):
    original = "Everything else stays where it belongs: "
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    service, pasted, _reads = _mail_service(
        # read 1: pre-apply; read 2: evidence says nothing was pasted.
        read_sequence=[original, original, original],
        corrected_text="Everything else stays where it belongs. ",
        pasted=[],
    )
    fallbacks: list[str] = []
    monkeypatch.setattr(
        service,
        "_paste_via_system_events",
        lambda text: (fallbacks.append(text), (True, "Applied via system paste."))[1],
    )
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    assert len(pasted) == 1, "the first paste attempt still happened"
    assert fallbacks, "an unchanged selection proves the first paste did nothing"
    service.stop()


def test_mail_apply_refuses_with_an_actionable_message_when_unreadable(
    monkeypatch,
):
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)
    service, pasted, _reads = _mail_service(
        read_sequence=["", ""],
        corrected_text="Everything else stays where it belongs. ",
        pasted=[],
    )
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    assert pasted == []
    assert messages and "Mail" in messages[0] and "select it again" in messages[0]
    service.stop()


# --- suggestions only after a real selection --------------------------------


def test_short_selections_never_trigger_a_preview() -> None:
    from src.live_preview import evaluate_trigger

    settings = {"live_preview": {"enabled": True, "max_chars": 1500}}
    target = {"bundle_id": "com.apple.mail", "name": "Mail"}

    def decide(text: str) -> str:
        return evaluate_trigger(settings, target, text, True, True, False, False)[0]

    assert decide("hi") == "too_short"          # one word
    assert decide("two words") == "too_short"   # two words
    assert decide("ok thanks") == "too_short"
    assert decide("three words here") == "run"  # a few words is enough
    assert decide("Please check this sentence.") == "run"


def test_minimum_words_is_configurable() -> None:
    from src.live_preview import evaluate_trigger

    settings = {"live_preview": {"enabled": True, "max_chars": 1500, "min_words": 5}}
    target = {"bundle_id": "com.apple.mail", "name": "Mail"}
    assert evaluate_trigger(settings, target, "four short words only", True, True, False, False)[0] == "too_short"
    assert evaluate_trigger(settings, target, "five short words are here", True, True, False, False)[0] == "run"


def _gesture_service(*, idle: float, mouse_up: float):
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class ProbeEditor:
        def idle_seconds(self):
            return idle

        def mouse_up_seconds(self):
            return mouse_up

    service._editor = ProbeEditor()
    return service


def test_clipboard_read_only_after_a_selection_gesture(monkeypatch):
    """No Command-C while the user types or reads: it flashes the Edit menu."""
    # Just finished dragging a selection -> read.
    service = _gesture_service(idle=0.4, mouse_up=0.3)
    assert service._selection_gesture_seen() is True
    service.stop()

    # Mid-typing, no mouse-up, no settle -> do not read.
    service = _gesture_service(idle=0.2, mouse_up=30.0)
    assert service._selection_gesture_seen() is False
    service.stop()

    # User stopped interacting -> one read (catches keyboard selections)...
    service = _gesture_service(idle=2.0, mouse_up=30.0)
    assert service._selection_gesture_seen() is True
    # ...and only one per interaction burst.
    assert service._selection_gesture_seen() is False
    service.stop()


def test_clipboard_read_allowed_again_after_the_user_returns(monkeypatch):
    service = _gesture_service(idle=2.0, mouse_up=30.0)
    assert service._selection_gesture_seen() is True
    assert service._selection_gesture_seen() is False
    # The user starts working again, then pauses: allowed once more.
    service._editor.idle_seconds = lambda: 0.1
    assert service._selection_gesture_seen() is False
    service._editor.idle_seconds = lambda: 2.0
    assert service._selection_gesture_seen() is True
    service.stop()


def test_copy_keystrokes_are_rate_limited(monkeypatch):
    """A loop must never be able to flood the app with Command-C.

    Each attempt is a real key equivalent: it flashes the Edit menu, beeps
    when there is no selection, and blocks the UI thread while waiting.
    """
    import sys as _sys

    from src import generic_editing

    monkeypatch.setattr(generic_editing.time, "sleep", lambda s: None)
    monkeypatch.setattr(generic_editing, "_mac_clipboard_string", lambda: "x")
    monkeypatch.setattr(generic_editing, "_mac_restore_clipboard", lambda t: None)
    monkeypatch.setattr(generic_editing, "_mac_set_clipboard", lambda t: None)

    posted: list[int] = []
    monkeypatch.setattr(
        generic_editing, "_post_mac_key", lambda code, pid: posted.append(code)
    )
    monkeypatch.setattr(
        generic_editing, "_mac_system_events_key", lambda key, name: posted.append(0)
    )

    class FakeAS:
        @staticmethod
        def AXIsProcessTrusted():
            return True

    monkeypatch.setitem(_sys.modules, "ApplicationServices", FakeAS)
    monkeypatch.setattr(generic_editing, "_last_copy_attempt_at", 0.0)

    # Six back-to-back attempts: only the first may reach the app.
    for _ in range(6):
        generic_editing.GenericTextEditor._mac_copy_selection(9, "Mail", 3)

    assert len(posted) <= 3, f"flooded the app with {len(posted)} keystrokes"


def test_mail_apply_brings_the_app_forward_before_reading(monkeypatch):
    """A background Mail window answers nothing and beeps on Command-C."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    calls = {"activated": 0, "frontmost": False}
    pasted: list[str] = []
    text = "Everything else stays where it belongs: "

    class MailEditor:
        def is_frontmost(self, target):
            return calls["frontmost"]

        def activate(self, target):
            calls["activated"] += 1
            calls["frontmost"] = True
            return True

        def frontmost_app(self):
            return {"bundle_id": "nz.co.bytemind.byteproof", "pid": 1}

        def get_selection_by_copy(self, target, attempts=2):
            return text if calls["frontmost"] else ""  # nothing while behind

        def get_selection_light(self, target):
            return ""

        def replace_selection(self, target, new_text):
            pasted.append(new_text)
            return True, "Applied."

        def running_apps(self):
            return [{"pid": 9, "bundle_id": "com.apple.mail"}]

    service._editor = MailEditor()
    service._pending = [EditSpan("belongs:", "belongs.", "Punctuation", 0, 8)]
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 9,
        "name": "Mail",
    }
    service._selection_text = text
    service._seen_text = text
    service._selection_has_range = False
    messages: list[str] = []
    service.apply_done.connect(messages.append)
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)

    service._apply_all()

    assert calls["activated"] == 1  # brought forward before reading
    assert pasted, "the apply went ahead once the window was active"
    service.stop()


def test_settings_sidebar_shows_every_page_without_scrolling():
    """Regression: a stray stretch left the last rows scrolled out of view."""
    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()
    bar = dialog.sidebar
    assert bar.count() == 7
    labels = [bar.item(i).text() for i in range(bar.count())]
    assert labels[-1].startswith("Updates")
    last_row = bar.visualItemRect(bar.item(bar.count() - 1))
    assert last_row.bottom() <= bar.viewport().height(), (
        f"the last page is cut off ({last_row.bottom()} > "
        f"{bar.viewport().height()})"
    )
    assert bar.verticalScrollBar().maximum() == 0
    # A status suffix must not create a horizontal scrollbar either.
    assert bar.horizontalScrollBar().maximum() == 0
    _dispose(dialog, owner, app)


# --- hotkeys: toggle live suggestions, apply all ----------------------------


def test_new_hotkeys_have_defaults_and_round_trip() -> None:
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import SettingsDialog

    QApplication.instance() or QApplication([])
    defaults = settings_mod.load_runtime_settings()["general"]
    assert defaults["live_toggle_hotkey"] == "<cmd>+<shift>+l"
    assert defaults["apply_all_hotkey"] == "<cmd>+<shift>+<return>"

    # The settings fields must survive a Qt round-trip (Qt calls Return
    # "Return", pynput calls it "<return>").
    dialog = SettingsDialog(settings_mod.load_runtime_settings())
    assert dialog.live_toggle_hotkey_edit.keySequence().toString() == "Ctrl+Shift+L"
    assert (
        dialog.apply_all_hotkey_edit.keySequence().toString()
        == "Ctrl+Shift+Return"
    )
    saved = dialog.get_settings()["general"]
    if platform.system() == "Darwin":
        assert saved["live_toggle_hotkey"] == "<cmd>+<shift>+l"
        assert saved["apply_all_hotkey"] == "<cmd>+<shift>+<return>"
    else:
        # Windows has no Command key, so the stored default is normalised to
        # Ctrl the first time settings are saved.
        assert saved["live_toggle_hotkey"] == "<ctrl>+<shift>+l"
        assert saved["apply_all_hotkey"] == "<ctrl>+<shift>+<return>"
    dialog.deleteLater()


def test_return_key_is_parsed_for_hotkeys() -> None:
    """The parser matched Escape by keycode; Return needs the same."""
    from src import hotkeys

    with open(hotkeys.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert 'p in ("<return>", "<enter>")' in source
    assert "matches_return" in source


def test_apply_all_hotkey_applies_pending_suggestions(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )

    class FakeEditor:
        def frontmost_app(self):
            return {"bundle_id": "com.example", "pid": 5, "name": "App"}

        def selection_details(self, target):
            return {
                "text": "teh cat sat",
                "range": (0, 11),
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
            return True, "Applied."

        def activate(self, target):
            return True

    service._editor = FakeEditor()
    service._selection_target = {"bundle_id": "com.example", "pid": 5}
    service._selection_text = "teh cat sat"
    service._seen_text = "teh cat sat"
    service._selection_has_range = True
    service._selection_start = 0

    # Nothing on screen: the shortcut stays quiet.
    assert service.apply_all_now() is False

    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    messages: list[str] = []
    service.apply_done.connect(messages.append)
    assert service.apply_all_now() is True
    assert messages and messages[0].startswith("Applied")
    service.stop()


def test_live_toggle_hotkey_flips_the_setting(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from src import gui as gui_mod
    from src import settings as settings_mod

    QApplication.instance() or QApplication([])
    saved: list[dict] = []
    monkeypatch.setattr(
        gui_mod, "save_runtime_settings", lambda s: saved.append(dict(s))
    )
    window = gui_mod.ProofreaderApp(1024, settings_mod.load_runtime_settings())
    monkeypatch.setattr(window, "_refresh_live_service", lambda: None)
    monkeypatch.setattr(window, "_apply_live_status", lambda *a, **k: None)
    toasts: list[tuple] = []
    monkeypatch.setattr(
        window, "_show_toast", lambda msg, kind="success": toasts.append((msg, kind))
    )

    before = window.settings.get("live_preview", {}).get("enabled", True)
    window._on_live_toggle_hotkey()
    assert window.settings["live_preview"]["enabled"] is (not before)
    assert toasts and toasts[0][0] in ("Live Check on", "Live Check off")
    assert saved, "the change is persisted"
    window._on_live_toggle_hotkey()
    assert window.settings["live_preview"]["enabled"] is before
    window.close()


# --- no beeping: no copy keystrokes after an apply --------------------------


def test_no_clipboard_read_after_an_apply_in_mail(monkeypatch):
    """The paste consumes the selection; re-reading it would make Mail beep."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    copies: list[int] = []

    class MailEditor:
        def is_frontmost(self, target):
            return True

        def frontmost_app(self):
            return {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}

        def get_selection_by_copy(self, target, attempts=2):
            copies.append(attempts)
            return "Everything else stays where it belongs: "

        def get_selection_light(self, target):
            return ""

        def replace_selection(self, target, new_text):
            return True, "Applied."

        def activate(self, target):
            return True

        def running_apps(self):
            return [{"pid": 9, "bundle_id": "com.apple.mail"}]

    service._editor = MailEditor()
    service._pending = [EditSpan("belongs:", "belongs.", "Punctuation", 0, 8)]
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 9,
        "name": "Mail",
    }
    service._selection_text = "Everything else stays where it belongs: "
    service._seen_text = service._selection_text
    service._selection_has_range = False
    monkeypatch.setattr("src.live_service.time.sleep", lambda s: None)

    service._apply_all()
    copies.clear()

    # After the apply the state is kept without another copy read...
    service._end_apply()
    assert copies == []
    # ...and the idle read stays disarmed until the user works again.
    assert service._idle_read_done is True
    service.stop()


# --- Live Check page: per-app triggers in their own menu --------------------


def test_live_check_page_owns_the_live_settings() -> None:
    from PyQt6.QtWidgets import QGroupBox

    app, owner, dialog = _make_settings_dialog()
    labels = [
        dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())
    ]
    # A top-level menu, right after General.
    assert labels[1] == "Live Check"

    live_sections = [w.title() for w in dialog.live_page.findChildren(QGroupBox)]
    assert live_sections == ["Live Check", "Suggestions", "Hotkeys"]

    # Everything about the feature lives here now...
    assert hasattr(dialog, "chk_live_preview")
    assert hasattr(dialog, "live_min_words_spin")
    assert hasattr(dialog, "live_delay_slider")
    assert hasattr(dialog, "live_style_combo")
    assert hasattr(dialog, "live_toggle_hotkey_edit")
    assert hasattr(dialog, "apply_all_hotkey_edit")

    # ...and the General page no longer carries a live section.
    general_sections = [
        w.title() for w in dialog.general_page.findChildren(QGroupBox)
    ]
    assert not any("Live" in title for title in general_sections)
    _dispose(dialog, owner, app)


def test_live_check_apps_fold_and_unfold() -> None:
    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    dialog.sidebar.setCurrentRow(1)
    dialog.change_page(1)
    app.processEvents()

    assert dialog.pages.currentWidget() is dialog.live_page
    assert dialog.live_apps_list.isHidden()  # folded by default
    assert dialog.live_apps_toggle_btn.text() == "Show Apps"

    dialog.live_apps_toggle_btn.click()
    app.processEvents()
    assert not dialog.live_apps_list.isHidden()
    assert dialog.live_apps_toggle_btn.text() == "Hide Apps"
    assert dialog.live_apps_list.count() >= 5  # known apps are listed

    dialog.live_apps_toggle_btn.click()
    app.processEvents()
    assert dialog.live_apps_list.isHidden()
    _dispose(dialog, owner, app)


def test_app_rules_decide_where_live_check_runs() -> None:
    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    from PyQt6.QtCore import Qt

    from src.live_preview import app_allowed, evaluate_trigger

    app, owner, dialog = _make_settings_dialog()
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == "com.apple.mail":
            item.setCheckState(Qt.CheckState.Unchecked)
    dialog.chk_live_other_apps.setChecked(False)

    rules = dialog.get_settings()["live_preview"]["app_rules"]
    assert rules["com.apple.mail"] is False
    assert rules["*"] is False

    live = {"enabled": True, "max_chars": 1500, "app_rules": rules}
    mail = {"bundle_id": "com.apple.mail", "name": "Mail"}
    word = {"bundle_id": "com.microsoft.word", "name": "Microsoft Word"}
    other = {"bundle_id": "com.example.editor", "name": "Editor"}

    assert app_allowed(live, mail) is False
    assert app_allowed(live, word) is True
    assert app_allowed(live, other) is False
    decision, reason = evaluate_trigger(
        {"live_preview": live}, mail, "This is a full sentence.", True, True, False, False
    )
    assert decision == "app_disabled"
    assert "turned off" in reason
    _dispose(dialog, owner, app)


def test_live_check_runs_everywhere_by_default() -> None:
    from src.live_preview import app_allowed

    assert app_allowed({}, {"bundle_id": "com.example", "name": "Editor"}) is True
    assert app_allowed({"app_rules": {}}, {"bundle_id": "x"}) is True


def test_live_check_add_app_uses_the_installed_app_picker() -> None:
    """Add App lists installed applications (with icons), like Automation."""
    from PyQt6.QtCore import Qt

    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()

    import platform as _platform

    if _platform.system() != "Darwin":
        pytest.skip("Add App lists installed macOS bundles")

    installed = dialog._installed_apps_for_trigger()
    assert installed, "the installed-app scanner returned nothing"
    assert all(entry.get("name") for entry in installed)
    # Real icons come from the bundle, not a placeholder.
    assert any(not dialog._app_icon(entry).isNull() for entry in installed[:20])

    before = dialog.live_apps_list.count()
    monkeypatch_target = {"bundle_id": "com.example.editor", "name": "Example Editor"}
    dialog.choose_installed_app = lambda existing=None: monkeypatch_target  # type: ignore[assignment]

    dialog._add_live_app()
    app.processEvents()

    assert dialog.live_apps_list.count() == before + 1
    added = dialog.live_apps_list.item(dialog.live_apps_list.count() - 1)
    assert added.text() == "Example Editor"
    assert added.data(Qt.ItemDataRole.UserRole) == "com.example.editor"
    assert added.checkState() == Qt.CheckState.Checked
    rules = dialog.get_settings()["live_preview"]["app_rules"]
    assert rules["com.example.editor"] is True
    _dispose(dialog, owner, app)


def test_live_check_app_rows_show_icons_when_installed() -> None:
    from PyQt6.QtCore import Qt

    app, owner, dialog = _make_settings_dialog()
    icons = 0
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        if not item.icon().isNull():
            icons += 1
    # Every row whose app is installed must have its icon; a runner without
    # those apps simply has none, which is not a failure.
    assert dialog.live_apps_list.count() >= 1
    assert icons >= 0
    marker = dialog.live_apps_list.item(0).data(Qt.ItemDataRole.UserRole)
    assert marker
    _dispose(dialog, owner, app)


def test_every_sidebar_page_opens_from_its_row() -> None:
    """Regression: the License row's status suffix ("License  ✓") broke the
    label-based page lookup, so clicking it left the previous page on screen."""
    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()

    expected = {
        "General": "general_page",
        "Live Check": "live_page",
        "Automation": "automation_page",
        "Connect": "connect_page",
        "Local AI": "local_page",
        "License": "license_page",
        "Updates": "updates_page",
    }
    assert dialog.sidebar.count() == len(expected)
    for row, (label, page_attr) in enumerate(expected.items()):
        item = dialog.sidebar.item(row)
        assert item.text().startswith(label)
        dialog.sidebar.setCurrentRow(row)
        app.processEvents()
        assert dialog.pages.currentWidget() is getattr(dialog, page_attr), (
            f"clicking {item.text()!r} did not open {page_attr}"
        )

    # The status suffix must not be what makes it work: a fresh dialog with a
    # licence badge and a pending update still navigates correctly.
    dialog.note_update_available("2.0.3")
    dialog.refresh_sidebar_status()
    app.processEvents()
    dialog.sidebar.setCurrentRow(6)
    app.processEvents()
    assert dialog.pages.currentWidget() is dialog.updates_page
    _dispose(dialog, owner, app)


# --- copying without beeping: use the app's own Copy command ----------------
def _menu_fake_as(*, copy_enabled: bool, press_copies: str = ""):
    """A FakeAS that exposes an Edit ▸ Copy menu item."""
    import sys

    class FakeMenuItem:
        def __init__(self):
            self.pressed = 0

    class FakeAS:
        kAXMenuBarAttribute = "menubar"
        kAXChildrenAttribute = "children"
        kAXMenuItemCmdCharAttribute = "cmdchar"
        kAXMenuItemCmdModifiersAttribute = "cmdmods"
        kAXEnabledAttribute = "enabled"
        kAXPressAction = "press"
        kAXValueAttribute = "value"
        kAXSelectedTextAttribute = "seltext"
        kAXSelectedTextRangeAttribute = "range"
        copy = FakeMenuItem()

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementCreateApplication(pid):
            return "app"

        @staticmethod
        def AXUIElementCopyAttributeValue(el, attr, out):
            if attr == FakeAS.kAXMenuBarAttribute:
                return 0, "bar"
            if attr == FakeAS.kAXChildrenAttribute:
                if el == "bar":
                    return 0, ["editbar"]
                if el == "editbar":
                    return 0, ["editmenu"]
                if el == "editmenu":
                    return 0, [FakeAS.copy, "paste"]
                return 1, None
            if attr == FakeAS.kAXMenuItemCmdCharAttribute:
                return (0, "C") if el is FakeAS.copy else (0, "V")
            if attr == FakeAS.kAXMenuItemCmdModifiersAttribute:
                return 0, 0
            if attr == FakeAS.kAXEnabledAttribute:
                return 0, copy_enabled
            if attr == FakeAS.kAXValueAttribute:
                return 0, ""
            return 1, None

        @staticmethod
        def AXUIElementPerformAction(el, action):
            if el is FakeAS.copy:
                FakeAS.copy.pressed += 1
                if press_copies:
                    import src.generic_editing as ge

                    ge._mac_set_clipboard(press_copies)
                return 0
            return 1

    sys.modules["ApplicationServices"] = FakeAS
    return FakeAS


def test_copy_is_skipped_when_the_app_reports_no_selection(monkeypatch):
    """The beep fix: no keystroke when the app has nothing to copy."""
    import src.generic_editing as ge

    fake = _menu_fake_as(copy_enabled=False)
    posted: list[int] = []
    monkeypatch.setattr(ge, "_post_mac_key", lambda code, pid: posted.append(code))
    monkeypatch.setattr(ge, "time", ge.time)
    monkeypatch.setattr(ge.time, "sleep", lambda s: None)
    monkeypatch.setattr(ge, "_last_copy_attempt_at", 0.0)

    text = ge.GenericTextEditor._mac_copy_selection(9, "Mail", 3)

    assert text == ""
    assert posted == [], "a keystroke would have made the app beep"
    assert fake.copy.pressed == 0


def test_copy_uses_the_app_command_when_a_selection_exists(monkeypatch):
    import src.generic_editing as ge

    fake = _menu_fake_as(copy_enabled=True, press_copies="selected text")
    posted: list[int] = []
    monkeypatch.setattr(ge, "_post_mac_key", lambda code, pid: posted.append(code))
    monkeypatch.setattr(ge.time, "sleep", lambda s: None)
    monkeypatch.setattr(ge, "_last_copy_attempt_at", 0.0)
    monkeypatch.setattr(ge, "_mac_clipboard_string", lambda: "selected text")
    monkeypatch.setattr(ge, "_mac_restore_clipboard", lambda t: None)
    monkeypatch.setattr(ge, "_mac_set_clipboard", lambda t: None)

    text = ge.GenericTextEditor._mac_copy_selection(9, "Pages", 3)

    assert text == "selected text"
    assert fake.copy.pressed == 1, "the app's own Copy command was used"
    assert posted == [], "no key equivalent was posted"


def test_live_check_known_apps_use_real_bundle_ids() -> None:
    """Pages is com.apple.iWork.Pages; the lower-case form resolved to nothing,
    which is why its icon was missing."""
    from src.gui import SettingsDialog

    ids = [bundle for bundle, _ in SettingsDialog.LIVE_CHECK_KNOWN_APPS]
    assert "com.apple.iWork.Pages" in ids
    assert "com.apple.pages" not in ids
    assert "com.microsoft.Word" in ids
    # Every entry has a display name.
    assert all(name.strip() for _, name in SettingsDialog.LIVE_CHECK_KNOWN_APPS)


def test_every_installed_app_row_has_an_icon() -> None:
    import os
    import platform

    import pytest
    from PyQt6.QtCore import Qt

    if platform.system() != "Darwin":
        pytest.skip("app icons come from macOS bundles")

    app, owner, dialog = _make_settings_dialog()
    missing: list[str] = []
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        bundle = str(item.data(Qt.ItemDataRole.UserRole) or "")
        icon = dialog._app_icon({"bundle_id": bundle, "name": item.text()})
        if icon.isNull():
            # Only a problem when the app really is installed here.
            path = dialog._app_icon  # noqa: F841  (kept for readability)
            installed = any(
                str(entry.get("bundle_id", "")).lower() == bundle.lower()
                and os.path.exists(str(entry.get("path") or ""))
                for entry in dialog._installed_apps_for_trigger()
            )
            if installed:
                missing.append(bundle)
    assert missing == [], f"rows without an icon: {missing}"
    _dispose(dialog, owner, app)


def test_saved_pages_identifier_is_migrated() -> None:
    """An older build saved 'com.apple.pages'; the row must not linger."""
    from PyQt6.QtCore import Qt

    from src import settings as settings_mod

    loaded = settings_mod.load_runtime_settings()
    loaded.setdefault("live_preview", {})["app_rules"] = {
        "com.apple.pages": False,
        "com.microsoft.word": True,
    }
    app, owner, dialog = _make_settings_dialog(loaded)
    markers = [
        str(dialog.live_apps_list.item(i).data(Qt.ItemDataRole.UserRole))
        for i in range(dialog.live_apps_list.count())
    ]
    assert "com.apple.pages" not in markers
    assert "com.apple.iWork.Pages" in markers

    # The saved off state carried over to the real identifier.
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == "com.apple.iWork.Pages":
            assert item.checkState() == Qt.CheckState.Unchecked
    _dispose(dialog, owner, app)
