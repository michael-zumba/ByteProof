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

import threading
import time
from typing import Any

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

from .generic_editing import _debug_log, get_generic_editor
from .live_overlay import LiveSuggestionPanel, apply_nonactivating_panel
from .live_preview import (
    DEFAULT_DELAY_MS,
    POLL_INTERVAL_MS,
    RETRY_COOLDOWN_S,
    RETRY_MAX_FAILURES,
    EditSpan,
    PreviewCache,
    evaluate_trigger,
    map_edits_to_ranges,
    preview_cache_key,
    settings_fingerprint,
    word_visible_to_doc,
)


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
        self._hide_panel()

    def refresh_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._fingerprint = settings_fingerprint(settings)
        if not settings.get("live_preview", {}).get("enabled", True):
            self._hide_panel()

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
        is_word = bool(getattr(self._editor, "is_word", lambda _t: False)(target))
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

        if self._selection_target_changed(target) or text != self._seen_text:
            self._hide_panel()
        if text != self._seen_text:
            self._seen_text = text
            self._changed_at = now
            self._previewed_text = ""
            self._retry_not_before = None
            self._fail_streak = 0

        stable = now - self._changed_at >= self._delay() / 1000.0
        decision, _reason = evaluate_trigger(
            self._settings,
            target,
            text,
            permission_ok,
            stable,
            self._worker is not None,
            text == self._previewed_text,
        )
        if decision != "run":
            if decision not in ("unchanged", "not_stable", "empty", "self"):
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
        key = preview_cache_key(
            str(target.get("bundle_id", "")),
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

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_done(self, result: dict[str, Any], key: str, text: str) -> None:
        if text != self._seen_text:
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
        _debug_log(
            f"LIVE DONE: edits={len(spans)} "
            f"provider={result.get('meta', {}).get('provider')}"
        )
        self._cache.put(key, spans)
        if not self._sync_selection():
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
        try:
            details = self._editor.selection_details(self._selection_target)
            text = details.get("text") or ""
            start = (details.get("range") or (0, 0))[0] or 0
            return str(text), int(start), 0
        except Exception:
            return None

    def _sync_selection(self) -> bool:
        """Verify the previewed selection still holds and re-anchor to it.

        The provider call takes seconds; the user may have re-selected the
        same phrase elsewhere in the meantime. Applying at the stale position
        would corrupt the document, so the current range is re-read before
        showing results or applying anything.
        """
        state = self._read_selection_state()
        if state is None:
            return False
        text, start, end = state
        if text != self._seen_text or text != self._selection_text:
            return False
        self._selection_start = start
        self._selection_end = end if self._selection_is_word else 0
        return True

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

    def _show_result(self, spans: list[EditSpan]) -> None:
        self._pending = spans
        if not spans:
            self._hide_panel()
            return
        if not self._settings.get("live_preview", {}).get("enabled", True):
            self._hide_panel()
            return
        panel = self._panel
        if panel is None:
            panel = LiveSuggestionPanel()
            panel.apply_requested.connect(self._apply_one)
            panel.apply_all_requested.connect(self._apply_all)
            panel.dismissed.connect(self._hide_panel)
            apply_nonactivating_panel(panel)
            self._panel = panel
        was_visible = panel.isVisible()
        panel.set_spans(spans)
        if not was_visible:
            panel.place_near(self._anchor_point())
        panel.show()
        self._install_escape_monitor()

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
        if self._panel is not None:
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
        self._hide_panel()

    # --- apply ---

    def _apply_abs(
        self, rel_start: int, length: int, replacement: str
    ) -> tuple[bool, str]:
        if self._selection_is_word:
            from .word_integration import get_word_integration

            word = get_word_integration()
            start = self._selection_start + rel_start
            end = start + length
            compensated = self._word_compensated_span(word, rel_start, length)
            if compensated is not None:
                start, end = compensated
            return word.apply_live_edit(0, start, end, replacement)
        return self._editor.ax_replace_range(
            self._selection_target,
            self._selection_start + rel_start,
            length,
            replacement,
        )

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
        if not self._sync_selection():
            self._hide_panel()
            return
        span = self._pending[index]
        ok, message = self._apply_abs(
            span.start, span.end - span.start, span.after
        )
        self.apply_done.emit(message)
        if not ok:
            return
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
            self._hide_panel()
            return
        panel = self._panel
        if panel is not None:
            panel.set_spans(remaining)
            panel.show()

    def _apply_all(self) -> None:
        if not self._pending:
            return
        if not self._sync_selection():
            self._hide_panel()
            return
        total = len(self._pending)
        applied = 0
        delta = 0
        for span in sorted(self._pending, key=lambda s: s.start):
            rel_start = span.start + delta
            rel_end = span.end + delta
            ok, _ = self._apply_abs(
                rel_start, rel_end - rel_start, span.after
            )
            if ok:
                applied += 1
                delta += len(span.after) - (span.end - span.start)
        self.apply_all_requested.emit()
        if applied == total:
            message = f"Applied {applied} suggestions."
        else:
            message = f"Applied {applied} of {total} suggestions."
        self.apply_done.emit(message)
        self._hide_panel()
