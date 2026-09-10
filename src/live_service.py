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

import subprocess
import threading
import time
from typing import Any

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from .generic_editing import _debug_log, get_generic_editor
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
UNDO_AVAILABLE_MS = 10000

# How long the "no changes needed" panel lingers before fading away.
CLEAN_PANEL_MS = 2600

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


class _EscapeBridge(QObject):
    """Receives global key events off the main thread and re-emits them."""

    pressed = pyqtSignal()

    def notify(self, event: Any) -> None:
        try:
            if int(event.keyCode()) == 53:  # kVK_Escape
                self.pressed.emit()
        except Exception:
            pass


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
        self._selection_text = ""
        self._selection_start = 0
        self._selection_end = 0
        self._selection_target: dict[str, Any] = {}
        self._selection_is_word = False
        self._retry_not_before: float | None = None
        self._fail_streak = 0
        self._last_now = 0.0
        self._selection_has_range = True
        self._last_clipboard_read_at = 0.0
        self._clipboard_empty_streak = 0
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
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_INTERVAL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
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
        if is_word:
            from .word_integration import get_word_integration

            try:
                text, start, end, before, after = (
                    get_word_integration().get_selection_info()
                )
            except Exception:
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
                    copied = self._editor._mac_copy_selection(
                        target.get("pid") or 0,
                        target.get("name") or "",
                        max_attempts=1,
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

        if self._selection_target_changed(target):
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
                    "Live suggestions paused — re-enable ByteProof in "
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

    def _selection_target_changed(self, target: dict[str, Any]) -> bool:
        if not self._selection_target:
            return False
        return self._selection_target.get("pid") != target.get("pid") or str(
            self._selection_target.get("bundle_id", "")
        ) != str(target.get("bundle_id", ""))

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
        if bundle == "com.apple.mail":
            return self._mail_is_composing(target)
        return bool(details.get("found"))

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

    def _spawn_preview(
        self,
        target: dict[str, Any],
        text: str,
        details: dict[str, Any],
        key: str,
    ) -> None:
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
                    "Live suggestions stay off for this selection."
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
        if state is None:
            return False, "selection read failed"
        text, start, end = state
        if text != self._selection_text:
            return (
                False,
                (
                    f"selection changed: previewed={self._selection_text[:30]!r} "
                    f"now={text[:30]!r}"
                ),
            )
        self._selection_start = start
        self._selection_end = end if self._selection_is_word else 0
        return True, "ok"

    def _sync_after_apply(self, expected: str) -> bool:
        """After an apply, keep state only when the selection still covers
        exactly the expected post-edit text (offsets stay valid then)."""
        state = self._read_selection_state()
        if state is None:
            return False
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
            panel.dismissed.connect(self._hide_panel)
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
        _debug_log(f"LIVE DISMISS: {span.before!r} -> {span.after!r}")
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

    def _hide_panel(self) -> None:
        self._pending = []
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
        self._undo_state = state
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
        if self._undo_timer is not None:
            self._undo_timer.stop()
        if self._undo_pill is not None:
            self._undo_pill.hide()

    def _perform_undo(self) -> None:
        state = self._undo_state
        self._hide_undo_pill()
        if not state:
            return
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
                    return
                ok, message = self._editor.replace_selection(
                    state.get("target") or {}, state["original"]
                )
                self.apply_done.emit("Undone." if ok else message)
                return
            restored = 0
            for abs_start, length, original in state.get("steps") or []:
                if state.get("is_word"):
                    from .word_integration import get_word_integration

                    ok, _ = get_word_integration().apply_live_edit(
                        0, abs_start, abs_start + length, original
                    )
                else:
                    ok, _ = self._editor.ax_replace_range(
                        state.get("target") or {},
                        abs_start,
                        length,
                        original,
                        allow_direct_paste=True,
                    )
                if ok:
                    restored += 1
            self.apply_done.emit("Undone." if restored else "Could not undo.")
        except Exception as exc:
            _debug_log(f"LIVE UNDO ERROR: {exc}")
            self.apply_done.emit("Could not undo.")

    # --- apply ---

    def _apply_abs(
        self,
        rel_start: int,
        length: int,
        replacement: str,
        before_text: str | None = None,
    ) -> tuple[bool, str, int]:
        """Replace a relative span; returns (ok, message, absolute start).

        ``before_text`` is the original text expected at the span; the
        editor uses it to guard against apps whose AX state lags behind the
        real document (e.g. ChatGPT applies range writes asynchronously).
        """
        if self._selection_is_word:
            from .word_integration import get_word_integration

            word = get_word_integration()
            start = self._selection_start + rel_start
            end = start + length
            compensated = self._word_compensated_span(word, rel_start, length)
            if compensated is not None:
                start, end = compensated
            ok, message = word.apply_live_edit(0, start, end, replacement)
            return ok, message, start
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

    def _word_compensated_span(
        self, word: Any, rel_start: int, length: int
    ) -> tuple[int, int] | None:
        """Map a visible-text span to absolute Word positions.

        Word counts tracked deletions and field codes in document positions
        but omits them from ``content``, so edits after them would land in
        the wrong place. This re-reads the live state (previous applies in
        an Apply-all shift these spans), then compensates when evidence of
        hidden characters exists; otherwise it returns None and the raw
        offsets are used.
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
                rel_start, sel_start, sel_end, hidden, fields
            )
            end = word_visible_to_doc(
                rel_start + length, sel_start, sel_end, hidden, fields
            )
        except Exception:
            return None
        if start is None or end is None or end <= start:
            return None
        return start, end

    def _apply_one(self, index: int) -> None:
        if not (0 <= index < len(self._pending)):
            return
        _debug_log(
            f"LIVE APPLY ONE: index={index} "
            f"app={self._selection_target.get('name')!r} "
            f"has_range={self._selection_has_range} "
            f"is_word={self._selection_is_word}"
        )
        if not self._selection_has_range:
            # No absolute range available (Mail/Pages clipboard path): paste
            # the fully corrected selection over the current one.
            self._apply_full_selection()
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
            self.apply_done.emit(
                "Could not verify the selection — please try again."
            )
            return
        span = self._pending[index]
        ok, message, abs_start = self._apply_abs(
            span.start,
            span.end - span.start,
            span.after,
            before_text=self._selection_text[span.start : span.end],
        )
        _debug_log(
            f"LIVE APPLY ONE RESULT: ok={ok} message={message!r} "
            f"rel=({span.start},{span.end})"
        )
        self.apply_done.emit(message)
        if not ok:
            return
        self._arm_undo(
            {
                "mode": "range",
                "target": dict(self._selection_target),
                "is_word": self._selection_is_word,
                "steps": [(abs_start, len(span.after), span.before)],
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
        expected = (
            self._selection_text[: span.start]
            + span.after
            + self._selection_text[span.end :]
        )
        if not self._sync_after_apply(expected):
            _debug_log("LIVE APPLY ONE: post-apply selection drifted; closing")
            self._hide_panel()
            return
        panel = self._panel
        if panel is not None:
            panel.set_spans(remaining)
            panel.show()

    def _apply_all(self) -> None:
        if not self._pending:
            return
        _debug_log(
            f"LIVE APPLY ALL: app={self._selection_target.get('name')!r} "
            f"has_range={self._selection_has_range} "
            f"count={len(self._pending)}"
        )
        if not self._selection_has_range:
            self._apply_full_selection()
            return
        sync_ok, sync_reason = self._sync_selection()
        for _ in range(2):
            if sync_ok:
                break
            time.sleep(0.12)
            sync_ok, sync_reason = self._sync_selection()
        if not sync_ok:
            _debug_log(f"LIVE APPLY ALL SYNC FAIL: {sync_reason}")
            self._hide_panel()
            self.apply_done.emit(
                "Could not verify the selection — please try again."
            )
            return
        total = len(self._pending)
        applied = 0
        delta = 0
        undo_steps: list[tuple[int, int, str]] = []
        for span in sorted(self._pending, key=lambda s: s.start):
            rel_start = span.start + delta
            rel_end = span.end + delta
            ok, _, abs_start = self._apply_abs(
                rel_start,
                rel_end - rel_start,
                span.after,
                before_text=self._selection_text[span.start : span.end],
            )
            if ok:
                applied += 1
                delta += len(span.after) - (span.end - span.start)
                undo_steps.append((abs_start, len(span.after), span.before))
        self.apply_all_requested.emit()
        if applied:
            self._arm_undo(
                {
                    "mode": "range",
                    "target": dict(self._selection_target),
                    "is_word": self._selection_is_word,
                    "steps": list(reversed(undo_steps)),
                }
            )
        if applied == total:
            message = f"Applied {applied} suggestions."
        else:
            message = f"Applied {applied} of {total} suggestions."
        self.apply_done.emit(message)
        self._hide_panel()

    def _apply_full_selection(self) -> None:
        """Paste the fully corrected text over the current selection.

        Used when the selection came from a clipboard read (Mail compose,
        Pages) and no absolute range exists, so sub-range replacement is
        impossible. The target app replaces its current selection on paste.
        """
        _debug_log(
            f"LIVE FULL APPLY: app={self._selection_target.get('name')!r} "
            f"count={len(self._pending)}"
        )
        state = self._read_selection_state()
        if state is None:
            _debug_log("LIVE FULL APPLY: selection read failed")
            self._hide_panel()
            self.apply_done.emit(
                "Could not verify the selection — please try again."
            )
            return
        if state[0] != self._seen_text:
            _debug_log(
                "LIVE FULL APPLY: selection changed "
                f"seen={self._seen_text[:30]!r} now={state[0][:30]!r}"
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
            ok, message = self._paste_via_system_events(corrected)
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

    def _paste_via_system_events(self, corrected: str) -> tuple[bool, str]:
        """Second paste attempt through System Events keystrokes."""
        from .generic_editing import (
            _mac_activate,
            _mac_clipboard_string,
            _mac_restore_clipboard,
            _mac_set_clipboard,
            _mac_system_events_key,
        )

        try:
            saved = _mac_clipboard_string()
            _mac_set_clipboard(corrected)
            _mac_activate(self._selection_target)
            _mac_system_events_key(
                "v", self._selection_target.get("name") or ""
            )
            time.sleep(0.4)
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
            got = self._editor.get_selection_light(self._selection_target)
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
