# pyright: reportAttributeAccessIssue=false
"""Cross-platform global hotkey support for ByteProof.

macOS uses AppKit event monitors (requires Accessibility permission).
Windows uses pynput's GlobalHotKeys (no special permission required).
"""

import os
import platform
from collections.abc import Callable
from typing import Any

SYSTEM = platform.system()


def get_hotkey_log_path() -> str:
    """Return a per-platform path for hotkey debug logs."""
    if SYSTEM == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ByteMind", "ByteProof", "debug_hotkeys.log")
    if SYSTEM == "Darwin":
        return os.path.expanduser("~/Library/Application Support/ByteMind/ByteProof/debug_hotkeys.log")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ByteMind", "ByteProof", "debug_hotkeys.log")


def log_debug(msg: str) -> None:
    try:
        log_path = get_hotkey_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass


class _WindowsHotkeyManager:
    """pynput-based global hotkeys for Windows."""

    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        self.callbacks = hotkeys
        self._global: Any = None

    def start(self, prompt_user: bool = True) -> bool:
        self.stop()
        if not self.callbacks:
            log_debug("No callbacks defined.")
            return True
        try:
            from pynput import keyboard
        except ImportError:
            log_debug("pynput is not available for Windows hotkeys.")
            return False

        try:
            normalized: dict[str, Callable[[], None]] = {}
            for hk_str, cb in self.callbacks.items():
                # Windows has no Command key; Command-style defaults map to Ctrl.
                win_str = hk_str.replace("<cmd>", "<ctrl>")
                normalized[win_str] = cb
            self._global = keyboard.GlobalHotKeys(normalized)
            self._global.daemon = True
            self._global.start()
            log_debug(f"Windows global hotkeys started: {list(normalized)}")
            return True
        except Exception as e:
            log_debug(f"Windows hotkey start error: {e}")
            return False

    def stop(self) -> None:
        if self._global is not None:
            try:
                self._global.stop()
            except Exception as e:
                log_debug(f"Error stopping Windows hotkeys: {e}")
            self._global = None

    def has_permission(self) -> bool:
        return True

    @staticmethod
    def check_permission_silently() -> bool:
        return True


class _MacOSHotkeyManager:
    """AppKit-based global hotkeys for macOS."""

    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        self.callbacks = hotkeys
        self.monitor = None
        self.local_monitor = None
        self.parsed_hotkeys: list[tuple[int, str, set, Callable[[], None]]] = []
        self.permission_granted = False
        self._handler_ref: Any = None
        self._local_handler_ref: Any = None

    def start(self, prompt_user: bool = True) -> bool:
        self.stop()
        log_debug(f"Starting macOS HotkeyManager, prompt={prompt_user}")

        if not self.callbacks:
            log_debug("No callbacks defined.")
            return True

        try:
            import AppKit
            import ApplicationServices
        except ImportError:
            log_debug("AppKit not available.")
            return False

        self._appkit = AppKit
        self._ax = ApplicationServices

        options = {self._ax.kAXTrustedCheckOptionPrompt: prompt_user}
        trusted = bool(self._ax.AXIsProcessTrustedWithOptions(options))
        self.permission_granted = trusted
        log_debug(f"AXIsProcessTrustedWithOptions returned: {trusted}")
        if not trusted:
            return False

        self.parsed_hotkeys = []
        for hk_str, cb in self.callbacks.items():
            flags, char, variants = self._parse_hotkey(hk_str)
            self.parsed_hotkeys.append((flags, char, variants, cb))
            log_debug(f"Parsed hotkey: {hk_str} -> flags={flags}, char={char}, variants={variants}")

        def handler(event: Any) -> None:
            try:
                ev_flags = event.modifierFlags()
                ev_chars = event.charactersIgnoringModifiers()
                log_debug(f"Key down: {ev_chars} flags={ev_flags}")
                if not ev_chars:
                    return
                ev_char = str(ev_chars).lower()

                mask = (
                    self._appkit.NSEventModifierFlagCommand
                    | self._appkit.NSEventModifierFlagShift
                    | self._appkit.NSEventModifierFlagControl
                    | self._appkit.NSEventModifierFlagOption
                )
                ev_flags_masked = ev_flags & mask

                for flags, _char, variants, cb in self.parsed_hotkeys:
                    if ev_flags_masked == flags and ev_char in variants:
                        log_debug(f"Matched hotkey: {variants}")
                        cb()
            except Exception as e:
                log_debug(f"Hotkey handler error: {e}")
                print(f"Hotkey handler error: {e}")

        self._handler_ref = handler

        try:
            self.monitor = self._appkit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                1 << 10, self._handler_ref
            )
            log_debug(f"Added global monitor: {self.monitor}")

            def local_handler(event: Any) -> Any:
                self._handler_ref(event)
                return event

            self._local_handler_ref = local_handler
            self.local_monitor = self._appkit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                1 << 10, self._local_handler_ref
            )
            log_debug(f"Added local monitor: {self.local_monitor}")
        except Exception as e:
            log_debug(f"Error adding global monitor: {e}")

        return self.monitor is not None

    def _parse_hotkey(self, hk_str: str) -> tuple[int, str, set]:
        parts = hk_str.lower().split("+")
        flags = 0
        char = ""
        for p in parts:
            if p == "<cmd>":
                flags |= self._appkit.NSEventModifierFlagCommand
            elif p == "<shift>":
                flags |= self._appkit.NSEventModifierFlagShift
            elif p == "<ctrl>":
                flags |= self._appkit.NSEventModifierFlagControl
            elif p == "<alt>":
                flags |= self._appkit.NSEventModifierFlagOption
            else:
                char = p
        variants = {char}
        if char == ";":
            variants.add(":")
        elif char == "'":
            variants.add('"')
        return flags, char, variants

    def stop(self) -> None:
        if self.monitor:
            try:
                self._appkit.NSEvent.removeMonitor_(self.monitor)
            except Exception as e:
                log_debug(f"Error removing global monitor: {e}")
            self.monitor = None
        if self.local_monitor:
            try:
                self._appkit.NSEvent.removeMonitor_(self.local_monitor)
            except Exception as e:
                log_debug(f"Error removing local monitor: {e}")
            self.local_monitor = None

    def has_permission(self) -> bool:
        return self.permission_granted

    @staticmethod
    def check_permission_silently() -> bool:
        try:
            import ApplicationServices
            return bool(ApplicationServices.AXIsProcessTrusted())
        except Exception:
            return False


class _NullHotkeyManager:
    """Graceful no-op for unsupported platforms."""

    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        self.callbacks = hotkeys

    def start(self, prompt_user: bool = True) -> bool:
        log_debug("Global hotkeys are not supported on this platform.")
        return False

    def stop(self) -> None:
        pass

    def has_permission(self) -> bool:
        return False

    @staticmethod
    def check_permission_silently() -> bool:
        return False


class HotkeyManager:
    """Platform-agnostic facade for global hotkeys."""

    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        if SYSTEM == "Windows":
            self._impl = _WindowsHotkeyManager(hotkeys)
        elif SYSTEM == "Darwin":
            self._impl = _MacOSHotkeyManager(hotkeys)
        else:
            self._impl = _NullHotkeyManager(hotkeys)

    def start(self, prompt_user: bool = True) -> bool:
        return self._impl.start(prompt_user=prompt_user)

    def stop(self) -> None:
        self._impl.stop()

    def has_permission(self) -> bool:
        return self._impl.has_permission()

    @staticmethod
    def check_permission_silently() -> bool:
        if SYSTEM == "Windows":
            return _WindowsHotkeyManager.check_permission_silently()
        if SYSTEM == "Darwin":
            return _MacOSHotkeyManager.check_permission_silently()
        return _NullHotkeyManager.check_permission_silently()
