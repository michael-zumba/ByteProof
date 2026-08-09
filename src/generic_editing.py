# pyright: reportAttributeAccessIssue=false
# pyright: reportMissingModuleSource=false
"""Cross-platform support for proofreading selections in non-Word apps.

Word gets tracked-changes proofreading through the dedicated Word integration.
Every other app (email drafts, browsers, editors, chat windows, etc.) uses this
module: the selected text is read, polished, and replaced with the final text.

macOS reads the selection through the Accessibility API and applies the text by
activating the source app and posting a Command-V event (requires the same
Accessibility permission the app already asks for).

Windows reads/applies through the clipboard with simulated Ctrl+C/Ctrl+V and
verifies the foreground window before applying.
"""

import os
import platform
import subprocess
import time
from typing import Any

from .settings import get_app_support_dir

SYSTEM = platform.system()
CONTEXT_CHARS = 400


def _debug_log(msg: str) -> None:
    """Append to a capture debug log for diagnosing selection issues."""
    try:
        path = os.path.join(get_app_support_dir(), "capture.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def normalize_selection_text(text: str) -> str:
    """Normalize text for safe before/after comparisons."""
    text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\xa0", " ")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    return " ".join(text.split())


def _parse_ax_range(value: Any) -> tuple[int | None, int | None]:
    """Parse an AXValue range into (location, length), tolerating PyObjC shapes."""
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return int(value[0]), int(value[1])
        location = getattr(value, "location", None)
        length = getattr(value, "length", None)
        if location is not None and length is not None:
            return int(location), int(length)
    except Exception:
        pass
    return None, None


def _mac_clipboard_string() -> str | None:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        value = pb.stringForType_(NSPasteboardTypeString)
        return str(value) if value is not None else None
    except Exception:
        return None


def _mac_set_clipboard(text: str) -> None:
    from AppKit import NSPasteboard, NSPasteboardTypeString
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def _mac_restore_clipboard(text: str | None) -> None:
    if text is None:
        return
    _mac_set_clipboard(text)


def _post_mac_key(keycode: int, pid: int = 0) -> None:
    """Post a key press/release, preferring delivery to a specific process."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventPostToPid,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    down = CGEventCreateKeyboardEvent(None, keycode, True)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    if pid:
        try:
            CGEventPostToPid(pid, down)
            CGEventPostToPid(pid, up)
            return
        except Exception:
            pass
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def _mac_system_events_key(key: str, app_name: str) -> None:
    """Send a Command+key keystroke via System Events (reliable for Office)."""
    safe_name = app_name.replace('"', '\\"')
    script = (
        'tell application "System Events" to tell process "'
        + safe_name
        + '" to keystroke "'
        + key
        + '" using command down'
    )
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        check=False,
    )


def _win_clipboard_text() -> str | None:
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return str(data) if data else ""
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _win_set_clipboard(text: str) -> bool:
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def _win_restore_clipboard(text: str | None) -> None:
    if text is None:
        return
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            if text:
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass


class GenericTextEditor:
    """Read/replace selected text in whatever app is in front."""

    def frontmost_app(self) -> dict[str, Any]:
        if SYSTEM == "Darwin":
            return self._mac_frontmost_app()
        if SYSTEM == "Windows":
            return self._win_frontmost_app()
        return {}

    def running_apps(self) -> list[dict[str, Any]]:
        """Return non-ByteProof running apps (used as target candidates)."""
        if SYSTEM == "Darwin":
            try:
                from AppKit import NSWorkspace
                apps: list[dict[str, Any]] = []
                for app in NSWorkspace.sharedWorkspace().runningApplications():
                    bundle = app.bundleIdentifier() or ""
                    name = app.localizedName() or ""
                    if "bytemind" in bundle.lower() or "byteproof" in name.lower():
                        continue
                    apps.append(
                        {
                            "pid": app.processIdentifier(),
                            "name": name,
                            "bundle_id": bundle,
                        }
                    )
                return apps
            except Exception:
                return []
        if SYSTEM == "Windows":
            return GenericTextEditor._win_running_apps()
        return []

    @staticmethod
    def is_word(target: dict[str, Any]) -> bool:
        bundle = str(target.get("bundle_id", "")).lower()
        name = str(target.get("name", "")).lower()
        exe = str(target.get("exe", "")).lower()
        return (
            "com.microsoft.word" in bundle
            or "microsoft word" in name
            or exe.endswith("winword.exe")
        )

    def get_selection(self, target: dict[str, Any]) -> str:
        if SYSTEM == "Darwin":
            return self._mac_selection(target)
        if SYSTEM == "Windows":
            return self._win_selection(target)
        return ""

    def get_selection_light(self, target: dict[str, Any]) -> str:
        """Read the selection with at most one keystroke (for verification).

        Used before applying so a single failed copy cannot produce repeated
        system beeps while still verifying the selection is unchanged.
        """
        if SYSTEM == "Darwin":
            pid = target.get("pid")
            ax = GenericTextEditor._mac_ax_selection(pid or 0)
            if ax:
                return ax
            return GenericTextEditor._mac_copy_selection(
                pid or 0, target.get("name") or "", max_attempts=1
            )
        if SYSTEM == "Windows":
            return self._win_selection(target)
        return ""

    def get_selection_info(
        self, target: dict[str, Any]
    ) -> tuple[str, str, str]:
        """Return (selected_text, context_before, context_after)."""
        if SYSTEM == "Darwin":
            return self._mac_selection_info(target)
        if SYSTEM == "Windows":
            return self._win_selection_info(target)
        return "", "", ""

    def get_selection_ax_only(self, target: dict[str, Any]) -> str:
        """Read selected text without touching the clipboard or the UI.

        Used to probe background apps safely: never sends keystrokes, so it
        cannot copy text from the wrong (frontmost) app.
        """
        if SYSTEM == "Darwin":
            return GenericTextEditor._mac_ax_selection(target.get("pid") or 0)
        return ""

    def activate(self, target: dict[str, Any]) -> bool:
        """Bring the target app to the front (used when ByteProof is active)."""
        if SYSTEM == "Darwin":
            return self._mac_activate(target)
        if SYSTEM == "Windows":
            return self._win_activate(target)
        return False

    @staticmethod
    def permission_status() -> tuple[bool, str]:
        """Return (ready, message) explaining whether generic editing works."""
        if SYSTEM == "Darwin":
            try:
                import ApplicationServices as AS
                if AS.AXIsProcessTrusted():
                    return True, ""
                return False, (
                    "Accessibility permission is required to read and replace "
                    "text in other apps. Enable it in System Settings > "
                    "Privacy & Security > Accessibility, then try again."
                )
            except Exception:
                return False, "Accessibility permission could not be checked."
        if SYSTEM == "Windows":
            return True, ""
        return False, "This platform is not supported yet."

    def replace_selection(
        self, target: dict[str, Any], new_text: str
    ) -> tuple[bool, str]:
        if SYSTEM == "Darwin":
            return self._mac_replace(target, new_text)
        if SYSTEM == "Windows":
            return self._win_replace(target, new_text)
        return False, "This platform is not supported yet."

    # --- macOS ---

    @staticmethod
    def _mac_frontmost_app() -> dict[str, Any]:
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return {}
            return {
                "pid": app.processIdentifier(),
                "name": app.localizedName() or "",
                "bundle_id": app.bundleIdentifier() or "",
            }
        except Exception:
            return {}

    @staticmethod
    def _mac_selection(target: dict[str, Any]) -> str:
        pid = target.get("pid")
        if not pid:
            return ""
        ax_text = GenericTextEditor._mac_ax_selection(pid)
        if ax_text:
            return ax_text
        # Some apps (e.g., Mail) do not expose the selected text through the
        # Accessibility API even when they support copying. Fall back to a real
        # Cmd+C and read the clipboard.
        return GenericTextEditor._mac_copy_selection(pid, target.get("name") or "")

    @staticmethod
    def _mac_ax_selection(pid: int) -> str:
        """Read selected text via the Accessibility API only (no clipboard)."""
        try:
            import ApplicationServices as AS
            app_el = AS.AXUIElementCreateApplication(pid)
            err, focused = AS.AXUIElementCopyAttributeValue(
                app_el, AS.kAXFocusedUIElementAttribute, None
            )
            if err == 0 and focused is not None:
                err, text = AS.AXUIElementCopyAttributeValue(
                    focused, AS.kAXSelectedTextAttribute, None
                )
                if err == 0 and text:
                    return str(text)
        except Exception:
            pass
        return ""

    @staticmethod
    def _mac_copy_selection(pid: int = 0, app_name: str = "", max_attempts: int = 3) -> str:
        """Copy the current selection via Cmd+C and return the clipboard text."""
        try:
            import ApplicationServices as AS
            if not AS.AXIsProcessTrusted():
                return ""
            saved = _mac_clipboard_string()
            text = ""
            keycode = 8  # kVK_ANSI_C
            attempts = [
                ("process", pid),
                ("system", 0),
                ("system_events", app_name),
            ][:max_attempts]
            for label, value in attempts:
                if label == "system_events" and not value:
                    continue
                # Clear the pasteboard first so a failed copy (no selection, or
                # an app that does not support copying) cannot be mistaken for
                # the selection by returning whatever was on the clipboard.
                _mac_set_clipboard("")
                if label == "process":
                    _post_mac_key(keycode, value)
                elif label == "system":
                    _post_mac_key(keycode, 0)
                else:
                    _mac_system_events_key("c", value)
                time.sleep(0.4)
                text = _mac_clipboard_string() or ""
                _debug_log(
                    f"copy attempt '{label}' -> "
                    f"{len(text)} chars, first={text[:40]!r}"
                )
                if text:
                    break
            _mac_restore_clipboard(saved)
            return text
        except Exception:
            return ""

    @staticmethod
    def _mac_selection_info(target: dict[str, Any]) -> tuple[str, str, str]:
        pid = target.get("pid")
        if not pid:
            return "", "", ""
        try:
            import ApplicationServices as AS
            app_el = AS.AXUIElementCreateApplication(pid)
            err, focused = AS.AXUIElementCopyAttributeValue(
                app_el, AS.kAXFocusedUIElementAttribute, None
            )
            selected = ""
            full = ""
            location: int | None = None
            length: int | None = None
            if err == 0 and focused is not None:
                err, text = AS.AXUIElementCopyAttributeValue(
                    focused, AS.kAXSelectedTextAttribute, None
                )
                if err == 0 and text:
                    selected = str(text)
                err_range, range_val = AS.AXUIElementCopyAttributeValue(
                    focused, AS.kAXSelectedTextRangeAttribute, None
                )
                if err_range == 0 and range_val is not None:
                    location, length = _parse_ax_range(range_val)
                err_value, value = AS.AXUIElementCopyAttributeValue(
                    focused, AS.kAXValueAttribute, None
                )
                if err_value == 0 and isinstance(value, str):
                    full = value

            if not selected:
                selected = GenericTextEditor._mac_copy_selection(
                    pid, target.get("name") or ""
                )

            before = ""
            after = ""
            if selected and full:
                if location is not None and 0 <= location <= len(full):
                    end = location + (length if length is not None else len(selected))
                    end = max(location, min(len(full), end))
                    before = full[:location]
                    after = full[end:]
                elif selected in full:
                    # Some apps expose the value but not the range; find the
                    # selection inside the field's text.
                    idx = full.find(selected)
                    before = full[:idx]
                    after = full[idx + len(selected):]

            if len(before) > CONTEXT_CHARS:
                before = before[-CONTEXT_CHARS:]
            if len(after) > CONTEXT_CHARS:
                after = after[:CONTEXT_CHARS]
            return selected, before, after
        except Exception:
            return "", "", ""

    @staticmethod
    def _mac_activate(target: dict[str, Any]) -> bool:
        pid = target.get("pid")
        if not pid:
            return False
        try:
            from AppKit import (
                NSApplicationActivateIgnoringOtherApps,
                NSRunningApplication,
            )
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False
            return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
        except Exception:
            return False

    @staticmethod
    def _mac_replace(target: dict[str, Any], new_text: str) -> tuple[bool, str]:
        try:
            import ApplicationServices as AS
            if not AS.AXIsProcessTrusted():
                return False, (
                    "Accessibility permission is required to apply text to other "
                    "apps. Enable it in System Settings > Privacy & Security > "
                    "Accessibility, then try again."
                )

            saved_clipboard = _mac_clipboard_string()
            _mac_set_clipboard(new_text)
            try:
                if not GenericTextEditor._mac_activate(target):
                    return False, "Could not activate the target app."
                time.sleep(0.3)

                keycode = 9  # kVK_ANSI_V
                _post_mac_key(keycode, target.get("pid") or 0)
                time.sleep(0.4)
                return True, "Applied."
            finally:
                if saved_clipboard is not None:
                    _mac_set_clipboard(saved_clipboard)
        except Exception as e:
            return False, str(e)

    # --- Windows ---

    @staticmethod
    def _win_activate(target: dict[str, Any]) -> bool:
        try:
            import win32gui
            hwnd = target.get("hwnd")
            if hwnd:
                win32gui.SetForegroundWindow(hwnd)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _win_frontmost_app() -> dict[str, Any]:
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return {}
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = win32gui.GetWindowText(hwnd)
            exe = ""
            try:
                handle = win32api.OpenProcess(
                    win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                try:
                    exe = win32process.GetModuleFileNameEx(handle, 0) or ""
                finally:
                    win32api.CloseHandle(handle)
            except Exception:
                pass
            return {
                "hwnd": hwnd,
                "pid": pid,
                "name": name or "",
                "exe": exe,
            }
        except Exception:
            return {}

    @staticmethod
    def _win_running_apps() -> list[dict[str, Any]]:
        """Enumerate visible top-level windows as target candidates."""
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
            apps: list[dict[str, Any]] = []
            seen_pids: set[int] = set()

            def _enum_callback(hwnd: int, _extra: Any) -> bool:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in seen_pids:
                    return True
                seen_pids.add(pid)
                exe = ""
                try:
                    handle = win32api.OpenProcess(
                        win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                    )
                    try:
                        exe = win32process.GetModuleFileNameEx(handle, 0) or ""
                    finally:
                        win32api.CloseHandle(handle)
                except Exception:
                    pass
                apps.append(
                    {
                        "hwnd": hwnd,
                        "pid": pid,
                        "name": title,
                        "exe": exe,
                    }
                )
                return True

            win32gui.EnumWindows(_enum_callback, None)
            return apps
        except Exception:
            return []

    @staticmethod
    def _win_selection(target: dict[str, Any]) -> str:
        try:
            from pynput import keyboard
        except ImportError:
            return ""
        saved = _win_clipboard_text()
        try:
            kb = keyboard.Controller()
            text = ""
            for _ in range(2):
                # Clear first so a failed Ctrl+C never returns stale clipboard.
                if not _win_set_clipboard(""):
                    break
                with kb.pressed(keyboard.Key.ctrl):
                    kb.press("c")
                    kb.release("c")
                time.sleep(0.25)
                text = _win_clipboard_text() or ""
                if text:
                    break
            return text
        except Exception:
            return ""
        finally:
            _win_restore_clipboard(saved)

    @staticmethod
    def _win_selection_info(target: dict[str, Any]) -> tuple[str, str, str]:
        selected = GenericTextEditor._win_selection(target)
        if not selected:
            return "", "", ""
        before = ""
        after = ""
        try:
            import uiautomation as auto  # pyright: ignore[reportMissingImports]
            control = auto.GetFocusedControl()
            full = ""
            try:
                full = control.GetValuePattern().Value or ""
            except Exception:
                pass
            if not full:
                try:
                    full = control.GetTextPattern().DocumentRange.GetText(-1) or ""
                except Exception:
                    pass
            if isinstance(full, str) and selected in full:
                idx = full.find(selected)
                before = full[:idx]
                after = full[idx + len(selected):]
                if len(before) > CONTEXT_CHARS:
                    before = before[-CONTEXT_CHARS:]
                if len(after) > CONTEXT_CHARS:
                    after = after[:CONTEXT_CHARS]
        except Exception:
            pass
        return selected, before, after

    @staticmethod
    def _win_replace(target: dict[str, Any], new_text: str) -> tuple[bool, str]:
        try:
            import win32gui
            from pynput import keyboard
        except ImportError:
            return False, "Required Windows components are not available."
        saved = _win_clipboard_text()
        try:
            if not _win_set_clipboard(new_text):
                return False, "Could not write to the clipboard."
            hwnd = target.get("hwnd")
            if hwnd:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            time.sleep(0.15)
            kb = keyboard.Controller()
            with kb.pressed(keyboard.Key.ctrl):
                kb.press("v")
                kb.release("v")
            time.sleep(0.4)
            return True, "Applied."
        except Exception as e:
            return False, str(e)
        finally:
            _win_restore_clipboard(saved)


_editor_instance: GenericTextEditor | None = None


def get_generic_editor() -> GenericTextEditor:
    global _editor_instance
    if _editor_instance is None:
        _editor_instance = GenericTextEditor()
    return _editor_instance


def capture_diagnostics() -> dict[str, Any]:
    """Report the frontmost app and selected text for debugging."""
    result: dict[str, Any] = {
        "platform": SYSTEM,
        "frontmost_app": {},
        "permission_ok": False,
        "ax_text": "",
        "selected_text": "",
        "context_before_len": 0,
        "context_after_len": 0,
        "clipboard_fallback_used": False,
        "errors": [],
    }
    try:
        editor = get_generic_editor()
        app_info = editor.frontmost_app()
        result["frontmost_app"] = app_info
        if not app_info:
            result["errors"].append("Could not determine the frontmost app.")
            return result

        permission_ok, permission_msg = editor.permission_status()
        result["permission_ok"] = permission_ok
        if not permission_ok:
            result["errors"].append(permission_msg)
            return result

        if SYSTEM == "Darwin":
            result["ax_text"] = GenericTextEditor._mac_ax_selection(app_info.get("pid") or 0)

        text, before, after = editor.get_selection_info(app_info)
        result["selected_text"] = text
        result["selected_text_preview"] = text[:120]
        result["context_before_len"] = len(before)
        result["context_after_len"] = len(after)
        result["is_word"] = GenericTextEditor.is_word(app_info)
        result["mode"] = "word" if result["is_word"] else "generic"
        result["clipboard_fallback_used"] = (
            SYSTEM == "Darwin" and not result["ax_text"]
        )
        return result
    except Exception as e:
        result["errors"].append(str(e))
        return result
