"""Launch-at-login support for ByteProof on macOS and Windows."""

import importlib
import os
import platform
import plistlib
import subprocess
import sys

SYSTEM = platform.system()
LAUNCH_AGENT_LABEL = "nz.co.bytemind.byteproof"
LAUNCH_AGENT_PATH = os.path.expanduser(
    f"~/Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist"
)
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_APP_NAME = "ByteProof"


def _program_arguments() -> list[str]:
    """Return the command used to start the app."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    run_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    return [sys.executable, run_script]


def _set_macos(enabled: bool) -> bool:
    args = _program_arguments()
    plist_data = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    plist_bytes = plistlib.dumps(plist_data, fmt=plistlib.FMT_XML)
    os.makedirs(os.path.dirname(LAUNCH_AGENT_PATH), exist_ok=True)
    # Tell launchd about the change; ignore failures so a permissions hiccup
    # never crashes the settings dialog.
    uid = os.getuid()
    try:
        if enabled:
            with open(LAUNCH_AGENT_PATH, "wb") as f:
                f.write(plist_bytes)
            # Unload any previous copy first (updates after an app move).
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
                capture_output=True,
                check=False,
            )
            # Do NOT bootstrap here: RunAtLoad would immediately launch a
            # second copy of ByteProof. The agent is picked up at next login.
        else:
            # Unload by service label so removal works even if the plist path
            # no longer exists.
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
                capture_output=True,
                check=False,
            )
            if os.path.exists(LAUNCH_AGENT_PATH):
                os.remove(LAUNCH_AGENT_PATH)
    except Exception:
        pass
    return True


def _set_windows(enabled: bool) -> bool:
    winreg = importlib.import_module("winreg")

    args = _program_arguments()
    command = f'"{args[0]}"' + ("".join(f' "{a}"' for a in args[1:]))
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        WIN_RUN_KEY,
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        if enabled:
            winreg.SetValueEx(key, WIN_APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, WIN_APP_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)
    return True


def set_launch_at_login(enabled: bool) -> bool:
    """Enable or disable launching ByteProof when the user logs in."""
    try:
        if SYSTEM == "Darwin":
            return _set_macos(enabled)
        if SYSTEM == "Windows":
            return _set_windows(enabled)
    except Exception:
        return False
    return False


def is_launch_at_login() -> bool:
    """Return whether launch-at-login is currently configured."""
    try:
        if SYSTEM == "Darwin":
            return os.path.exists(LAUNCH_AGENT_PATH)
        if SYSTEM == "Windows":
            winreg = importlib.import_module("winreg")
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, WIN_APP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
    except Exception:
        return False
    return False
