# pyright: reportAttributeAccessIssue=false
"""Small cross-platform sound helper for ByteProof."""

import os
import platform
import subprocess

from .settings import resource_path

START_SOUND_RELATIVE = os.path.join("sounds", "proofread_start.wav")


def _sound_path() -> str | None:
    path = resource_path(START_SOUND_RELATIVE)
    return path if os.path.exists(path) else None


def play_start_sound() -> None:
    """Play the proofread-start sound without blocking the UI."""
    path = _sound_path()
    if not path:
        return
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Windows":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        # Sound is a nicety; never let it break proofreading.
        pass
