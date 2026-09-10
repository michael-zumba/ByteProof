"""Update checking and download with integrity verification.

Version handling is pre-release aware: ``2.0.1-beta.2`` sorts *below* the
``2.0.1`` release but above ``2.0.0``, so beta testers are still offered the
releases that supersede their build. The previous implementation compared only
the numeric dot-parts, which made ``2.0.1-beta.2`` and ``2.0.2`` sort equal and
silently hid every following release from testers.

Downloads are restricted to the project's own hosts and, when the release feed
publishes a SHA-256 for the platform artifact, the payload is verified before it
is handed to the installer.
"""

import hashlib
import json
import os
import platform
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

import certifi

VERSION_CHECK_URL = "https://www.bytemind.co.nz/byteproof-version.json"
REQUEST_TIMEOUT = 10

# Update payloads may only come from these hosts (GitHub release assets and the
# ByteMind website). Anything else is refused before a single byte is written.
ALLOWED_UPDATE_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "www.bytemind.co.nz",
        "bytemind.co.nz",
    }
)

# Pre-release stage ordering: a release outranks every pre-release, and within
# pre-releases rc > beta > alpha > dev.
_STAGE_RANKS = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Return a comparable tuple for a version string.

    The last three elements encode release status so that tuple comparison
    matches human expectations::

        2.0.0      < 2.0.1-beta.1 < 2.0.1-beta.2 < 2.0.1-rc.1 < 2.0.1 < 2.0.2

    Layout: ``(major, minor, patch, build, is_release, stage_rank, stage_num)``.
    Raises ``ValueError`` for strings with no usable numeric components.
    """
    text = str(version_str).strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    # Strip build metadata (everything after '+') — it does not affect order.
    text = text.split("+", 1)[0]
    core, _, pre = text.partition("-")

    numbers: list[int] = []
    for part in core.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
    if not numbers:
        raise ValueError(f"Unparseable version: {version_str!r}")
    while len(numbers) < 3:
        numbers.append(0)
    major, minor, patch = numbers[0], numbers[1], numbers[2]
    build = numbers[3] if len(numbers) > 3 else 0

    if not pre:
        return (major, minor, patch, build, 1, 0, 0)

    stage_rank = 1
    stage_num = 0
    pre_text = pre.strip().lower()
    for name, rank in _STAGE_RANKS.items():
        if pre_text.startswith(name):
            stage_rank = rank
            remainder = pre_text[len(name) :].lstrip(".-_")
            digits = "".join(ch for ch in remainder if ch.isdigit())
            if digits:
                stage_num = int(digits)
            break
    else:
        digits = "".join(ch for ch in pre_text if ch.isdigit())
        if digits:
            stage_num = int(digits)

    return (major, minor, patch, build, 0, stage_rank, stage_num)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a newer version than ``current``."""
    try:
        return _parse_version(candidate) > _parse_version(current)
    except (ValueError, TypeError):
        return False


def _fetch_version_info() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(VERSION_CHECK_URL)
        req.add_header("User-Agent", "ByteProof-UpdateChecker/1.0")
        with urllib.request.urlopen(
            req, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        OSError,
    ):
        return None


def check_for_updates(current_version: str) -> tuple[bool, dict[str, Any] | None]:
    remote = _fetch_version_info()
    if remote is None:
        return False, None
    remote_version = remote.get("version")
    if not remote_version:
        return False, None
    try:
        if is_newer(str(remote_version), current_version):
            return True, remote
    except (ValueError, TypeError):
        return False, None
    return False, None


def _is_allowed_url(url: str) -> bool:
    """Only https URLs on the project's own hosts may be downloaded."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_UPDATE_HOSTS


def _artifact_key(version_info: dict[str, Any]) -> str:
    """The feed field name for this platform's artifact (e.g. ``windows_url``)."""
    system = platform.system()
    if system == "Windows":
        return "windows_url"
    if system == "Darwin" and platform.machine() == "x86_64":
        return "macos_intel_url"
    return "macos_apple_silicon_url"


def _expected_sha256(version_info: dict[str, Any], key: str) -> str | None:
    """Look up the published checksum for a platform artifact, if any.

    Accepts either a ``sha256`` map keyed by artifact field or a flat
    ``sha256_<field>`` entry, so the feed can grow checksums gradually.
    """
    checksums = version_info.get("sha256")
    if isinstance(checksums, dict):
        for candidate in (key, key.removesuffix("_url")):
            value = checksums.get(candidate)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    flat = version_info.get(f"sha256_{key}")
    if isinstance(flat, str) and flat.strip():
        return flat.strip().lower()
    return None


def sha256_of_file(path: str) -> str | None:
    """Return the hex SHA-256 of a file, or None when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def verify_file_sha256(path: str, expected: str | None) -> bool:
    """Whether ``path`` matches ``expected``.

    A missing expectation is treated as "nothing to verify" and passes, so the
    app keeps working with a feed that does not publish checksums yet.
    """
    if not expected:
        return True
    actual = sha256_of_file(path)
    return bool(actual) and actual == expected.strip().lower()


def download_update(
    version_info: dict[str, Any],
    download_dir: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str | None:
    """Download the platform installer, verifying host and checksum.

    Returns the local path on success, or None when the URL is not allowed,
    the download fails, or a published checksum does not match.
    """
    url = _get_download_url(version_info)
    if not url:
        return None
    if not _is_allowed_url(url):
        _log_update(f"refused download from untrusted URL: {url!r}")
        return None
    expected = _expected_sha256(version_info, _artifact_key(version_info))
    filename = os.path.basename(url.split("?")[0].split("#")[0]) or "ByteProof-update"
    dest_path = os.path.join(download_dir, filename)
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "ByteProof-Installer/1.0")
        with urllib.request.urlopen(
            req, timeout=300, context=_ssl_context()
        ) as response:
            final_url = response.geturl()
            if not _is_allowed_url(final_url):
                _log_update(f"refused redirect to untrusted URL: {final_url!r}")
                return None
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
        if not verify_file_sha256(dest_path, expected):
            _log_update(
                "checksum mismatch for downloaded update; discarding "
                f"{os.path.basename(dest_path)}"
            )
            os.remove(dest_path)
            return None
        if expected:
            _log_update(f"update checksum verified: {os.path.basename(dest_path)}")
        else:
            _log_update(
                "update feed published no checksum for "
                f"{os.path.basename(dest_path)}; signature is the only check"
            )
        return dest_path
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return None


def _log_update(message: str) -> None:
    """Write an update-flow diagnostic without importing heavy modules."""
    try:
        from .generic_editing import _debug_log

        _debug_log(f"UPDATE: {message}")
    except Exception:
        pass


def _get_download_url(version_info: dict[str, Any]) -> str | None:
    value = version_info.get(_artifact_key(version_info))
    return value if isinstance(value, str) and value.strip() else None
