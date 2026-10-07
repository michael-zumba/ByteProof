#!/usr/bin/env python3
"""Mock end-to-end test of the macOS update and install flow.

Builds a throwaway DMG containing a stub "ByteProof.app" that writes a
"relaunched" marker when it starts, serves it over localhost, and drives the
real code end to end:

    download_update       host allowlist + SHA-256 verification
    stage_macos_payload   hdiutil attach + ditto + quarantine clear
    stage_macos_update    the generated helper script
    the helper            wait for the app to exit, swap, relaunch, report
    take_update_result    the marker the next launch reads

It then exercises the failure branches: a checksum mismatch must be refused,
a volume without a payload must not quit the app, and a vanished staging
folder must leave the old bundle in place and report the failure.

Nothing outside a temporary folder is touched; the real /Applications copy is
never involved. Mock runs set BYTEPROOF_UPDATE_SKIP_OPEN / _SKIP_NOTIFY so the
stub is the only thing "relaunched" and no dialogs can appear.

Usage:
    venv/bin/python scripts/mock_update_and_install.py [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import app_version

APP_NAME = "ByteProof"
MOCK_VERSION = "9.9.9"
OLD_VERSION = "1.0.0"

_RESULTS: list[tuple[bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((bool(ok), label))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def stub_app(path: str, version: str, marker_path: str = "") -> None:
    """Write a minimal .app bundle whose executable records its launch."""
    macos_dir = os.path.join(path, "Contents", "MacOS")
    os.makedirs(macos_dir, exist_ok=True)
    with open(
        os.path.join(path, "Contents", "Info.plist"), "w", encoding="utf-8"
    ) as handle:
        handle.write(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<plist version="1.0"><dict>'
            "<key>CFBundleExecutable</key><string>ByteProof</string>"
            "<key>CFBundleIdentifier</key><string>nz.co.bytemind.mock</string>"
            "<key>CFBundleName</key><string>ByteProofMock</string>"
            "<key>CFBundlePackageType</key><string>APPL</string>"
            f"<key>CFBundleShortVersionString</key><string>{version}</string>"
            "</dict></plist>"
        )
    launcher = os.path.join(macos_dir, "ByteProof")
    with open(launcher, "w", encoding="utf-8") as handle:
        if marker_path:
            handle.write(
                "#!/bin/bash\n"
                f"/bin/echo relaunched > {shlex.quote(marker_path)}\n"
                "exit 0\n"
            )
        else:
            handle.write("#!/bin/bash\nexit 0\n")
    os.chmod(launcher, 0o755)


def bundle_version(path: str) -> str:
    plist = os.path.join(path, "Contents", "Info.plist")
    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", "Print CFBundleShortVersionString", plist],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_dmg(work: str, payload_root: str, name: str) -> str:
    dmg = os.path.join(work, name)
    subprocess.run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-volname",
            "ByteProof Mock Installer",
            "-srcfolder",
            payload_root,
            "-ov",
            "-format",
            "UDZO",
            dmg,
        ],
        capture_output=True,
        check=True,
    )
    return dmg


class FeedHandler(BaseHTTPRequestHandler):
    dmg_path: ClassVar[str] = ""
    feed: ClassVar[dict[str, Any]] = {}

    def do_GET(self) -> None:
        if self.path == "/feed.json":
            body = json.dumps(self.feed).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/ByteProof_Installer_AppleSilicon.dmg":
            with open(self.dmg_path, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args) -> None:
        return


def wait_for(predicate, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def run_mock(work: str) -> bool:
    # --- the payload: a DMG holding a stub app that proves the relaunch ------
    payload_root = os.path.join(work, "payload")
    relaunch_marker = os.path.join(work, "relaunched.txt")
    stub_app(
        os.path.join(payload_root, f"{APP_NAME}.app"),
        MOCK_VERSION,
        marker_path=relaunch_marker,
    )
    dmg = make_dmg(work, payload_root, "ByteProof_Mock_Installer.dmg")
    digest = sha256_of(dmg)
    print(f"mock DMG: {dmg} ({os.path.getsize(dmg)} bytes)")

    server = ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
    port = server.server_address[1]
    dmg_url = f"http://127.0.0.1:{port}/ByteProof_Installer_AppleSilicon.dmg"
    FeedHandler.dmg_path = dmg
    FeedHandler.feed = {
        "version": MOCK_VERSION,
        "release_date": "2026-10-05",
        "release_notes": "mock",
        "macos_apple_silicon_url": dmg_url,
        "windows_url": dmg_url,
        "sha256": {
            "macos_apple_silicon_url": digest,
            "windows_url": digest,
        },
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # The download refuses non-project hosts; the mock serves localhost only.
    real_allow = app_version._is_allowed_url
    app_version._is_allowed_url = lambda url: (
        url.startswith(f"http://127.0.0.1:{port}/") or real_allow(url)
    )
    try:
        print("\n1. download + checksum verification")
        downloads = os.path.join(work, "Downloads")
        os.makedirs(downloads, exist_ok=True)
        downloaded = app_version.download_update(FeedHandler.feed, downloads)
        check("the mock DMG downloads and passes its published SHA-256", bool(downloaded))
        check(
            "the downloaded file is the DMG the feed named",
            bool(downloaded)
            and os.path.basename(downloaded) == "ByteProof_Installer_AppleSilicon.dmg",
            os.path.basename(downloaded or ""),
        )

        bad_feed = json.loads(json.dumps(FeedHandler.feed))
        bad_feed["sha256"]["macos_apple_silicon_url"] = "0" * 64
        # A separate folder: the refused download deletes its own copy, and the
        # good one above is still needed for the staging step.
        bad_downloads = os.path.join(work, "Downloads-bad")
        os.makedirs(bad_downloads, exist_ok=True)
        refused = app_version.download_update(bad_feed, bad_downloads)
        check(
            "a wrong checksum is refused and the file is discarded",
            refused is None
            and not os.path.exists(
                os.path.join(
                    bad_downloads, "ByteProof_Installer_AppleSilicon.dmg"
                )
            ),
        )

        print("\n2. staging the payload next to the installed app")
        applications = os.path.join(work, "Applications")
        dest = os.path.join(applications, f"{APP_NAME}.app")
        stub_app(dest, OLD_VERSION)
        staging = app_version.stage_macos_payload(downloaded, dest)
        check("the new bundle is copied next to the old one", bool(staging))
        check(
            "the staged bundle is the new version",
            bool(staging)
            and os.path.isdir(staging)
            and bundle_version(staging) == MOCK_VERSION,
            bundle_version(staging) if staging else "",
        )
        check(
            "the old bundle is untouched while the app still runs",
            bundle_version(dest) == OLD_VERSION,
        )
        if staging:
            quarantine = subprocess.run(
                ["/usr/bin/xattr", "-p", "com.apple.quarantine", staging],
                capture_output=True,
                text=True,
                check=False,
            )
            check(
                "the staged copy carries no quarantine flag",
                quarantine.returncode != 0,
                (quarantine.stdout or "").strip(),
            )

        print("\n3. the helper waits for the app, swaps, and relaunches")
        # A two-second stand-in for the running app proves the wait is real:
        # the swap must not happen while it lives.
        fake_app = subprocess.Popen(["/bin/sleep", "2"])
        support = os.path.join(work, "support")
        script = app_version.stage_macos_update(
            dmg_path=downloaded,
            staging_path=staging or "",
            dest_path=dest,
            pid=fake_app.pid,
            version=MOCK_VERSION,
            support_dir=support,
        )
        helper = subprocess.Popen(
            ["/bin/bash", script],
            env={
                **os.environ,
                "BYTEPROOF_UPDATE_SKIP_OPEN": "0",
                "BYTEPROOF_UPDATE_SKIP_NOTIFY": "1",
            },
        )
        time.sleep(0.6)
        check(
            "nothing is replaced while the app is still running",
            bundle_version(dest) == OLD_VERSION,
            bundle_version(dest),
        )
        fake_app.wait()
        result_path = os.path.join(support, "update-result.json")
        wait_for(lambda: os.path.exists(result_path), timeout=30)
        result = app_version.take_update_result(support) or {}
        check(
            'the helper reports "ok" after the app exits',
            result.get("status") == "ok",
            str(result.get("status")),
        )
        helper.wait(timeout=30)
        check(
            "the installed bundle is the new version",
            bundle_version(dest) == MOCK_VERSION,
            bundle_version(dest),
        )
        check(
            "the new bundle was relaunched (stub left its marker)",
            wait_for(lambda: os.path.exists(relaunch_marker), timeout=15),
        )
        check(
            "no staging, backup, or DMG is left behind",
            not os.path.exists(staging or "")
            and not os.path.exists(dest + ".update-backup")
            and not os.path.exists(downloaded or ""),
        )

        print("\n4. failure branches")
        empty_volume = os.path.join(work, "empty-volume")
        os.makedirs(empty_volume, exist_ok=True)
        dest2 = os.path.join(work, "Applications2", f"{APP_NAME}.app")
        stub_app(dest2, OLD_VERSION)
        check(
            "a volume without a payload is refused before quitting",
            app_version.stage_macos_payload(empty_volume, dest2) is None,
        )
        check(
            "the refused payload leaves the old bundle untouched",
            bundle_version(dest2) == OLD_VERSION,
        )

        finished = subprocess.Popen([shutil.which("true") or "true"])
        finished.wait()
        support2 = os.path.join(work, "support2")
        script2 = app_version.stage_macos_update(
            dmg_path=downloaded or dmg,
            staging_path=dest2 + ".update-staging",  # never created
            dest_path=dest2,
            pid=finished.pid,
            version=MOCK_VERSION,
            support_dir=support2,
        )
        subprocess.run(
            ["/bin/bash", script2],
            env={
                **os.environ,
                "BYTEPROOF_UPDATE_SKIP_OPEN": "1",
                "BYTEPROOF_UPDATE_SKIP_NOTIFY": "1",
            },
            timeout=60,
            check=False,
        )
        result2 = app_version.take_update_result(support2) or {}
        check(
            "a vanished staging folder reports a failure",
            result2.get("status") == "failed",
            str(result2.get("status")),
        )
        check(
            "the roll-back leaves the old bundle working",
            bundle_version(dest2) == OLD_VERSION,
        )
    finally:
        app_version._is_allowed_url = real_allow
        server.shutdown()
        server.server_close()

    failures = [label for ok, label in _RESULTS if not ok]
    print(f"\nmock update+install: {len(_RESULTS) - len(failures)}/{len(_RESULTS)} checks passed")
    if failures:
        for label in failures:
            print(f"  failed: {label}")
    return not failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = parser.parse_args(argv)

    if platform.system() != "Darwin":
        print("This mock drives the macOS update flow; run it on macOS.")
        return 2

    work = tempfile.mkdtemp(prefix="byteproof-mock-update-")
    print(f"mock workspace: {work}")
    try:
        ok = run_mock(work)
    finally:
        if args.keep:
            print(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
