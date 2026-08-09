import json
import os
import platform
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import certifi

VERSION_CHECK_URL = "https://www.bytemind.co.nz/byteproof-version.json"
REQUEST_TIMEOUT = 10


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())

def _parse_version(version_str: str) -> tuple[int, ...]:
    parts = version_str.strip().split(".")
    return tuple(int(p) for p in parts if p.isdigit())

def _fetch_version_info() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(VERSION_CHECK_URL)
        req.add_header("User-Agent", "ByteProof-UpdateChecker/1.0")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None

def check_for_updates(current_version: str) -> tuple[bool, dict[str, Any] | None]:
    remote = _fetch_version_info()
    if remote is None:
        return False, None
    remote_version = remote.get("version")
    if not remote_version:
        return False, None
    try:
        current_tuple = _parse_version(current_version)
        remote_tuple = _parse_version(remote_version)
    except (ValueError, TypeError):
        return False, None
    if remote_tuple > current_tuple:
        return True, remote
    return False, None

def download_update(
    version_info: dict[str, Any],
    download_dir: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str | None:
    url = _get_download_url(version_info)
    if not url:
        return None
    filename = os.path.basename(url.split("?")[0].split("#")[0])
    dest_path = os.path.join(download_dir, filename)
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "ByteProof-Installer/1.0")
        with urllib.request.urlopen(req, timeout=300, context=_ssl_context()) as response:
            total_size = response.getheader("Content-Length")
            total = int(total_size) if total_size else 0
            downloaded = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)
            if progress_callback is not None:
                progress_callback(downloaded, total)
        return dest_path
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None

def _get_download_url(version_info: dict[str, Any]) -> str | None:
    system = platform.system()
    if system == "Windows":
        return version_info.get("windows_url")
    machine = platform.machine()
    if machine == "arm64":
        return version_info.get("macos_apple_silicon_url")
    elif machine == "x86_64":
        return version_info.get("macos_intel_url")
    return version_info.get("macos_apple_silicon_url")
