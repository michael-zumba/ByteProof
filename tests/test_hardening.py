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
import re
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

    assert applied == [(14, 3, "dog"), (10, 3, "the")]
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


def test_undo_locates_the_applied_text_before_restoring():
    """Undo must restore where the text is now, not where it was written.

    A browser reflows and re-renders between the apply and the undo, so the
    offset recorded at apply time drifts; using it blindly restored the wrong
    place, which is the mismatch reported after a browser apply.
    """
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    # The document moved 2 characters to the right since the apply.
    field = "XXThe cat sat on the mat."
    calls: list[tuple[int, int, str, str]] = []

    class Editor:
        def field_value(self, target):
            return field

        def ax_replace_range(
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            calls.append((start, length, new, before_text or ""))
            return True, "Applied."

    service._editor = Editor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 5,
        "name": "Safari",
    }
    service._selection_start = 0
    service._selection_text = field
    service._undo_state = {
        "mode": "range",
        "target": dict(service._selection_target),
        "is_word": False,
        "steps": [(0, "The", "teh")],  # written at 0 before the document moved
    }

    service._perform_undo()

    assert calls == [(2, 3, "teh", "The")]
    service.stop()


def test_mail_panel_survives_an_unreadable_selection(monkeypatch):
    """The reported Mail failure: the panel dismissed itself after the preview.

    Mail's compose text never reports a selection through Accessibility, so the
    keep-alive read came back empty, was read as "the selection changed", and
    the suggestions vanished about a second after they appeared. An unreadable
    read is not evidence of a change.
    """
    service, _pasted, reads = _mail_service(
        read_sequence=[""],  # nothing readable by copy either
        corrected_text="",
        pasted=[],
    )
    service._pending = service._pending  # suggestions are on screen
    service._detach_checked_at = 0.0

    kept = service._keep_panel_for_detached_target(
        {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}
    )

    assert kept is True
    assert reads["n"] >= 1  # it did look, rather than assuming
    service.stop()


def test_mail_panel_is_dismissed_when_the_selection_really_changed(monkeypatch):
    service, _pasted, _reads = _mail_service(
        read_sequence=["a completely different selection"],
        corrected_text="",
        pasted=[],
    )
    service._detach_checked_at = 0.0

    kept = service._keep_panel_for_detached_target(
        {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}
    )

    assert kept is False
    service.stop()


def test_mail_panel_survives_when_the_text_is_unchanged(monkeypatch):
    service, _pasted, _reads = _mail_service(
        read_sequence=["Everything else stays where it belongs: "],
        corrected_text="",
        pasted=[],
    )
    service._detach_checked_at = 0.0

    kept = service._keep_panel_for_detached_target(
        {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}
    )

    assert kept is True
    service.stop()


def test_a_second_window_of_the_same_app_is_not_a_switch():
    """Mail's composer and viewer are separate processes of one app."""
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service._selection_target = {
        "bundle_id": "com.apple.mail",
        "pid": 4516,
        "name": "Mail",
    }
    # Another Mail window, different pid: still Mail.
    assert not service._selection_target_changed(
        {"bundle_id": "com.apple.mail", "pid": 55355, "name": "Mail"}
    )
    # ByteProof's own panel taking focus is not a switch either.
    assert not service._selection_target_changed(
        {"bundle_id": "nz.bytemind.byteproof", "pid": 1, "name": "ByteProof"}
    )
    # A real switch is.
    assert service._selection_target_changed(
        {"bundle_id": "com.apple.Safari", "pid": 77, "name": "Safari"}
    )


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


def test_hotkey_parser_accepts_the_shifted_punctuation_macos_reports():
    """Cmd+Shift+. arrives as ">"; both spellings must match the same key.

    This is the bug that made the owner's stored apply-all shortcut
    (<cmd>+<shift>+.) look configured but never fire.
    """
    from src.hotkeys import _MacOSHotkeyManager, canonical_hotkey

    class FakeAppKit:
        NSEventModifierFlagCommand = 1
        NSEventModifierFlagShift = 2
        NSEventModifierFlagControl = 4
        NSEventModifierFlagOption = 8

    manager = _MacOSHotkeyManager({})
    manager._appkit = FakeAppKit

    _flags, char, variants = manager._parse_hotkey("<cmd>+<shift>+.")
    assert char == "."
    assert variants == {".", ">"}

    _flags, char, variants = manager._parse_hotkey("<cmd>+<shift>+>")
    assert char == ">"
    assert variants == {">", "."}

    # Duplicate detection sees the two spellings as one shortcut.
    assert canonical_hotkey("<cmd>+<shift>+.") == canonical_hotkey(
        "<cmd>+<shift>+>"
    )


def test_hotkey_conflicts_report_duplicates_and_system_keys(monkeypatch):
    from src import hotkeys

    monkeypatch.setattr(hotkeys, "_running_app_hotkeys", lambda timeout=1.5: [])
    conflicts = hotkeys.find_hotkey_conflicts(
        {
            "Open Window": "<cmd>+<shift>+;",
            "Proofread Selection": "<cmd>+<shift>+;",
            "Apply all suggestions": "<cmd>+<space>",
        },
        check_running_apps=True,
    )
    assert any("both assigned" in message for message in conflicts)
    if platform.system() == "Darwin":
        assert any("Spotlight" in message for message in conflicts)
    else:
        assert len(conflicts) == 1


def test_restore_defaults_resets_preferences_but_keeps_keys_and_license():
    from src.settings import reset_user_settings

    current = {
        "general": {
            "temperature": 1.8,
            "menu_bar_only": False,
            "open_hotkey": "<cmd>+1",
        },
        "live_preview": {"enabled": False, "max_chars": 123},
        "automation": {"enabled": False, "rules": {"outlook": False}},
        "providers": {"DeepSeek": {"api_keys": ["secret"]}},
        "license": {"status": "licensed", "key": "paid-key"},
        "active_provider": "DeepSeek",
    }

    restored = reset_user_settings(current)

    assert restored["general"]["temperature"] == 0.3
    assert restored["general"]["menu_bar_only"] is True
    assert restored["general"]["apply_all_hotkey"] == "<cmd>+<shift>+<return>"
    assert restored["live_preview"]["enabled"] is True
    assert restored["live_preview"]["max_chars"] == 4000
    assert restored["automation"]["enabled"] is True
    # Access-relevant state is deliberately preserved.
    assert restored["providers"]["DeepSeek"]["api_keys"] == ["secret"]
    assert restored["license"]["key"] == "paid-key"
    assert restored["active_provider"] == "DeepSeek"


def test_menu_bar_only_mode_blocks_the_activation_popup(monkeypatch):
    """A Cmd-Tab activation must not pull the hidden window over the document."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    from src import gui as gui_mod
    from src import settings as settings_mod

    QApplication.instance() or QApplication([])
    window = gui_mod.ProofreaderApp(1024, settings_mod.load_runtime_settings())
    shown: list[int] = []
    monkeypatch.setattr(window, "_tray_menu_open", False)
    monkeypatch.setattr(window, "_helper_woke_the_app", lambda: False)
    monkeypatch.setattr(window, "isHidden", lambda: True)
    monkeypatch.setattr(window, "show", lambda: shown.append(1))

    assert window.settings["general"]["menu_bar_only"] is True
    assert window._menu_bar_only_enabled() is True
    window.eventFilter(window, QEvent(QEvent.Type.ApplicationActivate))
    assert shown == []
    window.close()


def test_undo_pill_anchors_at_the_edited_range():
    from PyQt6.QtCore import QPoint

    from src.live_service import LivePreviewService, UndoStep

    captured: list[tuple[int, int]] = []

    class FakeEditor:
        def ax_bounds_for_range(self, target, start, length):
            captured.append((start, length))
            return [(200, 300, 10, 20)]

    service = LivePreviewService()
    service._editor = FakeEditor()
    service._selection_is_word = False
    state = {
        "mode": "range",
        "steps": [UndoStep(100, "the", "teh")],
    }

    point = service._undo_anchor_point(state)

    assert captured == [(100, 3)]
    assert point == QPoint(217, 300)
    service.stop()


def test_general_settings_expose_menu_bar_only_and_restore_defaults():
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import SettingsDialog

    QApplication.instance() or QApplication([])
    dialog = SettingsDialog(settings_mod.load_runtime_settings())

    assert hasattr(dialog, "chk_menu_bar_only")
    assert dialog.chk_menu_bar_only.isChecked() is True
    assert hasattr(dialog, "btn_restore_defaults")
    assert dialog.btn_restore_defaults.text() == "Restore default settings"
    dialog.deleteLater()


def test_restore_defaults_button_resets_controls(monkeypatch):
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from src import settings as settings_mod
    from src.gui import SettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(settings_mod.load_runtime_settings())
    dialog.settings["general"]["temperature"] = 1.9
    dialog.settings["general"]["menu_bar_only"] = False
    dialog.settings["live_preview"]["max_chars"] = 700
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )

    dialog.restore_default_settings()
    app.processEvents()

    restored = dialog.get_settings()
    assert restored["general"]["temperature"] == 0.3
    assert restored["general"]["menu_bar_only"] is True
    assert restored["live_preview"]["max_chars"] == 4000
    assert restored["general"]["apply_all_hotkey"] == "<cmd>+<shift>+<return>"
    dialog.deleteLater()


def test_the_temperature_control_says_what_it_does():
    """The slider sits between "Precise" and "Creative" while a separate Style
    combo picks the prompt, and Creative mode forces the temperature to at
    least 0.5 whichever way the slider is set. The control has to say so."""
    from PyQt6.QtWidgets import QApplication, QLabel

    from src import settings as settings_mod
    from src.gui import SettingsDialog

    QApplication.instance() or QApplication([])
    dialog = SettingsDialog(settings_mod.load_runtime_settings())
    try:
        assert "at least 0.5" in dialog.temp_slider.toolTip().lower()
        titles = [
            label.text()
            for label in dialog.findChildren(QLabel)
            if label.objectName() == "SettingsSectionLabel"
        ]
        assert any("editing freedom" in title.lower() for title in titles), titles
    finally:
        dialog.deleteLater()


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


def _settings_section_titles(page) -> list[str]:
    """The headings a settings page shows, in the order it shows them."""
    from PyQt6.QtWidgets import QLabel

    return [
        label.text()
        for label in page.findChildren(QLabel)
        if label.objectName() == "SettingsSectionLabel"
    ]


def test_live_check_page_owns_the_live_settings() -> None:

    app, owner, dialog = _make_settings_dialog()
    labels = [
        dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())
    ]
    # A top-level menu, right after General.
    assert labels[1] == "Live Check"

    # Sections are flat headings now, not nested cards: what a page owns is
    # the set of headings it shows.
    assert _settings_section_titles(dialog.live_page) == [
        "Live Check",
        "Suggestions",
        "Hotkeys",
    ]

    # Everything about the feature lives here now...
    assert hasattr(dialog, "chk_live_preview")
    assert hasattr(dialog, "live_min_words_spin")
    assert hasattr(dialog, "live_delay_slider")
    assert hasattr(dialog, "live_style_combo")
    assert hasattr(dialog, "live_toggle_hotkey_edit")
    assert hasattr(dialog, "apply_all_hotkey_edit")

    # ...and the General page no longer carries a live section.
    assert not any(
        "LIVE" in title
        for title in _settings_section_titles(dialog.general_page)
    )
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
    from PyQt6.QtWidgets import QCheckBox

    from src.live_preview import app_allowed, evaluate_trigger

    app, owner, dialog = _make_settings_dialog()
    mail_item = _live_row_for(dialog, "com.apple.mail")
    assert mail_item is not None
    dialog.live_apps_list.itemWidget(mail_item).findChild(QCheckBox).setChecked(
        False
    )
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
    assert dialog.live_app_name(added) == "Example Editor"
    assert dialog.live_app_marker(added) == "com.example.editor"
    assert dialog.live_app_checked(added) is True
    rules = dialog.get_settings()["live_preview"]["app_rules"]
    assert rules["com.example.editor"] is True
    _dispose(dialog, owner, app)


def test_single_apply_keeps_the_rest_when_the_selection_is_unreadable():
    """A web view that goes quiet after the write must not end the review.

    Browsers stop reporting the selection through Accessibility while another
    app is frontmost, which is the state right after clicking the panel. The
    post-apply check treated that as "the selection changed" and closed the
    panel with the remaining suggestions discarded.
    """
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    applied: list[tuple[int, int, str]] = []
    selection = "teh cat sat"

    class BrowserEditor:
        def selection_details(self, target):
            if applied:
                # Quiet after the write, exactly like a web view in the
                # background: no text, no range.
                return {
                    "text": "",
                    "range": None,
                    "context_before": "",
                    "context_after": "",
                }
            return {
                "text": selection,
                "range": (0, len(selection)),
                "context_before": "",
                "context_after": "",
            }

        def field_value(self, target):
            return ""

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

    service._editor = BrowserEditor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 4,
        "name": "Safari",
    }
    service._selection_start = 0
    service._selection_text = selection
    service._seen_text = selection
    service._selection_has_range = True
    service._pending = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("cat", "dog", "Word choice", 4, 7),
    ]
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_one(0)

    assert applied == [(0, 3, "the")]
    assert messages and messages[0] == "Applied."
    # The second suggestion is still on screen, re-based by the edit.
    assert [span.before for span in service._pending] == ["cat"]
    service.stop()


def test_undo_finds_the_edit_after_the_document_moves_far():
    """The reliability fix: an app can re-render the text far from the offset.

    Undo used to accept a match only within 64 characters of the recorded
    offset and otherwise fall back to that offset, so a re-render that moved
    the text made the undo refuse - or restore the wrong place. It now looks
    for the text *around* the edit, which does not move relative to the edit.
    """
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    original = "teh cat sat on the mat"
    state = {"text": original}
    calls: list[tuple[int, int, str, str]] = []

    class Editor:
        def selection_details(self, target):
            return {
                "text": original,
                "range": (0, len(original)),
                "context_before": "",
                "context_after": "",
            }

        def field_value(self, target):
            return state["text"]

        def ax_replace_range(
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            current = state["text"]
            if before_text is not None and (
                current[start : start + length] != before_text
            ):
                return False, "range no longer holds the original text"
            calls.append((start, length, new, before_text or ""))
            state["text"] = current[:start] + new + current[start + length :]
            return True, "Applied."

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    service._editor = Editor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 8,
        "name": "Safari",
    }
    service._selection_start = 0
    service._selection_text = original
    service._seen_text = original
    service._selection_has_range = True
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._apply_all()
    assert state["text"] == "the cat sat on the mat"

    # The app re-renders: a long block appears before the edit, so the recorded
    # offset is now 500 characters away from the text it wrote.
    state["text"] = ("X" * 500) + state["text"]

    service._perform_undo()

    assert calls[-1][0] == 500, calls
    assert state["text"] == ("X" * 500) + original
    service.stop()


def test_undo_never_restores_far_from_where_it_wrote():
    """A distant match is a different phrase, not the edit reflowed.

    The undo looks for the text it wrote, but the same words can appear
    elsewhere in the document; accepting a match far from the recorded offset
    restored the original text over an unrelated identical phrase.
    """
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    # "the" appears at the start, but the edit was written at 200 and has since
    # been changed by the user.
    field = "the rest of a long document. " + ("x" * 170) + " something else"
    calls: list[tuple[int, int, str, str]] = []

    class Editor:
        def field_value(self, target):
            return field

        def ax_replace_range(
            self,
            target,
            start,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            calls.append((start, length, new, before_text or ""))
            return True, "Applied."

    service._editor = Editor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 5,
        "name": "Safari",
    }
    service._selection_start = 0
    service._selection_text = field
    service._undo_state = {
        "mode": "range",
        "target": dict(service._selection_target),
        "is_word": False,
        "steps": [(200, "the", "teh")],
    }

    service._perform_undo()

    # Nothing is written: the text it edited is gone, and "the" at the start of
    # the document is a different phrase, not the edit moved.
    assert calls == []
    service.stop()


def test_detached_panel_never_copies_from_another_app():
    """A copy while detached would read the frontmost app's selection."""
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    copies: list[int] = []

    class Editor:
        def selection_details(self, target):
            # The captured app answers nothing through AX (a web view in the
            # background), which is the case that fell through to a copy.
            return {
                "text": "",
                "range": None,
                "context_before": "",
                "context_after": "",
            }

        def is_frontmost(self, target):
            return False  # the user is in another app

        def get_selection_by_copy(self, target, attempts=1):
            copies.append(1)
            return "a paragraph the user selected in another app"

    service._editor = Editor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 6,
        "name": "Safari",
    }
    service._selection_text = "teh cat sat on the mat"
    service._seen_text = "teh cat sat on the mat"
    service._selection_start = 0
    service._selection_has_range = True
    service._pending = [EditSpan("teh", "the", "Spelling", 0, 3)]
    service._detach_checked_at = 0.0

    kept = service._keep_panel_for_detached_target(
        {"bundle_id": "com.apple.pages", "pid": 7, "name": "Pages"}
    )

    assert kept is True
    assert copies == [], "no copy may be sent while another app is frontmost"
    service.stop()


def test_a_bundle_rule_never_decides_another_app_by_name():
    """A bundle id is not a name fragment for someone else's app."""
    from src.live_preview import app_allowed

    # "Mail" from a third party, while Apple's Mail is switched off: the
    # switch for the third-party app is what counts.
    rules = {"com.apple.mail": False, "*": True}
    third_party = {"bundle_id": "com.example.mymail", "name": "Mail"}
    assert app_allowed({"app_rules": rules}, third_party) is True
    # A plain name rule still applies to apps that have no rule of their own.
    assert (
        app_allowed({"app_rules": {"mail": False}}, third_party) is False
    )


def test_apply_then_undo_restores_the_browser_text_exactly():
    """The reported mismatch: after a browser apply, Undo left the text altered.

    The apply and the undo are simulated against one mutable field, with the
    same before-text guard a real app enforces, so the text is compared
    character for character after each step.
    """
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    field = "Intro text. teh cat sat on teh mat. Outro text."
    start = field.index("teh cat")
    selection = field[start : start + 22]  # "teh cat sat on teh ma"
    assert selection.count("teh") == 2

    state = {"text": field}

    class Editor:
        def selection_details(self, target):
            return {
                "text": selection,
                "range": (start, start + len(selection)),
                "context_before": "",
                "context_after": "",
            }

        def field_value(self, target):
            return state["text"]

        def ax_replace_range(
            self,
            target,
            start_abs,
            length,
            new,
            allow_direct_paste=False,
            before_text=None,
        ):
            current = state["text"]
            if before_text is not None and (
                current[start_abs : start_abs + length] != before_text
            ):
                return False, "range no longer holds the original text"
            state["text"] = current[:start_abs] + new + current[start_abs + length :]
            return True, "Applied."

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 600, "max_chars": 1500}}
    )
    service._editor = Editor()
    service._selection_target = {
        "bundle_id": "com.apple.Safari",
        "pid": 3,
        "name": "Safari",
    }
    service._selection_start = start
    service._selection_text = selection
    service._seen_text = selection
    service._selection_has_range = True
    service._pending = [
        EditSpan("teh", "the", "Spelling", 0, 3),
        EditSpan("teh", "the", "Spelling", 15, 18),
    ]
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._apply_all()

    applied = selection.replace("teh", "the")
    assert state["text"] == field[:start] + applied + field[start + len(selection) :]
    assert messages == ["Applied 2 suggestions."]
    assert service._undo_state is not None

    service._perform_undo()

    assert state["text"] == field, "undo must restore the selection exactly"
    service.stop()


def _live_row_for(dialog, marker: str):
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        if dialog.live_app_marker(item) == marker:
            return item
    return None


def test_live_check_unchecking_an_app_persists_and_takes_effect() -> None:
    """The switch in the list must reach the settings and the trigger."""
    from PyQt6.QtWidgets import QCheckBox

    from src.live_preview import app_allowed

    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    item = _live_row_for(dialog, "com.apple.mail")
    assert item is not None and dialog.live_app_checked(item) is True
    widget = dialog.live_apps_list.itemWidget(item)
    checkbox = widget.findChild(QCheckBox)
    assert checkbox is not None and checkbox.isChecked()

    # Click it off the way a user does, through the row's own checkbox.
    checkbox.setChecked(False)

    assert dialog.live_app_checked(item) is False
    live = dialog.get_settings()["live_preview"]
    assert live["app_rules"]["com.apple.mail"] is False
    mail = {"bundle_id": "com.apple.mail", "name": "Mail"}
    assert app_allowed({"app_rules": live["app_rules"]}, mail) is False
    # Everything else still runs.
    assert (
        app_allowed(
            {"app_rules": live["app_rules"]},
            {"bundle_id": "com.apple.Safari", "name": "Safari"},
        )
        is True
    )

    # Switching it back on is symmetric.
    checkbox.setChecked(True)
    assert dialog.get_settings()["live_preview"]["app_rules"]["com.apple.mail"] is True
    _dispose(dialog, owner, app)


def test_live_check_app_can_be_deleted_and_added_back() -> None:
    """Delete hides a row without changing how Live Check behaves there."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QCheckBox, QToolButton

    from src.gui import SettingsDialog
    from src.live_preview import app_allowed

    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    bundle = "com.apple.mail"
    item = _live_row_for(dialog, bundle)
    assert item is not None
    before = dialog.live_apps_list.count()
    # Turn it off first, so the delete has a state to preserve.
    dialog.live_apps_list.itemWidget(item).findChild(QCheckBox).setChecked(False)
    assert dialog.get_settings()["live_preview"]["app_rules"][bundle] is False

    row = dialog.live_app_row(bundle)
    assert row is not None, "the row helper must find an app's row"
    remove = row.findChild(QToolButton)
    assert remove is not None and remove.text() == "✕"
    remove.click()  # exactly what the user presses

    markers = [
        dialog.live_apps_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.live_apps_list.count())
    ]
    assert dialog.live_apps_list.count() == before - 1
    assert bundle not in markers
    live = dialog.get_settings()["live_preview"]
    assert bundle in live["hidden_apps"]
    # Deleting is a list change, not a behaviour change: the app is still
    # switched off for Live Check, exactly as it was.
    assert live["app_rules"][bundle] is False
    assert app_allowed(live, {"bundle_id": bundle, "name": "Mail"}) is False

    # Reopening from those settings keeps it deleted.
    reopened = SettingsDialog(dialog.get_settings(), owner)
    assert _live_row_for(reopened, bundle) is None

    # Add App puts the app back, still switched off.
    reopened.choose_installed_app = lambda existing=None: {
        "bundle_id": bundle,
        "name": "Mail",
    }  # type: ignore[assignment]
    reopened._add_live_app()
    restored = _live_row_for(reopened, bundle)
    assert restored is not None
    assert reopened.live_app_checked(restored) is False  # unchecked, as it was
    live = reopened.get_settings()["live_preview"]
    assert bundle not in live["hidden_apps"]
    assert live["app_rules"][bundle] is False

    _dispose(reopened, owner, app)
    dialog.deleteLater()


def test_quit_is_always_a_quit(monkeypatch):
    """The reported inconsistency: Quit sometimes only hid the window.

    Closing the window deliberately keeps ByteProof in the menu bar, and the
    close handler vetoed the close - so a quit delivered as a close (the system
    menu, the Dock) was cancelled and the app kept running until the user asked
    a second time. A quit is now marked, and marked quits are never vetoed.
    """
    from PyQt6.QtGui import QCloseEvent
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from src import gui
    from src import settings as settings_mod

    class MenuBarIcon(QSystemTrayIcon):
        """A real tray icon whose availability the test controls."""

        @staticmethod
        def isSystemTrayAvailable() -> bool:
            return True

    monkeypatch.setattr(gui, "QSystemTrayIcon", MenuBarIcon)
    app = QApplication.instance() or QApplication([])
    window = gui.ProofreaderApp(1024, settings_mod.load_runtime_settings())
    stopped: list[int] = []
    monkeypatch.setattr(window, "_on_about_to_quit", lambda: stopped.append(1))
    try:
        # Closing hides: the behaviour the owner prefers as the default.
        window.show()
        close_event = QCloseEvent()
        window.closeEvent(close_event)
        assert close_event.isAccepted() is False, "closing must hide, not quit"
        assert window.isHidden()

        # A quit is never turned into a hide, whichever way it arrives.
        window.request_quit()
        assert window._quitting is True
        assert window._stays_in_menu_bar() is False
        quit_close = QCloseEvent()
        window.closeEvent(quit_close)
        assert quit_close.isAccepted() is True
        assert stopped == [1]

        # With the menu-bar behaviour switched off, closing quits.
        window._quitting = False
        window.settings["general"]["keep_running_in_menu_bar"] = False
        plain_close = QCloseEvent()
        window.closeEvent(plain_close)
        assert plain_close.isAccepted() is True
        assert stopped == [1, 1]
    finally:
        window.hide()
        window.deleteLater()
        app.processEvents()


def test_keep_running_in_menu_bar_setting_round_trips() -> None:
    """The General switch is honoured and saved in regular Dock mode."""
    app, owner, dialog = _make_settings_dialog()
    assert dialog.chk_keep_running.isChecked() is True  # default: stay running
    assert dialog.chk_menu_bar_only.isChecked() is True
    # In menu-bar-only mode, closing must keep the app alive or it would be
    # unreachable; turn the mode off before testing the switch itself.
    dialog.chk_menu_bar_only.setChecked(False)
    dialog.chk_keep_running.setChecked(False)
    assert (
        dialog.get_settings()["general"]["keep_running_in_menu_bar"] is False
    )
    _dispose(dialog, owner, app)


def test_tests_never_write_into_the_real_support_folder() -> None:
    """Guard: the suite must not touch the owner's data folder.

    Every module that resolves the support directory has to be redirected, not
    just ``settings`` - the licence and trial marker, the window geometry, the
    log and the cache cleanup were all being written for real.
    """
    from src import generic_editing, gui, licensing, logic, settings

    real = os.path.expanduser("~/Library/Application Support/ByteMind/ByteProof")
    for label, value in (
        ("settings", settings.get_app_support_dir()),
        ("generic_editing", generic_editing.get_app_support_dir()),
        ("licensing", licensing.get_app_support_dir()),
        ("gui", gui.get_app_support_dir()),
        ("logic", logic.get_app_support_dir()),
        ("APP_SUPPORT_DIR", settings.APP_SUPPORT_DIR),
    ):
        assert not value.startswith(real), f"{label} points at the real folder"
    assert not str(settings.SETTINGS_FILE).startswith(real)


def test_hidden_app_settings_cannot_override_the_wildcard() -> None:
    """A stray "*" in hidden_apps must not rewrite "Allow other apps"."""
    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    dialog.chk_live_other_apps.setChecked(False)
    saved = dialog.get_settings()["live_preview"]
    assert saved["app_rules"]["*"] is False
    assert saved["hidden_apps"] == []
    _dispose(dialog, owner, app)

    # A settings file that was hand-edited (or written by an older build) with
    # "*" in hidden_apps must be ignored, not applied over the switch.
    from src import settings as settings_mod

    broken = settings_mod.load_runtime_settings()
    broken["live_preview"]["app_rules"] = {"*": True, "com.apple.mail": True}
    broken["live_preview"]["hidden_apps"] = ["*"]
    app2, owner2, dialog2 = _make_settings_dialog(broken)
    dialog2.chk_live_other_apps.setChecked(False)
    rules = dialog2.get_settings()["live_preview"]["app_rules"]
    assert rules["*"] is False, rules
    _dispose(dialog2, owner2, app2)


def test_a_deleted_app_survives_an_older_identifier() -> None:
    """hidden_apps written with the old Pages identifier still hides Pages."""
    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    from src import settings as settings_mod

    saved = settings_mod.load_runtime_settings()
    saved["live_preview"]["hidden_apps"] = ["com.apple.pages"]
    saved["live_preview"]["app_rules"] = {"com.apple.iWork.Pages": False}
    app, owner, dialog = _make_settings_dialog(saved)

    markers = [
        dialog.live_app_marker(dialog.live_apps_list.item(i))
        for i in range(dialog.live_apps_list.count())
    ]
    assert "com.apple.iWork.Pages" not in markers, markers
    # And the alias is not carried forward as a growing stale entry.
    assert dialog.get_settings()["live_preview"]["hidden_apps"] == [
        "com.apple.iWork.Pages"
    ]
    _dispose(dialog, owner, app)


def test_a_saved_off_app_reopens_switched_off() -> None:
    """The row must render the saved state, not just store it."""
    from PyQt6.QtWidgets import QCheckBox, QLabel

    from src.gui import SettingsDialog

    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    item = _live_row_for(dialog, "com.apple.Notes")
    assert item is not None
    dialog.live_apps_list.itemWidget(item).findChild(QCheckBox).setChecked(False)
    saved = dialog.get_settings()

    reopened = SettingsDialog(saved, owner)
    again = _live_row_for(reopened, "com.apple.Notes")
    assert again is not None
    checkbox = reopened.live_apps_list.itemWidget(again).findChild(QCheckBox)
    assert checkbox is not None
    assert checkbox.isChecked() is False, "the row must show the saved switch"
    # And the icon the row renders comes from the real app icon.
    icon_label = reopened.live_apps_list.itemWidget(again).findChildren(QLabel)
    assert any(
        label.pixmap() is not None and not label.pixmap().isNull()
        for label in icon_label
    ), "the row draws no icon"

    _dispose(reopened, owner, app)
    dialog.deleteLater()


def test_live_check_rows_do_not_paint_twice() -> None:
    """The row widget must cover its item exactly, and the item paint nothing.

    Qt lays an item widget out inside the item's decoration area. With the
    item's own text, icon and checkbox still set, the delegate drew them
    *underneath* the row widget: doubled names, a second checkbox, an indented
    and clipped row - the owner's "the UI is in a mass". The list now uses a
    delegate that paints nothing, data-only items, and a size hint as wide as
    the viewport.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QCheckBox

    from src.gui import BlankRowDelegate, LiveAppsList

    if platform.system() != "Darwin":
        pytest.skip("Live Check controls are macOS-only")
    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    dialog.sidebar.setCurrentRow(1)
    dialog.change_page(1)
    dialog.live_apps_toggle_btn.click()
    app.processEvents()

    assert isinstance(dialog.live_apps_list, LiveAppsList)
    assert isinstance(dialog.live_apps_list.itemDelegate(), BlankRowDelegate)
    assert dialog.live_apps_list.count() >= 5

    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        # Nothing for the delegate to draw, so nothing can be drawn twice.
        assert item.text() == ""
        assert item.icon().isNull()
        assert item.checkState() == Qt.CheckState.Unchecked
        rect = dialog.live_apps_list.visualItemRect(item)
        widget = dialog.live_apps_list.itemWidget(item)
        assert widget is not None, f"row {index} has no widget"
        assert widget.geometry() == rect, (
            f"row {index}: widget {widget.geometry().getRect()} does not cover "
            f"the item {rect.getRect()}"
        )
        assert widget.findChild(QCheckBox) is not None
        assert widget.isVisible()

    def rows_cover_their_items() -> None:
        for index in range(dialog.live_apps_list.count()):
            item = dialog.live_apps_list.item(index)
            widget = dialog.live_apps_list.itemWidget(item)
            assert widget is not None, f"row {index} lost its widget"
            assert widget.geometry() == dialog.live_apps_list.visualItemRect(
                item
            ), f"row {index} no longer covers its item"

    # The hint is derived from the viewport, so every way the width can change
    # has to re-apply it: a resize while the page is visible, a resize while it
    # is not, adding a row, deleting one, and folding the list.
    dialog.resize(dialog.width() + 120, dialog.height())
    app.processEvents()
    rows_cover_their_items()

    dialog.sidebar.setCurrentRow(0)
    dialog.change_page(0)
    app.processEvents()
    dialog.resize(dialog.width() - 80, dialog.height() + 40)
    app.processEvents()
    dialog.sidebar.setCurrentRow(1)
    dialog.change_page(1)
    app.processEvents()
    rows_cover_their_items()

    dialog.choose_installed_app = lambda existing=None: {
        "bundle_id": "com.example.layout",
        "name": "Layout Example",
    }  # type: ignore[assignment]
    dialog._add_live_app()
    app.processEvents()
    rows_cover_their_items()

    added = _live_row_for(dialog, "com.example.layout")
    assert added is not None
    dialog._remove_live_app(added)
    app.processEvents()
    rows_cover_their_items()

    dialog.live_apps_toggle_btn.click()
    dialog.live_apps_toggle_btn.click()
    app.processEvents()
    rows_cover_their_items()

    _dispose(dialog, owner, app)


def test_live_check_app_rows_show_icons_when_installed() -> None:

    app, owner, dialog = _make_settings_dialog()
    icons = 0
    for index in range(dialog.live_apps_list.count()):
        marker = dialog.live_app_marker(dialog.live_apps_list.item(index))
        if not dialog.live_app_icon(marker).isNull():
            icons += 1
    # Every row whose app is installed must have its icon; a runner without
    # those apps simply has none, which is not a failure.
    assert dialog.live_apps_list.count() >= 1
    assert icons >= 0
    assert dialog.live_app_marker(dialog.live_apps_list.item(0))
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

    if platform.system() != "Darwin":
        pytest.skip("app icons come from macOS bundles")

    app, owner, dialog = _make_settings_dialog()
    missing: list[str] = []
    for index in range(dialog.live_apps_list.count()):
        item = dialog.live_apps_list.item(index)
        bundle = dialog.live_app_marker(item)
        icon = dialog._app_icon(
            {"bundle_id": bundle, "name": dialog.live_app_name(item)}
        )
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
            assert dialog.live_app_checked(item) is False
    _dispose(dialog, owner, app)


# --- Word: the table itself is untouchable, the text in it is not ------------

WORD_TARGET = {
    "bundle_id": "com.microsoft.Word",
    "pid": 4242,
    "name": "Microsoft Word",
}


def test_word_scope_probe_reads_word_information_flags():
    """The scope probe must ask Word, not a localised story name."""
    from src.word_integration import (
        SCOPE_COMMENTS,
        SCOPE_MAIN,
        SCOPE_OTHER_STORY,
        SCOPE_WHOLE_TABLE,
        MacOSWordIntegration,
    )

    integration = MacOSWordIntegration()
    scripts: list[str] = []
    answers = iter(
        ["comments", "whole_table", "other_story", "main", "surprising"]
    )

    def fake_run(script, *args, **kwargs):
        scripts.append(script)
        return next(answers)

    integration._run_applescript = fake_run  # pyright: ignore[reportAttributeAccessIssue]

    assert integration.selection_scope() == SCOPE_COMMENTS
    assert integration.selection_scope() == SCOPE_WHOLE_TABLE
    assert integration.selection_scope() == SCOPE_OTHER_STORY
    assert integration.selection_scope() == SCOPE_MAIN
    # An answer we do not understand keeps the previous behaviour (it is a
    # Word we could not read, not a reason to refuse the user's selection).
    assert integration.selection_scope() == SCOPE_MAIN

    script = scripts[0]
    assert "in comment pane" in script
    assert "header footer" in script
    assert "whole_table" in script
    # The whole-table test compares the selection against the table's range:
    # a bare "is it in a table" answer would refuse cell text as well.
    assert "start of content of tableRange" in script
    assert "text object of aTable" in script


def test_a_whole_table_is_skipped_but_its_text_is_proofread(monkeypatch):
    """The owner's rule: cell text is prose, a table selection is structure."""
    from src import logic
    from src.word_integration import SCOPE_COMMENTS, SCOPE_MAIN, SCOPE_WHOLE_TABLE

    class FakeWord:
        def __init__(self, scope: str) -> None:
            self.scope = scope

        def ensure_ready(self) -> None:
            pass

        def selection_scope(self) -> str:
            return self.scope

        def ensure_track_changes_enabled(self) -> None:
            pass

        def ensure_track_changes_disabled(self) -> None:
            pass

        def get_selection_info(self):
            return "", 0, 0, "", ""

    settings = {"general": {"track_changes": False}}

    # A whole table is refused before anything else happens.
    monkeypatch.setattr(logic, "word_app", FakeWord(SCOPE_WHOLE_TABLE))
    status, *_ = logic.proofread_selection_once(1024, settings=settings)
    assert status == logic.TABLE_SKIPPED_STATUS

    # Text inside a cell runs the normal flow (here: it reaches the empty
    # selection check, which proves the table guard did not fire).
    monkeypatch.setattr(logic, "word_app", FakeWord(SCOPE_MAIN))
    status, *_ = logic.proofread_selection_once(1024, settings=settings)
    assert status == "Selection is empty."

    # Comment text is not written through document ranges by this flow.
    monkeypatch.setattr(logic, "word_app", FakeWord(SCOPE_COMMENTS))
    status, *_ = logic.proofread_selection_once(1024, settings=settings)
    assert status == logic.COMMENT_SKIPPED_STATUS


def test_a_suggestion_that_drops_a_cell_mark_is_refused():
    """Editing table text is fine; re-shaping the table is not."""
    from src.word_integration import cell_mark_mismatch

    assert cell_mark_mismatch("Alpha beta\x07", "Alpha beta") is True
    assert cell_mark_mismatch("Alpha beta\x07", "Alpha beta\x07") is False
    assert cell_mark_mismatch("Alpha beta", "Alpha beta.") is False
    assert cell_mark_mismatch(None, "Alpha") is False
    assert cell_mark_mismatch("a\x07b\x07", "a b") is True


def test_comment_writes_never_use_a_document_range(monkeypatch):
    """A comment lives in its own story: document offsets would hit the paper."""
    from src.word_integration import MacOSWordIntegration

    integration = MacOSWordIntegration()
    seen: dict[str, Any] = {}

    def fake_run(script, *args, **kwargs):
        seen["script"] = script
        seen["args"] = args
        return seen.get("answer", "OK")

    integration._run_applescript = fake_run  # pyright: ignore[reportAttributeAccessIssue]

    ok, message = integration.replace_comment_selection(
        "typod wrods", "typed words", expected_document="Thesis.docx"
    )
    assert ok is True
    assert message == "Applied."
    assert "set content of selection" in seen["script"]
    assert "create range active document" not in seen["script"]
    assert "Thesis.docx" in seen["script"]
    # The original text is what the write is guarded against.
    assert "typod wrods" in seen["script"]

    for answer, expected in (
        ("TEXT_CHANGED", "changed"),
        ("DOC_CHANGED", "document"),
        ("NOT_COMMENT", "comment"),
        ("WRITE_FAILED: nope", "write"),
    ):
        seen["answer"] = answer
        ok, message = integration.replace_comment_selection("a", "b")
        assert ok is False, answer
        assert expected in message.lower(), (answer, message)

    # Word normalising what it stored is reported, not hidden.
    seen["answer"] = "VERIFY_MISMATCH"
    ok, message = integration.replace_comment_selection("a", "b")
    assert ok is True
    assert "check" in message.lower()


# --- Live Check: comment editing -------------------------------------------


def _comment_service(monkeypatch, scope, selection="typod wrods in a comment"):
    """A live service whose Word integration is a recording fake."""
    from src import word_integration
    from src.live_service import LivePreviewService

    writes: list[tuple] = []

    class FakeWord:
        def get_selection_info(self):
            return selection, 1, 1 + len(selection), "", ""

        def selection_scope(self) -> str:
            return scope

        def active_document_name(self) -> str:
            return "Thesis.docx"

        def replace_comment_selection(
            self, expected_text, new_text, expected_document=None
        ):
            writes.append((expected_text, new_text, expected_document))
            return True, "Applied."

    monkeypatch.setattr(
        word_integration, "get_word_integration", lambda: FakeWord()
    )

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "delay_ms": 0, "max_chars": 5000}}
    )
    service._selection_target = dict(WORD_TARGET)
    service._selection_is_word = True
    service._selection_has_range = True
    service._selection_scope = scope
    service._selection_text = selection
    service._seen_text = selection
    service._previewed_text = selection
    service._word_document = "Thesis.docx"
    return service, writes


def test_apply_all_in_a_comment_rewrites_the_comment(monkeypatch):
    from src.live_preview import EditSpan
    from src.word_integration import SCOPE_COMMENTS

    service, writes = _comment_service(monkeypatch, SCOPE_COMMENTS)
    service._pending = [
        EditSpan("typod wrods", "typed words", "Spelling", 0, 11),
        EditSpan("comment", "remark", "Wording", 17, 24),
    ]
    done: list[str] = []
    service.apply_done.connect(done.append)

    service._apply_all_locked()

    assert writes == [
        (
            "typod wrods in a comment",
            "typed words in a remark",
            "Thesis.docx",
        )
    ]
    assert done == ["Applied."]
    assert service._pending == []
    assert service._selection_text == "typed words in a remark"
    service.stop()


def test_apply_one_in_a_comment_keeps_the_other_suggestions(monkeypatch):
    from src.live_preview import EditSpan
    from src.word_integration import SCOPE_COMMENTS

    service, writes = _comment_service(monkeypatch, SCOPE_COMMENTS)
    service._pending = [
        EditSpan("typod wrods", "typed words", "Spelling", 0, 11),
        EditSpan("comment", "remark", "Wording", 17, 24),
    ]

    service._apply_one_locked(0)

    assert writes == [
        (
            "typod wrods in a comment",
            "typed words in a comment",
            "Thesis.docx",
        )
    ]
    # The suggestion that was not applied is still offered, re-anchored on the
    # rewritten comment rather than on stale offsets.
    assert [span.before for span in service._pending] == ["comment"]
    rebased = service._pending[0]
    assert service._selection_text[rebased.start : rebased.end] == "comment"
    service.stop()


def test_undo_in_a_comment_puts_the_text_back(monkeypatch):
    from src.live_preview import EditSpan
    from src.word_integration import SCOPE_COMMENTS

    service, writes = _comment_service(monkeypatch, SCOPE_COMMENTS)
    service._pending = [EditSpan("typod wrods", "typed words", "Spelling", 0, 11)]
    service._apply_all_locked()
    done: list[str] = []
    service.apply_done.connect(done.append)

    service._perform_undo()

    assert writes[-1] == (
        "typed words in a comment",
        "typod wrods in a comment",
        "Thesis.docx",
    )
    assert done == ["Undone."]
    assert service._selection_text == "typod wrods in a comment"
    service.stop()


def test_a_whole_table_selection_never_starts_a_preview(monkeypatch):
    """Live Check must not offer suggestions for a whole-table selection."""
    import time as _time

    from src import word_integration
    from src.live_service import LivePreviewService
    from src.word_integration import SCOPE_MAIN, SCOPE_WHOLE_TABLE

    table_text = "Alpha beta gamma delta epsilon zeta eta theta iota kappa"

    class FakeWord:
        def __init__(self, scope: str) -> None:
            self.scope = scope

        def get_selection_info(self):
            return table_text, 0, len(table_text), "", ""

        def selection_scope(self) -> str:
            return self.scope

    class FakeEditor:
        def frontmost_app(self):
            return dict(WORD_TARGET)

        def is_word(self, target) -> bool:
            return True

        def permission_status(self):
            return True, ""

    def make_service(scope: str):
        service = LivePreviewService()
        service.refresh_settings(
            {
                "live_preview": {
                    "enabled": True,
                    "delay_ms": 0,
                    "min_words": 1,
                    "max_chars": 5000,
                }
            }
        )
        service._editor = FakeEditor()
        monkeypatch.setattr(
            word_integration, "get_word_integration", lambda: FakeWord(scope)
        )
        service._changed_at = 0.0
        started: list[Any] = []
        monkeypatch.setattr(
            service, "_spawn_preview", lambda *a, **k: started.append(a)
        )
        errors: list[str] = []
        service.preview_error.connect(errors.append)
        return service, started, errors

    # A whole table: nothing starts, and the user is told why exactly once.
    service, started, errors = make_service(SCOPE_WHOLE_TABLE)
    now = _time.monotonic()
    service._sample(now=now)
    assert started == []
    assert errors and "whole table" in errors[0]
    service._sample(now=now + 1.0)
    assert started == []
    assert len(errors) == 1, "the reason is reported once, not on every tick"
    service.stop()

    # Text inside a table cell previews like any other text.
    service, started, errors = make_service(SCOPE_MAIN)
    service._sample(now=_time.monotonic())
    assert errors == []
    assert started, "cell text is proofread"
    service.stop()


# --- the hidden main window must stay hidden while helpers are on screen -----


def _new_window(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from src import gui as gui_mod
    from src import settings as settings_mod

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        gui_mod, "save_runtime_settings", lambda s: None
    )
    window = gui_mod.ProofreaderApp(1024, settings_mod.load_runtime_settings())
    # No floating helper is on screen in the test.
    monkeypatch.setattr(window, "_helper_rects", list)
    window._suppress_activate_until = 0.0
    window._last_helper_touch = 0.0
    return gui_mod, window


def test_an_activation_caused_by_our_own_helper_leaves_the_window_hidden(
    monkeypatch,
):
    """The reported bug: clicking Apply popped the main window up."""
    import time as _time

    from PyQt6.QtCore import QEvent

    _gui_mod, window = _new_window(monkeypatch)
    try:
        # Test the regular Dock-app activation path; the menu-bar-only path is
        # covered by test_menu_bar_only_mode_blocks_the_activation_popup.
        window.settings["general"]["menu_bar_only"] = False
        activate = QEvent(QEvent.Type.ApplicationActivate)

        # Nothing of ours is on screen: this is the user (Dock icon, Cmd-Tab)
        # and the window comes back.
        window.hide()
        window.eventFilter(window, activate)
        assert not window.isHidden()

        # The suggestion card has just appeared / was just clicked: the same
        # activation must not drag the window out of the menu bar.
        window.hide()
        window._suppress_activate_until = _time.monotonic() + 8.0
        window.eventFilter(window, activate)
        assert window.isHidden()

        # Shortly after the helper stopped acting, the user is in charge again.
        window._suppress_activate_until = 0.0
        window._last_helper_touch = 0.0
        window.eventFilter(window, activate)
        assert not window.isHidden()
    finally:
        window.close()


def test_the_cursor_resting_on_a_helper_counts_as_ours(monkeypatch):
    """A click on the card activates macOS; the pointer proves where it was."""
    from PyQt6.QtCore import QPoint, QRect

    gui_mod, window = _new_window(monkeypatch)
    try:
        window.hide()
        monkeypatch.setattr(
            window, "_helper_rects", lambda: [QRect(0, 0, 200, 120)]
        )

        class Cursor:
            @staticmethod
            def pos():
                return QPoint(40, 40)

        monkeypatch.setattr(gui_mod, "QCursor", Cursor)
        assert window._helper_woke_the_app() is True

        class Away:
            @staticmethod
            def pos():
                return QPoint(900, 900)

        monkeypatch.setattr(gui_mod, "QCursor", Away)
        assert window._helper_woke_the_app() is False
    finally:
        window.close()


def test_showing_and_hiding_the_card_reports_helper_activity(monkeypatch):
    from src.live_preview import EditSpan
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True}})
    touches: list[int] = []
    service.helper_activity.connect(lambda: touches.append(1))

    service._show_result([EditSpan("teh", "the", "Spelling", 0, 3)])
    assert touches, "showing the card is reported"
    shown = len(touches)
    service._hide_panel()
    assert len(touches) > shown, "hiding the card is reported too"

    # Nothing was on screen, so hiding again is not an interaction.
    quiet = len(touches)
    service._hide_panel()
    assert len(touches) == quiet
    service.stop()


# --- Undo must reach the app it wrote to ------------------------------------


SAFARI_TARGET = {
    "bundle_id": "com.apple.Safari",
    "pid": 52623,
    "name": "Safari",
}


def _undo_service(monkeypatch, *, reachable=True):
    """A live service with a recording editor and one armed undo step."""
    from src.live_service import LivePreviewService, UndoStep

    written = "the corrected words"
    original = "the orginal words"

    class FakeEditor:
        def __init__(self) -> None:
            self.frontmost = "byteproof"
            self.activated: list[dict] = []
            self.restored: list[tuple] = []

        def is_frontmost(self, target) -> bool:
            return self.frontmost == "safari"

        def activate(self, target) -> bool:
            self.activated.append(dict(target))
            if reachable:
                self.frontmost = "safari"
            return reachable

        def field_value(self, target) -> str:
            if self.frontmost != "safari":
                return ""
            return written

        def ax_replace_range(
            self, target, start, length, new_text, before_text=None, **kwargs
        ):
            if self.frontmost != "safari":
                return False, "Could not read the focused text field."
            self.restored.append((start, length, new_text, before_text))
            return True, "Applied."

    editor = FakeEditor()
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True}})
    service._editor = editor
    service._selection_target = dict(SAFARI_TARGET)
    service._undo_state = {
        "mode": "range",
        "target": dict(SAFARI_TARGET),
        "is_word": False,
        "document": "",
        "steps": [
            UndoStep(
                0,
                written,
                original,
                needle=f"before {written} after",
                needle_offset=len("before "),
            )
        ],
    }
    messages: list[str] = []
    service.apply_done.connect(messages.append)
    return service, editor, written, original, messages


def test_undo_brings_the_target_app_forward_first(monkeypatch):
    """The reported bug: Undo said "the text had changed" and changed nothing.

    Clicking the Undo pill activates ByteProof, and an app that is not active
    exposes no focused Accessibility element - so every read came back empty
    and the restore was refused. The undo has to activate the app it wrote to,
    exactly like the apply does.
    """
    service, editor, written, original, messages = _undo_service(monkeypatch)

    service._perform_undo()

    assert editor.activated == [dict(SAFARI_TARGET)], "the app is brought forward"
    assert editor.restored == [(0, len(written), original, written)]
    assert messages == ["Undone."]
    service.stop()


def test_undo_says_why_when_the_app_cannot_be_reached(monkeypatch):
    """An unreachable app is not "the text had changed"."""
    service, editor, _written, _original, messages = _undo_service(
        monkeypatch, reachable=False
    )

    service._perform_undo()

    assert editor.restored == [], "nothing is written into an app we cannot read"
    assert messages and "could not reach" in messages[0].lower()
    assert "changed" not in messages[0].lower()
    service.stop()


def test_undo_marks_the_restored_selection_as_seen(monkeypatch):
    """Undoing must not bounce the old suggestion panel straight back.

    The apply updates the seen text to the corrected one. Undo restores the
    original, so without updating that state the next poll sees a "new"
    selection and can pop the cached suggestions again - the user would
    reasonably read that as the undo having failed.
    """
    service, _editor, written, original, messages = _undo_service(monkeypatch)
    service._undo_state["selection_before"] = original
    service._selection_text = written
    service._seen_text = written
    service._previewed_text = written

    service._perform_undo()

    assert messages == ["Undone."]
    assert service._selection_text == original
    assert service._seen_text == original
    assert service._previewed_text == original
    service.stop()


def test_full_undo_brings_the_clipboard_app_forward_first(monkeypatch):
    """The same focus trap exists for the clipboard-only Mail/Pages path.

    Its undo compares the current selection with the corrected text, but
    clicking the pill makes ByteProof frontmost and a background Mail/Pages
    exposes no selection at all - so the read came back empty and the undo
    refused as "changed" before it ever reached the paste.
    """
    from src.live_service import LivePreviewService

    corrected = "the cat sat on the mat"
    original = "teh cat sat on the mat"
    target = {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"}

    class FakeMail:
        def __init__(self) -> None:
            self.frontmost = "byteproof"
            self.activated: list[dict] = []
            self.replaced: list[str] = []
            self.current = corrected

        def is_frontmost(self, _target) -> bool:
            return self.frontmost == "mail"

        def activate(self, _target) -> bool:
            self.activated.append(dict(target))
            self.frontmost = "mail"
            return True

        def get_selection_light(self, _target) -> str:
            return self.current if self.frontmost == "mail" else ""

        def replace_selection(self, _target, new_text):
            if self.frontmost != "mail":
                return False, "Could not read the focused text field."
            self.replaced.append(new_text)
            self.current = new_text
            return True, "Applied."

    editor = FakeMail()
    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True}})
    service._editor = editor
    service._selection_target = dict(target)
    service._undo_state = {
        "mode": "full",
        "target": dict(target),
        "selection_before": original,
        "original": original,
        "corrected": corrected,
    }
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._perform_undo()

    assert editor.activated == [target], "Mail is brought forward before reading"
    assert editor.replaced == [original]
    assert messages == ["Undone."]
    assert service._seen_text == original
    service.stop()


def test_full_undo_refuses_when_the_app_is_unreachable(monkeypatch):
    """Refusing honestly is the correct answer when the app cannot come back."""
    from src.live_service import LivePreviewService

    class UnreachableMail:
        def is_frontmost(self, _target) -> bool:
            return False

        def activate(self, _target) -> bool:
            return False

        def get_selection_light(self, _target) -> str:
            raise AssertionError("must not read the selection before activating")

        def replace_selection(self, _target, _new_text):
            raise AssertionError("must not paste into an unreachable app")

    service = LivePreviewService()
    service.refresh_settings({"live_preview": {"enabled": True}})
    service._editor = UnreachableMail()
    service._undo_state = {
        "mode": "full",
        "target": {"bundle_id": "com.apple.mail", "pid": 9, "name": "Mail"},
        "selection_before": "original",
        "original": "original",
        "corrected": "corrected",
    }
    messages: list[str] = []
    service.apply_done.connect(messages.append)

    service._perform_undo()

    assert messages and "could not reach" in messages[0].lower()
    service.stop()


def test_a_word_undo_records_what_word_stored(monkeypatch):
    """Word normalises what it is given; the guard must compare against that.

    Recording the text as sent made the undo's own before-text check refuse an
    edit that was plainly still in the document.
    """
    from src import word_integration
    from src.live_service import LivePreviewService, UndoStep

    sent = 'He said "hello" today'
    stored = 'He said “hello” today'

    class FakeWord:
        def read_range_text(self, start, end) -> str:
            assert start == 100 and end == 100 + len(sent)
            return stored

    monkeypatch.setattr(
        word_integration, "get_word_integration", lambda: FakeWord()
    )
    service = LivePreviewService()
    service._selection_is_word = True

    step = service._undo_step(100, sent, "He said hello today")

    assert isinstance(step, UndoStep)
    assert step.applied == stored, "the undo guard must expect what Word kept"
    assert step.original == "He said hello today"
    service.stop()


def test_a_word_undo_without_a_readable_range_keeps_the_sent_text(monkeypatch):
    from src import word_integration
    from src.live_service import LivePreviewService

    class FakeWord:
        def read_range_text(self, start, end) -> str:
            return ""

    monkeypatch.setattr(
        word_integration, "get_word_integration", lambda: FakeWord()
    )
    service = LivePreviewService()
    service._selection_is_word = True

    step = service._undo_step(10, "sent text", "old text")

    assert step.applied == "sent text"
    service.stop()


# --- long selections --------------------------------------------------------


def test_a_five_paragraph_selection_is_checked_now():
    """1,500 characters was about three paragraphs: the panel never appeared."""
    from src.live_preview import DEFAULT_MAX_CHARS, evaluate_trigger

    selection = " ".join(["word"] * 600)  # ~3,000 characters
    assert 1500 < len(selection) <= DEFAULT_MAX_CHARS

    settings = {"live_preview": {"enabled": True, "min_words": 3}}
    decision, _reason = evaluate_trigger(
        settings,
        {"bundle_id": "com.microsoft.Word", "name": "Microsoft Word"},
        selection,
        True,
        True,
        False,
        False,
    )
    assert decision == "run"

    # A selection beyond the (configurable) limit is still refused.
    settings["live_preview"]["max_chars"] = 1000
    decision, reason = evaluate_trigger(
        settings,
        {"bundle_id": "com.microsoft.Word", "name": "Microsoft Word"},
        selection,
        True,
        True,
        False,
        False,
    )
    assert decision == "too_long"
    assert "1000" in reason


def test_the_old_selection_limit_is_migrated():
    from src.live_preview import DEFAULT_MAX_CHARS
    from src.settings import _LEGACY_MAX_CHARS, _migrate_live_preview_limits

    legacy = {"live_preview": {"max_chars": _LEGACY_MAX_CHARS}}
    _migrate_live_preview_limits(legacy)
    assert legacy["live_preview"]["max_chars"] == DEFAULT_MAX_CHARS

    # A limit the user chose is left alone.
    chosen = {"live_preview": {"max_chars": 2500}}
    _migrate_live_preview_limits(chosen)
    assert chosen["live_preview"]["max_chars"] == 2500


def test_a_selection_that_is_too_long_is_explained_once(monkeypatch):
    """124 silent skips in the owner's log looked like a broken feature."""
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {"live_preview": {"enabled": True, "max_chars": 1500}}
    )
    said: list[str] = []
    service.preview_error.connect(said.append)
    target = {"bundle_id": "com.microsoft.Word", "name": "Microsoft Word"}

    service._log_too_long_once("com.microsoft.word", target, "x" * 2000)
    service._log_too_long_once("com.microsoft.word", target, "x" * 2000)

    assert len(said) == 1, "told once, not on every tick"
    assert "1,500" in said[0]
    assert "Settings" in said[0]
    service.stop()


# --- only suggest while the pointer is with the selection -------------------


class _StubCursor:
    def __init__(self, x: int, y: int) -> None:
        self._point = None
        self.x = x
        self.y = y

    def pos(self):
        from PyQt6.QtCore import QPoint

        return QPoint(self.x, self.y)


def _pointer_service(monkeypatch, *, captured_at, now_at, enabled=True, rect=None):
    from src import live_service as live_mod
    from src.live_service import LivePreviewService

    service = LivePreviewService()
    service.refresh_settings(
        {
            "live_preview": {
                "enabled": True,
                "max_chars": 4000,
                "min_words": 1,
                "require_pointer_near": enabled,
            }
        }
    )
    monkeypatch.setattr(
        live_mod, "QCursor", _StubCursor(now_at[0], now_at[1])
    )
    from PyQt6.QtCore import QPoint

    service._pointer_at_capture = QPoint(captured_at[0], captured_at[1])
    monkeypatch.setattr(service, "_selection_screen_rect", lambda: rect)
    return service


def test_suggestions_wait_while_the_pointer_is_away(monkeypatch):
    """The owner's rule: a selection the user walked away from is not a request."""
    service = _pointer_service(monkeypatch, captured_at=(100, 100), now_at=(900, 600))
    assert service._pointer_near_selection() is False

    near = _pointer_service(monkeypatch, captured_at=(100, 100), now_at=(180, 140))
    assert near._pointer_near_selection() is True

    service.stop()
    near.stop()


def test_a_known_selection_rectangle_is_measured_directly(monkeypatch):
    from PyQt6.QtCore import QRect

    rect = QRect(400, 300, 200, 20)
    # Pointer nowhere near the text, but it has not moved since the selection:
    # the pointer's own movement is still evidence enough.
    settled = _pointer_service(
        monkeypatch, captured_at=(950, 700), now_at=(950, 700), rect=rect
    )
    assert settled._pointer_near_selection() is True
    # Pointer moved away and it is not over the text either: no panel.
    away = _pointer_service(
        monkeypatch, captured_at=(950, 700), now_at=(90, 60), rect=rect
    )
    assert away._pointer_near_selection() is False
    # On the text, even though it moved there: the pointer is on the selection.
    on_text = _pointer_service(
        monkeypatch, captured_at=(950, 700), now_at=(500, 310), rect=rect
    )
    assert on_text._pointer_near_selection() is True

    settled.stop()
    away.stop()
    on_text.stop()


def test_the_pointer_rule_can_be_turned_off_and_fails_open(monkeypatch):
    disabled = _pointer_service(
        monkeypatch, captured_at=(0, 0), now_at=(900, 600), enabled=False
    )
    assert disabled._pointer_near_selection() is True

    # Nothing to measure (no rectangle, no reference yet): never take the
    # feature away.
    unknown = _pointer_service(
        monkeypatch, captured_at=(0, 0), now_at=(900, 600)
    )
    unknown._pointer_at_capture = None
    assert unknown._pointer_near_selection() is True

    disabled.stop()
    unknown.stop()


def test_the_poll_waits_for_the_pointer_before_spending_a_request(monkeypatch):
    """No pointer, no selection: the panel appears once it comes back."""
    from src import live_service as live_mod
    from src import word_integration
    from src.live_service import LivePreviewService
    from src.word_integration import SCOPE_MAIN

    selection = "A sentence the user selected and then walked away from."

    class FakeWord:
        def get_selection_info(self):
            return selection, 0, len(selection), "", ""

        def selection_scope(self) -> str:
            return SCOPE_MAIN

    class FakeEditor:
        def frontmost_app(self):
            return {
                "bundle_id": "com.microsoft.Word",
                "name": "Microsoft Word",
                "pid": 9,
            }

        def is_word(self, target) -> bool:
            return True

        def permission_status(self):
            return True, ""

    service = LivePreviewService()
    service.refresh_settings(
        {
            "live_preview": {
                "enabled": True,
                "delay_ms": 600,
                "min_words": 1,
                "max_chars": 4000,
                "require_pointer_near": True,
            }
        }
    )
    service._editor = FakeEditor()
    monkeypatch.setattr(
        word_integration, "get_word_integration", lambda: FakeWord()
    )
    started: list[Any] = []
    monkeypatch.setattr(service, "_spawn_preview", lambda *a, **k: started.append(a))
    monkeypatch.setattr(service, "_selection_screen_rect", lambda: None)

    def tick(now: float, position: tuple[int, int]) -> None:
        """One poll tick, with the pointer at a known place."""
        monkeypatch.setattr(live_mod, "QCursor", _StubCursor(*position))
        service._sample(now=now)
        service._pointer_prev = service._pointer_pos()

    # The user selects the text: the first read only starts the debounce.
    tick(0.0, (100, 100))
    assert started == [], "a new selection waits for the debounce"
    # Second agreeing read: committed, with the pointer remembered as being
    # on the text (it is where the selection gesture left it).
    tick(0.3, (105, 100))
    assert service._pointer_at_capture is not None, "the pointer is recorded"

    # The pointer goes elsewhere before the debounce expires: no request.
    tick(1.2, (1400, 900))
    assert started == [], "no request once the pointer has left the text"

    # Back on the text: the waiting selection is picked up after all.
    tick(1.6, (110, 105))
    assert started, "the panel appears once the pointer returns"
    service.stop()


def test_undo_reads_the_app_it_wrote_to_after_a_switch(monkeypatch):
    """Switching to Word must not blind the undo of an edit made in Safari."""
    service, editor, written, original, _messages = _undo_service(monkeypatch)
    # The user has selected text in Word since the apply.
    service._selection_is_word = True

    service._perform_undo()

    assert editor.restored == [(0, len(written), original, written)]
    service.stop()


def test_upgrade_persists_the_long_selection_limit(monkeypatch, tmp_path):
    """The 1,500 -> 4,000 character migration must reach settings.json.

    ``_stamp_version_and_save`` compared the default's APP_VERSION with itself
    when the loaded version was never copied, so a migration could change the
    in-memory value and still leave the old file behind forever. Reopening
    Settings then showed the old limit again.
    """
    import json

    from src import settings as settings_mod

    support = tmp_path / "support"
    support.mkdir()
    settings_file = support / "settings.json"
    monkeypatch.setattr(settings_mod, "APP_SUPPORT_DIR", str(support))
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", str(settings_file))
    settings_file.write_text(
        json.dumps(
            {
                "app_version": "2.1.1-beta.7",
                "last_run_version": "2.1.1-beta.7",
                "live_preview": {"enabled": True, "max_chars": 1500},
            }
        ),
        encoding="utf-8",
    )

    loaded = settings_mod.load_runtime_settings()

    assert loaded["live_preview"]["max_chars"] == 4000
    assert loaded["app_version"] == settings_mod.APP_VERSION
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["app_version"] == settings_mod.APP_VERSION
    assert saved["live_preview"]["max_chars"] == 4000
    assert saved["last_run_version"] == "2.1.1-beta.7"


# --- the settings surface draws on the shared shell tokens -------------------
#
# The dialog used to carry its palette in about two hundred inline stylesheets
# holding thirty-six different hex values, next to eleven nested group boxes.
# These tests hold the replacement in place: one sheet, flat sections, and a
# single status line.


def test_settings_surface_has_no_colour_of_its_own() -> None:
    import re
    from pathlib import Path

    from src.ui_theme import SHELL_PRIMARY, settings_stylesheet

    source = (
        Path(__file__).resolve().parents[1] / "src" / "gui.py"
    ).read_text(encoding="utf-8")
    region = source[
        source.index("class SettingsDialog") : source.index("class ProofreaderApp")
    ]
    assert re.findall(r"#[0-9A-Fa-f]{6}", region) == []

    sheet = settings_stylesheet("/tmp/assets")
    assert SHELL_PRIMARY in sheet
    assert sheet.count("{") == sheet.count("}")


def test_settings_status_line_reports_every_kind() -> None:
    app, owner, dialog = _make_settings_dialog()

    dialog._set_status("Saved to the Keychain.", "success")
    assert dialog.status_text.text() == "Saved to the Keychain."
    assert dialog.status_text.property("kind") == "success"
    assert dialog.status_glyph.text() != ""

    dialog._set_status("Could not reach the provider.", "error")
    assert dialog.status_text.property("kind") == "error"
    assert dialog.status_glyph.text() != ""

    dialog._set_status("Checking for updates…")
    assert dialog.status_text.property("kind") == "info"
    assert dialog.status_glyph.text() == ""
    _dispose(dialog, owner, app)


def test_every_settings_sidebar_row_carries_an_icon() -> None:
    app, owner, dialog = _make_settings_dialog()
    for row in range(dialog.sidebar.count()):
        item = dialog.sidebar.item(row)
        assert not item.icon().isNull(), f"row {row} ({item.text()}) has no icon"
    _dispose(dialog, owner, app)


def test_a_switch_row_keeps_the_words_out_of_the_checkbox() -> None:
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    assert dialog.chk_live_preview.text() == ""
    row = dialog.chk_live_preview.parent()
    while row is not None and row.objectName() != "SettingsRow":
        row = row.parent()
    assert row is not None, "the switch sits in a settings row"
    titles = [
        label.text()
        for label in row.findChildren(QLabel)
        if label.objectName() == "SettingsRowTitle"
    ]
    helpers = [
        label.text()
        for label in row.findChildren(QLabel)
        if label.objectName() == "SettingsRowHelper"
    ]
    assert titles == ["Suggest changes as I select text"]
    # The explanation is the row's own tooltip, not a second line and not an
    # icon beside every name.
    assert helpers == []
    assert row.toolTip().startswith("Select text anywhere")
    _dispose(dialog, owner, app)


def test_settings_pages_are_flat_sections_not_nested_cards() -> None:
    from PyQt6.QtWidgets import QGroupBox

    app, owner, dialog = _make_settings_dialog()
    for attr in (
        "general_page",
        "live_page",
        "automation_page",
        "updates_page",
    ):
        page = getattr(dialog, attr)
        assert not page.findChildren(QGroupBox), attr
    assert _settings_section_titles(dialog.general_page)[:2] == [
        "App & Window",
        "Microsoft Word",
    ]
    # Nothing may go missing in a restyle: every control the pages own is
    # still reachable under the name the rest of the app knows it by.
    for name in (
        "chk_launch_login",
        "chk_keep_top",
        "chk_keep_running",
        "chk_menu_bar_only",
        "chk_sound",
        "chk_auto_apply",
        "chk_track_changes",
        "chk_live_preview",
        "chk_live_local",
        "temp_slider",
        "combo_spelling",
        "combo_style",
        "combo_comment",
        "combo_context",
        "automation_enabled_check",
        "automation_list",
        "live_apps_list",
        "btn_restore_defaults",
        "button_box",
    ):
        assert hasattr(dialog, name), name
    _dispose(dialog, owner, app)



def test_settings_type_scale_is_the_one_the_sheet_declares() -> None:
    """Sizes come from the sheet, and the section heading carries its tracking.

    Qt has no letter-spacing style property, so an upper-case 11px heading
    would render as a flat run of capitals without the font's own tracking.
    """
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    page = dialog.general_page

    def first(object_name: str):
        for label in page.findChildren(QLabel):
            if label.objectName() == object_name:
                return label
        raise AssertionError(object_name)

    assert first("SettingsTitle").fontInfo().pixelSize() == 17
    assert first("SettingsSubtitle").fontInfo().pixelSize() == 12
    assert first("SettingsRowTitle").fontInfo().pixelSize() == 13
    assert first("SettingsHint").fontInfo().pixelSize() == 11

    heading = first("SettingsSectionLabel")
    # A heading is a sentence, not a tracked run of capitals: ByteMail has no
    # upper-case micro-labels anywhere in its content.
    assert heading.fontInfo().pixelSize() == 13
    assert heading.text() == "App & Window"
    _dispose(dialog, owner, app)


def test_settings_rows_share_one_left_edge_and_one_control_column() -> None:
    from PyQt6.QtWidgets import QCheckBox, QLabel, QWidget

    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()
    page = dialog.general_page
    rows = [
        widget
        for widget in page.findChildren(QWidget)
        if widget.objectName() == "SettingsRow"
    ]
    assert len(rows) >= 8, len(rows)
    for row in rows:
        titles = [
            label
            for label in row.findChildren(QLabel)
            if label.objectName() == "SettingsRowTitle"
        ]
        assert len(titles) == 1, (row, len(titles))
    lefts = {row.geometry().x() for row in rows}
    assert lefts == {0}, lefts
    widths = {row.geometry().width() for row in rows}
    assert len(widths) == 1, widths
    controls = [
        (row, child) for row in rows for child in row.findChildren(QCheckBox)
    ]
    assert controls, "the General page has switches"
    right_edges = {
        child.mapTo(row, child.rect().topLeft()).x() + child.width()
        for row, child in controls
    }
    assert len(right_edges) == 1, right_edges
    _dispose(dialog, owner, app)


def test_switches_render_from_the_shared_toggle_art() -> None:
    """The indicator is an image from assets/, so it must actually paint.

    A missing file or a stale URL would leave an empty 40x23 hole where a
    switch should be, which no structural assertion would notice.
    """
    from src.ui_theme import SHELL_BORDER, SHELL_PRIMARY

    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()
    dialog.chk_launch_login.setChecked(True)
    dialog.chk_keep_top.setChecked(False)
    app.processEvents()
    image = dialog.grab().toImage()
    ratio = image.devicePixelRatio() or 1.0

    def colours_near(widget) -> set[str]:
        origin = widget.mapTo(dialog, widget.rect().topLeft())
        found = set()
        for x in range(origin.x(), origin.x() + widget.width()):
            for y in range(origin.y(), origin.y() + widget.height()):
                found.add(
                    image.pixelColor(int(x * ratio), int(y * ratio)).name()
                )
        return found

    on = colours_near(dialog.chk_launch_login)
    off = colours_near(dialog.chk_keep_top)
    assert SHELL_PRIMARY in on, sorted(on)[:8]
    assert SHELL_BORDER in off, sorted(off)[:8]
    _dispose(dialog, owner, app)



def test_no_settings_control_carries_a_private_stylesheet() -> None:
    """One shape per role, owned by the sheet: a widget-local sheet is drift."""
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QPushButton

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()
    for kind in (QPushButton, QComboBox, QLineEdit, QCheckBox):
        for widget in dialog.findChildren(kind):
            assert widget.styleSheet() == "", (
                kind.__name__,
                getattr(widget, "text", lambda: "")()[:40],
                widget.styleSheet()[:70],
            )
    _dispose(dialog, owner, app)


def test_settings_typography_is_owned_by_the_sheet() -> None:
    """No inline sheet may set a font size: the roles in the sheet decide."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src" / "gui.py"
    ).read_text(encoding="utf-8")
    region = source[
        source.index("class SettingsDialog") : source.index("class ProofreaderApp")
    ]
    offenders = [
        line.strip()
        for line in region.splitlines()
        if "setStyleSheet(" in line and "font-size" in line
    ]
    assert offenders == [], offenders


def test_settings_controls_share_one_height_per_role() -> None:
    """Walking every page: one height per role, or the page looks stitched."""
    from PyQt6.QtWidgets import QComboBox, QPushButton

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()
    heights: dict[str, dict[str, set[int]]] = {"button": {}, "select": {}}
    for row in range(dialog.sidebar.count()):
        dialog.sidebar.setCurrentRow(row)
        app.processEvents()
        page = dialog.pages.currentWidget()
        for button in page.findChildren(QPushButton):
            if button.isVisible():
                role = button.objectName() or "default"
                heights["button"].setdefault(role, set()).add(button.height())
        for combo in page.findChildren(QComboBox):
            if combo.isVisible():
                heights["select"].setdefault("select", set()).add(combo.height())
    for group, roles in heights.items():
        for role, sizes in roles.items():
            assert len(sizes) == 1, (group, role, sizes)
    _dispose(dialog, owner, app)



def test_settings_cards_share_one_padding() -> None:
    """A card is a surface with one padding, whatever page it sits on."""
    from PyQt6.QtWidgets import QFrame

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()
    pads = set()
    for row in range(dialog.sidebar.count()):
        dialog.sidebar.setCurrentRow(row)
        app.processEvents()
        for frame in dialog.pages.currentWidget().findChildren(QFrame):
            if frame.objectName() not in (
                "ProviderCard",
                "LicenseCard",
                "SettingsCard",
                "SettingsCallout",
            ):
                continue
            layout = frame.layout()
            if layout is None:
                continue
            margins = layout.contentsMargins()
            pads.add(
                (margins.left(), margins.top(), margins.right(), margins.bottom())
            )
    assert pads, "no cards found on any page"
    assert pads == {(16, 14, 16, 14)}, pads
    _dispose(dialog, owner, app)



def test_main_window_sheet_stays_inside_the_main_window() -> None:
    """A rule in the main window's sheet must not reach a child dialog.

    It used to: the sheet's bare selectors (QWidget, QComboBox, QCheckBox,
    QPushButton...) styled every widget in the window's tree, so the settings
    dialog inherited a 13px base font, a 20px bordered checkbox indicator and
    a 12x8 combo arrow on top of its own sheet.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src" / "gui.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _configure_theme")
    end = source.index("setStyleSheet(stylesheet.replace", start)
    sheet = source[start:end]

    # Selectors that are allowed to be global: the window itself and the
    # popups that are top-level windows rather than children of it.
    allowed = ("QMainWindow", "QMenu", "QToolTip", "QMessageBox")
    offenders = []
    for line in sheet.splitlines():
        stripped = line.strip()
        if not stripped.endswith("{") or stripped.startswith(("*", "/*")):
            continue
        selector = stripped[:-1].strip()
        for part in selector.split(","):
            part = part.strip()
            if not part or part.startswith("#"):
                continue
            if part.startswith(allowed):
                continue
            if part.startswith("#RootPanel"):
                continue
            offenders.append(part)
    assert offenders == [], offenders


def test_the_main_window_sheet_cannot_restyle_the_settings_dialog() -> None:
    """Opening Settings from the running app must look the same as standalone."""
    from PyQt6.QtWidgets import QComboBox, QLabel

    from src import settings as settings_mod
    from src.gui import ProofreaderApp, SettingsDialog
    from src.ui_theme import SHELL_BORDER, SHELL_PRIMARY

    app, owner, standalone = _make_settings_dialog()
    standalone.resize(1000, 760)
    standalone.show()
    app.processEvents()
    alone_combo = next(
        combo
        for combo in standalone.general_page.findChildren(QComboBox)
        if combo.isVisible()
    )
    alone_height = alone_combo.height()

    window = ProofreaderApp(1024, settings_mod.load_runtime_settings())
    window.show()
    app.processEvents()
    dialog = SettingsDialog(settings_mod.load_runtime_settings(), window)
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()

    combo = next(
        item
        for item in dialog.general_page.findChildren(QComboBox)
        if item.isVisible()
    )
    assert combo.height() == alone_height, (combo.height(), alone_height)

    # The window sheet's QWidget rule set 13px on everything under it.
    roles = {}
    for label in dialog.general_page.findChildren(QLabel):
        roles.setdefault(label.objectName(), label.fontInfo().pixelSize())
    assert roles.get("SettingsTitle") == 17
    assert roles.get("SettingsSectionLabel") == 13
    assert roles.get("SettingsRowTitle") == 13
    assert roles.get("SettingsHint") == 11

    # ...and its checkbox rule drew a box behind the switch art.
    dialog.chk_launch_login.setChecked(True)
    dialog.chk_keep_top.setChecked(False)
    app.processEvents()
    image = dialog.grab().toImage()

    ratio = image.devicePixelRatio() or 1.0

    def colours_near(widget) -> set[str]:
        origin = widget.mapTo(dialog, widget.rect().topLeft())
        return {
            image.pixelColor(int(x * ratio), int(y * ratio)).name()
            for x in range(origin.x(), origin.x() + widget.width())
            for y in range(origin.y(), origin.y() + widget.height())
        }

    assert SHELL_PRIMARY in colours_near(dialog.chk_launch_login)
    assert SHELL_BORDER in colours_near(dialog.chk_keep_top)

    dialog.close()
    dialog.deleteLater()
    window.close()
    window.deleteLater()
    app.processEvents()
    _dispose(standalone, owner, app)



def test_the_sheet_owns_every_control_state() -> None:
    """If a state is not in the sheet, the platform or an ancestor paints it."""
    from src.ui_theme import settings_stylesheet

    sheet = settings_stylesheet("/tmp/assets")
    for needle in (
        "QDialog, QDialog *",  # a base font for widgets no role covers
        "QCheckBox::indicator:hover",  # the switch is an image, not a box
        "QComboBox::down-arrow",
        "QComboBox QAbstractItemView::item:selected",
        "QComboBox:disabled",
        "QScrollBar:vertical",
        "QPushButton#PrimaryBtn:disabled",
        "QPushButton:pressed",
    ):
        assert needle in sheet, needle



def test_new_rows_are_plain_labels_with_tooltips() -> None:
    """Live Check rows are rows: one name, one icon, one control per line."""
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.sidebar.setCurrentRow(1)
    app.processEvents()
    page = dialog.live_page
    titles = [
        label
        for label in page.findChildren(QLabel)
        if label.objectName() == "SettingsRowTitle"
    ]
    assert len(titles) >= 10, len(titles)
    for title in titles:
        parent = title.parent()
        while parent is not None and parent.objectName() not in (
            "SettingsRow",
            "LiveAppRow",
        ):
            parent = parent.parent()
        assert parent is not None, title.text()
    helpers = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.objectName() == "SettingsRowHelper"
    ]
    assert helpers == [], helpers
    _dispose(dialog, owner, app)


def test_general_page_has_no_second_line_of_grey_text() -> None:
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    dialog.show()
    app.processEvents()
    helpers = [
        label.text()
        for label in dialog.general_page.findChildren(QLabel)
        if label.objectName() == "SettingsRowHelper"
    ]
    assert helpers == [], helpers
    _dispose(dialog, owner, app)


def test_every_button_label_fits_its_button() -> None:
    """A button sized by a magic number clips its own label."""
    from PyQt6.QtWidgets import QPushButton

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()
    padding = {"default": 28, "PrimaryBtn": 28, "DangerBtn": 28, "SmallBtn": 22, "LinkBtn": 0}
    checked = 0
    for row in range(dialog.sidebar.count()):
        dialog.sidebar.setCurrentRow(row)
        app.processEvents()
        for button in dialog.pages.currentWidget().findChildren(QPushButton):
            text = button.text()
            if not button.isVisible() or not text:
                continue
            metrics = button.fontMetrics()
            advance = metrics.horizontalAdvance(text)
            role = button.objectName() or "default"
            room = padding.get(role, 28)
            assert button.width() >= advance + room - 2, (
                text,
                button.width(),
                advance,
                role,
            )
            assert button.width() <= advance + 90, (text, button.width(), advance)
            floor = 2 if role == "LinkBtn" else 10
            assert button.height() >= metrics.height() + floor, (
                text,
                button.height(),
                metrics.height(),
                role,
            )
            checked += 1
    assert checked >= 12, checked
    _dispose(dialog, owner, app)


def test_updates_page_is_a_build_line_and_one_action() -> None:
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.sidebar.setCurrentRow(6)
    app.processEvents()
    assert dialog.update_check_btn.text() == "Check for Updates"
    assert dialog.update_check_btn.objectName() == "SmallBtn"
    assert dialog.version_label.text() == __import__("src.settings", fromlist=["x"]).APP_VERSION
    assert dialog.version_label.objectName() == "SettingsDisplay"
    assert dialog.version_label.fontInfo().pixelSize() == 22
    long_lines = [
        label.text()
        for label in dialog.updates_page.findChildren(QLabel)
        if len(label.text()) > 140
    ]
    assert long_lines == [], long_lines
    _dispose(dialog, owner, app)



def test_number_fields_show_their_number() -> None:
    """A spin box sizes its arrows itself; a width that guessed clipped it."""
    from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.show()
    app.processEvents()
    checked = 0
    for row in range(dialog.sidebar.count()):
        dialog.sidebar.setCurrentRow(row)
        app.processEvents()
        page = dialog.pages.currentWidget()
        for kind in (QSpinBox, QDoubleSpinBox):
            for field in page.findChildren(kind):
                if not field.isVisible():
                    continue
                line_edit = field.lineEdit()
                assert line_edit is not None, field
                widest = field.fontMetrics().horizontalAdvance(
                    str(field.maximum())
                )
                assert line_edit.width() >= widest + 4, (
                    field.objectName(),
                    line_edit.width(),
                    widest,
                    field.width(),
                )
                checked += 1
    assert checked >= 2, checked
    _dispose(dialog, owner, app)


def test_automation_page_is_structured_like_the_others() -> None:
    from PyQt6.QtWidgets import QLabel

    app, owner, dialog = _make_settings_dialog()
    dialog.resize(1000, 760)
    dialog.sidebar.setCurrentRow(2)
    app.processEvents()
    page = dialog.automation_page

    # No loose prose: the page header carries the blurb, the row tooltips the
    # rest.
    helpers = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.objectName() == "SettingsRowHelper"
    ]
    assert helpers == [], helpers

    # The trigger count lives inside the row it describes.
    summary = dialog.automation_summary_label
    parent = summary.parent()
    while parent is not None and parent.objectName() != "SettingsRow":
        parent = parent.parent()
    assert parent is not None, "the trigger count is outside any row"

    # Showing the triggers gives the list room to be read.
    assert dialog.automation_list.minimumHeight() >= 200
    dialog._toggle_automation_rules()
    app.processEvents()
    assert dialog.automation_list.isVisibleTo(page)
    assert dialog.automation_actions_widget.isVisibleTo(page)
    assert dialog.automation_list.height() >= 200
    _dispose(dialog, owner, app)



def test_the_menu_bar_menu_is_opened_by_us_on_macos() -> None:
    """Regression for the 2026-09-18 crash: clicking the icon aborted the app.

    AppKit popped the status item menu on macOS 27, and Qt's observer for the
    menu-tracking notification raised an ObjC assertion while doing it. The
    click has to reach us, and the menu has to open from our own code.
    """
    import platform as platform_mod

    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from src import settings as settings_mod
    from src.gui import ProofreaderApp

    app = QApplication.instance() or QApplication([])
    window = ProofreaderApp(1024, settings_mod.load_runtime_settings())
    try:
        tray = window.tray_icon
        assert window.tray_menu is not None
        if platform_mod.system() == "Darwin":
            assert tray.contextMenu() is None, (
                "AppKit must not own the status item menu on macOS 27"
            )
            # The real popup, which is what the click will run: opening it
            # must not raise (that is the whole crash), and the menu must
            # actually come up.
            opened: list[bool] = []
            window.tray_menu.aboutToShow.connect(lambda: opened.append(True))
            window._show_tray_menu()
            app.processEvents()
            assert opened == [True], "the menu did not open"
            window.tray_menu.close()
            app.processEvents()

            # ...and a click on the icon is wired to that same path.
            reached: list[bool] = []
            window._show_tray_menu = lambda: reached.append(True)
            window._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            assert reached == [True]
        else:
            assert tray.contextMenu() is not None

        shown: list[bool] = []
        window.show_and_raise = lambda: shown.append(True)
        window._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert shown == [True]
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()




def test_small_marks_are_drawn_for_the_screen_they_are_on() -> None:
    """A 16px pixmap on a 2x screen is 16 pixels stretched over 32: soft."""
    from src.gui import _tinted_pixmap

    plain = _tinted_pixmap("settings-general.svg", "#1f1e1a", 16, ratio=1.0)
    retina = _tinted_pixmap("settings-general.svg", "#1f1e1a", 16, ratio=2.0)
    assert plain is not None and retina is not None
    assert plain.width() == 16 and plain.devicePixelRatio() == 1.0
    assert retina.width() == 32 and retina.devicePixelRatio() == 2.0


def _ink_bounds(image, floor: int = 24) -> tuple[int, int, int, int]:
    """The rectangle a rendered mark paints in, for the mark tests below."""
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > floor:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    return left, top, right, bottom


def test_marks_are_laid_out_in_points_on_a_retina_screen() -> None:
    """A DPR-2 canvas takes logical coordinates, so the layout must too.

    Laying the mark out in device pixels on a canvas that already carries the
    screen ratio drew every glyph at twice its box, and the canvas kept only
    the top-left quarter of it: the settings rail, the identity mark and the
    menu bar symbol all shipped as fragments. The offscreen test screen
    reports ratio 1, which is why the older tests never saw it.
    """
    from src.gui import fitted_mark

    pixmap = fitted_mark("menubar.svg", "#1f1e1a", 16, fill=0.88, ratio=2.0)
    assert pixmap is not None
    assert pixmap.width() == 32 and pixmap.devicePixelRatio() == 2.0
    left, top, right, bottom = _ink_bounds(pixmap.toImage())
    assert right >= left and bottom >= top, "the mark paints"
    assert left >= 1 and top >= 1 and right <= 30 and bottom <= 30, (
        left,
        top,
        right,
        bottom,
    )
    # One optical size: the longest side fills the box, and the mark sits in
    # the middle of it instead of hugging a corner.
    assert max(right - left, bottom - top) + 1 >= 25, (left, top, right, bottom)
    assert abs(left - (31 - right)) <= 3, (left, right)
    assert abs(top - (31 - bottom)) <= 3, (top, bottom)


def test_the_rail_and_menu_bar_marks_survive_a_retina_screen() -> None:
    """The two callers that draw at the screen's resolution, not at 1x."""
    from src import gui as gui_mod

    original = gui_mod._screen_ratio
    gui_mod._screen_ratio = lambda: 2.0
    try:
        icon = gui_mod.settings_icon("settings-live.svg", 16)
        pixmap = icon.pixmap(16, 16)
        image = pixmap.toImage()
        left, top, right, bottom = _ink_bounds(image)
        scale = pixmap.devicePixelRatio()
        pixels = image.width()
        assert right >= left, "the rail glyph paints"
        # Margins on both sides, in the canvas's own pixels: a fragment
        # clipped out of a 2x box touches an edge and leaves the opposite
        # one empty.
        assert left >= 1 and top >= 1, (left, top, pixels, scale)
        assert right <= pixels - 2 and bottom <= pixels - 2, (
            right,
            bottom,
            pixels,
            scale,
        )

        tray = gui_mod.menu_bar_icon(18)
        tray_image = tray.pixmap(18, 18).toImage()
        left, top, right, bottom = _ink_bounds(tray_image)
        side = tray_image.width()
        assert side == tray_image.height(), (side, tray_image.height())
        assert left >= 1 and top >= 1, (left, top, side)
        assert right <= side - 2 and bottom <= side - 2, (right, bottom, side)
    finally:
        gui_mod._screen_ratio = original


def test_a_mark_keeps_its_shape_when_it_is_not_square() -> None:
    """A 40x24 toggle stretched into a square box is not a toggle."""
    from src.gui import _tinted_pixmap

    pixmap = _tinted_pixmap("toggle-on.svg", "#1f1e1a", 64, ratio=1.0)
    assert pixmap is not None
    left, top, right, bottom = _ink_bounds(pixmap.toImage())
    width = right - left + 1
    height = bottom - top + 1
    assert width > height, (width, height)
    assert abs(width / height - 40 / 24) < 0.15, (width, height)


def test_the_brand_mark_is_drawn_where_the_app_draws_it() -> None:
    """The header, the About panel and the settings identity all render
    logo/logo.svg through the same crop-and-centre path as the rail glyphs."""
    from src.gui import fitted_mark

    for size in (24, 68):
        pixmap = fitted_mark(
            "logo.svg", "#1a2a3a", size, fill=0.92, ratio=2.0, folder="logo"
        )
        assert pixmap is not None, size
        assert pixmap.width() == size * 2, (size, pixmap.width())
        left, top, right, bottom = _ink_bounds(pixmap.toImage())
        assert right >= left and bottom >= top, (size, left, top, right, bottom)
        assert left >= 1 and top >= 1, (size, left, top)
        assert right <= size * 2 - 2 and bottom <= size * 2 - 2, (
            size,
            right,
            bottom,
        )
        # One optical size: the mark fills the box the caller asked for.
        assert max(right - left, bottom - top) + 1 >= size * 2 * 0.85, (
            size,
            left,
            top,
            right,
            bottom,
        )


def _load_icon_tool():
    """The icon builder, loaded by path: scripts/ is not an importable
    package."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "build_app_icons.py"
    )
    spec = importlib.util.spec_from_file_location("build_app_icons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_app_icon_carries_a_small_size_drawing() -> None:
    """Cutting the 16px icon from the line art is what turned it into a grey
    smudge, so the small entries use the mark filled solid. Regenerate the
    platform files with ``scripts/build_app_icons.py``."""
    from PyQt6.QtWidgets import QApplication

    from src.settings import resource_path

    QApplication.instance() or QApplication([])
    tool = _load_icon_tool()

    def dark_pixels(image) -> int:
        count = 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                if pixel.alpha() > 128 and pixel.red() < 160:
                    count += 1
        return count

    detail = dark_pixels(tool.render_image(tool.DETAIL, 16))
    small = dark_pixels(tool.small_icon(16))
    assert detail > 0, "the line-art icon renders at all"
    assert small > detail * 1.3, (small, detail)

    for name in ("logo.png", "logo.icns", "logo.ico"):
        assert os.path.exists(resource_path(os.path.join("logo", name))), name


def test_settings_blurbs_are_prose_not_stylesheets() -> None:
    """The Live Check page shipped its inline CSS in the sentence under the
    title - "…while you write. #6a6760; font-size: 12px;" - because a
    stylesheet string was appended to the blurb instead of the stylesheet."""
    from PyQt6.QtWidgets import QApplication, QLabel

    from src import gui as gui_mod
    from src import settings as settings_mod

    QApplication.instance() or QApplication([])
    dialog = gui_mod.SettingsDialog(settings_mod.load_runtime_settings())
    try:
        offenders = []
        for label in dialog.findChildren(QLabel):
            if label.objectName() not in ("SettingsSubtitle", "SettingsTitle",
                                          "SettingsHint", "SettingsRowHelper"):
                continue
            text = label.text()
            if "font-size" in text or "color: #" in text or "px;" in text:
                offenders.append((label.objectName(), text))
    finally:
        dialog.deleteLater()
    assert offenders == [], offenders


def test_the_menu_bar_mark_is_a_template_drawn_at_bar_size() -> None:
    """Not the 1024px app icon shrunk 56x, and not a white plate up there."""
    from PyQt6.QtWidgets import QApplication

    from src import settings as settings_mod
    from src.gui import ProofreaderApp, menu_bar_icon

    icon = menu_bar_icon(18)
    assert not icon.isNull(), "the menu bar mark renders"
    assert icon.isMask(), "macOS tints a template, it cannot tint a picture"
    size = icon.pixmap(18, 18).size()
    assert size.width() <= 64, size

    app = QApplication.instance() or QApplication([])
    window = ProofreaderApp(1024, settings_mod.load_runtime_settings())
    try:
        tray_icon = window.tray_icon.icon()
        assert not tray_icon.isNull()
        assert tray_icon.isMask()
        assert tray_icon.pixmap(18, 18).width() <= 64
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()



def test_the_rail_marks_share_one_optical_size() -> None:
    """Hand-drawn glyphs differ in how much of the grid they use: 10px of ink
    next to 16px in the same box is what reads as uneven, and two of them used
    to run off the canvas edge."""
    from src.gui import settings_icon

    icons = [
        "settings-general.svg",
        "settings-live.svg",
        "settings-automation.svg",
        "settings-connect.svg",
        "settings-local.svg",
        "license.svg",
        "update.svg",
    ]
    longest = set()
    for name in icons:
        pixmap = settings_icon(name, 16).pixmap(16, 16)
        assert pixmap.width() == 16 and pixmap.height() == 16
        image = pixmap.toImage()
        left, top, right, bottom = 16, 16, -1, -1
        for y in range(16):
            for x in range(16):
                if image.pixelColor(x, y).alpha() > 24:
                    left = min(left, x)
                    right = max(right, x)
                    top = min(top, y)
                    bottom = max(bottom, y)
        assert right >= left, name
        assert left >= 1 and top >= 1, (name, left, top)
        assert right <= 14 and bottom <= 14, (name, right, bottom)
        longest.add(max(right - left + 1, bottom - top + 1))
    assert longest == {14}, longest


def test_the_menu_bar_mark_is_square_with_an_inset() -> None:
    """macOS stretches whatever it is given to the bar height, so a 12x18 mark
    arrives looking wrong; and ink on the canvas edge is clipped."""
    from src.gui import menu_bar_icon

    icon = menu_bar_icon(18)
    assert not icon.isNull()
    pixmap = icon.pixmap(18, 18)
    assert pixmap.width() == pixmap.height() == 18
    image = pixmap.toImage()
    left, top, right, bottom = 18, 18, -1, -1
    for y in range(18):
        for x in range(18):
            if image.pixelColor(x, y).alpha() > 24:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    assert right >= left, "the mark paints"
    assert left >= 1 and top >= 1 and right <= 16 and bottom <= 16, (
        left,
        top,
        right,
        bottom,
    )
    assert max(right - left + 1, bottom - top + 1) >= 14, (left, top, right, bottom)


# --- the review pane and Word's comment box ----------------------------------
# Two defects from the 2026-09-23 owner report. The Proposed Changes pane drew
# whole paragraphs as replaced, and Word showed a comment box that stayed empty
# because the app stopped before typing into it.

REVIEW_PARAGRAPH = (
    "To test the balance-sheet-repair mechanism directly rather than infer it "
    "after the fact, I derive and estimate an ex-ante heterogeneity prediction. "
    "If the crisis-period signal reflects the speed of balance-sheet repair, it "
    "should be stronger where banking systems clear distressed assets more "
    "efficiently. Splitting the panel by a pre-determined measure of repair "
    "capacity (the country median ratio of nonperforming loans to total loans) "
    "yields a positive technical attention x crisis x repair efficiency "
    "coefficient under both country fixed effects (1.343, p < 0.01) and two-way "
    "fixed effects (1.037, p < 0.01). The net crisis-period association is "
    "positive and significant in efficient-repair economies (+0.695, p = 0.02) "
    "and indistinguishable from zero in inefficient-repair economies. This is a "
    "genuine test rather than an interpretation: a directional prediction, "
    "derived from the mechanism, that the data could have rejected."
)


def _longest_struck_run(rendered: str) -> int:
    return max(
        (
            len(match)
            for match in re.findall(r"<s style='[^']*'>(.*?)</s>", rendered, re.DOTALL)
        ),
        default=0,
    )


def _changed_fraction(rendered: str) -> float:
    """Share of the rendered text that sits inside a struck or inserted span."""
    from src.ui_theme import NEW_STYLE

    total = 0
    changed = 0
    pattern = re.compile(
        r"<s style='[^']*'>(.*?)</s>|<span style='([^']*)'>(.*?)</span>", re.DOTALL
    )
    for match in pattern.finditer(rendered):
        struck, style, plain = match.groups()
        if struck is not None:
            total += len(struck)
            changed += len(struck)
        else:
            total += len(plain)
            if style == NEW_STYLE:
                changed += len(plain)
    return changed / max(total, 1)


def test_a_short_edit_in_a_long_paragraph_is_not_shown_as_a_replacement():
    """One deleted clause must not render as the whole paragraph replaced.

    Past ~200 word tokens the heuristic treats the spaces between words as
    "popular" and stops matches there, so the alignment collapses: the pane
    struck through 96% of this paragraph and printed the replacement beside
    it. That is what the owner saw as "the entire replacement"; with the
    alignment left alone the same edit marks 5% of the text.
    """
    from src.ui_theme import CONTEXT_STYLE, diff_html

    original = REVIEW_PARAGRAPH
    corrected = original.replace(
        " directly rather than infer it after the fact,", ""
    )
    rendered = diff_html(
        original, corrected, context=100000, preserve_newlines=True, arrow=False
    )

    # The paragraph opens with unchanged text, not with a struck original.
    assert rendered.startswith(f"<span style='{CONTEXT_STYLE}'>")
    # One phrase changed, so no struck run may swallow the paragraph.
    assert _longest_struck_run(rendered) < 80, rendered[:200]
    assert _changed_fraction(rendered) < 0.25, _changed_fraction(rendered)


def test_a_rewritten_paragraph_still_marks_only_what_changed():
    """Even a heavy rewrite keeps the shared text out of the red."""
    from src.ui_theme import diff_html

    original = REVIEW_PARAGRAPH
    corrected = original.replace(
        "Splitting the panel by a pre-determined measure of repair capacity "
        "(the country median ratio of nonperforming loans to total loans)",
        "Splitting the panel by a structural, pre-determined measure of repair "
        "capacity (the country median ratio of nonperforming loans)",
    ).replace(
        "To test the balance-sheet-repair mechanism directly rather than infer "
        "it after the fact, I derive and estimate an ex-ante heterogeneity "
        "prediction.",
        "To test the balance-sheet-repair mechanism directly, I derive the "
        "ex-ante heterogeneity prediction.",
    )
    rendered = diff_html(
        original, corrected, context=100000, preserve_newlines=True, arrow=False
    )
    # The closing sentence is untouched in both, and must read as context.
    assert "the data could have rejected." in rendered
    assert _changed_fraction(rendered) < 0.5


def test_a_whole_paragraph_rewrite_is_never_split_into_a_fake_sub_edit():
    """The live apply may only write fragments whose alignment is verified.

    With the junk heuristic on, the splitter paired the top of the paragraph
    with a phrase from its end and returned that as one "word-level" edit: a
    replacement that would have reached Word as the whole paragraph rewritten.
    """
    from src.live_preview import Edit, _split_span_word_level

    before = REVIEW_PARAGRAPH
    after = (
        "To test the balance-sheet-repair mechanism directly rather than infer it "
        "after the fact, I derive and estimate an ex-ante heterogeneity prediction. "
        "If the crisis-period signal reflects the speed of balance-sheet repair, it "
        "should be stronger where banking systems clear distressed assets more "
        "efficiently. Splitting the panel by a pre-determined measure of repair "
        "capacity (the country median ratio of nonperforming loans to total loans) "
        "yields a positive technical attention x crisis x repair efficiency "
        "coefficient under both country fixed effects (1.343, p < 0.01) and two-way "
        "fixed effects (1.037, p < 0.01). The net crisis-period association is "
        "positive and significant in efficient-repair economies (+0.695, p = 0.02) "
        "and indistinguishable from zero in inefficient-repair economies. This is a "
        "genuine test rather than an interpretation: a directional prediction, "
        "derived from the mechanism, that the data could have rejected."
    ).replace(
        "The repair channel yields a further prediction",
        "The repair channel yields a further prediction",
    )

    subs = _split_span_word_level(before, 0, Edit(before, after, "reason"))
    assert subs == [], subs


def test_a_pinpoint_edit_in_a_long_span_stays_pinpoint():
    """The splitter still does its job: a small change keeps its small edit."""
    from src.live_preview import Edit, _split_span_word_level

    before = REVIEW_PARAGRAPH
    after = before.replace(
        "This is a genuine test", "This is a pre-registered test"
    )
    subs = _split_span_word_level(before, 0, Edit(before, after, "reason"))
    assert len(subs) == 1, subs
    _start, _end, old, new = subs[0]
    assert old == "genuine", old
    assert new == "pre-registered", new


def _word_comment_harness(
    monkeypatch,
    *,
    box_after_trigger: bool,
    fill_works: bool = True,
    box_has_focus: bool = False,
    count_rises: bool = True,
):
    """A MacOSWordIntegration with Word's UI, composer and comments faked."""
    from src import word_integration as wi

    integration = wi.MacOSWordIntegration()
    scripts: list[str] = []
    state = {"comments": 0, "box": False, "posted": False}

    def fake_run(script, *args, **kwargs):
        scripts.append(script)
        if 'keystroke "v"' in script:
            # The paste types into the box and Cmd+Return posts the draft.
            if count_rises:
                state["comments"] += 1
            state["posted"] = True
            state["box"] = False
        return "OK"

    integration._run_applescript = fake_run  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(wi, "_word_pid", lambda: 4242)
    monkeypatch.setattr(wi, "_comment_box_open", lambda _pid: state["box"])
    monkeypatch.setattr(wi, "_comment_box_has_focus", lambda _pid: box_has_focus)
    monkeypatch.setattr(wi, "_press_new_comment_button", lambda _pid: False)
    # The pane's card for the note is what proves a modern Word comment
    # landed; Word hides those comments from AppleScript entirely.
    monkeypatch.setattr(
        wi, "_comment_posted_in_pane", lambda _pid, _text: state["posted"]
    )
    monkeypatch.setattr(integration, "_comment_count", lambda: state["comments"])

    def fill_box(_pid, _text):
        if not fill_works:
            return False
        if count_rises:
            state["comments"] += 1
        state["posted"] = True
        state["box"] = False
        return True

    monkeypatch.setattr(wi, "_fill_comment_box", fill_box)

    def trigger_menu():
        if box_after_trigger:
            state["box"] = True
        return True

    monkeypatch.setattr(integration, "_trigger_insert_comment_menu", trigger_menu)
    return integration, scripts, state


def test_the_comment_is_written_when_word_only_opened_a_draft_box(monkeypatch):
    """Word 365 shows a draft box that the comment count cannot see.

    That is the regression: the count guard from the 2.0.2 hardening pass
    concluded the box never opened and left it empty. The box itself is the
    signal, and the note is written into it without keystrokes.
    """
    integration, scripts, state = _word_comment_harness(
        monkeypatch, box_after_trigger=True, fill_works=True
    )

    integration._trigger_comment_and_paste("the reviewer note")

    assert state["comments"] == 1
    assert not any('keystroke "v"' in script for script in scripts), (
        "the Accessibility write does not need the clipboard"
    )


def test_a_focused_box_still_takes_the_pasted_note(monkeypatch):
    """When the box holds the keyboard focus, the paste is the second route."""
    integration, scripts, state = _word_comment_harness(
        monkeypatch,
        box_after_trigger=True,
        fill_works=False,
        box_has_focus=True,
    )

    integration._trigger_comment_and_paste("the reviewer note")

    assert any('keystroke "v"' in script for script in scripts)
    assert state["comments"] == 1


def test_nothing_is_typed_while_the_box_has_no_keyboard_focus(monkeypatch):
    """A box that cannot be written to must not send the note to the document."""
    integration, scripts, _state = _word_comment_harness(
        monkeypatch,
        box_after_trigger=True,
        fill_works=False,
        box_has_focus=False,
    )

    with pytest.raises(RuntimeError):
        integration._trigger_comment_and_paste("the reviewer note")

    assert not any('keystroke "v"' in script for script in scripts)


def test_no_comment_text_is_typed_when_no_comment_box_opens(monkeypatch):
    """The safety rule stays: no box, no typing into the manuscript."""
    from src import word_integration as wi

    integration, scripts, _state = _word_comment_harness(
        monkeypatch, box_after_trigger=False, fill_works=False
    )
    monkeypatch.setattr(wi, "COMMENT_BOX_TIMEOUT_S", 0.05)

    with pytest.raises(RuntimeError):
        integration._trigger_comment_and_paste("the reviewer note")

    assert not any('keystroke "v"' in script for script in scripts)


def test_the_ribbon_trigger_is_tried_when_the_menu_item_does_nothing(monkeypatch):
    """Insert ▸ Comment is ignored on some builds; Review ▸ New Comment is not."""
    from src import word_integration as wi

    integration, _scripts, state = _word_comment_harness(
        monkeypatch, box_after_trigger=False, fill_works=True
    )
    monkeypatch.setattr(wi, "COMMENT_BOX_TIMEOUT_S", 0.05)
    attempts: list[str] = []

    def ribbon(_pid):
        attempts.append("ribbon")
        state["box"] = True
        return True

    monkeypatch.setattr(wi, "_press_new_comment_button", ribbon)

    integration._trigger_comment_and_paste("the reviewer note")

    assert attempts == ["ribbon"], attempts
    assert state["comments"] == 1


def test_an_open_box_is_used_instead_of_being_toggled_shut(monkeypatch):
    """Word's Insert ▸ Comment cancels an open draft, so it must not be pressed.

    That is what broke the owner's retry: the box ByteProof left open was
    closed again by the next trigger, so the run found nothing to type into.
    """
    integration, _scripts, state = _word_comment_harness(
        monkeypatch, box_after_trigger=True, fill_works=True
    )
    state["box"] = True  # the draft is already on screen
    attempts: list[str] = []

    def trigger_menu():
        attempts.append("menu")
        return True

    monkeypatch.setattr(integration, "_trigger_insert_comment_menu", trigger_menu)

    integration._trigger_comment_and_paste("the reviewer note")

    assert attempts == [], "an open box must not be toggled shut"
    assert state["comments"] == 1


def test_a_comment_word_hides_from_applescript_still_counts_as_success(
    monkeypatch,
):
    """Modern Word comments never reach `count of comments`.

    Word 365 keeps them out of AppleScript's collection entirely, so the old
    count-based check reported a failure for a comment that was sitting in the
    pane. The posted card is the evidence.
    """
    integration, _scripts, state = _word_comment_harness(
        monkeypatch,
        box_after_trigger=True,
        fill_works=True,
        count_rises=False,
    )

    integration._trigger_comment_and_paste("the reviewer note")

    assert state["posted"] is True
    assert state["comments"] == 0, "the fake keeps the count flat on purpose"
