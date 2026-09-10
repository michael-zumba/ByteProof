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

import hashlib
import os
import platform
import subprocess
import time
from typing import Any

from .settings import get_app_support_dir
from .utils import normalize_text

SYSTEM = platform.system()
CONTEXT_CHARS = 400

# AX roles that imply an editable text context. Read-only selections (PDF
# text, browsed web pages) live in AXStaticText/AXGroup elements instead.
EDITABLE_ROLES = frozenset(
    {"axtextarea", "axtextfield", "axcombobox"}
)

# Browsers accept AXSelectedText writes with a success code but do not
# actually commit them to the page (Chrome/Safari web content). For these
# apps the live apply must go through a real paste instead.
BROWSER_BUNDLE_IDS = frozenset(
    {
        "com.google.chrome",
        "com.apple.safari",
        "com.brave.browser",
        "com.microsoft.edgemac",
        "company.thebrowser.browser",
        "com.operasoftware.opera",
        "com.vivaldi.vivaldi",
    }
)

# Some apps (e.g. ChatGPT) apply the AXSelectedTextRange write
# asynchronously: an immediate readback still shows the old range, so the
# paste-over-range confirmation re-reads for a short window before giving
# up, and also accepts a selected-text match on the original span.
RANGE_CONFIRM_RETRIES = 6
RANGE_CONFIRM_DELAY_S = 0.12

# A paste is consumed asynchronously by the target app. Before restoring the
# user's clipboard the app must be observed holding the pasted text; the short
# settle afterwards covers web views that finish the insert a beat later.
PASTE_CONFIRM_TIMEOUT_S = 1.2
PASTE_CONFIRM_INTERVAL_S = 0.08
PASTE_SETTLE_S = 0.15


def _redact(text: str | None) -> str:
    """Describe loggable text without storing the user's words.

    Diagnostics need to tell "the same text" from "different text" and to see
    sizes, but capture.log is a support artifact and must not accumulate the
    contents of the user's document. Length plus a short digest is enough to
    compare two reads.
    """
    if text is None:
        return "<unreadable>"
    digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
    return f"<len={len(text)} sha={digest}>"


def _debug_log(msg: str) -> None:
    """Append a timestamped line to a capture debug log."""
    try:
        path = os.path.join(get_app_support_dir(), "capture.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = time.strftime("%H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


# The Accessibility subtree walk to find the element that owns the selection is
# the most expensive part of every poll tick. The element for a given app is
# stable in practice, so it is cached and only re-discovered when it stops
# answering or the focused element moves to a different editable field.
# Minimum spacing between two copy keystrokes, whatever asks for them.
MIN_COPY_INTERVAL_S = 0.5
_last_copy_attempt_at = 0.0

_ax_element_cache: dict[int, tuple[float, Any, Any]] = {}
AX_ELEMENT_CACHE_S = 2.5
# Role/settable probes for the editable gate are stable for a given element, so
# they are cached per (app, element) instead of re-probed on every tick.
_editable_cache: dict[tuple[int, Any], tuple[float, bool]] = {}
EDITABLE_CACHE_S = 2.5

_last_logged: dict[str, str] = {}


def _log_once(key: str, msg: str) -> None:
    """Log a message once per key until its content changes.

    The live service polls every 350 ms, so per-attribute diagnostics must
    be deduplicated or capture.log becomes unreadable.
    """
    if _last_logged.get(key) != msg:
        _last_logged[key] = msg
        _debug_log(msg)


def _verify_range_write(
    AS: Any, elements: list[Any], start: int, new_text: str
) -> str:
    """Verify the range now holds new_text: 'ok', 'mismatch', 'unreadable'."""
    value_readable = False
    for el in elements:
        try:
            err, value = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXValueAttribute, None
            )
            if err == 0 and isinstance(value, str):
                value_readable = True
                if (
                    0 <= start
                    and value[start : start + len(new_text)] == new_text
                ):
                    return "ok"
                _debug_log(
                    "ax_replace_range verify: value slice "
                    f"{_redact(value[start : start + len(new_text)])} "
                    f"expected {_redact(new_text)}"
                )
        except Exception:
            continue
    selection_readable = False
    for el in elements:
        try:
            err, text = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXSelectedTextAttribute, None
            )
            if err == 0:
                selection_readable = True
                got = str(text or "")
                if got == new_text or new_text in got:
                    return "ok"
                if got:
                    _debug_log(
                        "ax_replace_range verify: selected text "
                        f"{_redact(got)} expected {_redact(new_text)}"
                    )
                    return "mismatch"
        except Exception:
            continue
    # A collapsed selection after a successful paste is ambiguous; a readable
    # value whose slice still mismatches is strong evidence of failure.
    if value_readable or selection_readable:
        return "mismatch"
    return "unreadable"


def _log_paste_verdict(verdict: str, new_text: str) -> None:
    _debug_log(
        "ax_replace_range paste verify: "
        f"{verdict} expected={_redact(new_text)}"
    )


def _mac_ax_field_value(AS: Any, pid: int) -> str:
    """Read the focused text element's AXValue (empty string when unreadable)."""
    try:
        _as, focused = GenericTextEditor._mac_ax_text_element(pid)
        if focused is None:
            return ""
        err, value = AS.AXUIElementCopyAttributeValue(
            focused, AS.kAXValueAttribute, None
        )
        return str(value) if err == 0 and isinstance(value, str) else ""
    except Exception:
        return ""


def _wait_for_paste_consumed(
    AS: Any,
    target: dict[str, Any],
    new_text: str,
    timeout: float = PASTE_CONFIRM_TIMEOUT_S,
) -> str:
    """Wait until the pasted text is visible in the target app.

    Returns ``"ok"`` when the text is observed, ``"mismatch"`` when the app's
    text is readable but does not contain it, and ``"unreadable"`` when the app
    exposes nothing to compare against. A clipboard paste is delivered
    asynchronously - the keystroke returns long before a busy app has read the
    pasteboard - so waiting here is what stops the app from pasting the user's
    restored clipboard instead.
    """
    readable = False
    deadline = time.monotonic() + timeout
    while True:
        pid = target.get("pid") or 0
        texts: list[str] = []
        try:
            selection = GenericTextEditor._mac_ax_selection(pid)
            if selection:
                texts.append(selection)
        except Exception:
            pass
        try:
            value = _mac_ax_field_value(AS, pid)
            if value:
                texts.append(value)
        except Exception:
            pass
        if any(new_text in text for text in texts):
            return "ok"
        if texts:
            readable = True
        if time.monotonic() >= deadline:
            return "mismatch" if readable else "unreadable"
        time.sleep(PASTE_CONFIRM_INTERVAL_S)


def normalize_selection_text(text: str) -> str:
    """Normalize text for safe before/after comparisons."""
    return normalize_text(text)


def _parse_ax_range(value: Any) -> tuple[int | None, int | None]:
    """Parse an AXValue range into (location, length), tolerating PyObjC shapes."""
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return int(value[0]), int(value[1])
        location = getattr(value, "location", None)
        length = getattr(value, "length", None)
        if location is not None and length is not None:
            return int(location), int(length)
        import ApplicationServices as AS

        ok, unwrapped = AS.AXValueGetValue(value, AS.kAXValueCFRangeType, None)
        if ok and isinstance(unwrapped, (tuple, list)) and len(unwrapped) >= 2:
            return int(unwrapped[0]), int(unwrapped[1])
    except Exception:
        pass
    return None, None


def _parse_ax_rect(value: Any) -> tuple[float, float, float, float] | None:
    """Parse an AX CGRect value into (x, y, width, height), if possible."""
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) == 4:
            return tuple(float(v) for v in value)  # type: ignore[return-value]
        origin = getattr(value, "origin", None)
        size = getattr(value, "size", None)
        if origin is not None and size is not None:
            return (
                float(origin.x),
                float(origin.y),
                float(size.width),
                float(size.height),
            )
    except Exception:
        pass
    return None


def _mac_copy_menu_item(AS: Any, pid: int) -> Any:
    """The app's Edit ▸ Copy menu item, or None when it cannot be found.

    Matching the *key equivalent* rather than the title keeps this working in
    every language, and it skips lookalikes such as Safari's "Copy Search
    Terms" (which carries an extra modifier).
    """
    try:
        app_el = AS.AXUIElementCreateApplication(pid)
        err, bar = AS.AXUIElementCopyAttributeValue(
            app_el, AS.kAXMenuBarAttribute, None
        )
        if err != 0 or bar is None:
            return None
        err, top_items = AS.AXUIElementCopyAttributeValue(
            bar, AS.kAXChildrenAttribute, None
        )
        for top in top_items or []:
            err, menus = AS.AXUIElementCopyAttributeValue(
                top, AS.kAXChildrenAttribute, None
            )
            for menu in menus or []:
                err, entries = AS.AXUIElementCopyAttributeValue(
                    menu, AS.kAXChildrenAttribute, None
                )
                for entry in entries or []:
                    try:
                        e_char, char = AS.AXUIElementCopyAttributeValue(
                            entry, AS.kAXMenuItemCmdCharAttribute, None
                        )
                        if e_char != 0 or str(char).upper() != "C":
                            continue
                        e_mod, modifiers = AS.AXUIElementCopyAttributeValue(
                            entry, AS.kAXMenuItemCmdModifiersAttribute, None
                        )
                        # 0 means "Command only": the plain Copy command.
                        if e_mod == 0 and int(modifiers or 0) != 0:
                            continue
                        return entry
                    except Exception:
                        continue
    except Exception:
        return None
    return None


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

    def browser_url(self, target: dict[str, Any]) -> str:
        """Return the active browser tab URL, when the target is a browser."""
        if SYSTEM == "Darwin":
            return self._mac_browser_url(target.get("bundle_id") or "")
        return ""

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

    def is_frontmost(self, target: dict[str, Any]) -> bool:
        """Whether the target app currently owns the keyboard focus."""
        if SYSTEM != "Darwin":
            return False
        return GenericTextEditor._mac_is_frontmost(target)

    def field_text_at(
        self, target: dict[str, Any], start: int, length: int
    ) -> str:
        """Document text at an absolute range, read without the selection.

        Web views stop reporting ``AXSelectedText`` (and ignore a posted
        Command-C) while another app is frontmost, which is exactly the state
        after the user clicks the suggestion panel. The field's own text is
        still readable, so the range can be verified positionally instead.
        """
        if SYSTEM != "Darwin" or length <= 0 or start < 0:
            return ""
        try:
            import ApplicationServices as AS

            _as, element = GenericTextEditor._mac_ax_text_element(
                target.get("pid") or 0
            )
            if element is None:
                return ""
            err, value = AS.AXUIElementCopyAttributeValue(
                element, AS.kAXValueAttribute, None
            )
            if err != 0 or not isinstance(value, str):
                return ""
            return value[start : start + length]
        except Exception:
            return ""

    @staticmethod
    def idle_seconds() -> float | None:
        """Seconds since the last keyboard or mouse input, system-wide.

        Used to tell "the user is working" from "the user is reading", so the
        clipboard-reading fallback does not fire in the background.
        """
        try:
            import Quartz

            return float(
                Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGAnyInputEventType,
                )
            )
        except Exception:
            return None  # unknown: callers fall back to their old behaviour

    @staticmethod
    def dragged_seconds() -> float | None:
        """Seconds since the last left-button drag.

        A plain click is not a text selection, and posting Command-C without
        one makes the app beep; requiring a recent drag avoids that.
        """
        try:
            import Quartz

            return float(
                Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGEventLeftMouseDragged,
                )
            )
        except Exception:
            return None  # unknown

    @staticmethod
    def mouse_up_seconds() -> float | None:
        """Seconds since the last left-button mouse-up.

        Finishing a drag (or a double-click) is the clearest signal that the
        user just made a selection, which is the only moment a clipboard read
        is worth its side effect (posting Command-C flashes the Edit menu).
        """
        try:
            import Quartz

            return float(
                Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGEventLeftMouseUp,
                )
            )
        except Exception:
            return None  # unknown

    def get_selection_by_copy(
        self, target: dict[str, Any], attempts: int = 2
    ) -> str:
        """Read the selection with a real Cmd+C (clipboard restored).

        Web views (Safari, Chrome) can answer the Accessibility selection
        query with an empty string even while a selection exists. A copy is
        authoritative, so it is used to verify a selection before refusing an
        apply.
        """
        if SYSTEM != "Darwin":
            return ""
        return GenericTextEditor._mac_copy_selection(
            target.get("pid") or 0,
            target.get("name") or "",
            max_attempts=max(1, attempts),
        )

    def invalidate_ax_element(self, pid: int) -> None:
        """Forget the cached Accessibility element for an app."""
        _ax_element_cache.pop(pid, None)

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
    def _mac_ax_focused(pid: int) -> tuple[Any, Any]:
        """Return (ApplicationServices module, focused AX element) or (None, None)."""
        try:
            import ApplicationServices as AS

            app_el = AS.AXUIElementCreateApplication(pid)
            err, focused = AS.AXUIElementCopyAttributeValue(
                app_el, AS.kAXFocusedUIElementAttribute, None
            )
            if err == 0 and focused is not None:
                return AS, focused
        except Exception:
            pass
        return None, None

    @staticmethod
    def _ax_editable(AS: Any, pid: int, found: Any) -> bool:
        """Cached wrapper around the editable probe (called every tick)."""
        key = (pid, id(AS), found)
        cached = _editable_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < EDITABLE_CACHE_S:
            return cached[1]
        verdict = GenericTextEditor._ax_editable_probe(AS, pid, found)
        _editable_cache[key] = (time.monotonic(), verdict)
        return verdict

    @staticmethod
    def _ax_editable_probe(AS: Any, pid: int, found: Any) -> bool:
        """Whether the selection context is editable.

        Two signals, either of which is enough:
        1. The element's AXRole is an editable text role (AXTextArea,
           AXTextField, AXComboBox). Read-only selections (PDF text, browsed
           web pages) live in AXStaticText/AXGroup elements.
        2. A settable write attribute. Note the PyObjC signature: the
           settable flag comes back as an out-parameter, so the call returns
           (error, settable) — the plain return value is just the error code.
        The found element and the app's focused element are both probed
        because apps often focus a container while the selection lives in a
        child.
        """
        elements: list[Any] = [found]
        alt_as, alt_focused = GenericTextEditor._mac_ax_focused(pid)
        if alt_as is not None and alt_focused is not None:
            if alt_focused != found:
                elements.append(alt_focused)
        attributes = (
            getattr(AS, "kAXValueAttribute", None),
            getattr(AS, "kAXSelectedTextAttribute", None),
            getattr(AS, "kAXSelectedTextRangeAttribute", None),
        )
        for el in elements:
            try:
                err, role = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXRoleAttribute, None
                )
                if err == 0 and str(role).lower() in EDITABLE_ROLES:
                    return True
            except Exception:
                pass
            if not hasattr(AS, "AXUIElementIsAttributeSettable"):
                return True  # older PyObjC: cannot probe, stay permissive
            for attr in attributes:
                if attr is None:
                    continue
                try:
                    result = AS.AXUIElementIsAttributeSettable(el, attr, None)
                    if isinstance(result, tuple):
                        err, settable = result
                        if err == 0 and settable:
                            return True
                    elif result:
                        return True
                except Exception:
                    continue
        return False

    @staticmethod
    def _mac_ax_text_element(pid: int) -> tuple[Any, Any]:
        """Find the element that actually owns the editable text.

        Some apps focus a container (scroll area, canvas) while the text view
        with the selection lives one or two levels deeper. Walk a bounded
        subtree of the focused element first, then of the focused window, and
        finally of the application element, returning the first element that
        exposes selection state. A string AXValue is preferred but not
        required: Pages-style canvases sometimes report only the range.
        """
        AS, focused = GenericTextEditor._mac_ax_focused(pid)
        if AS is None:
            return None, None

        def element_score(el: Any) -> int:
            try:
                err_text, text = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXSelectedTextAttribute, None
                )
                if err_text == 0 and text:
                    return 3  # holds the actual non-empty selection
                err_range, _ = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXSelectedTextRangeAttribute, None
                )
                if err_range == 0:
                    return 2
                if err_text == 0:
                    return 1  # supports selection, currently empty
            except Exception:
                pass
            return -1

        def search(roots: list[Any], budget: int, children_cap: int) -> Any:
            """Return the element that actually owns the selection.

            Apps like Gmail expose several editable fields (subject, body);
            the first field in tree order is often the wrong one. Score
            every candidate and prefer the element holding a non-empty
            selection.
            """
            queue: list[Any] = list(roots)
            visited = 0
            best: tuple[int, Any] = (-1, None)
            while queue and visited < budget:
                el = queue.pop(0)
                visited += 1
                score = element_score(el)
                if score > best[0]:
                    best = (score, el)
                    if score == 3:
                        return el  # non-empty selection: this is the one
                try:
                    _, children = AS.AXUIElementCopyAttributeValue(
                        el, AS.kAXChildrenAttribute, None
                    )
                    if children:
                        queue.extend(list(children)[:children_cap])
                except Exception:
                    pass
            return best[1] if best[0] >= 0 else None

        # 1. The focused element is already the text field in most apps.
        if focused is not None and element_score(focused) >= 2:
            GenericTextEditor._cache_ax_element(pid, AS, focused)
            GenericTextEditor._log_element_once(pid, AS, focused, "text-element")
            return AS, focused

        # 2. Reuse the element found on a recent tick when it still answers and
        #    the focus has not moved to a different editable field.
        cached = _ax_element_cache.get(pid)
        if cached is not None:
            stamp, cached_as, cached_el = cached
            if time.monotonic() - stamp < AX_ELEMENT_CACHE_S:
                try:
                    err_role, _role = cached_as.AXUIElementCopyAttributeValue(
                        cached_el, cached_as.kAXRoleAttribute, None
                    )
                    if err_role == 0:
                        return cached_as, cached_el
                except Exception:
                    pass
            _ax_element_cache.pop(pid, None)

        # 3. Full search (containers, canvases, web views).
        found = search([focused], 60, 24)
        if found is None:
            try:
                app_el = AS.AXUIElementCreateApplication(pid)
                _, window = AS.AXUIElementCopyAttributeValue(
                    app_el, AS.kAXFocusedWindowAttribute, None
                )
                if window is not None:
                    found = search([window], 150, 30)
            except Exception:
                pass
        if found is None:
            try:
                found = search([AS.AXUIElementCreateApplication(pid)], 250, 40)
            except Exception:
                pass
        if found is not None:
            GenericTextEditor._cache_ax_element(pid, AS, found)
            GenericTextEditor._log_element_once(
                pid, AS, found, "text-element"
            )
            return AS, found
        return AS, focused

    @staticmethod
    def _cache_ax_element(pid: int, AS: Any, element: Any) -> None:
        _ax_element_cache[pid] = (time.monotonic(), AS, element)

    @staticmethod
    def clear_ax_caches() -> None:
        """Drop cached AX elements (used when the frontmost app changes)."""
        _ax_element_cache.clear()
        _editable_cache.clear()

    @staticmethod
    def _log_element_once(pid: int, AS: Any, element: Any, tag: str) -> None:
        """Log the role/description of a found AX element once per app."""
        try:
            err_role, role = AS.AXUIElementCopyAttributeValue(
                element, AS.kAXRoleAttribute, None
            )
            err_desc, desc = AS.AXUIElementCopyAttributeValue(
                element, AS.kAXRoleDescriptionAttribute, None
            )
            _log_once(
                f"{pid}:{tag}",
                f"AX {tag} pid={pid} role={str(role) if err_role == 0 else '?'}"
                f" desc={str(desc) if err_desc == 0 else '?'}",
            )
        except Exception:
            pass

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
    def _mac_browser_url(bundle_id: str) -> str:
        """Return the active tab URL for supported macOS browsers.

        This is intentionally best-effort: a browser can be running without a
        tab, without AppleScript automation permission, or with a different
        AppleScript dictionary. Returning an empty string means the resolver
        falls back to app-name/exe matching.
        """
        bundle = bundle_id.lower()
        scripts: dict[str, str] = {
            "com.google.chrome": (
                'tell application id "com.google.Chrome" to tell active tab '
                "of front window to return URL"
            ),
            "com.apple.safari": (
                'tell application id "com.apple.Safari" to tell front window '
                "to tell current tab to return URL"
            ),
            "com.brave.browser": (
                'tell application id "com.brave.Browser" to tell active tab '
                "of front window to return URL"
            ),
            "com.microsoft.edgemac": (
                'tell application id "com.microsoft.edgemac" to tell active tab '
                "of front window to return URL"
            ),
            "company.thebrowser.browser": (
                'tell application id "company.thebrowser.Browser" to tell '
                "front window to tell active tab to return URL"
            ),
            "com.operasoftware.opera": (
                'tell application id "com.operasoftware.Opera" to tell active tab '
                "of front window to return URL"
            ),
            "com.vivaldi.vivaldi": (
                'tell application id "com.vivaldi.Vivaldi" to tell active tab '
                "of front window to return URL"
            ),
        }
        script = scripts.get(bundle)
        if not script:
            return ""
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").strip()

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
    def _is_secure_field(pid: int) -> bool:
        """Whether the focused element is a secure (password) text field."""
        try:
            AS, focused = GenericTextEditor._mac_ax_focused(pid)
            if AS is None or focused is None:
                return False
            subrole_attr = getattr(AS, "kAXSubroleAttribute", "AXSubrole")
            err, subrole = AS.AXUIElementCopyAttributeValue(
                focused, subrole_attr, None
            )
            if err == 0 and str(subrole) == "AXSecureTextField":
                return True
            err, role = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXRoleAttribute, None
            )
            return err == 0 and str(role) == "AXSecureTextField"
        except Exception:
            return False

    def _mac_ax_selection(pid: int) -> str:
        """Read selected text via the Accessibility API only (no clipboard)."""
        try:
            AS, focused = GenericTextEditor._mac_ax_focused(pid)
            if AS is not None:
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
        global _last_copy_attempt_at
        try:
            import ApplicationServices as AS
            if not AS.AXIsProcessTrusted():
                return ""
            # Every attempt is a real key equivalent: it flashes the app's Edit
            # menu and beeps when there is nothing to copy. Two of them landing
            # within this window means something is looping, so refuse.
            now = time.monotonic()
            if now - _last_copy_attempt_at < MIN_COPY_INTERVAL_S:
                _debug_log("copy selection skipped: rate limited")
                return ""
            _last_copy_attempt_at = now
            # Ask the app's own Copy command first. Its menu item reports
            # whether anything is selected, and performing it sends no key
            # equivalent - so nothing beeps and no menu flashes. Posting
            # Command-C to an app with nothing selected is what made Mail and
            # Pages beep when they were simply opened.
            entry = _mac_copy_menu_item(AS, pid)
            if entry is not None:
                try:
                    e_enabled, enabled = AS.AXUIElementCopyAttributeValue(
                        entry, AS.kAXEnabledAttribute, None
                    )
                except Exception:
                    e_enabled, enabled = 1, None
                if e_enabled == 0 and enabled is False:
                    _debug_log(
                        "copy selection skipped: the app reports nothing "
                        "selected (no keystroke sent)"
                    )
                    return ""
                if e_enabled == 0:
                    saved_for_menu = _mac_clipboard_string()
                    try:
                        _mac_set_clipboard("")
                        if AS.AXUIElementPerformAction(
                            entry, AS.kAXPressAction
                        ) == 0:
                            time.sleep(0.12)
                            text = _mac_clipboard_string() or ""
                            if text:
                                _debug_log(
                                    "copy selection used the app's Copy menu "
                                    "command"
                                )
                                return text
                    finally:
                        _mac_restore_clipboard(saved_for_menu)

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
                time.sleep(0.25)
                text = _mac_clipboard_string() or ""
                _debug_log(f"copy attempt '{label}' -> {_redact(text)}")
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
            AS, focused = GenericTextEditor._mac_ax_focused(pid)
            selected = ""
            full = ""
            location: int | None = None
            length: int | None = None
            if AS is not None:
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

    def selection_details(self, target: dict[str, Any]) -> dict[str, Any]:
        """Read (text, absolute range, context) through AX only, no clipboard.

        Also reports whether the selection lives in an *editable* context
        (a settable text element), so read-only selections — PDF readers,
        web pages being browsed — can be ignored by the caller.
        """
        result: dict[str, Any] = {
            "text": "",
            "range": None,
            "context_before": "",
            "context_after": "",
            "found": False,
            "editable": False,
            "role": "",
        }
        if SYSTEM != "Darwin":
            return result
        pid = target.get("pid")
        if not pid:
            return result
        bundle = str(target.get("bundle_id", "")).lower()
        if GenericTextEditor._is_secure_field(pid):
            # Never read (or send to a provider) the contents of a password
            # or other secure text field.
            _log_once(
                f"{bundle}:secure-field",
                f"AX selection_details: skipping secure text field pid={pid}",
            )
            return result
        AS, focused = GenericTextEditor._mac_ax_text_element(pid)
        if AS is None:
            _log_once(
                f"{bundle}:no-element",
                f"AX selection_details: no text element found for pid={pid}",
            )
            return result
        result["found"] = True
        try:
            err, role = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXRoleAttribute, None
            )
            if err == 0 and role:
                result["role"] = str(role)
        except Exception:
            pass
        result["editable"] = GenericTextEditor._ax_editable(AS, pid, focused)
        _log_once(
            f"{bundle}:editable",
            f"AX selection_details: editable={result['editable']} "
            f"role={result['role'] or '?'} pid={pid}",
        )
        try:
            err, text = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXSelectedTextAttribute, None
            )
            if err == 0 and text:
                result["text"] = str(text)
            else:
                _log_once(
                    f"{bundle}:selected-text",
                    f"AX selection_details: AXSelectedText err={err} "
                    f"pid={pid}",
                )
            err, value = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXValueAttribute, None
            )
            full = str(value) if err == 0 and isinstance(value, str) else ""
            if not full:
                _log_once(
                    f"{bundle}:value",
                    f"AX selection_details: AXValue err={err} pid={pid}",
                )
            err, range_val = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXSelectedTextRangeAttribute, None
            )
            if err == 0 and range_val is not None:
                location, length = _parse_ax_range(range_val)
                if full and location is not None:
                    # AX ranges are UTF-16 based; convert to code points so
                    # every downstream offset matches Python string indices
                    # (emoji before the selection would otherwise shift
                    # every edit to the wrong place).
                    from .live_preview import utf16_to_codepoint_index

                    cp_location = utf16_to_codepoint_index(full, location)
                    cp_length = (
                        utf16_to_codepoint_index(
                            full, location + (length or 0)
                        )
                        - cp_location
                    )
                    result["range"] = (cp_location, cp_length)
                else:
                    result["range"] = (location, length)
            else:
                _log_once(
                    f"{bundle}:range",
                    f"AX selection_details: AXSelectedTextRange err={err} "
                    f"pid={pid}",
                )
            if not result["text"] and full and result["range"]:
                location, length = result["range"]
                if location is not None and length:
                    result["text"] = full[location : location + length]
            location = (result["range"] or (None, None))[0]
            if result["text"] and full:
                before = ""
                after = ""
                if location is not None and 0 <= location <= len(full):
                    end = location + len(result["text"])
                    before = full[:location]
                    after = full[end:]
                elif result["text"] in full:
                    idx = full.find(result["text"])
                    before = full[:idx]
                    after = full[idx + len(result["text"]) :]
                from .live_preview import CONTEXT_CHARS

                result["context_before"] = before[-CONTEXT_CHARS:]
                result["context_after"] = after[:CONTEXT_CHARS]
        except Exception as exc:
            _log_once(
                f"{bundle}:exception",
                f"AX selection_details error for pid={pid}: {exc}",
            )
        return result

    def ax_bounds_for_range(
        self, target: dict[str, Any], start: int, length: int
    ) -> list[tuple[float, float, float, float]]:
        """Return per-character screen rects for the absolute range via AX."""
        if SYSTEM != "Darwin" or length <= 0:
            return []
        try:
            import ApplicationServices as AS

            if not AS.AXIsProcessTrusted():
                return []
        except Exception:
            return []
        AS, focused = GenericTextEditor._mac_ax_text_element(
            target.get("pid") or 0
        )
        if AS is None:
            return []
        # Convert the code-point offsets to the app's UTF-16 units.
        try:
            from .live_preview import codepoint_to_utf16_index

            err, value = AS.AXUIElementCopyAttributeValue(
                focused, AS.kAXValueAttribute, None
            )
            if err == 0 and isinstance(value, str):
                cp_start = start
                start = codepoint_to_utf16_index(value, cp_start)
                end = codepoint_to_utf16_index(value, cp_start + length)
                length = end - start
        except Exception:
            pass
        rects: list[tuple[float, float, float, float]] = []
        for offset in range(start, start + length):
            try:
                param = AS.AXValueCreate(AS.kAXValueTypeCFRange, (offset, 1))
                err, value = AS.AXUIElementCopyParameterizedAttributeValue(
                    focused,
                    AS.kAXBoundsForRangeParameterizedAttribute,
                    param,
                    None,
                )
                if err != 0 or value is None:
                    continue
                parsed = _parse_ax_rect(
                    AS.AXValueGetValue(value, AS.kAXValueCGRectType, None)[1]
                )
                if parsed is not None:
                    rects.append(parsed)
            except Exception:
                continue
        return rects

    def ax_replace_range(
        self,
        target: dict[str, Any],
        start: int,
        length: int,
        new_text: str,
        allow_direct_paste: bool = False,
        before_text: str | None = None,
    ) -> tuple[bool, str]:
        """Replace an absolute range in the focused field without keystrokes.

        Attempts, in order:
        1. The AXReplaceRangeWithText parameterized action (newer PyObjC).
        2. Selecting the sub-range and writing the selected-text attribute,
           only once the range is confirmed to hold ``before_text``.
        3. A clipboard-preserving paste over the sub-range; when the sub-range
           equals the whole selection, the existing selection is pasted over
           directly without setting a range.
        Every failure logs its AX error code so app-specific behaviour is
        visible in capture.log.

        ``before_text`` is the original text expected at the range. Passing
        it hardens the guards for apps whose AX state lags behind the real
        document: the range is confirmed before any write, and the paste is
        refused unless the range still holds exactly that text.
        """
        if SYSTEM != "Darwin":
            return False, "Live apply is only supported on macOS in this beta."
        _debug_log(
            f"ax_replace_range: pid={target.get('pid')} start={start} "
            f"length={length} direct_paste={allow_direct_paste} "
            f"text={_redact(new_text)}"
        )
        try:
            import ApplicationServices as AS

            if not AS.AXIsProcessTrusted():
                _debug_log("ax_replace_range: AX not trusted")
                return False, (
                    "Accessibility permission is required to apply edits."
                )
        except Exception:
            return False, "Accessibility permission could not be checked."
        AS, focused = GenericTextEditor._mac_ax_text_element(
            target.get("pid") or 0
        )
        if AS is None:
            return False, "Could not read the focused text field."

        elements: list[Any] = [focused]
        alt_as, alt_focused = GenericTextEditor._mac_ax_focused(
            target.get("pid") or 0
        )
        if (
            alt_as is not None
            and alt_focused is not None
            and alt_focused != focused
        ):
            elements.append(alt_focused)

        def _set_range(el: Any) -> tuple[int | None, int | None]:
            # The service works in code points, but the app expects UTF-16
            # code units for its range attribute. Convert before writing so
            # pastes land exactly where intended (emoji-safe). Returns the
            # written (start, length) on success, (None, None) on failure.
            cu_start = start
            cu_end = start + length
            try:
                err, value = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXValueAttribute, None
                )
                if err == 0 and isinstance(value, str):
                    from .live_preview import codepoint_to_utf16_index

                    cu_start = codepoint_to_utf16_index(value, start)
                    cu_end = codepoint_to_utf16_index(
                        value, start + length
                    )
            except Exception:
                pass
            param = AS.AXValueCreate(
                AS.kAXValueTypeCFRange, (cu_start, cu_end - cu_start)
            )
            err = int(
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXSelectedTextRangeAttribute, param
                )
            )
            if err != 0:
                return None, None
            return cu_start, cu_end - cu_start

        def _set_text(el: Any) -> int:
            return int(
                AS.AXUIElementSetAttributeValue(
                    el, AS.kAXSelectedTextAttribute, new_text
                )
            )

        # 1. Parameterized replacement.
        if hasattr(
            AS, "AXUIElementSetParameterizedAttributeValue"
        ) and hasattr(AS, "kAXReplaceRangeWithTextParameterizedAttribute"):
            for el in elements:
                try:
                    # The parameterized attribute takes the same UTF-16 range
                    # as AXSelectedTextRange; passing code points would write
                    # to the wrong offset whenever the text contains astral
                    # characters (emoji). Success is only trusted once the
                    # read-back shows the new text.
                    cu_start, cu_len = _set_range(el)
                    if cu_start is None:
                        continue
                    param = AS.AXValueCreate(
                        AS.kAXValueTypeCFRange, (cu_start, cu_len)
                    )
                    err = AS.AXUIElementSetParameterizedAttributeValue(
                        el,
                        AS.kAXReplaceRangeWithTextParameterizedAttribute,
                        param,
                        new_text,
                    )
                    if err != 0:
                        _debug_log(
                            f"ax_replace_range parameterized err={err} "
                            f"pid={target.get('pid')}"
                        )
                        continue
                    verdict = _verify_range_write(AS, elements, start, new_text)
                    _log_paste_verdict(verdict, new_text)
                    if verdict == "ok":
                        return True, "Applied."
                    if verdict == "unreadable":
                        return True, "Applied — please check the document."
                    _debug_log(
                        "ax_replace_range: parameterized write unverified; "
                        "continuing with the guarded paths"
                    )
                except Exception as exc:
                    _debug_log(f"ax_replace_range parameterized error: {exc}")

        bundle = str(target.get("bundle_id", "")).lower()
        is_browser = bundle in BROWSER_BUNDLE_IDS

        def _range_slice(n: int | None = None) -> str | None:
            want = length if n is None else n
            for el in elements:
                try:
                    err, value = AS.AXUIElementCopyAttributeValue(
                        el, AS.kAXValueAttribute, None
                    )
                    if err == 0 and isinstance(value, str):
                        return value[start : start + want]
                except Exception:
                    continue
            return None

        def _range_confirmed(el: Any, cu_start: int, cu_len: int) -> bool:
            try:
                err, rv = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXSelectedTextRangeAttribute, None
                )
                if err != 0 or rv is None:
                    return False
                loc, ln = _parse_ax_range(rv)
                return loc == cu_start and ln == cu_len
            except Exception:
                return False

        def _selection_holds(el: Any, text: str) -> bool:
            try:
                err, got = AS.AXUIElementCopyAttributeValue(
                    el, AS.kAXSelectedTextAttribute, None
                )
                if err != 0 or not isinstance(got, str):
                    return False
                return got == text or got.strip() == text.strip()
            except Exception:
                return False

        def _confirm_range(el: Any, cu_start: int, cu_len: int) -> bool:
            # Some apps (e.g. ChatGPT) apply the range-selection write
            # asynchronously: the immediate readback still shows the old
            # range. Re-read over a short window, and also accept a
            # selected-text match on the original span as the range having
            # landed, before deciding it was ignored.
            if _range_confirmed(el, cu_start, cu_len):
                return True
            for _attempt in range(RANGE_CONFIRM_RETRIES):
                time.sleep(RANGE_CONFIRM_DELAY_S)
                if _range_confirmed(el, cu_start, cu_len):
                    return True
                if before_text is not None and _selection_holds(
                    el, before_text
                ):
                    _debug_log(
                        "ax_replace_range: range confirmed via selected text"
                    )
                    return True
            _debug_log(
                "ax_replace_range: range confirmation failed after "
                f"{RANGE_CONFIRM_RETRIES} retries"
            )
            return False

        # 2. Select the sub-range, then write the selected-text attribute.
        #    Browsers are skipped: Chrome/Safari accept this write with a
        #    success code but never commit it to the page. Other apps get
        #    the write first, but only once the range verifiably holds the
        #    original text — writing to a stale range would corrupt the
        #    document in apps that apply range writes asynchronously.
        if not is_browser:
            for el in elements:
                try:
                    cu_start, cu_len = _set_range(el)
                    if cu_start is None:
                        _debug_log("ax_replace_range set-range failed")
                        continue
                    if not _confirm_range(el, cu_start, cu_len):
                        _debug_log(
                            "ax_replace_range: range not confirmed before "
                            "the AX write; skipping the text write"
                        )
                        continue
                    if before_text is not None:
                        held = _range_slice()
                        if held != before_text:
                            _debug_log(
                                "ax_replace_range: range no longer holds the "
                                f"original text ({_redact(held)}); skipping "
                                "the AX write"
                            )
                            continue
                    err = _set_text(el)
                    if err != 0:
                        _debug_log(
                            f"ax_replace_range set-selected-text err={err}"
                        )
                        continue
                    verdict = _verify_range_write(AS, elements, start, new_text)
                    _log_paste_verdict(verdict, new_text)
                    if verdict == "ok":
                        return True, "Applied."
                    _debug_log(
                        "ax_replace_range: AX write unverified "
                        f"({verdict}); falling back to paste"
                    )
                    break
                except Exception as exc:
                    _debug_log(
                        f"ax_replace_range attribute fallback error: {exc}"
                    )

        # 3. Clipboard paste over the sub-range — only when it is safe:
        #    the range must actually become selected (some apps silently
        #    ignore range writes), and the document must still hold the
        #    original text at that range, so a paste can never land on
        #    partially modified content.
        old_text = _range_slice()
        if old_text is not None:
            _debug_log(
                f"ax_replace_range: range holds {_redact(old_text)} before apply"
            )
        expected_before = before_text if before_text is not None else old_text

        range_ok = False
        for el in elements:
            try:
                cu_start, cu_len = _set_range(el)
                if cu_start is None:
                    continue
                if _confirm_range(el, cu_start, cu_len):
                    range_ok = True
                    break
            except Exception:
                continue
        if not range_ok and not allow_direct_paste:
            _debug_log(
                "ax_replace_range: cannot confirm the sub-range selection "
                "and the span does not cover the whole selection; giving up"
            )
            return False, "Could not apply the edit in this app."

        current = _range_slice()
        if (
            expected_before is not None
            and current is not None
            and current != expected_before
        ):
            if _range_slice(len(new_text)) == new_text:
                # A delayed AX write from step 2 already applied the edit;
                # nothing left to paste.
                _debug_log("ax_replace_range: edit already present")
                return True, "Applied."
            _debug_log(
                "ax_replace_range: range no longer holds the original text "
                f"({_redact(current)}); refusing to paste"
            )
            return False, "Could not apply the edit in this app."

        def _paste() -> None:
            _mac_set_clipboard(new_text)
            # Activating costs ~0.3 s; when the click already left the target
            # frontmost (the common case) it is skipped entirely.
            if not GenericTextEditor._mac_is_frontmost(target):
                GenericTextEditor._mac_activate(target)
                time.sleep(0.25)
            _post_mac_key(9, target.get("pid") or 0)  # kVK_ANSI_V
            time.sleep(0.2)

        saved = _mac_clipboard_string()
        try:
            _paste()
            verdict = _verify_range_write(AS, elements, start, new_text)
            _log_paste_verdict(verdict, new_text)
            if verdict == "mismatch":
                # Async apps (e.g. ChatGPT) can lag their AX value readback
                # briefly after a paste; re-check once before retrying.
                time.sleep(0.3)
                verdict = _verify_range_write(AS, elements, start, new_text)
                _log_paste_verdict(verdict, new_text)
            if verdict == "mismatch" and range_ok:
                # A retry is only safe while the range still holds the
                # ORIGINAL text — if the document changed at all, a second
                # paste would land on shifted content and corrupt it.
                still_original = _range_slice()
                if (
                    still_original is not None
                    and still_original == expected_before
                ):
                    _debug_log(
                        "ax_replace_range: retrying paste via System Events"
                    )
                    _mac_system_events_key("v", target.get("name") or "")
                    time.sleep(0.4)
                    verdict = _verify_range_write(
                        AS, elements, start, new_text
                    )
                    _log_paste_verdict(verdict, new_text)
                else:
                    _debug_log(
                        "ax_replace_range: range changed after paste "
                        f"({_redact(still_original)}); refusing retry"
                    )
            if verdict == "ok":
                return True, "Applied."
            if verdict == "unreadable":
                return True, "Applied — please check the document."
            return False, "Could not apply the edit in this app."
        finally:
            _mac_restore_clipboard(saved)

    @staticmethod
    def _mac_is_frontmost(target: dict[str, Any]) -> bool:
        """Whether the target app already owns the keyboard focus."""
        pid = target.get("pid")
        if not pid:
            return False
        try:
            from AppKit import NSWorkspace

            active = NSWorkspace.sharedWorkspace().frontmostApplication()
            return bool(active and int(active.processIdentifier()) == int(pid))
        except Exception:
            return False

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
                if not GenericTextEditor._mac_is_frontmost(target):
                    if not GenericTextEditor._mac_activate(target):
                        return False, "Could not activate the target app."
                    time.sleep(0.25)

                keycode = 9  # kVK_ANSI_V
                _post_mac_key(keycode, target.get("pid") or 0)
                # Wait for the app to actually consume the paste BEFORE the
                # user's clipboard is restored. Restoring too early let a slow
                # app paste the OLD clipboard contents into the document while
                # the UI reported success.
                verdict = _wait_for_paste_consumed(AS, target, new_text)
                if verdict == "mismatch":
                    _debug_log(
                        "mac_replace: paste was not observed in the target; "
                        "reporting for review"
                    )
                    return (
                        False,
                        "Could not confirm the paste — please check the document.",
                    )
                if verdict == "unreadable":
                    _debug_log(
                        "mac_replace: target exposes no text to verify the paste"
                    )
                    return True, "Applied — please check the document."
                return True, "Applied."
            finally:
                # The paste event is delivered; a web view may resolve it a
                # moment later, so leave the new text in place briefly before
                # flipping the user's clipboard back.
                time.sleep(PASTE_SETTLE_S)
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
        "browser_url": "",
        "automation_context": None,
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
        try:
            result["browser_url"] = editor.browser_url(app_info)
            from .automation import resolve_automation_context
            from .settings import load_runtime_settings

            result["automation_context"] = resolve_automation_context(
                app_info,
                load_runtime_settings(),
            )
        except Exception as exc:
            result["errors"].append(f"Automation context: {exc}")
        return result
    except Exception as e:
        result["errors"].append(str(e))
        return result
