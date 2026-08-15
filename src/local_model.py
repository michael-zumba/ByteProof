"""In-app local model engine for ByteProof.

ByteProof can download a small GGUF model (Phi-4 Mini or Qwen3) and run it
entirely on the user's machine through llama.cpp's OpenAI-compatible server.
The first run downloads the runtime (~30 MB) and the model (~1.2-9 GB) with
progress, resume, and SHA-256 verification. After that, proofreading is
private and offline. Local AI remains available in the limited free mode after
the 7-day trial (3 proofreads/day); the $49 license unlocks unlimited use.

The catalog is intentionally conservative: Qwen3 models are Apache 2.0,
Phi-4 Mini is MIT, and the newer MoE options (gpt-oss-20b, Qwen3-30B-A3B) are
Apache 2.0, which are all safe to bundle and redistribute in a commercial app.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any

import certifi

from .settings import LOCAL_MODEL_DIR, RUNTIME_DIR

LOCAL_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 17881

LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
LLAMA_DOWNLOAD_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{asset}"
MODEL_MANIFEST_URL = "https://www.bytemind.co.nz/byteproof-models.json"

# Pinned to a known-good release. b10436 was verified to spawn cleanly from
# Python on this setup (`llama-server --version` exits 0) and to serve a chat
# completion with the flags below. Earlier b10331-era launcher layouts hung
# when spawned from Python, so do not bump without re-running that check.
# Set to None to follow GitHub latest instead.
PINNED_LLAMA_RELEASE: str | None = "b10436"


class DownloadCancelledError(RuntimeError):
    """Raised when the user cancels a model/runtime download."""


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())

# Optional remote manifest override. When ByteMind starts hosting a signed
# manifest (e.g. a fine-tuned ByteProof Qwen3 model), put its URL here and the
# catalog below is replaced by the manifest's `models` list.
REMOTE_MANIFEST_ENABLED = False

MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "qwen3-1.7b",
        "name": "Qwen3 1.7B",
        "params": "1.7B",
        "tag": "Ultra light",
        "file": "Qwen3-1.7B-q4_k_m.gguf",
        "url": "https://huggingface.co/jburnford/dyslexic-writer-qwen3-1.7b/resolve/main/Qwen3-1.7B-q4_k_m.gguf",
        "size_bytes": 1282439232,
        "sha256": "c4ad6b2a7ffa5393066d7b75615c827d951a0c7930799a80764c0ffcb8a6a48d",
        "min_ram_gb": 6,
        "license": "Apache 2.0",
        "description": "Proofreading-tuned. Fastest option for older or low-RAM computers (6 GB+).",
    },
    {
        "id": "qwen3-4b-proofread",
        "name": "Qwen3 4B Proofreading",
        "params": "4B",
        "tag": "Proofreading tuned",
        "file": "Qwen3-4B-q4_k_m.gguf",
        "url": "https://huggingface.co/jburnford/dyslexic-writer-qwen3-4b/resolve/main/Qwen3-4B-q4_k_m.gguf",
        "size_bytes": 2497280608,
        "sha256": "aff288097e38c3498eff321c1325f1596cb7c2e386fc992de193851fd79b0c6e",
        "min_ram_gb": 8,
        "license": "Apache 2.0",
        "description": "Fine-tuned specifically for proofreading; a good alternative to try.",
    },
    {
        "id": "qwen3-4b",
        "name": "Qwen3 4B",
        "params": "4B",
        "tag": "General",
        "file": "Qwen3-4B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
        "size_bytes": 2497280256,
        "sha256": "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
        "min_ram_gb": 8,
        "license": "Apache 2.0",
        "description": "Balanced general model. Handles academic proofreading with good multilingual support.",
    },
    {
        "id": "phi4-mini",
        "name": "Phi-4 Mini",
        "params": "3.8B",
        "tag": "Recommended",
        "file": "Phi-4-mini-instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/lmstudio-community/Phi-4-mini-instruct-GGUF/resolve/main/Phi-4-mini-instruct-Q4_K_M.gguf",
        "size_bytes": 2491874400,
        "sha256": "3c4d3cbdf3006d81444f6c7a5a56eb93d8e0f0e2ba5963b8ab62f9fd42604233",
        "min_ram_gb": 8,
        "license": "MIT",
        "description": "Microsoft's Phi-4 Mini — strong English grammar correction with the safest MIT license.",
    },
    {
        "id": "qwen3-8b",
        "name": "Qwen3 8B",
        "params": "8B",
        "tag": "Powerful",
        "file": "Qwen3-8B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        "size_bytes": 5027783488,
        "sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
        "min_ram_gb": 16,
        "license": "Apache 2.0",
        "description": "Noticeably stronger corrections; best on 16 GB+ machines.",
    },
    {
        "id": "qwen3-14b",
        "name": "Qwen3 14B",
        "params": "14B",
        "tag": "Best quality",
        "file": "Qwen3-14B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf",
        "size_bytes": 9001752960,
        "sha256": "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0",
        "min_ram_gb": 20,
        "license": "Apache 2.0",
        "description": "Highest local quality; needs ~20 GB RAM or a 24 GB+ Apple Silicon Mac.",
    },
    {
        "id": "gpt-oss-20b",
        "name": "GPT-OSS 20B",
        "params": "20.9B (3.6B active)",
        "tag": "Flagship MoE",
        "file": "gpt-oss-20b-Q4_K_M.gguf",
        "url": "https://huggingface.co/ggfox00000/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-Q4_K_M.gguf",
        "size_bytes": 15805136480,
        "sha256": "c19729483d64b0076038d5b6df38dfc37f09312d6034cac90935a84a671c55a9",
        "min_ram_gb": 24,
        "license": "Apache 2.0",
        "description": "OpenAI's open-weight MoE (3.6B active per token) — near-frontier quality with fast decode. Needs a 24 GB+ machine.",
    },
    {
        "id": "qwen3-30b-a3b",
        "name": "Qwen3 30B-A3B",
        "params": "30B (3B active)",
        "tag": "Premium MoE",
        "file": "Qwen3-30B-A3B-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf",
        "size_bytes": 18556685824,
        "sha256": "0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5",
        "min_ram_gb": 32,
        "license": "Apache 2.0",
        "description": "Qwen's MoE flagship (3B active per token) — premium quality for 32 GB+ machines.",
    },
]


def _manifest_enabled() -> bool:
    return REMOTE_MANIFEST_ENABLED


def get_catalog() -> list[dict[str, Any]]:
    """Return the model catalog, optionally overridden by the remote manifest."""
    if not _manifest_enabled():
        return MODEL_CATALOG
    try:
        req = urllib.request.Request(MODEL_MANIFEST_URL, headers={"User-Agent": "ByteProof-Models/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = data.get("models") or []
        if models:
            return models
    except Exception:
        pass
    return MODEL_CATALOG


def get_model(model_id: str) -> dict[str, Any]:
    for model in get_catalog():
        if model["id"] == model_id:
            return model
    raise KeyError(f"Unknown local model: {model_id}")


def resolve_model_id(model_id: str | None) -> str:
    """Return a valid catalog id, falling back to the recommended model."""
    if model_id:
        try:
            get_model(model_id)
            return model_id
        except KeyError:
            pass
    return recommend_model()["id"]


def model_path(model_id: str) -> str:
    return os.path.join(LOCAL_MODEL_DIR, get_model(model_id)["file"])


def is_model_installed(model_id: str) -> bool:
    path = model_path(model_id)
    if not os.path.exists(path):
        return False
    model = get_model(model_id)
    expected = model.get("size_bytes")
    if expected:
        actual = os.path.getsize(path)
        # Allow a tiny difference; downloads are verified by SHA-256 anyway.
        if abs(actual - expected) > 1024 * 1024:
            return False
    return True


def remove_model(model_id: str) -> bool:
    """Delete a downloaded model. Returns False if it could not be removed."""
    path = model_path(model_id)
    part = path + ".part"
    if not os.path.exists(path):
        try:
            if os.path.exists(part):
                os.remove(part)
            return True
        except OSError:
            return False
    try:
        os.remove(path)
        if os.path.exists(part):
            os.remove(part)
        return True
    except OSError:
        return False


def model_size_gb(model_id: str) -> float:
    return get_model(model_id)["size_bytes"] / (1024 ** 3)


def detect_hardware() -> dict[str, Any]:
    """Return lightweight hardware info used for model recommendations."""
    system = platform.system()
    machine = platform.machine().lower()
    is_apple_silicon = system == "Darwin" and machine in ("arm64", "aarch64")
    total_ram_gb = 8.0

    try:
        if system == "Darwin":
            output = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                text=True,
                timeout=5,
            ).strip()
            total_ram_gb = int(output) / (1024 ** 3)
        elif system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            windll = getattr(ctypes, "windll", None)
            if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_ram_gb = stat.ullTotalPhys / (1024 ** 3)
        else:
            total_ram_gb = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3)
    except Exception:
        pass

    total_ram_gb = max(4.0, float(total_ram_gb))
    return {
        "system": system,
        "machine": machine,
        "is_apple_silicon": is_apple_silicon,
        "cpu_count": os.cpu_count() or 4,
        "total_ram_gb": total_ram_gb,
        "display_ram": f"{total_ram_gb:.0f} GB",
        "chip": "Apple Silicon" if is_apple_silicon else ("Intel Mac" if system == "Darwin" else platform.processor() or "CPU"),
    }


def recommend_model(hardware: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pick the best local model for the user's RAM."""
    hw = hardware or detect_hardware()
    ram = hw.get("total_ram_gb", 8.0)
    if ram < 7:
        return get_model("qwen3-1.7b")
    if ram < 16:
        return get_model("phi4-mini")
    if ram < 20:
        return get_model("qwen3-8b")
    return get_model("qwen3-14b")


def download_file(
    url: str,
    dest_path: str,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: int = 60,
) -> str:
    """Download with resume support and optional SHA-256 verification."""
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelledError("Download cancelled by user.")

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    part_path = dest_path + ".part"
    downloaded = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    headers = {"User-Agent": "ByteProof-Downloader/1.0"}
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and downloaded:
            response = None
        else:
            raise RuntimeError(f"Download failed (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Download failed: {exc.reason}") from exc

    total_size = expected_size or 0
    if response is not None:
        status = getattr(response, "status", 200)
        content_length = response.headers.get("Content-Length")
        if status == 200 and downloaded:
            # The server ignored our Range request; restart from scratch.
            downloaded = 0
            with open(part_path, "wb"):
                pass
        if total_size == 0 and content_length:
            total_size = int(content_length) + downloaded
        mode = "ab" if (status == 206 and downloaded) else "wb"
        try:
            with open(part_path, mode) as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelledError("Download cancelled by user.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelledError("Download cancelled by user.")
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total_size, "")
        finally:
            response.close()

    if expected_sha256:
        if progress_callback is not None:
            progress_callback(downloaded, downloaded, "Verifying download…")
        sha = hashlib.sha256()
        with open(part_path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelledError("Download cancelled by user.")
                sha.update(block)
        if sha.hexdigest().lower() != expected_sha256.lower():
            if os.path.exists(part_path):
                os.remove(part_path)
            raise RuntimeError(
                "The downloaded file failed its checksum and was discarded. "
                "Please try again."
            )

    os.replace(part_path, dest_path)
    if progress_callback is not None:
        progress_callback(downloaded, downloaded, "Complete")
    return dest_path


def runtime_binary_path() -> str | None:
    candidates = [
        os.path.join(RUNTIME_DIR, "bin", "llama-server"),
        os.path.join(RUNTIME_DIR, "bin", "llama-server.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Fall back to a recursive search (older layouts).
    if os.path.isdir(RUNTIME_DIR):
        for root, _dirs, files in os.walk(RUNTIME_DIR):
            for name in files:
                if name in ("llama-server", "llama-server.exe"):
                    return os.path.join(root, name)
    return None


def _platform_runtime_asset() -> tuple[str, str]:
    """Return (asset_name, url_format) for this platform."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"llama-{{tag}}-bin-win-cpu-{arch}.zip", LLAMA_DOWNLOAD_URL
    if system == "Darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"llama-{{tag}}-bin-macos-{arch}.tar.gz", LLAMA_DOWNLOAD_URL
    # Linux is not officially distributed by ByteProof yet; keep the code ready.
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return f"llama-{{tag}}-bin-linux-{arch}.tar.gz", LLAMA_DOWNLOAD_URL


def _latest_llama_release(cache_seconds: int = 86400) -> tuple[str, str, str]:
    """Return (tag, asset_name, url) for the latest llama.cpp release."""
    if PINNED_LLAMA_RELEASE:
        asset_pattern, url_template = _platform_runtime_asset()
        tag = PINNED_LLAMA_RELEASE
        asset_name = asset_pattern.format(tag=tag)
        return tag, asset_name, url_template.format(tag=tag, asset=asset_name)

    cache_path = os.path.join(RUNTIME_DIR, "llama-latest.json")
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if time.time() - cached.get("fetched_at", 0) < cache_seconds:
                return cached["tag"], cached["asset"], cached["url"]
        except Exception:
            pass

    asset_pattern, url_template = _platform_runtime_asset()
    request = urllib.request.Request(
        LLAMA_RELEASES_API,
        headers={"User-Agent": "ByteProof-Runtime/1.0", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            release = json.loads(response.read().decode("utf-8"))
        tag = release.get("tag_name") or "b0000"
        asset_name = asset_pattern.format(tag=tag)
        url = url_template.format(tag=tag, asset=asset_name)
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": time.time(), "tag": tag, "asset": asset_name, "url": url}, handle)
        return tag, asset_name, url
    except Exception as exc:
        raise RuntimeError(f"Could not fetch the local AI runtime: {exc}") from exc


def _extract_archive(archive_path: str, extract_dir: str) -> None:
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            try:
                archive.extractall(extract_dir, filter="data")  # pyright: ignore[reportCallIssue]
            except TypeError:
                # Python < 3.12 compatibility.
                archive.extractall(extract_dir)
        return
    with tarfile.open(archive_path, "r:*") as archive:
        try:
            archive.extractall(extract_dir, filter="data")
        except TypeError:
            # Python < 3.12 compatibility.
            archive.extractall(extract_dir)


def _find_server_binary(search_root: str) -> str | None:
    for root, _dirs, files in os.walk(search_root):
        for name in files:
            if name in ("llama-server", "llama-server.exe"):
                return os.path.join(root, name)
    return None


def ensure_runtime(
    progress_callback: Callable[[int, int, str], None] | None = None,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> str:
    """Ensure the llama.cpp server binary is present and return its path."""
    existing = runtime_binary_path()
    if existing and not force:
        return existing

    tag, asset_name, url = _latest_llama_release()
    archive_path = os.path.join(RUNTIME_DIR, asset_name)
    if not os.path.exists(archive_path):
        if progress_callback is not None:
            progress_callback(0, 0, "Downloading the local AI engine…")
        download_file(
            url,
            archive_path,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            timeout=60,
        )

    extract_dir = os.path.join(RUNTIME_DIR, tag)
    if progress_callback is not None:
        progress_callback(0, 0, "Installing the local AI engine…")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir, exist_ok=True)
    _extract_archive(archive_path, extract_dir)

    binary = _find_server_binary(extract_dir)
    if not binary:
        raise RuntimeError("The downloaded AI engine did not contain a server binary.")

    # Keep the full runtime directory (dylibs/symlinks must stay adjacent to
    # the binary) at a stable, version-independent location.
    bin_dir = os.path.join(RUNTIME_DIR, "bin")
    if os.path.isdir(bin_dir):
        shutil.rmtree(bin_dir, ignore_errors=True)
    shutil.copytree(os.path.dirname(binary), bin_dir)
    stable_path = os.path.join(bin_dir, os.path.basename(binary))
    if platform.system() != "Windows":
        os.chmod(stable_path, 0o755)
    shutil.rmtree(extract_dir, ignore_errors=True)
    try:
        os.remove(archive_path)
    except OSError:
        pass
    return stable_path


def ensure_local_model(
    model_id: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Download the runtime and model if needed; return the model file path."""
    ensure_runtime(progress_callback, cancel_event=cancel_event)
    # Keep the app lightweight: free old unused models if storage is over budget.
    try:
        from .cache_cleanup import enforce_storage_budget

        enforce_storage_budget(model_id)
    except Exception:
        pass
    model = get_model(model_id)
    path = model_path(model_id)
    if not is_model_installed(model_id):
        part_path = path + ".part"
        part_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        remaining = max(0, model["size_bytes"] - part_size)
        try:
            free = shutil.disk_usage(os.path.dirname(path) or ".").free
        except OSError:
            free = None
        if free is not None and free < remaining + 512 * 1024 * 1024:
            raise RuntimeError(
                f"Not enough free disk space. {model['name']} needs about "
                f"{model_size_gb(model_id):.1f} GB, and only "
                f"{free / (1024 ** 3):.1f} GB is available."
            )
        if progress_callback is not None:
            progress_callback(
                0,
                model["size_bytes"],
                f"Downloading {model['name']} ({model_size_gb(model_id):.1f} GB)…",
            )
        download_file(
            model["url"],
            path,
            expected_size=model["size_bytes"],
            expected_sha256=model.get("sha256"),
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            timeout=60,
        )
    return path


def find_free_port(preferred: int = DEFAULT_SERVER_PORT) -> int:
    for port in range(preferred, preferred + 64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((LOCAL_SERVER_HOST, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCAL_SERVER_HOST, 0))
        return sock.getsockname()[1]


def _http_get(url: str, timeout: float = 3.0) -> int | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ByteProof-Local/1.0"})
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return response.status
    except Exception:
        return None


def _build_server_cmd(
    runtime_path: str,
    model_path: str,
    model_id: str,
    port: int,
    hardware: dict[str, Any] | None = None,
) -> list[str]:
    """Build the llama-server command line for the current platform.

    Flags are kept compatible with both the previously pinned runtime (b8059)
    and newer releases (b10436+): flash attention is enabled on Apple Silicon
    (Metal), the KV cache uses q8_0 to reduce memory traffic, and mlock keeps
    the model resident on macOS when it comfortably fits in RAM.
    """
    hw = hardware or detect_hardware()
    cmd = [
        runtime_path,
        "--host", LOCAL_SERVER_HOST,
        "--port", str(port),
        "--model", model_path,
        "--ctx-size", "8192",
        "--parallel", "1",
        "--no-webui",
        "--reasoning-budget", "0",
        "--alias", model_id,
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
    ]
    if hw.get("is_apple_silicon"):
        cmd += ["--flash-attn", "on", "--n-gpu-layers", "999"]
    else:
        cmd += ["--threads", str(max(2, hw.get("cpu_count", 4) - 1))]
    if hw.get("system") == "Darwin":
        try:
            model_size_gb = get_model(model_id)["size_bytes"] / (1024 ** 3)
        except KeyError:
            model_size_gb = 0.0
        if hw.get("total_ram_gb", 0) >= model_size_gb + 6:
            cmd += ["--mlock"]
    return cmd


class LocalModelServer:
    """Manages a single llama.cpp server process for ByteProof."""

    def __init__(self, model_id: str | None = None, port: int | None = None) -> None:
        self.model_id = resolve_model_id(model_id)
        self.port = port or find_free_port()
        self.process: subprocess.Popen[str] | None = None
        self.runtime_path = ""
        self.model_path = ""
        self.log_path = os.path.join(RUNTIME_DIR, "server.log")

    @property
    def base_url(self) -> str:
        return f"http://{LOCAL_SERVER_HOST}:{self.port}/v1"

    def is_running(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        return _http_get(f"http://{LOCAL_SERVER_HOST}:{self.port}/health") == 200

    def start(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        wait_timeout: float = 240.0,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if self.is_running():
            return self.base_url
        self.stop()

        self.runtime_path = ensure_runtime(progress_callback, cancel_event=cancel_event)
        self.model_path = ensure_local_model(
            self.model_id, progress_callback, cancel_event=cancel_event
        )

        hw = detect_hardware()
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        cmd = _build_server_cmd(
            self.runtime_path,
            self.model_path,
            self.model_id,
            self.port,
            hw,
        )

        creation_flags = 0
        if platform.system() == "Windows":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        with open(self.log_path, "a", encoding="utf-8") as log:
            self.process = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creation_flags,
            )

        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                self.stop()
                raise DownloadCancelledError("Download cancelled by user.")
            if self.process.poll() is not None:
                raise RuntimeError(
                    "The local AI engine stopped unexpectedly. Check the log at "
                    f"{self.log_path}."
                )
            if self.is_running():
                return self.base_url
            time.sleep(0.5)
        raise RuntimeError(
            "The local AI engine did not finish starting. Check the log at "
            f"{self.log_path}."
        )

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None


_global_server: LocalModelServer | None = None


def get_local_server() -> LocalModelServer | None:
    return _global_server


def start_local_server(
    model_id: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    global _global_server
    model_id = resolve_model_id(model_id)
    if _global_server is None:
        _global_server = LocalModelServer(model_id)
    elif _global_server.model_id != model_id:
        _global_server.stop()
        _global_server = LocalModelServer(model_id)
    return _global_server.start(progress_callback, cancel_event=cancel_event)


def stop_local_server() -> None:
    global _global_server
    if _global_server is not None:
        _global_server.stop()
        _global_server = None


def local_server_info() -> dict[str, Any]:
    if _global_server is None:
        return {"running": False, "model_id": None, "base_url": None, "port": None}
    return {
        "running": _global_server.is_running(),
        "model_id": _global_server.model_id,
        "base_url": _global_server.base_url,
        "port": _global_server.port,
    }


def _reset_for_tests() -> None:
    global _global_server
    if _global_server is not None:
        _global_server.stop()
    _global_server = None


if __name__ == "__main__":
    hw = detect_hardware()
    rec = recommend_model(hw)
    print(json.dumps({**hw, "recommended_model": rec["id"]}, indent=2))
