"""Cache maintenance for ByteProof.

ByteProof stays lightweight by enforcing simple rules:

- Logs never grow beyond 1 MB (rotated down to the last 256 KB).
- Partial downloads older than 7 days are deleted.
- Downloaded runtime archives and old runtime extract folders are removed.
- At most MAX_MODELS_KEPT models are stored; the active model is never
  removed automatically.
- Before a new model download, enough space is freed so the total model
  storage stays under DEFAULT_MAX_TOTAL_MODEL_BYTES.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any

from .settings import APP_SUPPORT_DIR, LOCAL_MODEL_DIR, RUNTIME_DIR

MAX_MODELS_KEPT = 2
DEFAULT_MAX_TOTAL_MODEL_BYTES = 16 * 1024 * 1024 * 1024  # 16 GB
STALE_FILE_DAYS = 7
MAX_LOG_BYTES = 1024 * 1024  # 1 MB
LOG_TAIL_BYTES = 256 * 1024  # keep the last 256 KB

LOG_PATHS = (
    os.path.join(APP_SUPPORT_DIR, "capture.log"),
    os.path.join(APP_SUPPORT_DIR, "debug_hotkeys.log"),
    os.path.join(RUNTIME_DIR, "server.log"),
)


def _file_age_days(path: str) -> float:
    try:
        return (time.time() - os.path.getmtime(path)) / 86400.0
    except OSError:
        return 0.0


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _truncate_log(path: str) -> int:
    """Shrink an oversized log to its last LOG_TAIL_BYTES. Returns bytes freed."""
    try:
        size = os.path.getsize(path)
        if size <= MAX_LOG_BYTES:
            return 0
        with open(path, "rb") as f:
            f.seek(max(0, size - LOG_TAIL_BYTES))
            tail = f.read()
        # Drop the first (possibly partial) line so the log stays readable.
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1 :]
        with open(path, "wb") as f:
            f.write(tail)
        return size - os.path.getsize(path)
    except OSError:
        return 0


def cleanup_logs() -> int:
    freed = 0
    for path in LOG_PATHS:
        if os.path.exists(path):
            freed += _truncate_log(path)
    return freed


def cleanup_stale_partials() -> int:
    """Remove interrupted downloads older than STALE_FILE_DAYS."""
    freed = 0
    for directory in (LOCAL_MODEL_DIR, RUNTIME_DIR):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".part"):
                continue
            path = os.path.join(directory, name)
            if _file_age_days(path) >= STALE_FILE_DAYS:
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                except OSError:
                    pass
    return freed


def cleanup_runtime_artifacts() -> int:
    """Remove downloaded runtime archives and leftover extract folders."""
    freed = 0
    if not os.path.isdir(RUNTIME_DIR):
        return 0
    for name in os.listdir(RUNTIME_DIR):
        path = os.path.join(RUNTIME_DIR, name)
        try:
            if os.path.isfile(path) and name.endswith(
                (".zip", ".tar.gz", ".tgz")
            ):
                if _file_age_days(path) >= STALE_FILE_DAYS:
                    freed += os.path.getsize(path)
                    os.remove(path)
            elif os.path.isdir(path) and name != "bin":
                # Extract staging folders are disposable; the stable copy
                # lives in bin/.
                freed += _dir_size(path)
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    return freed


def installed_model_ids() -> list[str]:
    from .local_model import get_catalog, is_model_installed

    return [
        model["id"]
        for model in get_catalog()
        if is_model_installed(model["id"])
    ]


def _model_mtime(model_id: str) -> float:
    from .local_model import model_path

    try:
        return os.path.getmtime(model_path(model_id))
    except OSError:
        return 0.0


def remove_oldest_inactive_models(
    active_model_id: str | None,
    max_models: int = MAX_MODELS_KEPT,
) -> int:
    """Remove oldest unused models beyond the cap. Returns bytes freed."""
    installed = installed_model_ids()
    if len(installed) <= max_models:
        return 0
    candidates = [
        model_id
        for model_id in installed
        if model_id != active_model_id
    ]
    candidates.sort(key=_model_mtime)
    freed = 0
    remaining = set(installed)
    for model_id in candidates:
        if len(remaining) <= max_models:
            break
        try:
            from .local_model import model_path

            freed += os.path.getsize(model_path(model_id))
        except OSError:
            pass
        from .local_model import remove_model

        if remove_model(model_id):
            remaining.discard(model_id)
    return freed


def enforce_storage_budget(
    active_model_id: str | None,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_MODEL_BYTES,
) -> int:
    """Free space before a new download so model storage stays under budget."""
    total = _dir_size(LOCAL_MODEL_DIR)
    if total <= max_total_bytes:
        return 0
    installed = installed_model_ids()
    candidates = [
        model_id
        for model_id in installed
        if model_id != active_model_id
    ]
    candidates.sort(key=_model_mtime)
    freed = 0
    for model_id in candidates:
        if total - freed <= max_total_bytes:
            break
        try:
            from .local_model import model_path

            freed += os.path.getsize(model_path(model_id))
        except OSError:
            pass
        from .local_model import remove_model

        remove_model(model_id)
    return freed


def local_storage_usage() -> dict[str, int]:
    return {
        "models": _dir_size(LOCAL_MODEL_DIR),
        "runtime": _dir_size(RUNTIME_DIR),
        "logs": sum(
            os.path.getsize(path)
            for path in LOG_PATHS
            if os.path.exists(path)
        ),
        "total": _dir_size(LOCAL_MODEL_DIR) + _dir_size(RUNTIME_DIR),
    }


def cleanup_cache(active_model_id: str | None = None) -> dict[str, Any]:
    """Run all maintenance rules and report bytes freed per category."""
    return {
        "logs_freed": cleanup_logs(),
        "partials_freed": cleanup_stale_partials(),
        "runtime_freed": cleanup_runtime_artifacts(),
        "models_freed": remove_oldest_inactive_models(active_model_id),
    }
