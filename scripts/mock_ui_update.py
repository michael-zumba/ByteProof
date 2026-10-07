#!/usr/bin/env python3
"""Mock UI-level test: "Download and Install" through to the relaunch.

The pipeline-level mock (mock_update_and_install.py) proves the download,
staging, helper and rollback. This one proves the glue the owner actually
clicks: a real ProofreaderApp calls _handle_download_finished() with a mock
DMG, stages it, quits itself, and lets the detached helper swap the bundle and
relaunch. A child process plays the app so the helper's "wait for the app to
exit" is real.

The destination and the support folder are redirected into a temporary
workspace, so the real /Applications copy is never touched (the script asserts
it is still the same version at the end).

Usage:
    venv/bin/python scripts/mock_ui_update.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from mock_update_and_install import (
    MOCK_VERSION,
    OLD_VERSION,
    bundle_version,
    make_dmg,
    stub_app,
    wait_for,
)

RESULTS: list[tuple[bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def run_child(dmg_path: str, dest_path: str, support_dir: str, _marker: str) -> int:
    """The 'app': build the real window, hand it the DMG, and quit into the helper."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from src import gui as gui_mod
    from src import settings as settings_mod

    app = QApplication.instance() or QApplication([])
    settings = settings_mod.load_runtime_settings()
    settings_mod.SETTINGS_FILE = os.path.join(support_dir, "settings.json")
    # The real window must never update or replace the real install.
    gui_mod.installed_app_bundle_path = lambda *_a, **_k: dest_path
    gui_mod.get_app_support_dir = lambda: support_dir

    window = gui_mod.ProofreaderApp(1024, settings)
    window.pending_update_version = MOCK_VERSION

    # Safety net: if the update path fails to quit, end the child anyway.
    QTimer.singleShot(60_000, app.quit)
    QTimer.singleShot(200, lambda: window._handle_download_finished(dmg_path))
    app.exec()
    # The helper waits for this pid; exiting here is the signal to swap.
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--child", nargs=4, metavar=("DMG", "DEST", "SUPPORT", "MARKER"))
    args = parser.parse_args(argv)

    if platform.system() != "Darwin":
        print("This mock drives the macOS update flow; run it on macOS.")
        return 2

    if args.child:
        dmg, dest, support, marker = args.child
        return run_child(dmg, dest, support, marker)

    real_app = "/Applications/ByteProof.app"
    real_version = bundle_version(real_app) if os.path.isdir(real_app) else ""
    work = tempfile.mkdtemp(prefix="byteproof-mock-ui-")
    print(f"mock workspace: {work}")
    try:
        payload_root = os.path.join(work, "payload")
        relaunch_marker = os.path.join(work, "relaunched.txt")
        stub_app(
            os.path.join(payload_root, "ByteProof.app"),
            MOCK_VERSION,
            marker_path=relaunch_marker,
        )
        dmg = make_dmg(work, payload_root, "ByteProof_Mock_Installer.dmg")

        dest = os.path.join(work, "Applications", "ByteProof.app")
        stub_app(dest, OLD_VERSION)
        support = os.path.join(work, "support")
        os.makedirs(support, exist_ok=True)

        print("\nUI flow: _handle_download_finished -> stage -> quit -> helper")
        child = subprocess.run(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--child",
                dmg,
                dest,
                support,
                relaunch_marker,
            ],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            timeout=180,
            check=False,
        )
        check("the app process exits into the helper", child.returncode == 0)

        result_path = os.path.join(support, "update-result.json")
        check(
            "the helper reports a result after the app exits",
            wait_for(lambda: os.path.exists(result_path), timeout=60),
        )
        try:
            with open(result_path, encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, json.JSONDecodeError):
            result = {}
        check("the result is ok", result.get("status") == "ok", str(result.get("status")))
        check(
            "the mock bundle is now the new version",
            bundle_version(dest) == MOCK_VERSION,
            bundle_version(dest),
        )
        check(
            "the new bundle was relaunched",
            wait_for(lambda: os.path.exists(relaunch_marker), timeout=20),
        )
        check(
            "no staging or backup is left behind",
            not os.path.exists(dest + ".update-staging")
            and not os.path.exists(dest + ".update-backup"),
        )
        check(
            "the real /Applications copy was never touched",
            not real_version or bundle_version(real_app) == real_version,
            f"{real_version} -> {bundle_version(real_app) if real_version else ''}",
        )
    finally:
        if args.keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    failures = [label for ok, label in RESULTS if not ok]
    print(f"\nmock UI update: {len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    for label in failures:
        print(f"  failed: {label}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
