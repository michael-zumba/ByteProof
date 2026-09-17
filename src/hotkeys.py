# pyright: reportAttributeAccessIssue=false
"""Cross-platform global hotkey support for ByteProof.

macOS uses AppKit event monitors (requires Accessibility permission).
Windows uses pynput's GlobalHotKeys (no special permission required).
"""

import os
import platform
import threading
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


# Shifted punctuation produced by macOS when Shift is held. The old parser
# only knew ':' and '"' for ';' and "'", so a shortcut like Cmd+Shift+. was
# stored as "." but the event arrived as ">" and never matched.
_BASE_TO_SHIFTED = {
    "`": "~",
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    "-": "_",
    "=": "+",
    "[": "{",
    "]": "}",
    "\\": "|",
    ";": ":",
    "'": '"',
    ",": "<",
    ".": ">",
    "/": "?",
}
_SHIFTED_TO_BASE = {shifted: base for base, shifted in _BASE_TO_SHIFTED.items()}
_MODIFIER_ORDER = ("<cmd>", "<ctrl>", "<alt>", "<shift>")


def _canonical_key(key: str) -> str | None:
    """Canonicalise a hotkey's final key so shift spellings compare equal."""
    if not key:
        return None
    lowered = key.lower()
    if lowered in ("<return>", "<enter>", "\r"):
        return "<return>"
    if lowered in ("<esc>", "<escape>", "\x1b"):
        return "<esc>"
    if len(lowered) == 1:
        return _SHIFTED_TO_BASE.get(lowered, lowered)
    return lowered


def _canonical_from_parts(modifiers: set[str], key: str) -> str | None:
    canonical_key = _canonical_key(key)
    if canonical_key is None:
        return None
    ordered = [token for token in _MODIFIER_ORDER if token in modifiers]
    return "+".join([*ordered, canonical_key])


def canonical_hotkey(hotkey: str) -> str | None:
    """Return a comparable form of a stored hotkey (None when unusable)."""
    if not hotkey or not hotkey.strip():
        return None
    modifiers: set[str] = set()
    key = ""
    for part in hotkey.strip().lower().split("+"):
        if part in _MODIFIER_ORDER:
            modifiers.add(part)
        elif part:
            key = part
    if not key:
        return None
    return _canonical_from_parts(modifiers, key)


def _known_hotkey_conflicts() -> dict[str, str]:
    """Common OS and menu shortcuts worth warning about.

    This is only the fast, always-available part; running apps' menu bars are
    checked separately on macOS.
    """
    if SYSTEM == "Darwin":
        return {
            "<cmd>+<space>": "macOS Spotlight",
            "<cmd>+<tab>": "the macOS app switcher",
            "<cmd>+<shift>+3": "macOS screenshot",
            "<cmd>+<shift>+4": "macOS screenshot",
            "<cmd>+<shift>+5": "macOS screenshot",
            "<cmd>+<ctrl>+<space>": "macOS Emoji & Symbols",
            "<cmd>+<alt>+<esc>": "macOS Force Quit",
            "<cmd>+<shift>+q": "macOS Log Out",
            "<cmd>+<q>": "the standard Quit shortcut",
            "<cmd>+<w>": "the standard Close Window shortcut",
            "<cmd>+<s>": "the standard Save shortcut",
            "<cmd>+<v>": "the standard Paste shortcut",
            "<cmd>+<c>": "the standard Copy shortcut",
            "<cmd>+<x>": "the standard Cut shortcut",
            "<cmd>+<z>": "the standard Undo shortcut",
            "<cmd>+<shift>+<z>": "the standard Redo shortcut",
            "<cmd>+<a>": "the standard Select All shortcut",
            "<cmd>+<f>": "the standard Find shortcut",
            "<cmd>+<h>": "the standard Hide shortcut",
            "<cmd>+<m>": "the standard Minimize shortcut",
        }
    if SYSTEM == "Windows":
        return {
            "<ctrl>+<shift>+<esc>": "Windows Task Manager",
            "<ctrl>+<alt>+<delete>": "the Windows security screen",
            "<ctrl>+<alt>+<shift>": "Windows keyboard layout switch",
        }
    return {}


def _menu_modifier_tokens(modifiers: int) -> set[str]:
    tokens: set[str] = set()
    # AXMenuItemCmdModifiers: bit 0 means "no Command key"; bits 1..3 are
    # Shift, Option, Control. Every other menu shortcut carries Command.
    if not modifiers & 0x01:
        tokens.add("<cmd>")
    if modifiers & 0x02:
        tokens.add("<shift>")
    if modifiers & 0x04:
        tokens.add("<alt>")
    if modifiers & 0x08:
        tokens.add("<ctrl>")
    return tokens


def _scan_running_app_hotkeys() -> list[tuple[str, str, str]]:
    """(canonical, app name, menu item title) for running apps' menu bars."""
    try:
        import AppKit
        import ApplicationServices as AS
    except Exception:
        return []
    findings: list[tuple[str, str, str]] = []
    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        apps = list(workspace.runningApplications() or [])[:12]
    except Exception:
        return []
    for app in apps:
        bundle = str(app.bundleIdentifier() or "").lower()
        name = str(app.localizedName() or "")
        if "byteproof" in bundle or "bytemind" in bundle or "byteproof" in name.lower():
            continue
        try:
            app_el = AS.AXUIElementCreateApplication(app.processIdentifier())
            err, menu_bar = AS.AXUIElementCopyAttributeValue(
                app_el, AS.kAXMenuBarAttribute, None
            )
            if err != 0 or menu_bar is None:
                continue
        except Exception:
            continue
        stack: list[tuple[Any, int]] = [(menu_bar, 0)]
        visited = 0
        while stack and visited < 500:
            element, depth = stack.pop()
            visited += 1
            try:
                err_char, char = AS.AXUIElementCopyAttributeValue(
                    element, AS.kAXMenuItemCmdCharAttribute, None
                )
                _err_mods, modifiers = AS.AXUIElementCopyAttributeValue(
                    element, AS.kAXMenuItemCmdModifiersAttribute, None
                )
                if err_char == 0 and char:
                    canonical = _canonical_from_parts(
                        _menu_modifier_tokens(int(modifiers or 0)),
                        str(char),
                    )
                    if canonical is not None:
                        try:
                            err_title, title = AS.AXUIElementCopyAttributeValue(
                                element, AS.kAXTitleAttribute, None
                            )
                        except Exception:
                            err_title, title = 1, ""
                        findings.append(
                            (
                                canonical,
                                name or f"pid {app.processIdentifier()}",
                                str(title) if err_title == 0 else "menu command",
                            )
                        )
            except Exception:
                pass
            if depth >= 4:
                continue
            try:
                err_children, children = AS.AXUIElementCopyAttributeValue(
                    element, AS.kAXChildrenAttribute, None
                )
                if err_children == 0 and children:
                    stack.extend((child, depth + 1) for child in list(children)[:40])
            except Exception:
                pass
    return findings


def _running_app_hotkeys(timeout: float = 1.5) -> list[tuple[str, str, str]]:
    """Scan other apps' menu shortcuts without ever hanging Settings.

    Accessibility calls cannot be cancelled; the scan runs in a daemon thread
    and the caller keeps whatever was found before the timeout. A busy app
    therefore costs at most ``timeout`` seconds and never blocks Save.
    """
    findings: list[tuple[str, str, str]] = []
    thread = threading.Thread(
        target=lambda: findings.extend(_scan_running_app_hotkeys()),
        daemon=True,
    )
    thread.start()
    thread.join(timeout)
    return list(findings)


def find_hotkey_conflicts(
    hotkeys: dict[str, str], check_running_apps: bool = True
) -> list[str]:
    """Return gentle, human-readable conflict messages for ``hotkeys``.

    ``hotkeys`` maps a user-facing action name to its stored shortcut. The
    result is empty when no conflict is known; it never raises.
    """
    canonical: dict[str, list[str]] = {}
    for label, hotkey in hotkeys.items():
        norm = canonical_hotkey(hotkey)
        if norm is not None:
            canonical.setdefault(norm, []).append(label)

    messages: list[str] = []
    known = _known_hotkey_conflicts()
    for norm, labels in canonical.items():
        if len(labels) > 1:
            messages.append(
                f"{' and '.join(labels)} are both assigned to "
                f"{_pretty_hotkey(norm)}."
            )
        known_label = known.get(norm)
        if known_label:
            messages.append(
                f"{labels[0]} uses {_pretty_hotkey(norm)}, which is "
                f"{known_label}."
            )

    if check_running_apps and SYSTEM == "Darwin":
        for norm, app_name, title in _running_app_hotkeys():
            if norm in canonical:
                messages.append(
                    f"{canonical[norm][0]} uses {_pretty_hotkey(norm)}, "
                    f"which is already {app_name} → {title}."
                )
    # Preserve order but never repeat the same sentence.
    unique: list[str] = []
    for message in messages:
        if message not in unique:
            unique.append(message)
    return unique


def _pretty_hotkey(canonical: str) -> str:
    labels = {
        "<cmd>": "Cmd",
        "<ctrl>": "Ctrl",
        "<alt>": "Option",
        "<shift>": "Shift",
        "<return>": "Return",
        "<esc>": "Esc",
    }
    return "+".join(
        labels.get(part, part.upper() if len(part) == 1 else part)
        for part in canonical.split("+")
    )


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
                    matches_char = ev_char in variants
                    matches_escape = _char == "\x1b" and event.keyCode() == 53
                    matches_return = _char == "\r" and event.keyCode() in (36, 76)
                    if ev_flags_masked == flags and (
                        matches_char or matches_escape or matches_return
                    ):
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
            elif p == "<esc>":
                char = "\x1b"
            elif p in ("<return>", "<enter>"):
                char = "\r"
            else:
                char = p
        variants = {char}
        if len(char) == 1:
            # macOS reports the shifted character for Shift+punctuation
            # (Cmd+Shift+. arrives as ">"), so accept both spellings. This is
            # what made an apply-all shortcut stored as "<cmd>+<shift>+." fire
            # only by accident, if at all.
            shifted = _BASE_TO_SHIFTED.get(char)
            if shifted is not None:
                variants.add(shifted)
            base = _SHIFTED_TO_BASE.get(char)
            if base is not None:
                variants.add(base)
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
