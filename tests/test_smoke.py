"""Offline smoke tests for ByteProof.

Run from the project root:
    QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_smoke.py
"""

import hashlib
import importlib.util
import json
import os
import platform
import sys
import tempfile
import time
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
    # Polar is the canonical payment + license owner.
    assert settings.POLAR_ORGANIZATION_ID
    assert settings.POLAR_CHECKOUT_URL.startswith("https://buy.polar.sh/")
    assert "ByteProof Local (Qwen3)" in settings.PROVIDERS


def test_shared_text_normalization() -> None:
    """Word and generic-app comparison must use the same canonical rules."""
    from src.generic_editing import normalize_selection_text
    from src.logic import normalize_for_comparison
    from src.utils import normalize_text

    sample = "Hello\u00a0world \u201cquoted\u201d \u2018single\u2019\r\nnext"
    assert normalize_text(sample) == "Hello world \"quoted\" 'single' next"
    assert normalize_selection_text(sample) == normalize_text(sample)
    # Word comparison keeps original whitespace so diff offsets stay valid.
    assert normalize_for_comparison(sample) == (
        "Hello world \"quoted\" 'single'\nnext"
    )


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
    assert opened == [settings.POLAR_CHECKOUT_URL]


def test_licensing_roundtrip() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")
    original_secure_set = licensing._secure_store_set
    original_secure_get = licensing._secure_store_get
    original_secure_delete = licensing._secure_store_delete
    licensing._secure_store_set = lambda _value: None
    licensing._secure_store_get = lambda: None
    licensing._secure_store_delete = lambda: None

    spec = importlib.util.spec_from_file_location(
        "generate_license", os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    try:
        key = generator.generate_license_key("buyer@example.com", "unlimited", "")
        result = licensing.activate_license(key)
        assert result["valid"], result
        assert licensing.is_licensed()
        assert licensing.get_license_info()["status"] == "licensed"
    finally:
        licensing._secure_store_set = original_secure_set
        licensing._secure_store_get = original_secure_get
        licensing._secure_store_delete = original_secure_delete


def _patch_license_storage(tmpdir: str):
    """Patch licensing to use a temp license file and disable Keychain calls."""
    from src import licensing

    original_license = licensing._get_license_path
    original_secure_set = licensing._secure_store_set
    original_secure_get = licensing._secure_store_get
    original_secure_delete = licensing._secure_store_delete
    licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")
    licensing._secure_store_set = lambda _value: None
    licensing._secure_store_get = lambda: None
    licensing._secure_store_delete = lambda: None
    return original_license, original_secure_set, original_secure_get, original_secure_delete


def _restore_license_storage(originals) -> None:
    from src import licensing

    (
        licensing._get_license_path,
        licensing._secure_store_set,
        licensing._secure_store_get,
        licensing._secure_store_delete,
    ) = originals


def test_activation_from_url() -> None:
    from src import activation, licensing, polar

    tmpdir = tempfile.mkdtemp()
    originals = _patch_license_storage(tmpdir)
    original_activate = polar.activate_key
    original_org = activation.POLAR_ORGANIZATION_ID
    try:
        activation.POLAR_ORGANIZATION_ID = "org_test"
        polar.activate_key = lambda key, label: {
            "ok": True,
            "key": key,
            "activation_id": "act_123",
            "status": "granted",
            "limit_activations": 2,
            "expires_at": None,
        }

        result = activation.activate_from_url(
            "byteproof://activate?key=POLAR_TEST_KEY"
        )
        assert result["ok"], result
        assert licensing.is_licensed()
        assert licensing.get_license_info()["provider"] == "polar"
        assert licensing.get_license_info()["activation_id"] == "act_123"

        bad = activation.activate_from_url("https://example.com/not-byteproof")
        assert not bad["ok"]

        # Stripe session deep links are obsolete with Polar.
        session = activation.activate_from_url(
            "byteproof://activate?session=cs_test_123"
        )
        assert not session["ok"]
    finally:
        activation.POLAR_ORGANIZATION_ID = original_org
        polar.activate_key = original_activate
        _restore_license_storage(originals)


def test_polar_key_activation_and_validation() -> None:
    from src import activation, licensing, polar

    tmpdir = tempfile.mkdtemp()
    originals = _patch_license_storage(tmpdir)
    original_activate = polar.activate_key
    original_validate = polar.validate_key
    original_deactivate = polar.deactivate_key
    original_org = activation.POLAR_ORGANIZATION_ID
    try:
        activation.POLAR_ORGANIZATION_ID = "org_test"
        polar.activate_key = lambda key, label: {
            "ok": True,
            "key": key,
            "activation_id": "act_456",
            "status": "granted",
            "limit_activations": 2,
            "expires_at": None,
        }
        result = activation.activate_with_key("BP_TEST_1234")
        assert result["ok"], result
        assert licensing.is_licensed()

        polar.validate_key = lambda key, activation_id: {
            "status": "granted",
            "activation": {"id": activation_id},
        }
        assert activation.validate_license_remote()["ok"] is True

        polar.validate_key = lambda key, activation_id: {"status": "revoked"}
        remote = activation.validate_license_remote()
        assert remote["ok"] is False
        assert "revoked" in remote["error"]

        polar.deactivate_key = lambda key, activation_id: {"ok": True}
        deactivated = activation.deactivate_license()
        assert deactivated["ok"]
        assert not licensing.is_licensed()
    finally:
        polar.activate_key = original_activate
        polar.validate_key = original_validate
        polar.deactivate_key = original_deactivate
        activation.POLAR_ORGANIZATION_ID = original_org
        _restore_license_storage(originals)


def test_polar_activate_parses_top_level_activation() -> None:
    from src import polar

    calls: list[tuple[str, dict]] = []
    original_post = polar._post

    def fake_post(path: str, payload: dict) -> dict:
        calls.append((path, payload))
        return {
            "id": "act_789",
            "label": "test-machine",
            "license_key": {
                "status": "granted",
                "limit_activations": 2,
                "expires_at": None,
            },
        }

    polar._post = fake_post
    try:
        result = polar.activate_key("BP_TEST", label="test-machine")
        assert result["activation_id"] == "act_789"
        assert result["status"] == "granted"
        assert result["limit_activations"] == 2
        assert calls[0][0] == "/v1/customer-portal/license-keys/activate"
        assert calls[0][1]["conditions"]["machine_fingerprint"]
    finally:
        polar._post = original_post


def test_developer_email_activation() -> None:
    from src import activation, licensing
    from src.settings import DEVELOPER_EMAILS

    tmpdir = tempfile.mkdtemp()
    originals = _patch_license_storage(tmpdir)
    original_org = activation.POLAR_ORGANIZATION_ID
    try:
        dev = activation.activate_with_key(DEVELOPER_EMAILS[0])
        assert dev["ok"], dev
        assert licensing.is_licensed()
        assert licensing.get_license_info()["provider"] == "dev"

        # With Polar configured, a customer email is not a valid activation.
        activation.POLAR_ORGANIZATION_ID = "org_test"
        stranger = activation.activate_with_key("somebody@example.com")
        assert not stranger["ok"]
        assert "license key" in stranger["error"].lower()
    finally:
        activation.POLAR_ORGANIZATION_ID = original_org
        _restore_license_storage(originals)


def test_legacy_email_and_signed_key_fallback() -> None:
    from src import activation, licensing

    tmpdir = tempfile.mkdtemp()
    originals = _patch_license_storage(tmpdir)
    spec = importlib.util.spec_from_file_location(
        "generate_license", os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    original_post = activation._post_json
    original_org = activation.POLAR_ORGANIZATION_ID
    try:
        # Polar is not configured: support-issued signed keys still work.
        activation.POLAR_ORGANIZATION_ID = ""
        key = generator.generate_license_key("paid@example.com", "unlimited", "")
        result = activation.activate_with_key(key)
        assert result["ok"], result
        assert result["email"] == "paid@example.com"
        assert licensing.is_licensed()

        # Legacy email activation falls back to the ByteMind server.
        licensing.delete_license_data()
        activation._post_json = lambda url, payload: {
            "ok": True,
            "license_key": key,
        }
        email_result = activation.activate_with_key("paid@example.com")
        assert email_result["ok"], email_result
        assert email_result["email"] == "paid@example.com"
    finally:
        activation.POLAR_ORGANIZATION_ID = original_org
        activation._post_json = original_post
        _restore_license_storage(originals)


def test_strict_editing_rules_contract() -> None:
    from src.logic import with_strict_editing_rules

    prompt = with_strict_editing_rules("You are an editor.")
    assert "OUTPUT CONTRACT" in prompt
    assert "never refuse to edit" in prompt.lower()
    # Appending twice must not duplicate the contract.
    assert with_strict_editing_rules(prompt).count("OUTPUT CONTRACT") == 1


def test_tampered_license_rejected() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    original_license = licensing._get_license_path
    original_secure_set = licensing._secure_store_set
    original_secure_get = licensing._secure_store_get
    original_secure_delete = licensing._secure_store_delete
    try:
        licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")
        licensing._secure_store_set = lambda _value: None
        licensing._secure_store_get = lambda: None
        licensing._secure_store_delete = lambda: None

        # A forged license file with a fake key and the correct machine
        # fingerprint must NOT be accepted.
        licensing._save_license_data(
            {
                "email": "buyer@example.com",
                "expiry": None,
                "key": "not-a-real-key",
                "activated_at": time.time(),
                "machine_fp": licensing._get_machine_fingerprint(),
            }
        )
        assert not licensing.is_licensed()
        assert licensing.get_license_info()["status"] == "unlicensed"
    finally:
        licensing._get_license_path = original_license
        licensing._secure_store_set = original_secure_set
        licensing._secure_store_get = original_secure_get
        licensing._secure_store_delete = original_secure_delete


def test_secure_store_fallback_restores_license() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    original_license = licensing._get_license_path
    original_secure_set = licensing._secure_store_set
    original_secure_get = licensing._secure_store_get
    original_secure_delete = licensing._secure_store_delete
    try:
        licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")
        licensing._secure_store_set = lambda _value: None
        licensing._secure_store_delete = lambda: None

        spec = importlib.util.spec_from_file_location(
            "generate_license", os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
        )
        assert spec is not None and spec.loader is not None
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        machine_fp = licensing._get_machine_fingerprint()
        key = generator.generate_license_key("keychain@example.com", "unlimited", machine_fp)

        # License file is missing, but the OS credential store has the payload.
        licensing._secure_store_get = lambda: json.dumps(
            {
                "email": "keychain@example.com",
                "expiry": None,
                "key": key,
                "activated_at": time.time(),
                "machine_fp": machine_fp,
            }
        )
        assert licensing.is_licensed()
        assert licensing.get_license_info()["email"] == "keychain@example.com"
    finally:
        licensing._get_license_path = original_license
        licensing._secure_store_set = original_secure_set
        licensing._secure_store_get = original_secure_get
        licensing._secure_store_delete = original_secure_delete


def test_server_activation_core_two_machine_limit() -> None:
    from pathlib import Path

    server_dir = os.path.join(PROJECT_ROOT, "server")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    from activation_core import (
        deactivate_machine,
        load_json,
        register_machine,
        validate_machine,
    )

    tmpdir = Path(tempfile.mkdtemp())
    licenses_path = tmpdir / "licenses.json"
    issued: list[tuple[str, str]] = []

    def issue_key(email: str, machine_fp: str) -> str:
        issued.append((email, machine_fp))
        return f"key-{email}-{machine_fp}"

    ok1, key1, err1 = register_machine(
        licenses_path, "Buyer@Example.com", "fp-a", issue_key
    )
    assert ok1 and err1 is None
    assert key1 == "key-buyer@example.com-fp-a"

    ok2, key2, _ = register_machine(licenses_path, "buyer@example.com", "fp-b", issue_key)
    assert ok2 and key2 == "key-buyer@example.com-fp-b"

    # Third computer is rejected.
    ok3, key3, err3 = register_machine(
        licenses_path, "buyer@example.com", "fp-c", issue_key
    )
    assert not ok3 and key3 is None
    assert "device limit" in (err3 or "").lower()

    # Re-requesting an existing machine returns its key without issuing a new one.
    ok_re, key_re, _ = register_machine(
        licenses_path, "buyer@example.com", "fp-a", issue_key
    )
    assert ok_re and key_re == key1
    assert len(issued) == 2

    # Deactivation frees a slot.
    assert deactivate_machine(licenses_path, "buyer@example.com", "fp-b")
    ok4, key4, _ = register_machine(
        licenses_path, "buyer@example.com", "fp-c", issue_key
    )
    assert ok4 and key4 == "key-buyer@example.com-fp-c"

    state = validate_machine(
        load_json(licenses_path), "buyer@example.com", "fp-c"
    )
    assert state["valid"] is True
    assert state["device_count"] == 2
    assert state["device_limit"] == 2


def test_emailer_graceful_and_link_build() -> None:
    server_dir = os.path.join(PROJECT_ROOT, "server")
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    import emailer

    keys = (
        "BYTEPROOF_SMTP_HOST",
        "BYTEPROOF_SMTP_PORT",
        "BYTEPROOF_SMTP_USER",
        "BYTEPROOF_SMTP_PASSWORD",
        "BYTEPROOF_SMTP_FROM",
    )
    saved = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        assert emailer.send_activation_email("buyer@example.com", "cs_test_1") is False
        os.environ["BYTEPROOF_SMTP_USER"] = "sender@bytemind.co.nz"
        msg = emailer._build_message("buyer@example.com", "cs_test_1")
        body = msg.get_body(preferencelist=("plain",)).get_content()
        assert "byteproof://activate?session=cs_test_1" in body
        assert msg["To"] == "buyer@example.com"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_parse_dev_emails() -> None:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "server"))
    from activation_core import parse_dev_emails

    assert parse_dev_emails(" a@b.c, d@e.f g@h.i ") == {
        "a@b.c",
        "d@e.f",
        "g@h.i",
    }
    assert parse_dev_emails("") == set()
    assert parse_dev_emails(None) == set()


def test_register_machine_developer_unlimited() -> None:
    from pathlib import Path

    sys.path.insert(0, os.path.join(PROJECT_ROOT, "server"))
    from activation_core import register_machine

    licenses_path = Path(tempfile.mkdtemp()) / "licenses.json"

    def issue(email: str, machine_fp: str) -> str:
        return f"key-{email}-{machine_fp}"

    for fp in ("fp-1", "fp-2", "fp-3", "fp-4"):
        ok, key, err = register_machine(
            licenses_path,
            "dev@bytemind.co.nz",
            fp,
            issue,
            device_limit=None,
        )
        assert ok and key == f"key-dev@bytemind.co.nz-{fp}" and err is None


def test_cache_cleanup_logs_and_stale_partials() -> None:
    import src.cache_cleanup as cc

    tmpdir = tempfile.mkdtemp()
    log = os.path.join(tmpdir, "big.log")
    with open(log, "w") as f:
        f.write("x" * (cc.MAX_LOG_BYTES + 1000))

    original_logs = cc.LOG_PATHS
    cc.LOG_PATHS = (log,)
    try:
        freed = cc.cleanup_logs()
        assert freed > 0
        assert os.path.getsize(log) <= cc.LOG_TAIL_BYTES + 1
    finally:
        cc.LOG_PATHS = original_logs

    models_dir = os.path.join(tmpdir, "models")
    os.makedirs(models_dir)
    old_part = os.path.join(models_dir, "old.gguf.part")
    fresh_part = os.path.join(models_dir, "fresh.gguf.part")
    with open(old_part, "wb") as f:
        f.write(b"a" * 100)
    with open(fresh_part, "wb") as f:
        f.write(b"b" * 100)
    old_ts = time.time() - (8 * 86400)
    os.utime(old_part, (old_ts, old_ts))

    original_model_dir = cc.LOCAL_MODEL_DIR
    original_runtime_dir = cc.RUNTIME_DIR
    cc.LOCAL_MODEL_DIR = models_dir
    cc.RUNTIME_DIR = os.path.join(tmpdir, "runtime")
    try:
        freed = cc.cleanup_stale_partials()
        assert freed == 100
        assert not os.path.exists(old_part)
        assert os.path.exists(fresh_part)
    finally:
        cc.LOCAL_MODEL_DIR = original_model_dir
        cc.RUNTIME_DIR = original_runtime_dir


def test_cache_cleanup_keeps_only_two_models() -> None:
    import src.cache_cleanup as cc
    from src import local_model

    tmpdir = tempfile.mkdtemp()
    paths = {}
    removed: list[str] = []
    for mid in ("a", "b", "c"):
        path = os.path.join(tmpdir, mid + ".gguf")
        with open(path, "wb") as f:
            f.write(mid.encode() * 100)
        paths[mid] = path

    original_installed = cc.installed_model_ids
    original_mtime = cc._model_mtime
    original_path = local_model.model_path
    original_remove = local_model.remove_model
    try:
        cc.installed_model_ids = lambda: ["a", "b", "c"]
        cc._model_mtime = lambda mid: {"a": 1.0, "b": 2.0, "c": 3.0}[mid]
        local_model.model_path = lambda mid: paths[mid]
        local_model.remove_model = lambda mid: removed.append(mid) or True

        freed = cc.remove_oldest_inactive_models(
            active_model_id="c",
            max_models=2,
        )
        assert removed == ["a"]
        assert freed == len(b"a" * 100)
    finally:
        cc.installed_model_ids = original_installed
        cc._model_mtime = original_mtime
        local_model.model_path = original_path
        local_model.remove_model = original_remove


def test_conversational_reply_guard() -> None:
    from src.logic import _looks_like_conversational_reply

    refusal = (
        "I notice you're asking for business strategy advice, but I must clarify "
        "my role here. I am an Academic Editor and Writing Coach and I cannot "
        "provide business advice."
    )
    assert _looks_like_conversational_reply(refusal)

    plain_question = "What is the best way to improve academic writing?"
    assert not _looks_like_conversational_reply(plain_question)

    normal_edit = (
        "We are writing to inform you that, in accordance with our records, "
        "your account currently shows an outstanding balance."
    )
    assert not _looks_like_conversational_reply(normal_edit)


def test_proofread_prompt_uses_markers_and_retries_conversational_reply() -> None:
    import json as json_module

    from src import logic

    captured: list[dict] = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "I notice you're asking for business strategy advice, "
                            "but I am an Academic Editor and cannot help."
                        )
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "I am writing to ask whether you can review my draft. "
                            "Please advise on the best approach."
                        )
                    }
                }
            ]
        },
    ]

    original_urlopen = logic.urllib.request.urlopen

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def read(self) -> bytes:
            return json_module.dumps(self._payload).encode("utf-8")

    def fake_urlopen(request, timeout=0, context=None):
        body = json_module.loads(request.data)
        captured.append(body)
        return FakeResponse(responses[len(captured) - 1])

    logic.urllib.request.urlopen = fake_urlopen
    try:
        result = logic.proofread_with_provider(
            "I want you to review the Byteproof app. What should I do?",
            "sk-test",
            512,
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            provider_name="DeepSeek",
        )
    finally:
        logic.urllib.request.urlopen = original_urlopen

    # First response was conversational, so the request must have been retried.
    assert len(captured) == 2
    first_user = captured[0]["messages"][1]["content"]
    first_system = captured[0]["messages"][0]["content"]
    assert logic.TEXT_BEGIN_MARKER in first_user
    assert logic.TEXT_END_MARKER in first_user
    assert "OUTPUT CONTRACT" in first_system

    retry_system = captured[1]["messages"][0]["content"]
    retry_user = captured[1]["messages"][1]["content"]
    assert "CRITICAL INSTRUCTION" in retry_system
    assert "CRITICAL INSTRUCTION" in retry_user
    assert result == (
        "I am writing to ask whether you can review my draft. "
        "Please advise on the best approach."
    )


def test_trial_expired_enters_free_mode() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import gui, settings

    window = gui.ProofreaderApp(1024, settings.load_runtime_settings())
    window.settings["active_provider"] = settings.LOCAL_MODEL_PROVIDER
    original_licensed = gui.is_licensed
    original_ensure = gui.ensure_trial_started
    original_status = gui.get_trial_status
    original_access = gui.get_access_status
    original_dialog = window._show_purchase_dialog
    try:
        gui.is_licensed = lambda: False
        gui.ensure_trial_started = lambda: 1000000000.0
        gui.get_trial_status = lambda _ts: {
            "in_trial": False,
            "days_left": 0,
            "trial_expired": True,
        }
        window._show_purchase_dialog = lambda *a, **k: None

        free_access = {
            "tier": "free",
            "licensed": False,
            "in_trial": False,
            "trial_expired": True,
            "free_mode_allowed": True,
            "daily_count": 1,
            "daily_limit": 3,
            "trial_usage": 12,
            "total_usage": 20,
        }
        gui.get_access_status = lambda: free_access

        window._update_proofread_button()
        assert window.run_btn.isEnabled()
        assert "Free mode" in window.run_btn.text()
        assert "2 left today" in window.run_btn.text()
        assert window._check_license_access() is True

        free_access["free_mode_allowed"] = False
        free_access["daily_count"] = free_access["daily_limit"]
        assert window._check_license_access() is False
        window._update_proofread_button()
        assert window.run_btn.isEnabled()
        assert "Free Limit Reached" in window.run_btn.text()

        # Cloud providers stay locked in free mode.
        free_access["free_mode_allowed"] = True
        window.settings["active_provider"] = "DeepSeek"
        assert window._check_license_access() is False
    finally:
        gui.is_licensed = original_licensed
        gui.ensure_trial_started = original_ensure
        gui.get_trial_status = original_status
        gui.get_access_status = original_access
        window._show_purchase_dialog = original_dialog
        window.close()


def test_free_mode_daily_cap() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    original_usage = licensing._get_usage_path
    original_license = licensing._get_license_path
    original_secure_get = licensing._secure_store_get
    original_secure_delete = licensing._secure_store_delete
    original_secondary_read = licensing._trial_secondary_read
    original_secondary_write = licensing._trial_secondary_write
    original_ensure = licensing.ensure_trial_started
    try:
        licensing._get_usage_path = lambda: os.path.join(tmpdir, "usage.json")
        licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")
        licensing._secure_store_get = lambda: None
        licensing._secure_store_delete = lambda: None
        licensing._trial_secondary_read = lambda: None
        licensing._trial_secondary_write = lambda _ts: None
        # Trial expired 8 days ago.
        licensing.ensure_trial_started = lambda: time.time() - (8 * 86400)

        status = licensing.get_access_status()
        assert status["tier"] == "free"
        assert status["free_mode_allowed"] is True

        for _ in range(3):
            licensing.record_proofread_usage()

        status = licensing.get_access_status()
        assert status["daily_count"] == 3
        assert status["free_mode_allowed"] is False
        assert status["trial_usage"] == 0
        assert status["total_usage"] == 3
    finally:
        licensing._get_usage_path = original_usage
        licensing._get_license_path = original_license
        licensing._secure_store_get = original_secure_get
        licensing._secure_store_delete = original_secure_delete
        licensing._trial_secondary_read = original_secondary_read
        licensing._trial_secondary_write = original_secondary_write
        licensing.ensure_trial_started = original_ensure


def test_ensure_trial_started_uses_earliest_secondary() -> None:
    from src import licensing

    tmpdir = tempfile.mkdtemp()
    original_license = licensing._get_license_path
    original_secondary_read = licensing._trial_secondary_read
    original_secondary_write = licensing._trial_secondary_write
    try:
        licensing._get_license_path = lambda: os.path.join(tmpdir, "license.json")

        # Secondary location holds an older start than the primary file:
        # the earlier timestamp must win so deleting one file cannot reset.
        primary_path = os.path.join(tmpdir, ".trial_start")
        with open(primary_path, "w") as f:
            f.write(str(999999.0))
        licensing._trial_secondary_read = lambda: 123456.0
        written: list[float] = []
        licensing._trial_secondary_write = lambda ts: written.append(ts)

        assert licensing.ensure_trial_started() == 123456.0
        with open(primary_path) as f:
            assert float(f.read().strip()) == 123456.0
        assert written == []

        # Only secondary exists: primary is created from it.
        os.remove(primary_path)
        assert licensing.ensure_trial_started() == 123456.0
        with open(primary_path) as f:
            assert float(f.read().strip()) == 123456.0
    finally:
        licensing._get_license_path = original_license
        licensing._trial_secondary_read = original_secondary_read
        licensing._trial_secondary_write = original_secondary_write


def test_hotkey_conversion() -> None:
    from src.gui import SettingsDialog

    if platform.system() == "Darwin":
        # On macOS, Qt reports the Command key as "Ctrl" and the physical
        # Control key as "Meta". The round trip must preserve the real intent.
        assert SettingsDialog.pynput_to_qt(None, "<cmd>+<shift>+;") == "Ctrl+Shift+;"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.qt_to_pynput(None, "Ctrl+Shift+;") == "<cmd>+<shift>+;"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.pynput_to_qt(None, "<ctrl>+<shift>+'") == "Meta+Shift+'"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.qt_to_pynput(None, "Meta+Shift+'") == "<ctrl>+<shift>+'"  # pyright: ignore[reportArgumentType]
    else:
        assert SettingsDialog.pynput_to_qt(None, "<cmd>+<shift>+;") == "Ctrl+Shift+;"  # pyright: ignore[reportArgumentType]
        assert SettingsDialog.qt_to_pynput(None, "Ctrl+Shift+;") == "<ctrl>+<shift>+;"  # pyright: ignore[reportArgumentType]


def test_mac_hotkey_default_migration() -> None:
    from src import settings

    if platform.system() != "Darwin":
        return

    tmpdir = tempfile.mkdtemp()
    original_file = settings.SETTINGS_FILE
    original_dir = settings.APP_SUPPORT_DIR
    try:
        settings.SETTINGS_FILE = os.path.join(tmpdir, "settings.json")
        settings.APP_SUPPORT_DIR = tmpdir
        with open(settings.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "general": {
                        "open_hotkey": "<ctrl>+<shift>+;",
                        "proofread_hotkey": "<ctrl>+<shift>+'",
                    }
                },
                f,
            )
        loaded = settings.load_runtime_settings()
        assert loaded["general"]["open_hotkey"] == "<cmd>+<shift>+;"
        assert loaded["general"]["proofread_hotkey"] == "<cmd>+<shift>+'"
        assert loaded.get("app_version") == settings.APP_VERSION
    finally:
        settings.SETTINGS_FILE = original_file
        settings.APP_SUPPORT_DIR = original_dir


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


def test_fieldcode_pattern_matches_word_control_chars() -> None:
    from src.logic import (
        _FIELDCODE_PATTERN,
        _find_protected_spans,
        protect_special_chars,
        restore_special_chars,
    )

    code = (
        " ADDIN EN.CITE <EndNote><Cite>"
        "<DisplayText>(Al-Qahtani & Elgharbawy, 2020)</DisplayText>"
        "</Cite></EndNote>"
    )
    text = "sectors \x13" + code + " \x14(Al-Qahtani & Elgharbawy, 2020)\x15. A company"
    start = text.index("\x13")
    match = _FIELDCODE_PATTERN.match(text, start)
    assert match is not None
    assert match.end() > text.index("\x15")

    protected, replacements = protect_special_chars(text)
    assert "ADDIN" not in protected
    assert "Al-Qahtani" not in protected
    assert "{{OBJ_0}}" in protected
    assert restore_special_chars(protected, replacements) == text

    spans = _find_protected_spans(text)
    assert spans == [(start, match.end())]


def test_citation_fields_are_masked_and_mapped() -> None:
    from src.logic import (
        _CITATION_SPANS,
        _find_protected_spans,
        _locate_field_result_spans,
        _merge_spans,
        _visible_to_doc,
        protect_special_chars,
        restore_special_chars,
    )

    current_text = "abc (Al-Qahtani & Elgharbawy, 2020) def"
    citation = "(Al-Qahtani & Elgharbawy, 2020)"
    field_spans = [(54, 185, citation)]
    result_spans = _locate_field_result_spans(current_text, field_spans, 50)
    assert result_spans == [(4, 35)]

    plain_spans = [
        (m.start(), m.end()) for m in _CITATION_SPANS.finditer(current_text)
    ]
    assert plain_spans == [(4, 35)]
    mask_spans = _merge_spans(result_spans + plain_spans)
    assert mask_spans == [(4, 35)]

    protected, replacements = protect_special_chars(
        current_text, extra_spans=mask_spans
    )
    assert "Al-Qahtani" not in protected
    assert "{{OBJ_0}}" in protected
    assert restore_special_chars(protected, replacements) == current_text

    protected_spans = _find_protected_spans(
        current_text, extra_spans=mask_spans
    )
    assert protected_spans == [(3, 36)]

    mapper = _visible_to_doc(50, field_spans, result_spans)
    assert mapper(0) == 50
    assert mapper(4) == 54
    assert mapper(35) == 185
    assert mapper(36) == 186
    assert mapper(len(current_text)) == 50 + len(current_text) + 100


def test_field_result_spans_use_document_positions_not_text_search() -> None:
    from src.logic import _locate_field_result_spans

    # The first "(X, 2020)" is plain text; the second is the Word field.
    # A text search would wrongly map the field to the first occurrence.
    current_text = "abc (X, 2020) def (X, 2020) ghi"
    citation = "(X, 2020)"
    # Selection starts at 50. Visible text before the field is 18 chars, so
    # the field begins at doc 68. Hidden code length 10 + result 9 + end char
    # 1 -> doc_end 88.
    field_spans = [(68, 88, citation)]
    result_spans = _locate_field_result_spans(current_text, field_spans, 50)
    assert result_spans == [(18, 27)]
    assert current_text[18:27] == citation


def test_long_citation_result_maps_fully() -> None:
    from src.logic import _locate_field_result_spans

    # A citation whose visible result is longer than the old 500-char scan
    # bound must still map to its full span (regression for the page-sized
    # scan limit).
    prefix = "First paragraph text. "  # 22 visible chars before the field
    suffix = " Second paragraph text."
    citation = "Long citation result word. " * 90  # 26 * 90 = 2340 chars
    current_text = prefix + citation + suffix
    start_offset = 50
    begin_doc = start_offset + len(prefix)
    code_len = 30
    result_start = begin_doc + code_len + 1
    doc_end = result_start + len(citation) + 1
    field_spans = [(begin_doc, doc_end, citation)]

    result_spans = _locate_field_result_spans(
        current_text, field_spans, start_offset
    )
    assert result_spans == [(22, 22 + len(citation))]
    assert current_text[22:22 + len(citation)] == citation


def test_field_result_unique_match_recovers_mismatched_span() -> None:
    from src.logic import _locate_field_result_spans

    # If Word's positions disagree with the visible text by a character, a
    # unique text match safely recovers the span (the old behaviour aborted
    # the whole proofread).
    current_text = "abc (X, 2020) def"
    citation = "(X, 2020)"
    start_offset = 50
    # Slightly wrong doc_end (93 instead of 94) makes the strict position
    # check fail; the citation is unique, so the fallback must recover.
    field_spans = [(53, 93, citation)]
    result_spans = _locate_field_result_spans(
        current_text, field_spans, start_offset
    )
    assert result_spans == [(4, 13)]


def test_field_result_ambiguous_match_still_raises() -> None:
    from src.logic import _locate_field_result_spans

    # When the result text appears more than once, a text search would be
    # unsafe, so the mapping must keep raising (and the proofread skips).
    current_text = "(X, 2020) and (X, 2020)"
    citation = "(X, 2020)"
    field_spans = [(52, 92, citation)]
    try:
        _locate_field_result_spans(current_text, field_spans, 50)
    except ValueError as exc:
        assert "does not match" in str(exc)
        return
    raise AssertionError("Expected ValueError for ambiguous citation text")


def test_field_result_mapping_accounts_for_tracked_deletions() -> None:
    from src.logic import _locate_field_result_spans

    # Re-proofreading tracked-change text is the failure this guards against:
    # the first citation is plain text, the second is the Word field, and a
    # tracked deletion before the field shifts Word's document positions
    # without changing the visible text. Deletion spans must be subtracted so
    # the field maps to the second occurrence.
    current_text = "zzz (X, 2020) yyy (X, 2020) www"
    citation = "(X, 2020)"
    start_offset = 50
    field_spans = [(71, 91, citation)]  # result occupies visible 18..27
    deletion_spans = [(68, 71)]         # three tracked-deleted chars before it

    result_spans = _locate_field_result_spans(
        current_text, field_spans, start_offset, deletion_spans
    )
    assert result_spans == [(18, 27)]
    assert current_text[18:27] == citation

    # Without the deletion spans the position math is wrong, and the citation
    # is duplicated so the safe unique-match fallback must refuse to guess.
    try:
        _locate_field_result_spans(current_text, field_spans, start_offset)
    except ValueError as exc:
        assert "does not match" in str(exc)
        return
    raise AssertionError(
        "Expected ValueError when tracked deletions are not accounted for"
    )


def test_macos_field_spans_use_word_result_range() -> None:
    from src.word_integration import FieldSpan, MacOSWordIntegration

    integration = MacOSWordIntegration()
    raw = (
        "58###FIELD_SPAN###92###FIELD_SPAN###93###FIELD_SPAN###114"
        "###FIELD_SPAN###United Nations (2020)###FIELD_END###"
    )
    integration._run_applescript = lambda script, *args: raw

    spans = integration.get_selection_field_spans()
    assert spans == [FieldSpan(57, 115, "United Nations (2020)")]


def test_plain_text_citations_are_detected_and_masked() -> None:
    from src.logic import (
        _CITATION_SPANS,
        _merge_spans,
        protect_special_chars,
        restore_special_chars,
    )

    text = (
        "policies (He et al., 2022). "
        "World Economic Forum (2023) and Smith et al. (2020) found "
        "something (Depoers et al., 2016)."
    )
    spans = [
        (m.start(), m.end()) for m in _CITATION_SPANS.finditer(text)
    ]
    spans = _merge_spans(spans)
    masked_texts = [text[s:e] for s, e in spans]
    assert "(He et al., 2022)" in masked_texts
    assert "World Economic Forum (2023)" in masked_texts
    assert "Smith et al. (2020)" in masked_texts
    assert "(Depoers et al., 2016)" in masked_texts

    protected, replacements = protect_special_chars(text, extra_spans=spans)
    assert "He et al." not in protected
    assert "World Economic Forum" not in protected
    assert "{{OBJ_0}}" in protected
    assert restore_special_chars(protected, replacements) == text


def test_citations_force_marker_prompt_not_segments() -> None:
    """Citations must be visible to the AI as markers.

    The segmented path hides protected spans entirely, so the model cannot
    keep sentence punctuation on the correct side of a citation (it moved
    periods before citations and duplicated citation text). When citations
    are present, the whole text is sent with {{OBJ_N}} markers instead.
    """
    from src import logic

    captured: dict[str, str] = {}

    class FakeWord:
        def ensure_ready(self) -> None:
            pass

        def is_selection_in_table(self) -> bool:
            return False

        def ensure_track_changes_enabled(self) -> None:
            pass

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "abc (X, 2020) def", 50, 74, "", ""

        def selection_has_fields(self) -> bool:
            return True

        def get_selection_field_spans(self) -> list[tuple[int, int, str]]:
            return [(54, 74, "(X, 2020)")]

        def add_comment(self, comment_text: str) -> None:
            pass

    def fake_provider(
        source_text: str,
        api_key: str,
        max_tokens: int,
        base_url: str,
        model: str,
        **_kwargs: Any,
    ) -> str:
        captured["source_text"] = source_text
        captured["reviewer_comment"] = _kwargs.get("reviewer_comment", "")
        return "abc {{OBJ_0}} DEF"

    original = {
        "word_app": logic.word_app,
        "access": logic.get_access_status,
        "resolve": logic.resolve_provider_connection,
        "requires": logic.provider_requires_api_key,
        "settings": logic.load_runtime_settings,
        "provider": logic.proofread_with_provider,
        "generate_comment": logic.generate_comment,
    }
    logic.word_app = FakeWord()
    logic.get_access_status = lambda: {"tier": "paid", "free_mode_allowed": True}
    logic.resolve_provider_connection = lambda settings: (
        "Fake",
        "",
        "http://fake",
        "fake-model",
    )
    logic.provider_requires_api_key = lambda name: False
    logic.load_runtime_settings = lambda: {
        "general": {
            "comment_type": "None",
            "auto_apply": False,
            "temperature": 0.3,
            "spelling": "UK/AU/NZ",
            "style": "Precise (Minimal Changes)",
            "context": "General Editing",
        },
        "active_provider": "Fake",
        "providers": {},
    }
    logic.proofread_with_provider = fake_provider
    logic.generate_comment = lambda *_args, **_kwargs: "LANGUAGE_REVIEW"
    try:
        status, _original, corrected, _comment, _start = logic.proofread_selection_once(
            max_tokens=100
        )
        assert "Changes added as comment" in status
        assert "{{OBJ_0}}" in captured["source_text"]
        assert "===SEGMENT_0===" not in captured["source_text"]
        assert captured["reviewer_comment"] == "LANGUAGE_REVIEW"
        assert corrected == "abc (X, 2020) DEF"
    finally:
        logic.word_app = original["word_app"]
        logic.get_access_status = original["access"]
        logic.resolve_provider_connection = original["resolve"]
        logic.provider_requires_api_key = original["requires"]
        logic.load_runtime_settings = original["settings"]
        logic.proofread_with_provider = original["provider"]
        logic.generate_comment = original["generate_comment"]


def test_proofread_repolish_with_citation_and_tracked_deletions() -> None:
    """Re-proofreading tracked-change text must not skip citation fields.

    The first citation is plain text, the second is a Word field, and a
    tracked deletion before the field shifts Word's internal positions.
    """
    from src import logic

    captured: dict[str, str] = {}

    class FakeWord:
        def ensure_ready(self) -> None:
            pass

        def is_selection_in_table(self) -> bool:
            return False

        def ensure_track_changes_enabled(self) -> None:
            pass

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "zzz (X, 2020) yyy (X, 2020) www", 50, 95, "", ""

        def selection_has_fields(self) -> bool:
            return True

        def get_selection_field_spans(self) -> list[tuple[int, int, str]]:
            return [(71, 91, "(X, 2020)")]

        def get_selection_hidden_spans(
            self,
            start: int = 0,
            end: int = 0,
            exclude_spans: list[tuple[int, int]] | None = None,
            max_hidden: int = 0,
        ) -> list[tuple[int, int]]:
            assert exclude_spans == [(71, 91)]
            return [(68, 71)]

        def add_comment(self, comment_text: str) -> None:
            captured["comment"] = comment_text

    def fake_provider(
        source_text: str,
        api_key: str,
        max_tokens: int,
        base_url: str,
        model: str,
        **_kwargs: Any,
    ) -> str:
        captured["source_text"] = source_text
        return "zzz {{OBJ_0}} yyy {{OBJ_1}} WWW"

    original = {
        "word_app": logic.word_app,
        "access": logic.get_access_status,
        "resolve": logic.resolve_provider_connection,
        "requires": logic.provider_requires_api_key,
        "settings": logic.load_runtime_settings,
        "provider": logic.proofread_with_provider,
        "generate_comment": logic.generate_comment,
    }
    logic.word_app = FakeWord()
    logic.get_access_status = lambda: {"tier": "paid", "free_mode_allowed": True}
    logic.resolve_provider_connection = lambda settings: (
        "Fake",
        "",
        "http://fake",
        "fake-model",
    )
    logic.provider_requires_api_key = lambda name: False
    logic.load_runtime_settings = lambda: {
        "general": {
            "comment_type": "None",
            "auto_apply": False,
            "temperature": 0.3,
            "spelling": "UK/AU/NZ",
            "style": "Precise (Minimal Changes)",
            "context": "General Editing",
        },
        "active_provider": "Fake",
        "providers": {},
    }
    logic.proofread_with_provider = fake_provider
    logic.generate_comment = lambda *_args, **_kwargs: ""
    try:
        status, _original, corrected, _comment, _start = (
            logic.proofread_selection_once(max_tokens=100)
        )
        assert not status.startswith("Skipped:"), status
        assert status.startswith("Changes added as comment"), status
        assert corrected == "zzz (X, 2020) yyy (X, 2020) WWW"
        assert "{{OBJ_0}}" in captured["source_text"]
        assert "{{OBJ_1}}" in captured["source_text"]
    finally:
        logic.word_app = original["word_app"]
        logic.get_access_status = original["access"]
        logic.resolve_provider_connection = original["resolve"]
        logic.provider_requires_api_key = original["requires"]
        logic.load_runtime_settings = original["settings"]
        logic.proofread_with_provider = original["provider"]
        logic.generate_comment = original["generate_comment"]


def test_reviewer_guidance_always_runs_comment_optional() -> None:
    """Proofreading output must not depend on the reviewer-comment setting.

    Paid users always receive the internal Language review as guidance; the
    setting only decides whether a reviewer note is inserted into Word.
    """
    from src import logic

    calls: dict[str, list] = {
        "comments": [],
        "comment_generations": [],
        "reviewer_comment": [],
    }

    class FakeWord:
        def ensure_ready(self) -> None:
            pass

        def is_selection_in_table(self) -> bool:
            return False

        def ensure_track_changes_enabled(self) -> None:
            pass

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "abc def", 50, 56, "", ""

        def selection_has_fields(self) -> bool:
            return False

        def add_comment(self, comment_text: str) -> None:
            calls["comments"].append(comment_text)

    def fake_generate_comment(*_args: Any, **kwargs: Any) -> str:
        calls["comment_generations"].append(kwargs.get("comment_type"))
        if kwargs.get("comment_type") == "Technical (Reviewer)":
            return "TECH_COMMENT"
        return "LANGUAGE_GUIDANCE"

    def fake_provider(
        source_text: str,
        api_key: str,
        max_tokens: int,
        base_url: str,
        model: str,
        **_kwargs: Any,
    ) -> str:
        calls["reviewer_comment"].append(_kwargs.get("reviewer_comment", ""))
        return "abc DEF"

    current_comment_type = {"value": "None"}

    original = {
        "word_app": logic.word_app,
        "access": logic.get_access_status,
        "resolve": logic.resolve_provider_connection,
        "requires": logic.provider_requires_api_key,
        "settings": logic.load_runtime_settings,
        "provider": logic.proofread_with_provider,
        "generate_comment": logic.generate_comment,
    }
    logic.word_app = FakeWord()
    logic.get_access_status = lambda: {"tier": "paid", "free_mode_allowed": True}
    logic.resolve_provider_connection = lambda settings: (
        "Fake",
        "",
        "http://fake",
        "fake-model",
    )
    logic.provider_requires_api_key = lambda name: False
    logic.load_runtime_settings = lambda: {
        "general": {
            "comment_type": current_comment_type["value"],
            "auto_apply": False,
            "temperature": 0.3,
            "spelling": "UK/AU/NZ",
            "style": "Precise (Minimal Changes)",
            "context": "General Editing",
        },
        "active_provider": "Fake",
        "providers": {},
    }
    logic.proofread_with_provider = fake_provider
    logic.generate_comment = fake_generate_comment
    try:
        for comment_type, expected_note in [
            ("None", None),
            ("Language", "LANGUAGE_GUIDANCE"),
            ("Technical (Reviewer)", "TECH_COMMENT"),
        ]:
            calls["comments"] = []
            calls["comment_generations"] = []
            calls["reviewer_comment"] = []
            current_comment_type["value"] = comment_type
            logic.proofread_selection_once(max_tokens=100)

            # Guidance is always the same Language review regardless of the
            # comment setting.
            assert calls["reviewer_comment"] == ["LANGUAGE_GUIDANCE"]
            assert "Language" in calls["comment_generations"]
            if comment_type == "None":
                assert "Technical (Reviewer)" not in calls["comment_generations"]
                assert not any(
                    "LANGUAGE_GUIDANCE" in text for text in calls["comments"]
                )
            else:
                joined = "\n".join(calls["comments"])
                assert "Reviewer note:\n" + expected_note in joined
    finally:
        logic.word_app = original["word_app"]
        logic.get_access_status = original["access"]
        logic.resolve_provider_connection = original["resolve"]
        logic.provider_requires_api_key = original["requires"]
        logic.load_runtime_settings = original["settings"]
        logic.proofread_with_provider = original["provider"]
        logic.generate_comment = original["generate_comment"]


def test_apply_corrections_maps_offsets_around_fields() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.text = ""
            self.hidden_spans: list[tuple[int, int]] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return self.text, 50, 190, "", ""

        def get_selection_hidden_spans(
            self, start: int = 0, end: int = 0
        ) -> list[tuple[int, int]]:
            return self.hidden_spans

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        citation = "(Al-Qahtani & Elgharbawy, 2020)"
        current_text = "abc " + citation + " def"
        logic.word_app.text = current_text
        corrected = "abc " + citation + " DEF"
        field_spans = [(54, 185, citation)]
        result_spans = logic._locate_field_result_spans(
            current_text, field_spans, 50
        )
        protected_spans = logic._find_protected_spans(
            current_text, extra_spans=result_spans
        )

        ok = logic.apply_corrections_with_diff(
            current_text,
            corrected,
            start_offset=50,
            protected_spans=protected_spans,
            field_info=(field_spans, result_spans),
        )
        assert ok is True
        assert logic.word_app.calls == [("replace", 186, 189, "DEF")]

        # Edits that touch the citation itself must never be applied.
        logic.word_app.calls = []
        corrected = "abc (X, 2020) def"
        ok = logic.apply_corrections_with_diff(
            current_text,
            corrected,
            start_offset=50,
            protected_spans=protected_spans,
            field_info=(field_spans, result_spans),
        )
        assert ok is True
        assert logic.word_app.calls == []
    finally:
        logic.word_app = original


def test_revision_mapper_compensates_tracked_deletions() -> None:
    from src.logic import _build_hidden_items, _map_visible_offset

    # Visible text is "abcdeFGhij" but Word still counts the deleted "fg"
    # characters at internal positions 7-9.
    items = _build_hidden_items(0, [], [], [(7, 9)])
    assert items == [(7, 2)]
    assert _map_visible_offset(0, items, 6) == 6
    assert _map_visible_offset(0, items, 7) == 9
    assert _map_visible_offset(0, items, 10) == 12


def test_apply_corrections_maps_offsets_around_tracked_deletions() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.text = ""
            self.hidden_spans: list[tuple[int, int]] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return self.text, 0, 12, "", ""

        def get_selection_hidden_spans(
            self, start: int = 0, end: int = 0
        ) -> list[tuple[int, int]]:
            return self.hidden_spans

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        current = "abcdeFGhij"
        logic.word_app.text = current
        logic.word_app.hidden_spans = [(7, 9)]
        ok = logic.apply_corrections_with_diff(
            current, "abcdeFGHij", start_offset=0
        )
        assert ok is True
        assert logic.word_app.calls == [("replace", 9, 10, "H")]
    finally:
        logic.word_app = original


def test_apply_corrections_splits_edits_straddling_tracked_deletions() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.text = ""
            self.hidden_spans: list[tuple[int, int]] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return self.text, 0, 12, "", ""

        def get_selection_hidden_spans(
            self, start: int = 0, end: int = 0
        ) -> list[tuple[int, int]]:
            return self.hidden_spans

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        # Visible text hides a deletion of "fg" at internal positions 5-7.
        # Replacing visible "eh" with "X" straddles that hidden span.
        current = "abcdehij"
        logic.word_app.text = current
        logic.word_app.hidden_spans = [(5, 7)]
        ok = logic.apply_corrections_with_diff(
            current, "abcdXij", start_offset=0
        )
        assert ok is True
        assert logic.word_app.calls == [
            ("delete", 7, 8),
            ("delete", 4, 5),
            ("insert", 4, "X"),
        ]
    finally:
        logic.word_app = original


def test_apply_corrections_combines_fields_and_tracked_deletions() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.text = ""
            self.hidden_spans: list[tuple[int, int]] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return self.text, 50, 195, "", ""

        def get_selection_hidden_spans(
            self, start: int = 0, end: int = 0
        ) -> list[tuple[int, int]]:
            return self.hidden_spans

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        citation = "(Al-Qahtani & Elgharbawy, 2020)"
        current = "abc " + citation + " def"
        logic.word_app.text = current
        # A tracked deletion sits between the field and the trailing text.
        logic.word_app.hidden_spans = [(186, 189)]
        field_spans = [(54, 185, citation)]
        result_spans = logic._locate_field_result_spans(
            current, field_spans, 50
        )
        ok = logic.apply_corrections_with_diff(
            current,
            "abc " + citation + " DEF",
            start_offset=50,
            field_info=(field_spans, result_spans),
        )
        assert ok is True
        assert logic.word_app.calls == [("replace", 189, 192, "DEF")]
    finally:
        logic.word_app = original


def test_apply_corrections_aborts_when_selection_text_changed() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "changed text", 0, 12, "", ""

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        ok = logic.apply_corrections_with_diff(
            "original text", "corrected text", start_offset=0
        )
        assert ok is False
        assert logic.word_app.calls == []
    finally:
        logic.word_app = original


def test_apply_corrections_skips_hidden_scan_for_clean_selection() -> None:
    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.hidden_calls = 0

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "abcd efghi", 0, 10, "", ""

        def get_selection_hidden_spans(self, *args, **kwargs):
            self.hidden_calls += 1
            return []

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    try:
        ok = logic.apply_corrections_with_diff(
            "abcd efghi", "abcd efghi!", start_offset=0
        )
        assert ok is True
        assert logic.word_app.hidden_calls == 0
        assert logic.word_app.calls == [("insert", 10, "!")]
    finally:
        logic.word_app = original


def test_apply_corrections_cancels_during_hidden_scan() -> None:
    import threading
    import time

    from src import logic

    class FakeWord:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def get_selection_info(self) -> tuple[str, int, int, str, str]:
            return "abcd efghi", 0, 12, "", ""

        def get_selection_hidden_spans(self, *args, **kwargs):
            time.sleep(0.2)
            return [(10, 12)]

        def replace_range(self, start: int, end: int, text: str) -> None:
            self.calls.append(("replace", start, end, text))

        def delete_range(self, start: int, end: int) -> None:
            self.calls.append(("delete", start, end))

        def insert_at_position(self, pos: int, text: str) -> None:
            self.calls.append(("insert", pos, text))

    original = logic.word_app
    logic.word_app = FakeWord()
    cancel_event = threading.Event()
    threading.Timer(0.03, cancel_event.set).start()
    try:
        try:
            logic.apply_corrections_with_diff(
                "abcd efghi",
                "abcd efghi!",
                start_offset=0,
                cancel_event=cancel_event,
            )
            raise AssertionError("expected TaskCancelledError")
        except logic.TaskCancelledError:
            pass
        assert logic.word_app.calls == []
    finally:
        logic.word_app = original


def test_windows_hidden_span_revision_filtering() -> None:
    from src.word_integration import WindowsWordIntegration

    class _RevRange:
        def __init__(self, start: int, end: int) -> None:
            self.Start = start
            self.End = end

    class _Revision:
        def __init__(self, rev_type: int, start: int, end: int) -> None:
            self.Type = rev_type
            self.Range = _RevRange(start, end)

    class _RevisionCollection:
        def __init__(self, items: list[_Revision]) -> None:
            self._items = items

        def __iter__(self):
            return iter(self._items)

    class _RangeWithRevisions:
        def __init__(self, revs: _RevisionCollection) -> None:
            self.Revisions = revs

    class _Doc:
        def __init__(self, revs: _RevisionCollection) -> None:
            self._revs = revs

        def Range(self, start: int, end: int) -> _RangeWithRevisions:
            return _RangeWithRevisions(self._revs)

    class _Documents:
        Count = 1

    class _Selection:
        Start = 0
        End = 10

    class _Word:
        Documents = _Documents()
        ActiveDocument = _Doc(
            _RevisionCollection(
                [
                    _Revision(2, 1, 3),   # wdRevisionDelete
                    _Revision(1, 4, 5),   # insertion: visible, skip
                    _Revision(17, 5, 7),  # wdRevisionMovedFrom
                    _Revision(3, 8, 9),   # property: skip
                ]
            )
        )
        Selection = _Selection()

    integration = WindowsWordIntegration()
    integration._get_word = lambda: _Word()
    assert integration.get_selection_hidden_spans() == [(1, 3), (5, 7)]


def test_macos_hidden_span_parsing() -> None:
    from src.word_integration import MacOSWordIntegration

    integration = MacOSWordIntegration()
    integration._run_applescript = lambda script, *args: (
        "7###HSPAN###9###HSEND###12###HSPAN###15###HSEND###"
    )
    assert integration.get_selection_hidden_spans(0, 20) == [(7, 9), (12, 15)]


def test_api_call_with_retry_cancellation() -> None:
    import threading

    from src.logic import TaskCancelledError, _api_call_with_retry

    cancel = threading.Event()

    def slow_request() -> str:
        time.sleep(5)
        return "done"

    def arm_cancel() -> None:
        time.sleep(0.1)
        cancel.set()

    threading.Thread(target=arm_cancel, daemon=True).start()
    start = time.monotonic()
    try:
        _api_call_with_retry(slow_request, cancel_event=cancel)
        raise AssertionError("expected cancellation")
    except TaskCancelledError:
        assert time.monotonic() - start < 2


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
    assert "OUTPUT CONTRACT" in enriched
    assert "never a message, question, or request" in enriched
    assert "never refuse to edit" in enriched.lower()
    assert with_strict_editing_rules(enriched) == enriched

    assert "STRICT LANGUAGE-EDITING MODE" in load_polish_prompt()
    assert "STRICT LANGUAGE-EDITING MODE" in load_proofreading_prompt("Precise (Minimal Changes)")
    assert "STRICT LANGUAGE-EDITING MODE" in load_proofreading_prompt("Creative (Rewrite)")


def test_local_model_output_cleaning() -> None:
    from src.logic import _clean_local_model_output

    assert (
        _clean_local_model_output(
            "<think>I should fix the spelling.</think>The cat sat on the mat."
        )
        == "The cat sat on the mat."
    )
    assert (
        _clean_local_model_output(
            "<|im_start|>assistant\nThe cat sat.<|im_end|>"
        )
        == "The cat sat."
    )


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
            cancel_event=None,
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


def test_hidden_word_review_shows_apply_button() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src import settings
    from src.gui import ProofreaderApp

    window = ProofreaderApp(1024, settings.load_runtime_settings())
    window.show()
    app.processEvents()
    window.hide()
    app.processEvents()
    window.worker = SimpleNamespace(mode="word")  # type: ignore[assignment]
    window._record_usage_if_completed = lambda _status: None  # type: ignore[method-assign]
    window.handle_result(
        "REVIEW_NEEDED:0.4",
        "Hello world.",
        "Hello, wonderful world.",
        "",
        0,
    )
    app.processEvents()
    assert window.pending_generic_apply is not None
    assert window.pending_generic_apply.get("mode") == "word"
    assert not window.apply_btn.isHidden()
    assert window.apply_btn.isEnabled()
    assert window.apply_btn.text() == "Apply Changes"
    window.show()
    app.processEvents()
    assert window.apply_btn.isVisible()
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
        assert len(catalog) >= 5
        model = local_model.get_model("qwen3-4b")
        assert model["sha256"]
        assert model["size_bytes"] > 1_000_000_000
        for model_id in ("qwen3-1.7b", "qwen3-4b-proofread", "phi4-mini"):
            entry = local_model.get_model(model_id)
            assert entry["sha256"]
            assert entry["size_bytes"] > 100_000_000
        assert local_model.model_path("qwen3-4b").endswith(".gguf")
        assert local_model.is_model_installed("qwen3-4b") is False
        assert local_model.resolve_model_id("qwen3-4b") == "qwen3-4b"
        assert local_model.resolve_model_id("not-a-model") == local_model.recommend_model()["id"]
        assert local_model.resolve_model_id(None) == local_model.recommend_model()["id"]

        assert local_model.recommend_model({"total_ram_gb": 6.0})["id"] == "qwen3-1.7b"
        assert local_model.recommend_model({"total_ram_gb": 8.0})["id"] == "phi4-mini"
        assert local_model.recommend_model({"total_ram_gb": 16.0})["id"] == "qwen3-8b"
        assert local_model.recommend_model({"total_ram_gb": 24.0})["id"] == "qwen3-14b"
        # Newer MoE options are catalogued (with checksums) for 24 GB+ users
        # without changing the proven defaults.
        for model_id, min_ram in (("gpt-oss-20b", 24), ("qwen3-30b-a3b", 32)):
            entry = local_model.get_model(model_id)
            assert entry["sha256"]
            assert entry["size_bytes"] > 10_000_000_000
            assert entry["min_ram_gb"] == min_ram
    finally:
        local_model.LOCAL_MODEL_DIR = original_dir


def test_local_server_command_flags() -> None:
    from src import local_model

    cmd = local_model._build_server_cmd(
        "/fake/llama-server",
        "/fake/model.gguf",
        "qwen3-4b",
        17881,
        {
            "system": "Darwin",
            "machine": "arm64",
            "is_apple_silicon": True,
            "cpu_count": 10,
            "total_ram_gb": 24.0,
        },
    )
    assert "--flash-attn" in cmd and "on" in cmd
    assert "--n-gpu-layers" in cmd
    assert "--cache-type-k" in cmd and "q8_0" in cmd
    assert "--cache-type-v" in cmd and "q8_0" in cmd
    assert "--mlock" in cmd  # 24 GB RAM vs ~2.3 GB model fits comfortably
    assert "--reasoning-budget" in cmd and "0" in cmd
    assert "--no-webui" in cmd

    # 8 GB Intel/Windows machines: no Metal flash attention, no mlock.
    cmd_win = local_model._build_server_cmd(
        "/fake/llama-server.exe",
        "/fake/model.gguf",
        "phi4-mini",
        17881,
        {
            "system": "Windows",
            "machine": "x86_64",
            "is_apple_silicon": False,
            "cpu_count": 4,
            "total_ram_gb": 8.0,
        },
    )
    assert "--flash-attn" not in cmd_win
    assert "--mlock" not in cmd_win
    assert "--threads" in cmd_win
    assert "--cache-type-k" in cmd_win


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
                progress_callback=lambda done, total, stage: calls.append((done, total, stage)),
            )
            with open(dest, "rb") as f:
                assert f.read() == payload
            assert calls and calls[-1][0] == len(payload)
            assert any(stage.startswith("Verifying") for _, _, stage in calls)

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


def test_local_model_disk_space_check() -> None:
    from src import local_model

    original_dir = local_model.LOCAL_MODEL_DIR
    original_usage = local_model.shutil.disk_usage
    original_runtime = local_model.ensure_runtime
    local_model.LOCAL_MODEL_DIR = tempfile.mkdtemp(prefix="byteproof-disk-")
    local_model.shutil.disk_usage = lambda _path: SimpleNamespace(free=50 * 1024 * 1024)
    local_model.ensure_runtime = lambda *args, **kwargs: "/tmp/llama-server"
    try:
        try:
            local_model.ensure_local_model("qwen3-1.7b")
            raise AssertionError("Expected disk space failure")
        except RuntimeError as exc:
            assert "disk space" in str(exc)
    finally:
        local_model.shutil.disk_usage = original_usage
        local_model.ensure_runtime = original_runtime
        local_model.LOCAL_MODEL_DIR = original_dir


def test_settings_new_pages() -> None:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    from src import settings
    from src.gui import SettingsDialog

    dialog = SettingsDialog(settings.load_runtime_settings())
    dialog.show()
    app.processEvents()

    assert dialog.sidebar.count() == 5
    local_item = dialog.sidebar.item(2)
    assert local_item is not None and local_item.text() == "Local AI"
    license_item = dialog.sidebar.item(3)
    assert license_item is not None and license_item.text() == "License"
    updates_item = dialog.sidebar.item(4)
    assert updates_item is not None and updates_item.text() == "Updates"

    dialog.sidebar.setCurrentRow(2)
    dialog.change_page(2)
    app.processEvents()
    assert dialog.local_page is not None
    assert "Recommended" in dialog.local_recommend_label.text()

    dialog._use_local_model("qwen3-4b")
    assert dialog.settings["active_provider"] == "ByteProof Local (Qwen3)"
    assert dialog.settings["local_model"]["active_model"] == "qwen3-4b"
    assert dialog.settings["providers"]["ByteProof Local (Qwen3)"]["model"] == "qwen3-4b"

    dialog.sidebar.setCurrentRow(4)
    dialog.change_page(4)
    app.processEvents()
    assert dialog.update_check_btn.text() == "Check for Updates"
    assert dialog.version_label.text() == settings.APP_VERSION
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
    original_save = gui.save_runtime_settings
    gui.LocalModelDownloadWorker = FakeDownloadWorker
    gui.save_runtime_settings = lambda _settings: None
    try:
        dialog._download_local_model("qwen3-4b")
        worker = getattr(window, "_local_download_worker", None)
        assert worker is not None, "worker must be stored on the main window"
        assert worker.isRunning()
        assert worker.wait(3000)
        assert not worker.isRunning()
    finally:
        gui.LocalModelDownloadWorker = original_worker
        gui.save_runtime_settings = original_save
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
    original_save = gui.save_runtime_settings
    gui.is_model_installed = lambda _model_id: True
    gui.save_runtime_settings = lambda _settings: None
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
        gui.save_runtime_settings = original_save
        dialog.close()
        window.close()


def test_local_download_done_activates_model() -> None:
    """Download completion must install, activate, and persist the model."""
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
            self.done.emit(self.model_id)

    original_worker = gui.LocalModelDownloadWorker
    original_installed = gui.is_model_installed
    original_save = gui.save_runtime_settings
    saved: list[dict[str, Any]] = []
    gui.LocalModelDownloadWorker = FakeDownloadWorker
    gui.is_model_installed = lambda mid: mid == "qwen3-4b-proofread"
    gui.save_runtime_settings = lambda new_settings: saved.append(new_settings)
    try:
        dialog._download_local_model("qwen3-4b-proofread")
        worker = getattr(window, "_local_download_worker", None)
        assert worker is not None
        assert worker.wait(3000)
        app.processEvents()

        assert dialog.settings["active_provider"] == settings.LOCAL_MODEL_PROVIDER
        assert dialog.settings["local_model"]["active_model"] == "qwen3-4b-proofread"
        assert (
            dialog.settings["providers"][settings.LOCAL_MODEL_PROVIDER]["model"]
            == "qwen3-4b-proofread"
        )
        assert window.settings["local_model"]["active_model"] == "qwen3-4b-proofread"
        assert saved and saved[-1]["local_model"]["active_model"] == "qwen3-4b-proofread"
        assert "ready" in dialog.local_status_label.text().lower()
        assert dialog.local_model_cards["qwen3-4b-proofread"]["use"].text() == "Active"
    finally:
        gui.LocalModelDownloadWorker = original_worker
        gui.is_model_installed = original_installed
        gui.save_runtime_settings = original_save
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


def test_download_cancel_during_verification_keeps_partial_file() -> None:
    """Cancelling while the checksum runs must keep the .part file for resume."""
    import http.server
    import socketserver
    import threading

    from src import local_model

    payload = b"verification-cancel-payload-" * 2000
    sha = hashlib.sha256(payload).hexdigest()

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
            dest = os.path.join(tmpdir, "model.gguf")
            cancel_event = threading.Event()
            result: dict[str, Any] = {}

            original_sha256 = local_model.hashlib.sha256

            class CancellingSha256:
                def __init__(self) -> None:
                    self._inner = original_sha256()

                def update(self, block: bytes) -> None:
                    cancel_event.set()
                    self._inner.update(block)
                    raise local_model.DownloadCancelledError(
                        "Download cancelled by user."
                    )

                def hexdigest(self) -> str:
                    return self._inner.hexdigest()

            local_model.hashlib.sha256 = lambda *args, **kwargs: CancellingSha256()
            try:
                try:
                    local_model.download_file(
                        f"http://127.0.0.1:{port}/model.gguf",
                        dest,
                        expected_size=len(payload),
                        expected_sha256=sha,
                        cancel_event=cancel_event,
                    )
                    result["ok"] = True
                except Exception as exc:
                    result["error"] = exc
            finally:
                local_model.hashlib.sha256 = original_sha256

            assert "ok" not in result
            assert isinstance(result.get("error"), local_model.DownloadCancelledError)
            assert not os.path.exists(dest)
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
    # Keep test runs from writing into the real app's support folder logs,
    # which made real-world debugging confusing (e.g. FakeMail entries).
    import src.generic_editing as _ge
    import src.hotkeys as _hk

    _ge._debug_log = lambda _msg: None
    _hk.log_debug = lambda _msg: None

    test_updater_url_selection()
    print("PASS updater URL selection")
    test_settings_branding()
    print("PASS settings branding")
    test_shared_text_normalization()
    print("PASS shared text normalization")
    test_open_purchase_url_uses_live_link()
    print("PASS open purchase URL uses live link")
    test_licensing_roundtrip()
    print("PASS licensing roundtrip")
    test_activation_from_url()
    print("PASS activation from URL (Polar key)")
    test_polar_key_activation_and_validation()
    print("PASS Polar key activation + validation + deactivation")
    test_polar_activate_parses_top_level_activation()
    print("PASS Polar activate response parsing")
    test_developer_email_activation()
    print("PASS developer email activation")
    test_legacy_email_and_signed_key_fallback()
    print("PASS legacy email + signed key fallback")
    test_tampered_license_rejected()
    print("PASS tampered license rejected")
    test_secure_store_fallback_restores_license()
    print("PASS secure store fallback restores license")
    test_server_activation_core_two_machine_limit()
    print("PASS server two-machine limit")
    test_emailer_graceful_and_link_build()
    print("PASS activation email builder + graceful skip")
    test_parse_dev_emails()
    print("PASS developer email parsing")
    test_register_machine_developer_unlimited()
    print("PASS developer unlimited devices")
    test_cache_cleanup_logs_and_stale_partials()
    print("PASS cache cleanup logs + partials")
    test_cache_cleanup_keeps_only_two_models()
    print("PASS cache cleanup keeps only two models")
    test_trial_expired_enters_free_mode()
    print("PASS trial expiry enters free mode")
    test_free_mode_daily_cap()
    print("PASS free mode daily cap")
    test_ensure_trial_started_uses_earliest_secondary()
    print("PASS trial hardening uses earliest start")
    test_hotkey_conversion()
    print("PASS hotkey conversion")
    test_mac_hotkey_default_migration()
    print("PASS macOS hotkey default migration")
    test_gui_constructs()
    print("PASS GUI construction")
    test_segment_reassembly_safety()
    print("PASS segment reassembly safety")
    test_fieldcode_pattern_matches_word_control_chars()
    print("PASS Word field-code control characters are protected")
    test_citation_fields_are_masked_and_mapped()
    print("PASS citation fields are masked and mapped")
    test_field_result_spans_use_document_positions_not_text_search()
    print("PASS field result spans use document positions")
    test_long_citation_result_maps_fully()
    print("PASS long citation result maps fully")
    test_field_result_unique_match_recovers_mismatched_span()
    print("PASS field result unique match recovery")
    test_field_result_ambiguous_match_still_raises()
    print("PASS field result ambiguous match still raises")
    test_field_result_mapping_accounts_for_tracked_deletions()
    print("PASS field result mapping accounts for tracked deletions")
    test_macos_field_spans_use_word_result_range()
    print("PASS macOS field spans use Word result range")
    test_plain_text_citations_are_detected_and_masked()
    print("PASS plain-text citations are detected and masked")
    test_citations_force_marker_prompt_not_segments()
    print("PASS citations force marker prompt instead of blind segments")
    test_proofread_repolish_with_citation_and_tracked_deletions()
    print("PASS repolish with citation + tracked deletions")
    test_reviewer_guidance_always_runs_comment_optional()
    print("PASS reviewer guidance is independent of comment setting")
    test_apply_corrections_maps_offsets_around_fields()
    print("PASS apply corrections maps offsets around citation fields")
    test_revision_mapper_compensates_tracked_deletions()
    print("PASS revision mapper compensates tracked deletions")
    test_apply_corrections_maps_offsets_around_tracked_deletions()
    print("PASS apply corrections maps offsets around tracked deletions")
    test_apply_corrections_splits_edits_straddling_tracked_deletions()
    print("PASS apply corrections splits edits straddling tracked deletions")
    test_apply_corrections_combines_fields_and_tracked_deletions()
    print("PASS apply corrections combines fields and tracked deletions")
    test_apply_corrections_aborts_when_selection_text_changed()
    print("PASS apply corrections aborts when selection text changed")
    test_apply_corrections_skips_hidden_scan_for_clean_selection()
    print("PASS apply corrections skips hidden scan for clean selection")
    test_apply_corrections_cancels_during_hidden_scan()
    print("PASS apply corrections cancels during hidden scan")
    test_windows_hidden_span_revision_filtering()
    print("PASS Windows hidden span revision filtering")
    test_macos_hidden_span_parsing()
    print("PASS macOS hidden span parsing")
    test_api_call_with_retry_cancellation()
    print("PASS API call cancellation")
    test_provider_connection_tester()
    print("PASS provider connection tester")
    test_strict_editing_rules()
    print("PASS strict editing rules")
    test_local_model_output_cleaning()
    print("PASS local model output cleaning")
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
    test_hidden_word_review_shows_apply_button()
    print("PASS hidden Word review shows Apply button")
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
    test_local_server_command_flags()
    print("PASS local server command flags")
    test_local_model_download_with_checksum_and_resume()
    print("PASS local model download (checksum + resume)")
    test_local_model_disk_space_check()
    print("PASS local model disk space check")
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
    test_download_cancel_during_verification_keeps_partial_file()
    print("PASS download cancel during verification keeps partial file")
    test_local_download_done_activates_model()
    print("PASS local download done activates model")
    test_double_escape_cancels_download()
    print("PASS double escape cancels download")
    print("\nALL_SMOKE_TESTS_PASSED")


if __name__ == "__main__":
    main()
