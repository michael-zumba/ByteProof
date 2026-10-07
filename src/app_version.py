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
import shlex
import shutil
import ssl
import subprocess
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

# GitHub downloads release assets through a CDN whose hostname has already
# changed once - objects.githubusercontent.com became
# release-assets.githubusercontent.com - and a download follows that redirect,
# so pinning only the old name made the app refuse its own installers
# ("refused redirect to untrusted URL"). Every githubusercontent.com host is
# GitHub-owned, so the whole family is trusted while the exact hosts above stay
# for everything else. The feed URL itself must still be https and exact.
ALLOWED_UPDATE_HOST_SUFFIXES = (".githubusercontent.com",)

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
    if host in ALLOWED_UPDATE_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_UPDATE_HOST_SUFFIXES)


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


# --- macOS unattended install ------------------------------------------------
#
# The app cannot replace its own bundle while it is running without macOS
# complaining that ByteProof is open (and Finder offering to close it first).
# So the app quits itself and hands the swap to a detached shell helper: the
# helper waits for the process to exit, replaces the bundle, clears the
# quarantine flag, and reopens the new build. The old bundle is kept until the
# copy has verified, so a failed update leaves a working app behind and the
# next launch can say so.

_MACOS_UPDATE_TEMPLATE = r"""#!/bin/bash
# ByteProof unattended updater. Written by the app; safe to delete.
set -u

APP_PID=@PID@
DMG=@DMG@
DEST=@DEST@
STAGING=@STAGING@
LOG=@LOG@
RESULT=@RESULT@
APP_NAME=@APP_NAME@
VERSION=@VERSION@

log() {
  printf '%s %s\n' "$(/bin/date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG" 2>/dev/null
}

write_result() {
  printf '{"status":"%s","message":"%s","version":"%s"}\n' "$1" "$2" "$VERSION" > "$RESULT" 2>/dev/null
}

notify() {
  # Mock runs set these so a failure branch cannot pop dialogs on screen.
  [ "${BYTEPROOF_UPDATE_SKIP_NOTIFY:-}" = "1" ] && return 0
  /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"ByteProof Update\" with icon caution" >/dev/null 2>&1 &
}

log "update to $VERSION staged"

# 1. Wait for the old app to quit; it asks itself to quit as the helper starts.
waited=0
while [ "$waited" -lt 300 ]; do
  /bin/kill -0 "$APP_PID" 2>/dev/null || break
  /bin/sleep 0.2
  waited=$((waited + 1))
done
if /bin/kill -0 "$APP_PID" 2>/dev/null; then
  log "the app is still running; the update will be offered again next launch"
  exit 0
fi

# 2. The new bundle was copied next to the old one while the app still ran, so
#    the headless step is only two renames.
if [ ! -d "$STAGING" ]; then
  log "no staged bundle at $STAGING"
  write_result "failed" "The downloaded update did not contain ByteProof.app."
  notify "ByteProof could not install the update automatically. The installer has been opened - drag ByteProof to Applications to finish."
  /usr/bin/open "$DMG" >/dev/null 2>&1
  exit 1
fi

# 3. Swap the bundle, keeping the old one until the new one is in place.
BACKUP="$DEST.update-backup"
/bin/rm -rf "$BACKUP" 2>/dev/null
moved_old=0
if [ -e "$DEST" ]; then
  if /bin/mv "$DEST" "$BACKUP" 2>>"$LOG"; then
    moved_old=1
  else
    log "could not move the old bundle aside"
  fi
fi

swapped=0
/bin/mv "$STAGING" "$DEST" 2>>"$LOG" && swapped=1
if [ "$swapped" != "1" ]; then
  # Only needed when the app lives somewhere the user cannot write; this is
  # the one path macOS may ask for an administrator password.
  if /usr/bin/osascript - "$STAGING" "$DEST" >>"$LOG" 2>&1 <<'APPLESCRIPT'
on run argv
  do shell script "/bin/mv " & quoted form of (item 1 of argv) & " " & quoted form of (item 2 of argv) with administrator privileges
end run
APPLESCRIPT
  then
    swapped=1
  fi
fi

if [ "$swapped" = "1" ]; then
  /usr/bin/xattr -dr com.apple.quarantine "$DEST" 2>/dev/null
  /bin/rm -rf "$BACKUP" 2>/dev/null
  /bin/rm -f "$DMG" 2>/dev/null
  write_result "ok" "ByteProof was updated and relaunched."
  log "installed $VERSION; relaunching"
  if [ "${BYTEPROOF_UPDATE_SKIP_OPEN:-}" != "1" ]; then
    /usr/bin/open "$DEST" >/dev/null 2>&1
  fi
  exit 0
fi

# 4. Put the old app back and hand the DMG to the user.
if [ "$moved_old" = "1" ] && [ -e "$BACKUP" ]; then
  /bin/rm -rf "$DEST" 2>/dev/null
  /bin/mv "$BACKUP" "$DEST" 2>/dev/null
fi
write_result "failed" "ByteProof could not install the update automatically."
log "install failed; the old version is back in place"
notify "ByteProof could not install the update automatically. The installer has been opened - drag ByteProof to Applications to finish."
if [ "${BYTEPROOF_UPDATE_SKIP_OPEN:-}" != "1" ]; then
  /usr/bin/open "$DMG" >/dev/null 2>&1
fi
exit 1
"""


def macos_update_script(
    dmg_path: str,
    staging_path: str,
    dest_path: str,
    pid: int,
    log_path: str,
    result_path: str,
    app_name: str = "ByteProof",
    version: str = "",
) -> str:
    """The detached shell script that swaps the bundle after the app exits."""
    values = {
        "@PID@": str(int(pid)),
        "@DMG@": shlex.quote(dmg_path),
        "@DEST@": shlex.quote(dest_path),
        "@STAGING@": shlex.quote(staging_path),
        "@LOG@": shlex.quote(log_path),
        "@RESULT@": shlex.quote(result_path),
        "@APP_NAME@": shlex.quote(app_name),
        "@VERSION@": shlex.quote(version),
    }
    script = _MACOS_UPDATE_TEMPLATE
    for marker, value in values.items():
        script = script.replace(marker, value)
    return script


def stage_macos_payload(
    dmg_path: str,
    dest_path: str,
    app_name: str = "ByteProof",
) -> str | None:
    """Copy the new app next to the installed one, while the app still runs.

    Doing the slow copy here means the user is still looking at a running app
    if macOS refuses it (the App Management permission, a read-only location),
    so the caller can fall back instead of quitting into a failed install.
    The headless helper then only has to rename two bundles.

    ``dmg_path`` may be a DMG file or an already-mounted directory (tests use
    the directory form). Returns the staging path, or None on any failure.
    """
    staging = dest_path + ".update-staging"
    source_root = dmg_path
    attached = ""
    if os.path.isfile(dmg_path):
        try:
            attach = subprocess.run(
                [
                    "/usr/bin/hdiutil",
                    "attach",
                    dmg_path,
                    "-nobrowse",
                    "-noautoopen",
                    "-readonly",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if attach.returncode != 0:
            return None
        for line in attach.stdout.splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) >= 3 and parts[2].startswith("/Volumes/"):
                source_root = parts[2]
                attached = parts[2]
                break
        if not attached:
            return None

    source = os.path.join(source_root, f"{app_name}.app")
    if not os.path.isdir(source):
        if attached:
            subprocess.run(
                ["/usr/bin/hdiutil", "detach", attached, "-quiet"],
                check=False,
            )
        return None

    try:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        try:
            copied = subprocess.run(
                ["/usr/bin/ditto", source, staging],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired):
            shutil.rmtree(staging, ignore_errors=True)
            return None
        if copied.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            return None
        subprocess.run(
            ["/usr/bin/xattr", "-dr", "com.apple.quarantine", staging],
            capture_output=True,
            check=False,
        )
        return staging
    finally:
        if attached:
            subprocess.run(
                ["/usr/bin/hdiutil", "detach", attached, "-quiet"],
                check=False,
            )


def stage_macos_update(
    dmg_path: str,
    staging_path: str,
    dest_path: str,
    pid: int,
    version: str,
    support_dir: str,
    app_name: str = "ByteProof",
) -> str:
    """Write the updater helper next to the app's support files."""
    folder = os.path.join(support_dir, "update")
    os.makedirs(folder, exist_ok=True)
    script_path = os.path.join(folder, "install-macos-update.sh")
    script = macos_update_script(
        dmg_path=dmg_path,
        staging_path=staging_path,
        dest_path=dest_path,
        pid=pid,
        log_path=os.path.join(folder, "update.log"),
        result_path=os.path.join(support_dir, "update-result.json"),
        app_name=app_name,
        version=version,
    )
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(script)
    os.chmod(script_path, 0o700)
    return script_path


def take_update_result(support_dir: str) -> dict[str, Any] | None:
    """Read (and clear) the result the unattended helper left behind."""
    path = os.path.join(support_dir, "update-result.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return data if isinstance(data, dict) else None
