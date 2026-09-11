"""Selection-triggered suggestion panel (no in-place document marks).

The original overlay-and-underline model fought the platform in Word (native
formatting left traces), Chrome/Gmail (bounds flicker), and Mail/Pages (no AX
selection). The current model is deliberately simpler and non-invasive:

1. The user selects text in any app that exposes a selection.
2. After a debounce, ByteProof asks the AI for a compact edit list (cached).
3. A non-activating panel appears with pinpoint word-level diffs and
   per-edit Apply buttons plus Apply all.
4. Applying an edit replaces only that range; nothing in the document is ever
   formatted, underlined, or otherwise modified until the user accepts.
"""

import os
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Any

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from .generic_editing import _debug_log, _redact, get_generic_editor
from .live_overlay import (
    LiveSuggestionPanel,
    UndoPill,
    apply_nonactivating_panel,
)
from .live_preview import (
    CLIPBOARD_FALLBACK_BUNDLE_IDS,
    CLIPBOARD_READ_BACKOFF_S,
    CLIPBOARD_READ_INTERVAL_S,
    DEFAULT_DELAY_MS,
    POLL_INTERVAL_MS,
    RETRY_COOLDOWN_S,
    RETRY_MAX_FAILURES,
    EditSpan,
    PreviewCache,
    apply_edits_to_text,
    evaluate_trigger,
    map_edits_to_ranges,
    preview_cache_key,
    settings_fingerprint,
    word_visible_to_doc,
)

# How often the Mail compose-window check re-runs while Mail is frontmost.
MAIL_COMPOSE_CHECK_INTERVAL_S = 5.0

# How long the Undo pill stays available after an apply.
# Live Check respects the same entitlement as the manual flow. The
# entitlement check is cached so the 350 ms poll never reads licence files.
# A clipboard read posts Command-C, which makes the app's Edit menu flash.
# It is therefore only attempted when the user has just made a selection:
# within this window after a mouse-up, or once after they stop interacting.
SELECTION_MOUSE_WINDOW_S = 1.2
# A drag must have happened this recently for a mouse selection to count.
SELECTION_DRAG_WINDOW_S = 3.0
SELECTION_IDLE_SETTLE_S = 1.2
# A keyboard selection chord must be this old before a read is worth it.
CHORD_SETTLE_S = 0.5
USER_ACTIVE_WINDOW_S = 0.6
ACCESS_CACHE_TTL_S = 30.0
# After a refusal, do not re-check on every selection change.
ACCESS_RETRY_S = 60.0
UNDO_AVAILABLE_MS = 10000
# How many previous applies can still be undone.
UNDO_STACK_MAX = 5

# How long the "no changes needed" panel lingers before fading away.
CLEAN_PANEL_MS = 2600

# If the user switches to another app while suggestions are on screen, the
# panel is kept (so Apply still works on the captured selection) for this
# long before it is dismissed as stale.
PANEL_DETACH_KEEPALIVE_MS = 20000
# How often the detached panel re-checks that its selection is intact.
DETACH_REVALIDATE_S = 1.5
# Selections that appeared during an apply are ignored for this long.
SUPPRESS_SELECTION_S = 15.0
# When Word stops answering (modal dialog), stop polling it for this long.
WORD_BUSY_BACKOFF_S = 5.0
# After an applied edit the document has moved, so the next suggestion's
# offset is only a hint. Its original text is searched for around that hint
# before anything is written; nothing is written when it cannot be found.
SPAN_RELOCATE_WINDOW = 400

# Word's AppleScript selection read takes a few hundred milliseconds, so
# polling Word at the full rate keeps the UI thread busy and makes the app
# feel slow. Word polls at a relaxed cadence; every other app keeps the
# fast poll.
WORD_POLL_INTERVAL_MS = 800

# Two Escape presses within this window cancel the in-flight preview.
DOUBLE_ESC_WINDOW_S = 0.6


class PreviewWorker(QThread):
    """Runs the provider call off the UI thread, cancellable on stop."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        settings: dict[str, Any],
        target: dict[str, Any],
        selected: str,
        before: str,
        after: str,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.target = target
        self.selected = selected
        self.before = before
        self.after = after
        self.cancel_event = cancel_event

    def run(self) -> None:
        from . import logic

        try:
            status, edits, meta = logic.preview_edits_once(
                self.settings,
                self.target,
                self.selected,
                self.before,
                self.after,
                cancel_event=self.cancel_event,
            )
            self.done.emit({"status": status, "edits": edits, "meta": meta})
        except logic.TaskCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _InputWatcher(QObject):
    """Notices when the user actually made a text selection.

    Mail and Pages expose no Accessibility selection, so reading one means
    posting Command-C - which beeps when nothing is selected. This observes
    (never consumes) the user's own input and records the last moment a
    selection gesture happened: a drag, a double-click, or a Shift/Cmd chord.
    """

    def __init__(self) -> None:
        super().__init__()
        self.drag_selection_at = 0.0
        self.chord_at = 0.0
        self._dragged_at = 0.0
        self._last_click_at = 0.0
        self._monitor: Any = None
        self._handler: Any = None

    def start(self) -> bool:
        """Install the observe-only monitor; False when unavailable."""
        if self._monitor is not None:
            return True
        app = QApplication.instance()
        if app is None or "offscreen" in app.platformName():
            return False
        try:
            import AppKit

            mask = 0
            for name in (
                "NSLeftMouseDraggedMask",
                "NSEventMaskLeftMouseDragged",
                "NSLeftMouseUpMask",
                "NSEventMaskLeftMouseUp",
                "NSKeyDownMask",
                "NSEventMaskKeyDown",
            ):
                value = getattr(AppKit, name, None)
                if value is not None:
                    mask |= int(value)
            if not mask:
                return False
            self._handler = self._on_event
            self._monitor = (
                AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    mask, self._handler
                )
            )
            return self._monitor is not None
        except Exception as exc:
            _debug_log(f"LIVE WATCHER: could not start ({exc})")
            self._monitor = None
            self._handler = None
            return False

    def stop(self) -> None:
        if self._monitor is None:
            return
        try:
            import AppKit

            AppKit.NSEvent.removeMonitor_(self._monitor)
        except Exception:
            pass
        self._monitor = None
        self._handler = None

    def _on_event(self, event: Any) -> None:
        try:
            import AppKit

            kind = int(event.type())
            now = time.monotonic()
            if kind == int(getattr(AppKit, "NSEventTypeLeftMouseDragged", 6)):
                self._dragged_at = now
            elif kind == int(getattr(AppKit, "NSEventTypeLeftMouseUp", 2)):
                if now - self._dragged_at <= 1.5 or now - self._last_click_at <= 0.45:
                    self.drag_selection_at = now
                self._last_click_at = now
            elif kind == int(getattr(AppKit, "NSEventTypeKeyDown", 10)):
                shift = int(getattr(AppKit, "NSEventModifierFlagShift", 1 << 17))
                command = int(
                    getattr(AppKit, "NSEventModifierFlagCommand", 1 << 20)
                )
                if int(event.modifierFlags()) & (shift | command):
                    self.chord_at = now
        except Exception:
            pass

    def drag_selection_age(self) -> float | None:
        if not self.drag_selection_at:
            return None
        return time.monotonic() - self.drag_selection_at

    def chord_age(self) -> float | None:
        if not self.chord_at:
            return None
        return time.monotonic() - self.chord_at


class _EscapeBridge(QObject):
    """Receives global key events off the main thread and re-emits them."""

    pressed = pyqtSignal()

    def notify(self, event: Any) -> None:
        try:
            if int(event.keyCode()) == 53:  # kVK_Escape
                self.pressed.emit()
        except Exception:
            pass


_word_busy_logged_at = 0.0


def _log_word_busy_once(exc: Exception) -> None:
    """Report a busy Word once per back-off window instead of every tick."""
    global _word_busy_logged_at
    now = time.monotonic()
    if now - _word_busy_logged_at < 30.0:
        return
    _word_busy_logged_at = now
    _debug_log(f"LIVE SKIP: Word busy ({exc})")


class LivePreviewService(QObject):
    """Shows a suggestion panel for the current selection, nothing more."""

    apply_done = pyqtSignal(str)
    apply_all_requested = pyqtSignal()
    preview_error = pyqtSignal(str)
    live_status = pyqtSignal(str)  # "ready" | "no_permission" | "disabled"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings: dict[str, Any] = {}
        self._editor = get_generic_editor()
        self._cache = PreviewCache()
        self._fingerprint = ""
        self._seen_text = ""
        self._changed_at = 0.0
        self._previewed_text = ""
        self._worker: PreviewWorker | None = None
        self._cancel_event = threading.Event()
        self._timer: QTimer | None = None
        self._panel: LiveSuggestionPanel | None = None
        self._pending: list[EditSpan] = []
        # True while the panel still holds suggestions from a partly applied
        # batch: the app has already rewritten the text, so it will not confirm
        # the old selection and the retry must be allowed to locate the spans.
        self._partial_retry = False
        self._selection_text = ""
        self._selection_start = 0
        self._selection_end = 0
        self._selection_target: dict[str, Any] = {}
        self._selection_is_word = False
        self._word_document = ""
        self._word_busy_until = 0.0
        # Word position mapping for the batch being applied: prepared once,
        # then shifted by each edit instead of re-scanned per suggestion.
        self._word_prepared = False
        self._word_identity = False
        self._word_map: dict[int, int] = {}
        self._word_doc_delta = 0
        self._retry_not_before: float | None = None
        self._fail_streak = 0
        self._last_now = 0.0
        self._selection_has_range = True
        self._last_clipboard_read_at = 0.0
        self._clipboard_empty_streak = 0
        self._idle_read_done = False
        self._watcher = _InputWatcher()
        self._last_clipboard_text = ""
        self._last_clipboard_target: dict[str, Any] = {}
        self._last_permission_ok: bool | None = None
        self._candidate_text = ""
        self._candidate_count = 0
        self._mail_composing = True  # fail-open: never break Mail editing
        self._mail_check_at = 0.0
        self._read_only_logged: dict[str, str] = {}
        self._spawned_at = 0.0
        self._undo_pill: UndoPill | None = None
        self._undo_state: dict[str, Any] | None = None
        self._undo_previous: list[dict[str, Any]] = []
        self._access_checked_at: float | None = None
        self._access_allowed = True
        # True from the moment the user commits to an apply until it has
        # finished: the poll loop stays out of the way so a new selection
        # (or another app coming forward) can neither abort the apply nor
        # start a second preview.
        self._applying = False
        self._apply_started_text = ""
        self._poll_paused_for_apply = False
        self._apply_previous_app: dict[str, Any] = {}
        self._apply_foreign_frontmost: dict[str, Any] = {}
        self._detach_timer: QTimer | None = None
        self._detach_checked_at = 0.0
        # (pid, text) selections that appeared while an apply was running:
        # the user was reading, not asking for suggestions, so these are not
        # auto-previewed until the selection changes again.
        self._suppressed_selections: dict[tuple[int, str], float] = {}
        self._dismissed_at: float | None = None
        self._undo_timer: QTimer | None = None
        self._last_anchor: QPoint | None = None
        self._last_escape_at = 0.0
        self._last_user_app: dict[str, Any] = {}
        self._clean_timer: QTimer | None = None
        self._dismissed: set[tuple[str, str]] = set()
        self._remembered_pos: QPoint | None = None
        self._drag_paused_poll = False
        self._escape_bridge = _EscapeBridge()
        self._escape_bridge.pressed.connect(self._on_escape_pressed)
        self._escape_token: Any = None
        self._escape_handler: Any = None

    # --- lifecycle ---

    def start(self) -> None:
        self._cancel_event.clear()
        self._watcher.start()
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_INTERVAL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
        self._watcher.stop()
        if self._timer is not None:
            self._timer.stop()
        self._drag_paused_poll = False
        self._cancel_event.set()
        worker = self._worker
        if worker is not None:
            # preview_edits_once observes the cancel event within ~100 ms;
            # this bound only guards against a stuck worker.
            worker.wait(2000)
        self._seen_text = ""
        self._previewed_text = ""
        self._retry_not_before = None
        self._fail_streak = 0
        self._hide_undo_pill()
        self._hide_panel()

    def refresh_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._fingerprint = settings_fingerprint(settings)
        if not settings.get("live_preview", {}).get("enabled", True):
            self._hide_panel()
            self.live_status.emit("disabled")

    # --- sampling ---

    def _poll(self) -> None:
        try:
            self._sample(now=time.monotonic())
        except Exception:
            pass

    def _sample(self, now: float) -> None:
        self._last_now = now
        if not self._settings.get("live_preview", {}).get("enabled", True):
            return
        if self._applying:
            # An apply owns the selection right now. Reading AX state here
            # would (a) slow the apply down and (b) hide the panel or retarget
            # the selection while edits are still being written.
            return
        target = self._editor.frontmost_app()
        if not target:
            return
        bundle_check = str(target.get("bundle_id", "")).lower()
        name_check = str(target.get("name", "")).lower()
        if not any(
            marker in bundle_check or marker in name_check
            for marker in ("bytemind", "byteproof")
        ):
            # Remember the last real app the user worked in, so "Test now"
            # can probe it even after ByteProof becomes frontmost.
            self._last_user_app = target
        is_word = bool(getattr(self._editor, "is_word", lambda _t: False)(target))
        # Word's AppleScript read is heavy: poll Word at a relaxed cadence
        # so the UI thread stays responsive while Word is frontmost.
        if self._timer is not None:
            wanted = WORD_POLL_INTERVAL_MS if is_word else POLL_INTERVAL_MS
            if self._timer.interval() != wanted:
                self._timer.setInterval(wanted)
        permission_ok, _ = self._editor.permission_status()
        if is_word and now < getattr(self, "_word_busy_until", 0.0):
            # Word is busy (usually a modal dialog); skip the expensive read
            # until the back-off expires instead of blocking the UI thread.
            return
        if is_word:
            from .word_integration import get_word_integration

            try:
                text, start, end, before, after = (
                    get_word_integration().get_selection_info()
                )
            except Exception as exc:
                # A modal dialog in Word makes every AppleScript call time out.
                # Backing off keeps the UI responsive and stops the poll from
                # hammering a busy app; the user is told once.
                self._word_busy_until = now + WORD_BUSY_BACKOFF_S
                _log_word_busy_once(exc)
                return
            details = {
                "text": text,
                "range": (start, end),
                "context_before": before,
                "context_after": after,
            }
            permission_ok = True
        else:
            details = self._editor.selection_details(target)
        text = details.get("text") or ""

        bundle = str(target.get("bundle_id", "")).lower()
        editable = self._context_is_editable(target, details, is_word, bundle)
        if not editable:
            # Read-only selections (PDF text, browsed web pages) must not
            # trigger a suggestion panel: there is nothing to edit.
            if text.strip():
                self._log_read_only_once(bundle, details.get("role") or "")
            text = ""
            details = {
                "text": "",
                "range": None,
                "context_before": "",
                "context_after": "",
                "found": details.get("found", False),
            }
        if not text and bundle in CLIPBOARD_FALLBACK_BUNDLE_IDS:
            if self._clipboard_fallback_allowed(details, bundle, target, now):
                # Mail's WebKit compose view and Pages' canvas never expose
                # the selection through AX. Fall back to a throttled Cmd+C
                # read that preserves the user's clipboard.
                self._last_clipboard_read_at = now
                try:
                    # pyright: ignore[reportPrivateUsage]
                    # One keystroke normally; a second strategy only when a
                    # mouse selection was just made, where a miss would mean
                    # no panel at all for that selection.
                    mouse_reader = getattr(
                        self._editor, "mouse_up_seconds", None
                    )
                    just_selected = bool(
                        callable(mouse_reader)
                        and mouse_reader() <= SELECTION_MOUSE_WINDOW_S
                    )
                    copied = self._editor._mac_copy_selection(
                        target.get("pid") or 0,
                        target.get("name") or "",
                        max_attempts=2 if just_selected else 1,
                    )
                except Exception:
                    copied = ""
                if copied and copied.strip():
                    self._clipboard_empty_streak = 0
                    self._last_clipboard_text = copied
                    self._last_clipboard_target = target
                    text = copied
                    details = {
                        "text": text,
                        "range": None,
                        "context_before": "",
                        "context_after": "",
                    }
                    _debug_log(
                        f"LIVE CLIPBOARD READ: app={target.get('name')!r} "
                        f"chars={len(text)}"
                    )
                else:
                    self._clipboard_empty_streak += 1
                    self._last_clipboard_text = ""
                    self._last_clipboard_target = {}
            elif (
                editable
                and self._last_clipboard_text
                and self._last_clipboard_target.get("pid")
                == target.get("pid")
                and str(self._last_clipboard_target.get("bundle_id", ""))
                == str(target.get("bundle_id", ""))
            ):
                # Throttled: keep the previous clipboard-based selection text
                # so the debounce and unchanged-selection logic stays stable.
                text = self._last_clipboard_text
                details = {
                    "text": text,
                    "range": None,
                    "context_before": "",
                    "context_after": "",
                }
        else:
            self._last_clipboard_text = ""
            self._last_clipboard_target = {}

        if self._selection_is_suppressed(target, text):
            # Reading, not requesting: keep the state in sync without
            # triggering a preview for this particular selection.
            self._seen_text = text
            self._previewed_text = text
            self._candidate_text = ""
            self._candidate_count = 0
            # Any panel already on screen belongs to the captured selection
            # (the keep-alive case), so it is deliberately left alone.
            return
        if self._selection_target_changed(target):
            # Cached Accessibility elements belong to the previous app.
            clear_caches = getattr(self._editor, "clear_ax_caches", None)
            if callable(clear_caches):
                clear_caches()
            # The user moved to another app. The suggestions belong to the
            # selection they captured earlier, and Apply still works on it
            # (the apply activates that app just long enough to paste), so the
            # panel is kept for a while instead of vanishing under the cursor.
            if not self._keep_panel_for_detached_target(target):
                self._hide_panel()
        if text != self._seen_text:
            # Restart the debounce clock on the first read of a new value,
            # but only commit the change once two consecutive polls agree:
            # a single differing read is usually a transient AX/AppleScript
            # glitch, not a real selection change.
            if text == self._candidate_text:
                self._candidate_count += 1
            else:
                self._changed_at = now
                self._candidate_text = text
                self._candidate_count = 1
            if self._candidate_count >= 2:
                self._seen_text = self._candidate_text
                self._candidate_text = ""
                self._candidate_count = 0
                self._previewed_text = ""
                self._retry_not_before = None
                self._fail_streak = 0
                self._hide_panel()
        else:
            self._candidate_text = ""
            self._candidate_count = 0

        stable = now - self._changed_at >= self._delay() / 1000.0
        if self._last_permission_ok != permission_ok:
            self._last_permission_ok = permission_ok
            _debug_log(
                f"LIVE PERMISSION: trusted={permission_ok} "
                f"app={target.get('name')!r}"
            )
            if permission_ok:
                self.live_status.emit("ready")
            else:
                self.live_status.emit("no_permission")
                self.preview_error.emit(
                    "Live Check paused — re-enable ByteProof in "
                    "System Settings > Privacy & Security > Accessibility."
                )
        decision, _reason = evaluate_trigger(
            self._settings,
            target,
            text,
            permission_ok,
            stable,
            self._worker is not None,
            text == self._previewed_text,
        )
        if decision == "busy":
            # Another selection's preview is still running; if this text was
            # previewed before, serve the cached panel immediately so
            # re-selecting the same text always works.
            key = preview_cache_key(
                bundle,
                text,
                details.get("context_before", ""),
                details.get("context_after", ""),
                self._fingerprint,
            )
            cached = self._cache.get(key)
            if cached is not None:
                self._previewed_text = text
                self._selection_text = text
                self._selection_target = target
                self._selection_start = (details.get("range") or (0, 0))[0]
                self._selection_end = (
                    (details.get("range") or (0, 0))[1] if is_word else 0
                )
                self._selection_is_word = is_word
                self._word_document = ""
                self._selection_has_range = (
                    details.get("range") is not None
                )
                self._show_result(cached)
            return
        if decision != "run":
            if decision not in (
                "unchanged",
                "not_stable",
                "empty",
                "self",
                "no_permission",
                # Turning an app off is a deliberate setting, not a glitch:
                # do not fill the log with it on every tick.
                "app_disabled",
            ):
                _debug_log(f"LIVE SKIP: {decision} app={target.get('name')!r}")
            return
        if self._retry_not_before is not None and now < self._retry_not_before:
            return

        self._previewed_text = text
        self._selection_text = text
        self._selection_target = target
        self._selection_start = (details.get("range") or (0, 0))[0]
        self._selection_end = (details.get("range") or (0, 0))[1] if is_word else 0
        self._selection_is_word = is_word
        self._word_document = ""
        self._selection_has_range = details.get("range") is not None
        key = preview_cache_key(
            bundle,
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._fingerprint,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._show_result(cached)
            return
        self._spawn_preview(target, text, details, key)

    def _is_self_target(self, target: dict[str, Any]) -> bool:
        """Whether this target is ByteProof's own window."""
        if target.get("pid") and target.get("pid") == os.getpid():
            return True
        name = str(target.get("name", "")).lower()
        bundle = str(target.get("bundle_id", "")).lower()
        return "byteproof" in name or "bytemind" in bundle

    def _selection_target_changed(self, target: dict[str, Any]) -> bool:
        """Whether the frontmost app is a different one from the capture.

        Compared by bundle id when both sides have one: Mail's composer and its
        message viewer are separate processes, so the pid test alone treated a
        second window of the same app as "the user switched away" and dismissed
        the panel while they were still working in Mail. ByteProof itself is not
        a switch either - the panel and the undo pill are the app's own windows,
        and clicking them makes ByteProof frontmost.
        """
        if not self._selection_target:
            return False
        if self._is_self_target(target):
            return False
        captured_bundle = str(self._selection_target.get("bundle_id", "")).lower()
        current_bundle = str(target.get("bundle_id", "")).lower()
        if captured_bundle and current_bundle:
            return captured_bundle != current_bundle
        return self._selection_target.get("pid") != target.get("pid")

    def _keep_panel_for_detached_target(self, target: dict[str, Any]) -> bool:
        """Whether the panel should survive the user switching apps.

        Kept only while suggestions are waiting, the captured app is still
        running, and its selection still matches what was previewed; otherwise
        the panel is stale and gets dismissed.
        """
        if not self._pending:
            return False
        if not self._target_still_running():
            _debug_log("LIVE PANEL: captured app closed; dismissing")
            return False
        now = time.monotonic()
        # While detached, re-validating on every tick would read the other
        # app's Accessibility tree 4x a second for no benefit.
        if now - self._detach_checked_at < DETACH_REVALIDATE_S:
            self._arm_detach_timer()
            return True
        self._detach_checked_at = now
        evidence = self._selection_still_matches()
        if evidence is False:
            _debug_log("LIVE PANEL: captured selection changed; dismissing")
            return False
        if evidence is None:
            # No witness either way (Mail's composer never reports a selection,
            # a web view goes quiet while another app is frontmost). Keeping the
            # panel is safer than dismissing a review the user is still reading:
            # Apply re-reads the selection and refuses if it really changed.
            _debug_log("LIVE PANEL: selection unreadable; keeping the panel")
        self._arm_detach_timer()
        return True

    def _selection_still_matches(self) -> bool | None:
        """Whether the captured selection is still there: True/False/None.

        None means "no evidence either way" and is not treated as a change.
        Word is read through AppleScript, a clipboard-captured selection (Mail,
        Pages) through the app's own Copy command, and everything else through
        Accessibility - falling back to a copy when AX answers nothing, which
        is what web views do while another app is frontmost.
        """
        if self._selection_is_word:
            state = self._read_selection_state()
            if state is None or not state[0]:
                return None
            return self._same_selection(state[0])
        bundle = str(self._selection_target.get("bundle_id", "")).lower()
        clipboard_only = (not self._selection_has_range) or (
            bundle in CLIPBOARD_FALLBACK_BUNDLE_IDS
        )
        if not clipboard_only:
            state = self._read_selection_state()
            if state is not None and state[0]:
                return self._same_selection(state[0])
        copied = self._read_selection_by_copy(attempts=1)
        if not copied:
            return None
        return self._same_selection(copied)

    def _same_selection(self, text: str) -> bool:
        return text.strip() == (self._selection_text or "").strip()

    def _target_still_running(self) -> bool:
        pid = self._selection_target.get("pid")
        if not pid:
            return False
        try:
            for app in self._editor.running_apps():
                if app.get("pid") == pid:
                    return True
        except Exception:
            return True  # cannot tell: do not dismiss on a guess
        return False

    def _arm_detach_timer(self) -> None:
        if self._detach_timer is None:
            self._detach_timer = QTimer(self)
            self._detach_timer.setSingleShot(True)
            self._detach_timer.timeout.connect(self._hide_panel)
        self._detach_timer.start(PANEL_DETACH_KEEPALIVE_MS)

    def _delay(self) -> int:
        return int(
            self._settings.get("live_preview", {}).get(
                "delay_ms", DEFAULT_DELAY_MS
            )
        )

    def _clipboard_fallback_allowed(
        self,
        details: dict[str, Any],
        bundle: str,
        target: dict[str, Any],
        now: float,
    ) -> bool:
        """Gate the Cmd+C read fallback for apps without AX selection.

        Throttled (with backoff while it keeps coming back empty), and
        additionally requires a genuinely editable context: a live canvas
        element for Pages, an open compose window for Mail.
        """
        interval = (
            CLIPBOARD_READ_BACKOFF_S
            if self._clipboard_empty_streak >= 3
            else CLIPBOARD_READ_INTERVAL_S
        )
        if now - self._last_clipboard_read_at < interval:
            return False
        if not self._selection_gesture_seen():
            return False
        if bundle == "com.apple.mail":
            return self._mail_is_composing(target)
        return bool(details.get("found"))

    def _selection_gesture_seen(self) -> bool:
        """Whether it is worth posting Command-C to find out.

        Reading a clipboard-only selection costs a keystroke, and macOS flashes
        the app's Edit menu for it. So the read happens only when the user has
        just finished a mouse selection, or once after they stop interacting
        (which also catches keyboard selections) - never in a steady loop while
        they type or read.
        """
        if self._watcher_active():
            # Precise path: the monitor saw exactly what the user did.
            drag_age = self._watcher.drag_selection_age()
            if drag_age is not None and drag_age <= SELECTION_MOUSE_WINDOW_S:
                _debug_log("LIVE CLIPBOARD: read after a selection gesture")
                return True
            chord_age = self._watcher.chord_age()
            if (
                chord_age is not None
                and CHORD_SETTLE_S <= chord_age <= SELECTION_MOUSE_WINDOW_S
            ):
                _debug_log("LIVE CLIPBOARD: read after a keyboard selection")
                return True
            # Nothing the user just did selected text, so a copy keystroke
            # would only make the app beep.
            return False

        idle_reader = getattr(self._editor, "idle_seconds", None)
        mouse_reader = getattr(self._editor, "mouse_up_seconds", None)
        if not callable(idle_reader) or not callable(mouse_reader):
            # No activity information available: keep the previous behaviour.
            return True
        idle = idle_reader()
        mouse_up = mouse_reader()
        if idle is None and mouse_up is None:
            # The OS would not report input activity; do not silently disable
            # the fallback for apps whose selection can only be read by copy.
            return True
        active = idle is not None and idle < USER_ACTIVE_WINDOW_S
        if active:
            # The user is working again: re-arm the "one read after they stop"
            # allowance for this burst of activity.
            self._idle_read_done = False
        dragged = None
        drag_reader = getattr(self._editor, "dragged_seconds", None)
        if callable(drag_reader):
            dragged = drag_reader()
        if (
            mouse_up is not None
            and mouse_up <= SELECTION_MOUSE_WINDOW_S
            and (dragged is None or dragged <= SELECTION_DRAG_WINDOW_S)
        ):
            # A drag ended a moment ago: that is a text selection, so the copy
            # is worth its keystroke. A plain click is not.
            _debug_log("LIVE CLIPBOARD: read after a drag selection")
            return True
        if (
            idle is not None
            and idle >= SELECTION_IDLE_SETTLE_S
            and not self._idle_read_done
        ):
            self._idle_read_done = True
            _debug_log("LIVE CLIPBOARD: read after the user stopped interacting")
            return True
        return False

    def _context_is_editable(
        self,
        target: dict[str, Any],
        details: dict[str, Any],
        is_word: bool,
        bundle: str,
    ) -> bool:
        """Whether the current selection lives in an editable context.

        Word always counts (an active document is being edited). Other apps
        must expose a settable text element; Pages and Mail fall back to
        their own signals because their AX trees never report settability.
        """
        if is_word:
            return True
        if details.get("editable"):
            return True
        if bundle in ("com.apple.pages", "com.apple.iwork.pages"):
            # Pages' canvas exposes nothing until a real caret exists, so a
            # found element already proves an active editing session.
            return bool(details.get("found"))
        if bundle == "com.apple.mail":
            return self._mail_is_composing(target)
        return False

    def _mail_is_composing(self, target: dict[str, Any]) -> bool:
        """Whether Mail's frontmost window is a compose window.

        Mail exposes drafts as 'outgoing messages'; a viewer window is never
        named after a draft subject. Cached briefly because the poll calls
        this constantly, and fail-open so Mail editing never breaks.
        """
        now = time.monotonic()
        if now - self._mail_check_at < MAIL_COMPOSE_CHECK_INTERVAL_S:
            return self._mail_composing
        self._mail_check_at = now
        self._mail_composing = True
        try:
            subjects = subprocess.run(
                [
                    "osascript",
                    "-e",
                    (
                        'tell application "Mail" to get subject of every '
                        "outgoing message"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            front = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "Mail" to get name of front window',
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            draft_subjects = [
                s.strip()
                for s in (subjects.stdout or "").split(", ")
                if s.strip()
            ]
            front_name = (front.stdout or "").strip()
            # A fresh compose window has no title yet (empty name), while a
            # viewer window is always named after its mailbox.
            self._mail_composing = bool(
                front_name in draft_subjects
                or front_name.lower() == "new message"
                or not front_name
            )
        except Exception as exc:
            _debug_log(f"LIVE MAIL COMPOSE CHECK ERROR: {exc}")
        if not self._mail_composing:
            self._log_read_only_once("com.apple.mail", "viewer")
        return self._mail_composing

    def _log_read_only_once(self, bundle: str, role: str) -> None:
        key = f"{bundle}:read_only"
        message = f"LIVE SKIP: read_only bundle={bundle} role={role}"
        if self._read_only_logged.get(key) != message:
            self._read_only_logged[key] = message
            _debug_log(message)

    # --- provider ---

    def _access_allows_preview(self) -> bool:
        """Whether the current licence state still allows Live Check.

        The live panel spends provider credits, so it must respect the same
        trial/free limits as the manual flow. The answer is cached briefly: the
        check reads local JSON files and must never slow the poll loop.
        """
        now = time.monotonic()
        if (
            self._access_checked_at is not None
            and now - self._access_checked_at < ACCESS_CACHE_TTL_S
        ):
            return self._access_allowed
        allowed = True
        tier = ""
        try:
            from .licensing import get_access_status

            status = get_access_status()
            tier = str(status.get("tier", ""))
            if tier == "free" and not status.get("free_mode_allowed"):
                allowed = False
        except Exception:
            allowed = True
        self._access_checked_at = now
        self._access_allowed = allowed
        if not allowed:
            _debug_log(f"LIVE SKIP: licence limit reached tier={tier!r}")
        return allowed

    def _spawn_preview(
        self,
        target: dict[str, Any],
        text: str,
        details: dict[str, Any],
        key: str,
    ) -> None:
        if not self._access_allows_preview():
            self._hide_panel()
            self._retry_not_before = time.monotonic() + ACCESS_RETRY_S
            self.preview_error.emit(
                "Live Check is paused: the free daily limit or trial has "
                "ended. Open Settings → License to keep them running."
            )
            return
        _debug_log(f"LIVE PREVIEW: app={target.get('name')!r} chars={len(text)}")
        self._retry_not_before = None
        self._spawned_at = time.monotonic()
        worker = PreviewWorker(
            self._settings,
            target,
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._cancel_event,
        )
        self._worker = worker
        worker.done.connect(
            lambda result, k=key, t=text: self._on_done(result, k, t)
        )
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_worker_finished)
        worker.start()
        self._show_checking_panel()

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_done(self, result: dict[str, Any], key: str, text: str) -> None:
        # Drop the result when the user has moved to a different app since
        # the preview was spawned. Comparing the polled text was unreliable:
        # transient reads poisoned it and silently dropped good results.
        current = self._editor.frontmost_app()
        if (
            not current
            or current.get("pid") != self._selection_target.get("pid")
            or str(current.get("bundle_id", ""))
            != str(self._selection_target.get("bundle_id", ""))
        ):
            _debug_log("LIVE DONE: target no longer frontmost; dropping")
            self._hide_panel()
            return
        if not self._settings.get("live_preview", {}).get("enabled", True):
            return
        status = result.get("status")
        if status in ("limit_reached", "no_api_key"):
            self._hide_panel()
            if status == "limit_reached":
                message = (
                    "You've used all your free proofreads for today. "
                    "Live Check stays off for this selection."
                )
            else:
                message = (
                    "Add an API key for the active provider to use live "
                    "suggestions."
                )
            _debug_log(f"LIVE {status.upper()}")
            self.preview_error.emit(message)
            self._previewed_text = text
            return
        self._fail_streak = 0
        spans = map_edits_to_ranges(text, result.get("edits") or [])
        provider_ms = int((time.monotonic() - self._spawned_at) * 1000)
        _debug_log(
            f"LIVE DONE: edits={len(spans)} provider_ms={provider_ms} "
            f"provider={result.get('meta', {}).get('provider')}"
        )
        self._cache.put(key, spans)
        sync_ok, sync_reason = self._sync_selection()
        if not sync_ok:
            _debug_log(f"LIVE DONE SYNC FAIL: {sync_reason}")
            self._hide_panel()
            return
        if (
            self._dismissed_at is not None
            and self._dismissed_at >= self._spawned_at
        ):
            _debug_log("LIVE DONE: panel was dismissed while checking; not reopening")
            return
        self._show_result(spans)

    def _on_failed(self, message: str) -> None:
        self._hide_panel()
        _debug_log(f"LIVE ERROR: {message}")
        if not self._settings.get("live_preview", {}).get("enabled", True):
            return
        self._fail_streak += 1
        if self._fail_streak == 1:
            self.preview_error.emit(message)
        if self._fail_streak >= RETRY_MAX_FAILURES:
            self._retry_not_before = None
            self._previewed_text = self._seen_text  # give up until reselect
            return
        self._previewed_text = ""
        self._retry_not_before = self._last_now + RETRY_COOLDOWN_S

    def _on_cancelled(self) -> None:
        self._hide_panel()
        _debug_log("LIVE CANCEL: preview worker cancelled.")

    # --- selection state ---

    def _read_selection_state(self) -> tuple[str, int, int] | None:
        """Re-read the live selection; (text, start, end) or None on failure."""
        if not self._selection_target:
            return None
        if self._selection_is_word:
            try:
                from .word_integration import get_word_integration

                text, start, end, _before, _after = (
                    get_word_integration().get_selection_info()
                )
            except Exception:
                return None
            return str(text or ""), int(start or 0), int(end or 0)
        if not self._selection_has_range:
            # Mail/Pages selections come from a clipboard read; re-verify
            # with the same mechanism (AX cannot see them).
            try:
                text = self._editor.get_selection_light(
                    self._selection_target
                )
            except Exception:
                return None
            return (str(text or ""), 0, 0) if text else None
        try:
            details = self._editor.selection_details(self._selection_target)
            text = details.get("text") or ""
            if not text:
                # Web views re-render constantly: the element cached a moment
                # ago may no longer hold the selection. Drop it and re-read
                # once with a freshly discovered element before giving up.
                invalidate = getattr(self._editor, "invalidate_ax_element", None)
                if callable(invalidate):
                    invalidate(self._selection_target.get("pid") or 0)
                    details = self._editor.selection_details(
                        self._selection_target
                    )
                    text = details.get("text") or ""
            start = (details.get("range") or (0, 0))[0] or 0
            return str(text), int(start), 0
        except Exception:
            return None

    def _sync_selection(self) -> tuple[bool, str]:
        """Verify the previewed selection still holds and re-anchor to it.

        The provider call takes seconds; the user may have re-selected the
        same phrase elsewhere in the meantime. Applying at the stale position
        would corrupt the document, so the current range is re-read before
        showing results or applying anything. Returns (ok, reason).

        Only the previewed text is compared: the polling state can hold a
        transiently glitched read, and failing the user's click because of
        it produced spurious "could not verify" toasts.
        """
        state = self._read_selection_state()
        read_text = state[0] if state is not None else ""
        if read_text == self._selection_text and state is not None:
            self._selection_start = state[1]
            self._selection_end = state[2] if self._selection_is_word else 0
            return True, "ok"

        # The re-read came back empty or different. In browsers the AX query
        # often returns nothing while the selection is perfectly intact, so
        # before refusing, ask the app itself: a real copy of the selection is
        # authoritative and the clipboard is restored straight afterwards.
        if not read_text and not self._selection_is_word:
            # A web view answers nothing at all while another app is frontmost
            # (the state the user is in right after clicking the panel), so
            # bring the captured app forward first - the apply needs it for the
            # paste anyway - and read again.
            if self._bring_target_forward():
                state = self._read_selection_state()
                read_text = state[0] if state is not None else ""
                if state is not None and read_text == self._selection_text:
                    self._selection_start = state[1]
                    self._selection_end = (
                        state[2] if self._selection_is_word else 0
                    )
                    _debug_log("LIVE SYNC: verified after bringing the app forward")
                    return True, "ok"
            copied = self._read_selection_by_copy()
            if copied and copied == self._selection_text:
                if state is not None:
                    self._selection_start = state[1]
                    self._selection_end = (
                        state[2] if self._selection_is_word else 0
                    )
                _debug_log("LIVE SYNC: verified the selection by copy")
                return True, "ok"
            if copied:
                _debug_log(
                    "LIVE SYNC: copy read differs from the previewed selection"
                )
                return (
                    False,
                    (
                        "selection changed: previewed="
                        f"{_redact(self._selection_text)} "
                        f"now={_redact(copied)}"
                    ),
                )
            # Nothing readable at all. The document itself is then the
            # authority: when the captured range still holds the previewed
            # text, applying is safe - the write path checks that same range
            # again before it writes anything.
            held = self._read_field_text_at_selection()
            if held and held == self._selection_text:
                _debug_log("LIVE SYNC: verified by document content at the range")
                return True, "ok"
            if held:
                _debug_log("LIVE SYNC: document text at the range differs")
                return (
                    False,
                    (
                        "selection changed: previewed="
                        f"{_redact(self._selection_text)} now={_redact(held)}"
                    ),
                )
            _debug_log(
                "LIVE SYNC: selection unreadable (AX, copy and content all empty)"
            )
            return False, "selection could not be read"

        return (
            False,
            (
                f"selection changed: previewed={_redact(self._selection_text)} "
                f"now={_redact(read_text)}"
            ),
        )

    def _sync_failure_message(self, reason: str) -> str:
        """Explain a failed selection check in terms the user can act on."""
        app = self._selection_target.get("name") or "that app"
        if reason == "selection could not be read":
            return (
                f"Could not read the selection in {app}. Click into the text, "
                "select it again, and press Apply."
            )
        if reason.startswith("selection changed"):
            return (
                f"The selection in {app} changed before the edit was applied. "
                "Select the text again to get fresh suggestions."
            )
        return "Could not verify the selection — please try again."

    def _watcher_active(self) -> bool:
        """Whether the input monitor is installed (and authoritative)."""
        return getattr(self._watcher, "_monitor", None) is not None

    def _bring_target_forward(self) -> bool:
        """Activate the captured app so its selection can be read again.

        Returns True when the app was actually brought forward (so the caller
        knows a re-read is worth attempting).
        """
        try:
            if self._editor.is_frontmost(self._selection_target):
                return False
            current = self._editor.frontmost_app()
            if current and current.get("pid") != self._selection_target.get(
                "pid"
            ):
                name = str(current.get("name", "")).lower()
                bundle = str(current.get("bundle_id", "")).lower()
                # Remember where the user really was, so focus can be handed
                # back after the apply (never to ByteProof itself).
                if "byteproof" not in name and "bytemind" not in bundle:
                    self._apply_foreign_frontmost = current
            self._editor.activate(self._selection_target)
            time.sleep(0.25)
            return True
        except Exception as exc:
            _debug_log(f"LIVE SYNC: could not bring the app forward: {exc}")
            return False

    def _read_field_text_at_selection(self) -> str:
        """Document text at the captured range (works without a selection)."""
        reader = getattr(self._editor, "field_text_at", None)
        if not callable(reader):
            return ""
        try:
            return str(
                reader(
                    self._selection_target,
                    self._selection_start,
                    len(self._selection_text),
                )
                or ""
            )
        except Exception:
            return ""

    def _read_selection_by_copy(self, attempts: int = 2) -> str:
        """Read the live selection with a real copy (clipboard preserved).

        All copy strategies are tried: Mail ignores a process-targeted
        Command-C intermittently, and the global one works once the app has
        been brought forward.
        """
        reader = getattr(self._editor, "get_selection_by_copy", None)
        try:
            if callable(reader):
                return str(reader(self._selection_target, attempts) or "")
            return str(
                self._editor.get_selection_light(self._selection_target) or ""
            )
        except Exception:
            return ""

    def _sync_after_apply(self, expected: str, tolerant: bool = False) -> bool:
        """After an apply, keep state only when the selection still covers
        exactly the expected post-edit text (offsets stay valid then).

        ``tolerant`` keeps the panel when the app cannot report the selection at
        all (Mail's composer never does): unreadable is not the same as wrong,
        and the writes that remain are still guarded by their own text.
        """
        state = self._read_selection_state()
        if state is None or not state[0]:
            return tolerant
        text, start, end = state
        if text != expected:
            return False
        self._selection_text = text
        self._seen_text = text
        self._previewed_text = text
        self._selection_start = start
        self._selection_end = end if self._selection_is_word else 0
        self._changed_at = time.monotonic()
        return True

    # --- presentation ---

    def _ensure_panel(self) -> LiveSuggestionPanel:
        panel = self._panel
        if panel is None:
            panel = LiveSuggestionPanel()
            panel.apply_requested.connect(self._apply_one)
            panel.apply_all_requested.connect(self._apply_all)
            panel.dismissed.connect(self._on_panel_dismissed)
            panel.dismiss_requested.connect(self._on_dismiss)
            panel.dragging_started.connect(self._pause_polling)
            panel.dragging_finished.connect(self._resume_polling)
            panel.dragging_finished.connect(self._on_panel_dragged)
            apply_nonactivating_panel(panel)
            self._panel = panel
        return panel

    def _place_panel(self, panel: LiveSuggestionPanel) -> None:
        """Position the panel: the user's last dragged spot wins."""
        if self._remembered_pos is not None:
            panel.place_at(self._remembered_pos)
            self._last_anchor = self._remembered_pos
        else:
            anchor = self._anchor_point()
            self._last_anchor = anchor
            panel.place_near(anchor)
        panel.pop_in()

    def _show_result(
        self, spans: list[EditSpan], clean_when_empty: bool = True
    ) -> None:
        filtered = self._filter_dismissed(spans)
        self._pending = filtered
        self._partial_retry = False
        if not filtered:
            if clean_when_empty and not spans:
                self._show_clean_panel()
            else:
                self._hide_panel()
            return
        if not self._settings.get("live_preview", {}).get("enabled", True):
            self._hide_panel()
            return
        panel = self._ensure_panel()
        was_visible = panel.isVisible()
        panel.set_spans(filtered)
        panel.show()
        if not was_visible:
            self._place_panel(panel)
        self._install_escape_monitor()

    def _show_clean_panel(self) -> None:
        """Gently confirm that the selected text needs no changes."""
        panel = self._ensure_panel()
        was_visible = panel.isVisible()
        panel.set_clean()
        panel.show()
        if not was_visible:
            self._place_panel(panel)
        self._install_escape_monitor()
        if self._clean_timer is None:
            self._clean_timer = QTimer(self)
            self._clean_timer.setSingleShot(True)
            self._clean_timer.timeout.connect(self._hide_panel)
        self._clean_timer.start(CLEAN_PANEL_MS)

    def _filter_dismissed(
        self, spans: list[EditSpan]
    ) -> list[EditSpan]:
        """Drop session-dismissed suggestions (even from the cache)."""
        if not self._dismissed:
            return spans
        return [
            span
            for span in spans
            if (span.before, span.after) not in self._dismissed
        ]

    def _on_dismiss(self, index: int) -> None:
        """'Don't suggest this again' for one suggestion row."""
        if not (0 <= index < len(self._pending)):
            return
        span = self._pending.pop(index)
        self._dismissed.add((span.before, span.after))
        _debug_log(
            f"LIVE DISMISS: {_redact(span.before)} -> {_redact(span.after)}"
        )
        if not self._pending:
            self._hide_panel()
            return
        panel = self._panel
        if panel is not None:
            panel.set_spans(self._pending)
            panel.show()

    # --- drag handling ---

    def _pause_polling(self) -> None:
        """Freeze the poll while the user drags the panel.

        The poll runs heavy AX/AppleScript reads on the UI thread, which
        makes drags stutter; suspending it keeps movement smooth.
        """
        if self._timer is not None and self._timer.isActive():
            self._drag_paused_poll = True
            self._timer.stop()
        else:
            self._drag_paused_poll = False

    def _resume_polling(self) -> None:
        if self._drag_paused_poll and self._timer is not None:
            self._timer.start()
        self._drag_paused_poll = False

    def _on_panel_dragged(self) -> None:
        """Remember where the user put the panel for the next time."""
        if self._panel is not None:
            self._remembered_pos = QPoint(self._panel.pos())

    def _anchor_point(self) -> QPoint:
        """Anchor the panel at the selection when bounds exist, else cursor."""
        if not self._selection_is_word:
            try:
                bounds = self._editor.ax_bounds_for_range(
                    self._selection_target,
                    self._selection_start,
                    min(80, max(1, len(self._selection_text))),
                )
                if bounds:
                    rect = QRect(
                        int(min(b[0] for b in bounds)),
                        int(min(b[1] for b in bounds)),
                        int(max(b[0] + b[2] for b in bounds))
                        - int(min(b[0] for b in bounds)),
                        int(max(b[1] + b[3] for b in bounds))
                        - int(min(b[1] for b in bounds)),
                    )
                    if not rect.isNull():
                        return QPoint(rect.right() + 8, rect.top())
            except Exception:
                pass
        return QCursor.pos()

    def _on_panel_dismissed(self) -> None:
        """Remember that the user closed the panel for this selection.

        Without this, a preview that was already in flight when the user hit
        Escape or clicked the close button would pop the panel back open.
        """
        self._dismissed_at = time.monotonic()
        self._hide_panel()

    def _hide_panel(self) -> None:
        self._pending = []
        self._partial_retry = False
        self._remove_escape_monitor()
        if self._clean_timer is not None:
            self._clean_timer.stop()
        # If the panel was hidden mid-drag, the release event never arrives;
        # make sure the poll resumes so the service can't get stuck.
        self._resume_polling()
        if self._panel is not None:
            stop_pop = getattr(self._panel, "stop_pop", None)
            if stop_pop is not None:
                stop_pop()
            self._panel.hide()

    # --- escape key ---

    def _install_escape_monitor(self) -> None:
        """Watch for Escape in other apps while the panel is visible.

        The panel never accepts keyboard focus, so it cannot see Escape
        itself. A global NSEvent monitor (observe-only; the key still reaches
        the target app) closes the panel when the user presses Escape.
        """
        if self._escape_token is not None:
            return
        app = QApplication.instance()
        if app is None or "offscreen" in app.platformName():
            return
        try:
            import AppKit

            mask = getattr(AppKit, "NSKeyDownMask", None)
            if mask is None:
                mask = getattr(AppKit, "NSEventMaskKeyDown", None)
            if mask is None:
                return
            handler = self._escape_bridge.notify
            self._escape_handler = handler  # keep the block alive
            self._escape_token = (
                AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    int(mask), handler
                )
            )
        except Exception:
            self._escape_token = None
            self._escape_handler = None

    def _remove_escape_monitor(self) -> None:
        token = self._escape_token
        self._escape_token = None
        self._escape_handler = None
        if token is None:
            return
        try:
            import AppKit

            AppKit.NSEvent.removeMonitor_(token)
        except Exception:
            pass

    def _on_escape_pressed(self) -> None:
        # Escape means "go away": a preview still in flight must not pop the
        # panel back open when it finishes.
        self._dismissed_at = time.monotonic()
        now = time.monotonic()
        double = now - self._last_escape_at < DOUBLE_ESC_WINDOW_S
        self._last_escape_at = now
        if double and self._worker is not None:
            _debug_log("LIVE CANCEL: double-Esc cancelling the preview")
            self._cancel_event.set()
            self.apply_done.emit("Preview cancelled.")
        self._hide_panel()

    # --- checking panel ---

    def _show_checking_panel(self) -> None:
        """Show the suggestion panel in a 'Checking…' state immediately.

        The opaque card renders reliably everywhere, so the user gets
        instant feedback the moment the provider call starts; the panel is
        rebuilt with the real suggestions when the result arrives.
        """
        panel = self._ensure_panel()
        if panel.isVisible():
            return
        panel.set_checking()
        panel.show()
        self._place_panel(panel)
        self._install_escape_monitor()

    # --- undo ---

    def _arm_undo(self, state: dict[str, Any]) -> None:
        """Offer an undo for the edit just applied.

        States stack (bounded) so a sequence of applies can be unwound one at a
        time instead of the newest apply discarding the previous undo.
        """
        if self._undo_state is not None and self._undo_pill is not None:
            self._undo_previous.append(self._undo_state)
            del self._undo_previous[:-UNDO_STACK_MAX]
        self._undo_state = state
        self._show_undo_pill()

    def _show_undo_pill(self) -> None:
        if self._undo_pill is None:
            self._undo_pill = UndoPill()
            self._undo_pill.undo_requested.connect(self._perform_undo)
        anchor = self._last_anchor or self._anchor_point()
        self._undo_pill.place_near(anchor)
        self._undo_pill.show()
        if self._undo_timer is None:
            self._undo_timer = QTimer(self)
            self._undo_timer.setSingleShot(True)
            self._undo_timer.timeout.connect(self._hide_undo_pill)
        self._undo_timer.start(UNDO_AVAILABLE_MS)

    def _hide_undo_pill(self) -> None:
        self._undo_state = None
        self._undo_previous = []
        if self._undo_timer is not None:
            self._undo_timer.stop()
        if self._undo_pill is not None:
            self._undo_pill.hide()

    def _perform_undo(self) -> None:
        state = self._undo_state
        if not state:
            self._hide_undo_pill()
            return
        self._undo_state = None
        try:
            if state.get("mode") == "full":
                # The full-selection apply pasted over the current selection;
                # undo is only safe while that selection still holds the
                # corrected text.
                current = self._editor.get_selection_light(
                    state.get("target") or {}
                )
                if (current or "").strip() != state["corrected"].strip():
                    self.apply_done.emit("Selection changed — could not undo.")
                    self._restore_previous_undo()
                    return
                ok, message = self._editor.replace_selection(
                    state.get("target") or {}, state["original"]
                )
                self.apply_done.emit("Undone." if ok else message)
                if ok:
                    self._restore_previous_undo()
                return
            restored = 0
            total = 0
            for abs_start, applied, original in state.get("steps") or []:
                total += 1
                if state.get("is_word"):
                    from .word_integration import get_word_integration

                    ok, _ = get_word_integration().apply_live_edit(
                        0,
                        abs_start,
                        abs_start + len(applied),
                        original,
                        before_text=applied,
                        expected_document=state.get("document") or None,
                    )
                else:
                    # The recorded offset is a hint, not a fact: apps reflow,
                    # normalise and re-render between the apply and the undo, so
                    # the text that was written is located in the live field
                    # first (field coordinates, like abs_start). before_text
                    # then makes the undo refuse when the document really has
                    # moved on, instead of overwriting whatever now sits there.
                    target_start = abs_start
                    live = self._live_edit_text()
                    if live:
                        located = self._locate_span(live, applied, abs_start)
                        if located is not None:
                            target_start = located
                        else:
                            _debug_log(
                                "LIVE UNDO: could not locate the applied text; "
                                "trying the recorded offset"
                            )
                    ok, _ = self._editor.ax_replace_range(
                        state.get("target") or {},
                        target_start,
                        len(applied),
                        original,
                        before_text=applied,
                    )
                if ok:
                    restored += 1
            if total == 0:
                self.apply_done.emit("Could not undo.")
            elif restored == total:
                self.apply_done.emit("Undone.")
            elif restored:
                self.apply_done.emit(
                    f"Undone {restored} of {total} — the rest had changed."
                )
            else:
                self.apply_done.emit("Could not undo — the text had changed.")
            if restored:
                self._restore_previous_undo()
        except Exception as exc:
            _debug_log(f"LIVE UNDO ERROR: {exc}")
            self.apply_done.emit("Could not undo.")
            self._restore_previous_undo()

    def _restore_previous_undo(self) -> None:
        """Offer the next older undo step, if any remains."""
        if not self._undo_previous:
            self._hide_undo_pill()
            return
        self._undo_state = self._undo_previous.pop()
        self._show_undo_pill()

    # --- apply ---

    def _begin_apply(self) -> None:
        """Mark an apply as running so the poll loop leaves it alone."""
        self._applying = True
        self._apply_started_text = self._seen_text
        try:
            # Remember where the user actually is: applying activates the
            # target app for the paste, and they should be returned to their
            # own window afterwards.
            self._apply_previous_app = self._editor.frontmost_app() or {}
            self._apply_foreign_frontmost = {}
        except Exception:
            self._apply_previous_app = {}
        if self._timer is not None and self._timer.isActive():
            self._timer.stop()
            self._poll_paused_for_apply = True

    def _end_apply(self) -> None:
        """Resume polling, without previewing a selection made mid-apply.

        The user may have selected other text while the edits were being
        written. That selection must not silently trigger a fresh preview, so
        it is marked as already seen and the normal debounce only restarts on
        the next deliberate selection change.
        """
        self._applying = False
        # An apply consumes the selection: the paste replaced it. Reading it
        # again would post Command-C with nothing selected, which makes Mail
        # and Pages beep - so the idle read stays disarmed until the user
        # interacts again.
        self._idle_read_done = True
        try:
            if not self._selection_target:
                self._resume_polling_after_apply()
                return
            if self._selection_has_range or self._selection_is_word:
                # Accessibility or AppleScript read: no keystroke involved.
                state = self._read_selection_state()
                if state is not None:
                    text, start, _end = state
                    self._selection_text = text
                    self._seen_text = text
                    self._previewed_text = text
                    self._selection_start = start
                    self._candidate_text = ""
                    self._candidate_count = 0
                    self._changed_at = time.monotonic()
            else:
                # Clipboard-only app (Mail, Pages): keep the known state; the
                # next real selection updates it.
                self._candidate_text = ""
                self._candidate_count = 0
                self._changed_at = time.monotonic()
        except Exception as exc:
            _debug_log(f"LIVE APPLY END: state re-read failed: {exc}")
        self._suppress_current_selection()
        self._restore_user_focus(
            self._selection_target, getattr(self, "_apply_previous_app", {})
        )
        self._resume_polling_after_apply()

    def _suppress_current_selection(self) -> None:
        """Ignore whatever is selected right now (any app) for a short while.

        If the user selected other text to read while edits were being
        written, that selection must not be treated as a request for
        suggestions. Any later selection change still previews normally.
        """
        try:
            reader = getattr(self._editor, "frontmost_app", None)
            if not callable(reader):
                return  # a test double without app awareness
            target = reader()
            if not target:
                return
            pid = int(target.get("pid") or 0)
            if not pid or pid == int(self._selection_target.get("pid") or 0):
                return  # same app: _end_apply already marks the text as seen
            text = self._editor.get_selection_ax_only(target)
            if text:
                now = time.monotonic()
                self._suppressed_selections[(pid, text)] = now
                # Bound the store: it is only meaningful for a few seconds.
                self._suppressed_selections = {
                    key: stamp
                    for key, stamp in self._suppressed_selections.items()
                    if now - stamp < SUPPRESS_SELECTION_S
                }
        except Exception as exc:
            _debug_log(f"LIVE APPLY: selection suppression skipped: {exc}")

    def _selection_is_suppressed(self, target: dict[str, Any], text: str) -> bool:
        """Whether this (app, selection) was made while an apply was running."""
        if not self._suppressed_selections or not text:
            return False
        pid = int(target.get("pid") or 0)
        stamp = self._suppressed_selections.get((pid, text))
        if stamp is None:
            return False
        if time.monotonic() - stamp > SUPPRESS_SELECTION_S:
            self._suppressed_selections.pop((pid, text), None)
            return False
        return True

    def _resume_polling_after_apply(self) -> None:
        if getattr(self, "_poll_paused_for_apply", False):
            self._poll_paused_for_apply = False
        if self._timer is not None and not self._timer.isActive():
            self._timer.start()

    def _note_foreign_frontmost(self) -> None:
        """Remember the last app the user was in that is not the target.

        The paste briefly activates the target app, so this is what lets the
        user be returned to their own window - even if they moved to it after
        clicking Apply.
        """
        try:
            current = self._editor.frontmost_app()
        except Exception:
            return
        if not current:
            return
        if current.get("pid") == self._selection_target.get("pid"):
            return
        self._apply_foreign_frontmost = current

    def _restore_user_focus(self, target: dict[str, Any], previous: dict[str, Any]) -> None:
        """Give focus back to the app the user was in before the paste.

        Applying needs the target app frontmost for a moment (a paste has to
        land in a focused field). If the user had switched to another app
        while the edits were being prepared, they are returned to it instead
        of being stranded in the edited window.
        """
        try:
            recent = getattr(self, "_apply_foreign_frontmost", None) or previous
            previous_pid = recent.get("pid") if recent else None
            if not previous_pid or previous_pid == target.get("pid"):
                return
            previous = recent
            current = self._editor.frontmost_app()
            if current and current.get("pid") == previous_pid:
                return
            self._editor.activate(previous)
        except Exception as exc:
            _debug_log(f"LIVE APPLY: could not restore focus: {exc}")

    def _read_word_document(self) -> str:
        """Name of the Word document the current selection belongs to.

        Captured when a preview is created so the apply can refuse when the
        user switched documents in the meantime (the offsets would be stale).
        """
        try:
            from .word_integration import get_word_integration

            return get_word_integration().active_document_name() or ""
        except Exception:
            return ""

    def _pending_located(self, text: str) -> bool:
        """Whether every pending span's own text is still in the document.

        This is the evidence that replaces the selection check on a retry: the
        app will not confirm a selection it has already rewritten, but a span
        whose text is present where it is expected can be applied safely.
        """
        if not text or not self._pending:
            return False
        return all(
            self._locate_span(text, span.before, span.start) is not None
            for span in self._pending
        )

    def _live_edit_text(self) -> str:
        """The target field's current text, or "" when it cannot be read.

        Word is excluded: its document is read through AppleScript at apply
        time, and the AX value of a Word window is not the document.
        """
        if self._selection_is_word:
            return ""
        reader = getattr(self._editor, "field_value", None)
        if reader is None:
            return ""
        try:
            return reader(self._selection_target) or ""
        except Exception:
            return ""

    def _relocate_rel_start(self, before_text: str, rel_start: int) -> int | None:
        """Where a span's text now sits, in selection-relative coordinates.

        The live field text is in *field* coordinates while a span offset is
        relative to the captured selection, so the expected field position is
        the selection start plus the span offset - and the located position has
        to be converted back, because the editor adds the selection start again
        when it writes. Mixing the two spaces put an edit outside the selection
        (caught by a regression test), which is also what made a browser undo
        restore the wrong place.

        Returns the offset unchanged when the field cannot be read, and None
        when the text cannot be found inside the selection: writing outside it
        is never what the user asked for.
        """
        live = self._live_edit_text()
        if not live:
            return rel_start
        expected_abs = self._selection_start + rel_start
        located_abs = self._locate_span(live, before_text, expected_abs)
        if located_abs is None:
            return None
        if not (
            self._selection_start
            <= located_abs
            <= self._selection_start + len(self._selection_text)
        ):
            _debug_log(
                "LIVE RELOCATE: located the span outside the selection "
                f"(abs={located_abs} selection={self._selection_start}.."
                f"{self._selection_start + len(self._selection_text)})"
            )
            return None
        return located_abs - self._selection_start

    def _locate_span(self, text: str, before: str, expected: int) -> int | None:
        """Find ``before`` in the live text, preferring the ``expected`` offset.

        Returning None means "do not write": a wrong offset would rewrite words
        the user never asked to change, so an edit that cannot be placed is
        skipped rather than guessed.
        """
        if not before:
            return None
        if text[expected : expected + len(before)] == before:
            return expected
        window_start = max(0, expected - SPAN_RELOCATE_WINDOW)
        window_end = min(
            len(text), expected + len(before) + SPAN_RELOCATE_WINDOW
        )
        window = text[window_start:window_end]
        first = window.find(before)
        if first < 0:
            # Nothing nearby: accept a single match anywhere, never one of
            # several.
            if text.count(before) == 1:
                return text.index(before)
            return None
        best: tuple[int, int] | None = None
        position = first
        while position >= 0:
            candidate = window_start + position
            distance = abs(candidate - expected)
            if best is None or distance < best[0]:
                best = (distance, candidate)
            position = window.find(before, position + 1)
        return best[1] if best is not None else None

    def _apply_abs(
        self,
        rel_start: int,
        length: int,
        replacement: str,
        before_text: str | None = None,
        visible_start: int | None = None,
    ) -> tuple[bool, str, int]:
        """Replace a relative span; returns (ok, message, absolute start).

        ``before_text`` is the original text expected at the span; the
        editor uses it to guard against apps whose AX state lags behind the
        real document (e.g. ChatGPT applies range writes asynchronously).

        ``visible_start`` is the span's offset in the *previewed* text. Word
        needs it because earlier edits shift the running offset while the
        prepared position map is keyed by the original offsets.
        """
        if self._selection_is_word:
            from .word_integration import get_word_integration

            self._note_foreign_frontmost()
            word = get_word_integration()
            start = self._selection_start + rel_start
            end = start + length
            compensated = self._word_compensated_span(
                word,
                rel_start if visible_start is None else visible_start,
                len(before_text) if before_text else length,
            )
            if compensated is not None:
                start, end = compensated
            if not self._word_document:
                # One AppleScript read per selection, at apply time (not on
                # every preview, where it only added latency).
                self._word_document = self._read_word_document()
            ok, message = word.apply_live_edit(
                0,
                start,
                end,
                replacement,
                before_text=before_text,
                expected_document=self._word_document or None,
            )
            if ok:
                # Everything after a replaced range moves by its length change,
                # including the tracked deletions still hidden in the document.
                self._word_doc_delta += len(replacement) - (end - start)
            return ok, message, start
        self._note_foreign_frontmost()
        abs_start = self._selection_start + rel_start
        ok, message = self._editor.ax_replace_range(
            self._selection_target,
            abs_start,
            length,
            replacement,
            allow_direct_paste=(
                rel_start == 0 and length == len(self._selection_text)
            ),
            before_text=before_text,
        )
        return ok, message, abs_start

    def _prepare_word_batch(self, spans: Sequence[Any]) -> None:
        """Resolve every span's Word document range before the first write.

        Word counts tracked deletions and field codes in document positions but
        omits them from ``content``, so visible offsets must be mapped. Doing
        that per span used to re-read the selection and re-scan the whole
        selection character by character for every suggestion - seconds each on
        a tracked-changes document, which is what froze the app. One selection
        read plus one mapping call now covers the whole batch, and each applied
        edit only shifts the mapping by its own length change.
        """
        self._word_prepared = False
        self._word_identity = False
        self._word_map = {}
        self._word_doc_delta = 0
        if not self._selection_is_word or not spans:
            return
        state = self._read_selection_state()
        if state is None:
            return
        text, sel_start, sel_end = state
        if sel_end - sel_start <= len(text):
            # Nothing hidden in the selection: visible offsets are document
            # offsets and no mapping work is needed at all.
            self._word_prepared = True
            self._word_identity = True
            return
        offsets = {
            offset
            for span in spans
            for offset in (span.start, span.end)
        }
        mapper = getattr(self._word_editor(), "live_doc_positions", None)
        if not callable(mapper):
            return
        mapping = mapper(int(sel_start), int(sel_end), sorted(offsets))
        if not mapping:
            return
        self._word_map = {int(k): int(v) for k, v in mapping.items()}
        self._word_prepared = True

    def _word_editor(self) -> Any:
        from .word_integration import get_word_integration

        return get_word_integration()

    def _word_compensated_span_scan(
        self, word: Any, visible_start: int, length: int
    ) -> tuple[int, int] | None:
        """Slow, exhaustive mapping kept as the fallback.

        This is the original path: read the selection, list every field, then
        locate each hidden character one range at a time. Correct, but on a
        tracked-changes selection it costs one Word read per character - the
        reason a single suggestion could take seconds. It only runs now when
        the binary-search mapping is unavailable (an integration that predates
        it, or an AppleScript error).
        """
        state = self._read_selection_state()
        if state is None:
            return None
        text, sel_start, sel_end = state
        if sel_end - sel_start <= len(text):
            return None  # no evidence of hidden characters
        try:
            if word.selection_has_fields():
                fields = list(word.get_selection_field_spans())
            else:
                fields = []
        except Exception:
            return None
        field_extra = sum(
            (field.doc_end - field.doc_start) - len(field.result_text)
            for field in fields
        )
        missing = (sel_end - sel_start) - len(text) - field_extra
        if missing <= 0:
            hidden: list[tuple[int, int]] = []
        else:
            try:
                hidden = word.get_selection_hidden_spans(
                    sel_start,
                    sel_end,
                    exclude_spans=[
                        (field.doc_start, field.doc_end) for field in fields
                    ],
                    max_hidden=min(missing, max(1, sel_end - sel_start)),
                )
            except Exception:
                return None
        try:
            start = word_visible_to_doc(
                visible_start, sel_start, sel_end, hidden, fields
            )
            end = word_visible_to_doc(
                visible_start + length, sel_start, sel_end, hidden, fields
            )
        except Exception:
            return None
        if start is None or end is None or end <= start:
            return None
        return start, end

    def _word_compensated_span(
        self, word: Any, visible_start: int, length: int
    ) -> tuple[int, int] | None:
        """Map a visible-text span to absolute Word positions.

        Returns None when the raw offsets are already correct (nothing hidden,
        or no mapping available) so the caller keeps its normal path.
        """
        if self._word_prepared:
            if self._word_identity:
                return None
            start = self._word_map.get(visible_start)
            end = self._word_map.get(visible_start + length)
            if start is None or end is None or end <= start:
                return None
            return start + self._word_doc_delta, end + self._word_doc_delta
        mapper = getattr(word, "live_doc_positions", None)
        if not callable(mapper):
            return self._word_compensated_span_scan(word, visible_start, length)
        state = self._read_selection_state()
        if state is None:
            return None
        text, sel_start, sel_end = state
        if sel_end - sel_start <= len(text):
            return None
        mapping = mapper(
            int(sel_start), int(sel_end), [visible_start, visible_start + length]
        )
        if not mapping:
            # The mapper exists but could not answer (Word busy, AppleScript
            # error). Do NOT fall back to the character scan here: on a busy
            # Word that is hundreds of timed-out reads. Returning None leaves
            # the raw offsets, and Word's own before-text guard then refuses
            # the write rather than editing the wrong words.
            _debug_log(
                "WORD: position mapping failed; leaving the offsets raw"
            )
            return None
        start = mapping.get(visible_start)
        end = mapping.get(visible_start + length)
        if start is None or end is None or end <= start:
            return None
        return int(start), int(end)

    def _apply_one(self, index: int) -> None:
        if not (0 <= index < len(self._pending)):
            return
        if self._applying:
            return
        _debug_log(
            f"LIVE APPLY ONE: index={index} "
            f"app={self._selection_target.get('name')!r} "
            f"has_range={self._selection_has_range} "
            f"is_word={self._selection_is_word}"
        )
        self._begin_apply()
        try:
            self._apply_one_locked(index)
        finally:
            self._end_apply()

    def _apply_one_locked(self, index: int) -> None:
        if not self._selection_has_range:
            # No absolute range available (Mail/Pages clipboard path): paste
            # the fully corrected selection over the current one.
            self._apply_full_selection_locked()
            return
        sync_ok, sync_reason = self._sync_selection()
        for _ in range(2):
            if sync_ok:
                break
            # AX/AppleScript reads glitch transiently; retry before failing.
            time.sleep(0.12)
            sync_ok, sync_reason = self._sync_selection()
        if not sync_ok:
            _debug_log(f"LIVE APPLY SYNC FAIL: {sync_reason}")
            self._hide_panel()
            self.apply_done.emit(self._sync_failure_message(sync_reason))
            return
        span = self._pending[index]
        before_text = span.before or self._selection_text[span.start : span.end]
        self._prepare_word_batch([span])
        rel_start = span.start
        live_text = self._live_edit_text()
        if live_text:
            located = self._relocate_rel_start(before_text, rel_start)
            if located is None:
                _debug_log(
                    "LIVE APPLY ONE: could not locate the span "
                    f"({_redact(before_text)}) near rel={rel_start}"
                )
                self.apply_done.emit(
                    "That suggestion no longer matches the text — "
                    "select it again to refresh."
                )
                return
            rel_start = located
        ok, message, abs_start = self._apply_abs(
            rel_start,
            len(before_text),
            span.after,
            before_text=before_text,
            visible_start=span.start,
        )
        _debug_log(
            f"LIVE APPLY ONE RESULT: ok={ok} message={message!r} "
            f"rel=({rel_start},{rel_start + len(before_text)})"
        )
        self.apply_done.emit(message)
        if not ok:
            return
        self._arm_undo(
            {
                "mode": "range",
                "target": dict(self._selection_target),
                "is_word": self._selection_is_word,
                "document": self._word_document,
                "steps": [(abs_start, span.after, span.before)],
            }
        )
        delta = len(span.after) - (span.end - span.start)
        remaining: list[EditSpan] = []
        for i, other in enumerate(self._pending):
            if i == index:
                continue
            if other.start < span.end and other.end > span.start:
                continue  # overlapping suggestion is stale after the edit
            if other.start >= span.end:
                other = EditSpan(
                    other.before,
                    other.after,
                    other.reason,
                    other.start + delta,
                    other.end + delta,
                )
            remaining.append(other)
        self._pending = remaining
        if not remaining:
            self._hide_panel()
            return
        # Build the expectation from where the edit was actually written: the
        # span may have been relocated in the live text, and an app can rewrite
        # what it inserts, so span.start is not always the truth.
        expected = (
            self._selection_text[:rel_start]
            + span.after
            + self._selection_text[rel_start + len(before_text) :]
        )
        if not self._sync_after_apply(expected, tolerant=True):
            _debug_log("LIVE APPLY ONE: post-apply selection drifted; closing")
            self._hide_panel()
            return
        panel = self._panel
        if panel is not None:
            panel.set_spans(remaining)
            panel.show()

    def panel_showing_suggestions(self) -> bool:
        """Whether the suggestion panel is on screen with something to apply."""
        return bool(self._pending) and self._panel is not None

    def apply_all_now(self) -> bool:
        """Apply every pending suggestion (used by the Apply All hotkey).

        Returns False when there is nothing on screen to apply, so the caller
        can stay silent instead of showing an error.
        """
        if not self._pending or self._applying:
            return False
        self._apply_all()
        return True

    def _apply_all(self) -> None:
        if not self._pending:
            return
        if self._applying:
            return
        _debug_log(
            f"LIVE APPLY ALL: app={self._selection_target.get('name')!r} "
            f"has_range={self._selection_has_range} "
            f"count={len(self._pending)}"
        )
        self._begin_apply()
        try:
            self._apply_all_locked()
        finally:
            self._end_apply()

    def _apply_all_locked(self) -> None:
        if not self._selection_has_range:
            self._apply_full_selection_locked()
            return
        sync_ok, sync_reason = self._sync_selection()
        for _ in range(2):
            if sync_ok:
                break
            time.sleep(0.12)
            sync_ok, sync_reason = self._sync_selection()
        if not sync_ok:
            # A retry after a partial apply: the app has already rewritten the
            # text this batch inserted, so it no longer confirms the original
            # selection. Every write is still guarded by its own span text, so
            # applying the spans that can be located (and skipping the rest) is
            # safe - refusing here would strand the suggestions the panel is
            # still showing.
            if self._partial_retry and self._pending_located(self._live_edit_text()):
                _debug_log(
                    "LIVE APPLY ALL: retry without selection sync "
                    f"({sync_reason}); every remaining span was located"
                )
            else:
                _debug_log(f"LIVE APPLY ALL SYNC FAIL: {sync_reason}")
                self._hide_panel()
                self.apply_done.emit(self._sync_failure_message(sync_reason))
                return
        total = len(self._pending)
        batch = list(self._pending)
        # One selection read and one position map for the whole batch; the
        # per-suggestion rescan is what made Word with tracked changes crawl.
        self._prepare_word_batch(batch)
        applied = 0
        delta = 0
        failure_message = ""
        skipped: list[EditSpan] = []
        undo_steps: list[tuple[int, str, str]] = []
        live_text = self._live_edit_text()
        for span in sorted(batch, key=lambda s: s.start):
            before_text = span.before or self._selection_text[span.start : span.end]
            rel_start, rel_end = span.start + delta, span.end + delta
            if live_text:
                located = self._relocate_rel_start(before_text, rel_start)
                if located is None:
                    # Teams and friends rewrite what they insert, so after an
                    # earlier edit the arithmetic offset is only a hint. Not
                    # finding the text means "do not write": a guess would edit
                    # the wrong words. Skip this one and keep going, so the
                    # rest of the suggestions still land.
                    _debug_log(
                        "LIVE APPLY ALL: could not locate a span "
                        f"({_redact(before_text)}) near rel={rel_start}; "
                        "skipping it"
                    )
                    skipped.append(span)
                    continue
                rel_start = located
                rel_end = located + len(before_text)
            ok, message, abs_start = self._apply_abs(
                rel_start,
                rel_end - rel_start,
                span.after,
                before_text=before_text,
                visible_start=span.start,
            )
            if not ok:
                # Keep going: the previous behaviour stopped here and left the
                # remaining suggestions unapplied, which is what "Apply All"
                # must never do.
                failure_message = message or "Could not apply the edit in this app."
                _debug_log(
                    "LIVE APPLY ALL: skipping a span after failure "
                    f"({failure_message!r}) at rel=({rel_start},{rel_end})"
                )
                skipped.append(span)
                continue
            applied += 1
            delta = rel_start + len(span.after) - span.start - len(before_text)
            undo_steps.append((abs_start, span.after, span.before))
            refreshed = self._live_edit_text()
            if refreshed:
                live_text = refreshed
            elif live_text:
                live_text = (
                    live_text[:rel_start]
                    + span.after
                    + live_text[rel_start + len(before_text) :]
                )
        self.apply_all_requested.emit()
        if applied:
            self._arm_undo(
                {
                    "mode": "range",
                    "target": dict(self._selection_target),
                    "is_word": self._selection_is_word,
                    "document": self._word_document,
                    "steps": list(reversed(undo_steps)),
                }
            )
        if not skipped:
            message = (
                "Applied 1 suggestion."
                if applied == 1
                else f"Applied {applied} suggestions."
            )
            self._hide_panel()
        elif applied == 0:
            # Report the real reason instead of a bare "0 of N".
            self._pending = skipped
            message = failure_message
        else:
            # Keep what is left on screen so the user can retry it instead of
            # losing the rest of the review. The selection snapshot now holds
            # the edits that landed, so the retry compares against the document
            # as it really is.
            self._pending = skipped
            self._partial_retry = True
            landed = [span for span in batch if span not in skipped]
            self._selection_text = apply_edits_to_text(self._selection_text, landed)
            self._seen_text = self._selection_text
            message = (
                f"Applied {applied} of {total} suggestions — "
                f"{len(skipped)} could not be placed. Press Apply All to retry."
            )
            panel = self._panel
            if panel is not None:
                panel.set_spans(skipped)
                panel.show()
        self.apply_done.emit(message)

    def _apply_full_selection(self) -> None:
        """Paste the fully corrected text over the current selection.

        Used when the selection came from a clipboard read (Mail compose,
        Pages) and no absolute range exists, so sub-range replacement is
        impossible. The target app replaces its current selection on paste.
        """
        if self._applying:
            return
        self._begin_apply()
        try:
            self._apply_full_selection_locked()
        finally:
            self._end_apply()

    def _apply_full_selection_locked(self) -> None:
        _debug_log(
            f"LIVE FULL APPLY: app={self._selection_target.get('name')!r} "
            f"count={len(self._pending)}"
        )
        # A selection in Mail/Pages can only be read by copying, and a copy of
        # a background window returns nothing (and beeps). Bring the captured
        # app forward first - the paste needs it there anyway.
        self._bring_target_forward()
        state = self._read_selection_state()
        if state is None:
            # One more try, not a burst: each attempt is a real keystroke.
            state = self._read_full_selection_with_retries(attempts=2)
        if state is None:
            _debug_log("LIVE FULL APPLY: selection read failed")
            self._hide_panel()
            self.apply_done.emit(
                self._sync_failure_message("selection could not be read")
            )
            return
        if state[0] != self._seen_text:
            _debug_log(
                "LIVE FULL APPLY: selection changed "
                f"seen={_redact(self._seen_text)} now={_redact(state[0])}"
            )
            self._hide_panel()
            self.apply_done.emit("Selection changed — could not apply.")
            return
        corrected = apply_edits_to_text(self._selection_text, self._pending)
        if not corrected or corrected == self._selection_text:
            _debug_log("LIVE FULL APPLY: nothing to apply")
            self._hide_panel()
            return
        ok, message = self._editor.replace_selection(
            self._selection_target, corrected
        )
        _debug_log(f"LIVE FULL APPLY: replace ok={ok} message={message!r}")
        if not ok:
            # A failed confirmation is NOT proof that nothing was pasted: apps
            # like Mail expose no text to verify against. Ask the app what the
            # selection holds now, and only paste again when the text shows the
            # first attempt really did nothing - a second paste over a
            # collapsed selection would duplicate the paragraph.
            evidence = self._paste_evidence(corrected)
            _debug_log(f"LIVE FULL APPLY: paste evidence={evidence}")
            if evidence == "applied":
                ok, message = True, "Applied all suggestions to the selection."
            elif evidence == "not_applied":
                ok, message = self._paste_via_system_events(corrected)
            else:
                ok, message = True, "Applied — please check the document."
        if ok:
            self._arm_undo(
                {
                    "mode": "full",
                    "target": dict(self._selection_target),
                    "original": self._selection_text,
                    "corrected": corrected,
                }
            )
            message = self._verify_full_apply(corrected)
        self.apply_done.emit(message)
        self._hide_panel()

    def _read_full_selection_with_retries(
        self, attempts: int = 2
    ) -> tuple[str, int, int] | None:
        """Read a clipboard-only selection (Mail, Pages) with a bounded retry.

        Two attempts at most: every attempt posts Command-C, which flashes the
        Edit menu and beeps when nothing is selected, so a failure must never
        turn into a burst of keystrokes.
        """
        for index in range(max(1, min(attempts, 2))):
            text = self._read_selection_by_copy(attempts=1)
            if text:
                return text, 0, 0
            if index + 1 < attempts:
                # Wide enough to clear the copy rate limit in generic_editing;
                # the UI thread blocks here, so this stays a single retry.
                time.sleep(0.55)
        return None

    def _paste_evidence(self, corrected: str) -> str:
        """What the selection holds after a paste: applied/not_applied/unknown.

        A real copy is the only trustworthy signal for apps that expose no
        Accessibility text. "unknown" deliberately blocks a retry: pasting a
        second time could duplicate the paragraph.
        """
        current = self._read_selection_by_copy(attempts=1)
        if not current:
            return "unknown"
        if current.strip() == corrected.strip():
            return "applied"
        if current.strip() == (self._selection_text or "").strip():
            return "not_applied"
        return "unknown"

    def _paste_via_system_events(self, corrected: str) -> tuple[bool, str]:
        """Second paste attempt through System Events keystrokes.

        Used when the direct paste is rejected, usually because the target app
        was not frontmost. The activation helper lives on the editor class,
        not at module level (importing it from the module raised ImportError,
        so this fallback could never work).
        """
        from .generic_editing import (
            GenericTextEditor,
            _mac_clipboard_string,
            _mac_restore_clipboard,
            _mac_set_clipboard,
            _mac_system_events_key,
        )

        try:
            saved = _mac_clipboard_string()
            _mac_set_clipboard(corrected)
            GenericTextEditor._mac_activate(self._selection_target)
            time.sleep(0.25)
            _mac_system_events_key(
                "v", self._selection_target.get("name") or ""
            )
            time.sleep(0.4)
            # Only hand the clipboard back once the paste has had its chance.
            _mac_restore_clipboard(saved)
            _debug_log("LIVE FULL APPLY: system-events paste sent")
            return True, "Applied via system paste."
        except Exception as exc:
            _debug_log(f"LIVE FULL APPLY: system-events fallback error: {exc}")
            return False, "Could not apply the edit in this app."

    def _verify_full_apply(self, corrected: str) -> str:
        """Read the selection back and report honestly."""
        try:
            time.sleep(0.2)
            # Accessibility first (free); only fall back to one copy keystroke,
            # which beeps if the app has nothing selected.
            got = self._editor.get_selection_light(
                self._selection_target
            ) or self._read_selection_by_copy(attempts=1)
            if got and got.strip() == corrected.strip():
                _debug_log("LIVE FULL APPLY: verified")
                return "Applied all suggestions to the selection."
            _debug_log(
                "LIVE FULL APPLY: verify mismatch "
                f"got={got[:40]!r} want={corrected[:40]!r}"
            )
            return "Applied — please check the document."
        except Exception as exc:
            _debug_log(f"LIVE FULL APPLY: verify error: {exc}")
            return "Applied — please check the document."
