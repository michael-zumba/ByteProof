"""Orchestrates live-preview sampling, provider calls, rendering, and apply."""

import time
from typing import Any

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor

from .generic_editing import _debug_log, get_generic_editor
from .live_overlay import (
    LiveOverlay,
    OverlaySpan,
    WordSuggestionCard,
    span_at_point,
)
from .live_preview import (
    DEFAULT_DELAY_MS,
    POLL_INTERVAL_MS,
    EditSpan,
    PreviewCache,
    evaluate_trigger,
    map_edits_to_ranges,
    preview_cache_key,
    settings_fingerprint,
)


class PreviewWorker(QThread):
    """Runs the provider call off the UI thread."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        settings: dict[str, Any],
        target: dict[str, Any],
        selected: str,
        before: str,
        after: str,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.target = target
        self.selected = selected
        self.before = before
        self.after = after

    def run(self) -> None:  # noqa: D102
        from . import logic

        try:
            status, edits, meta = logic.preview_edits_once(
                self.settings,
                self.target,
                self.selected,
                self.before,
                self.after,
            )
            self.done.emit({"status": status, "edits": edits, "meta": meta})
        except Exception as exc:
            self.failed.emit(str(exc))


class LivePreviewService(QObject):
    """Watches the frontmost app and selection, then renders live edits."""

    apply_done = pyqtSignal(str)
    apply_all_requested = pyqtSignal()
    preview_error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings: dict[str, Any] = {}
        self._editor = get_generic_editor()
        self._overlay = LiveOverlay()
        self._cache = PreviewCache()
        self._fingerprint = ""
        self._marks: list[EditSpan] = []
        self._mark_target: dict[str, Any] = {}
        self._mark_is_word = False
        self._mark_rects: list[QRect] = []
        self._overlay_spans: list[OverlaySpan] = []
        self._overlay_indices: list[int] = []
        self._seen_text = ""
        self._changed_at = 0.0
        self._previewed_text = ""
        self._worker: PreviewWorker | None = None
        self._timer: QTimer | None = None
        self._word_card: WordSuggestionCard | None = None
        self._tap: Any = None
        self._tap_source: Any = None
        self._tap_callback_ref: Any = None
        self._hovered: int | None = None
        self._last_hover_ts = 0.0
        self._last_rect_refresh = 0.0
        self._overlay.apply_requested.connect(self._apply_overlay_mark)
        self._overlay.apply_all_requested.connect(self._apply_all)

    # --- lifecycle ---

    def start(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_INTERVAL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._seen_text = ""
        self._previewed_text = ""
        self._clear_marks()

    def refresh_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._fingerprint = settings_fingerprint(settings)
        if not settings.get("live_preview", {}).get("enabled", True):
            self._clear_marks()

    # --- sampling ---

    def _poll(self) -> None:
        try:
            self._sample(now=time.monotonic())
        except Exception:
            pass

    def _sample(self, now: float) -> None:
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

        # Switching to a different app discards marks from the previous one.
        if self._marks and self._target_changed(target):
            self._clear_marks()

        # Keep underlines aligned while the user scrolls or the window moves:
        # refresh character bounds for existing marks even with no selection.
        if (
            self._marks
            and not self._mark_is_word
            and now - self._last_rect_refresh >= 0.8
            and not self._target_changed(target)
        ):
            self._last_rect_refresh = now
            self._render_generic(self._marks)

        if text != self._seen_text:
            self._seen_text = text
            self._changed_at = now
            # Allow the same selection to be previewed again later.
            self._previewed_text = ""

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

        self._previewed_text = text
        selection_start = (details.get("range") or (0, 0))[0]
        key = preview_cache_key(
            str(target.get("bundle_id", "")),
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._fingerprint,
        )
        cached = self._cache.get(key)
        if cached is not None:
            absolute = [
                EditSpan(
                    span.before,
                    span.after,
                    span.reason,
                    selection_start + span.start,
                    selection_start + span.end,
                )
                for span in cached
            ]
            self._set_marks(absolute, target, is_word)
            return
        self._spawn_preview(target, text, details, key, selection_start, is_word)

    def _target_changed(self, target: dict[str, Any]) -> bool:
        old = self._mark_target
        if not old:
            return False
        return old.get("pid") != target.get("pid") or str(
            old.get("bundle_id", "")
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
        selection_start: int,
        is_word: bool,
    ) -> None:
        _debug_log(f"LIVE PREVIEW: app={target.get('name')!r} chars={len(text)}")
        self._worker = PreviewWorker(
            self._settings,
            target,
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
        )
        self._worker.done.connect(
            lambda result, k=key, t=text, tg=target, s=selection_start, w=is_word: self._on_done(
                result, k, t, tg, s, w
            )
        )
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(
        self,
        result: dict[str, Any],
        key: str,
        text: str,
        target: dict[str, Any],
        selection_start: int,
        is_word: bool,
    ) -> None:
        self._worker = None
        if text != self._seen_text:
            return
        spans = map_edits_to_ranges(text, result.get("edits") or [])
        _debug_log(
            f"LIVE DONE: edits={len(spans)} "
            f"provider={result.get('meta', {}).get('provider')}"
        )
        self._cache.put(key, spans)
        absolute = [
            EditSpan(
                span.before,
                span.after,
                span.reason,
                selection_start + span.start,
                selection_start + span.end,
            )
            for span in spans
        ]
        self._set_marks(absolute, target, is_word)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._clear_marks()
        _debug_log(f"LIVE ERROR: {message}")
        self.preview_error.emit(message)

    # --- marks and rendering ---

    def _set_marks(
        self,
        spans: list[EditSpan],
        target: dict[str, Any],
        is_word: bool,
    ) -> None:
        """Merge new spans into the existing marks (deduplicating)."""
        existing = {
            (span.start, span.end, span.before, span.after)
            for span in self._marks
        }
        for span in spans:
            key = (span.start, span.end, span.before, span.after)
            if key not in existing:
                self._marks.append(span)
                existing.add(key)
        self._mark_target = target
        self._mark_is_word = is_word
        if not self._marks:
            self._clear_marks()
            return
        if is_word:
            self._render_word(self._marks)
        else:
            self._render_generic(self._marks)

    def _render_generic(self, spans: list[EditSpan]) -> None:
        overlay_spans: list[OverlaySpan] = []
        overlay_indices: list[int] = []
        for index, span in enumerate(spans):
            bounds = self._editor.ax_bounds_for_range(
                self._mark_target,
                span.start,
                span.end - span.start,
            )
            rect = self._rect_from_bounds(bounds)
            if not rect.isNull():
                overlay_spans.append(
                    OverlaySpan(span.before, span.after, span.reason, rect)
                )
                overlay_indices.append(index)
        self._overlay_spans = overlay_spans
        self._overlay_indices = overlay_indices
        if not overlay_spans:
            self._mark_rects = []
            self._show_card(spans)
            return
        self._mark_rects = [span.rect for span in overlay_spans]
        self._overlay.set_spans(overlay_spans)
        self._start_tap()

    def _render_word(self, spans: list[EditSpan]) -> None:
        from .word_integration import get_word_integration

        word = get_word_integration()
        try:
            for span in spans:
                word.set_live_underline(
                    0, span.start, span.end, True
                )
        except Exception:
            pass
        self._mark_rects = []
        self._show_card(spans)
        self._start_tap()

    def _show_card(self, spans: list[EditSpan]) -> None:
        """Show the floating suggestions card for the given spans."""
        if self._word_card is None:
            self._word_card = WordSuggestionCard()
            self._word_card.apply_requested.connect(self._apply_mark)
            self._word_card.apply_all_requested.connect(self._apply_all)
            self._word_card.dismissed.connect(self._clear_marks)
        self._word_card.set_spans(spans)
        self._word_card.show()
        self._word_card.place_near(QCursor.pos())

    @staticmethod
    def _rect_from_bounds(
        bounds: list[tuple[float, float, float, float]],
    ) -> QRect:
        if not bounds:
            return QRect()
        xs = [b[0] for b in bounds]
        ys = [b[1] for b in bounds]
        rights = [b[0] + b[2] for b in bounds]
        bottoms = [b[1] + b[3] for b in bounds]
        return QRect(
            int(min(xs)),
            int(min(ys)),
            int(max(rights) - min(xs)),
            int(max(bottoms) - min(ys)),
        )

    # --- input (Quartz event tap, macOS) ---

    def _start_tap(self) -> None:
        self._stop_tap()
        try:
            import ApplicationServices as AS
            import Quartz

            if not AS.AXIsProcessTrusted():
                return
            mask = (
                (1 << Quartz.kCGEventMouseMoved)
                | (1 << Quartz.kCGEventLeftMouseDown)
                | (1 << Quartz.kCGEventKeyDown)
            )
            service = self

            def handler(proxy: Any, event_type: Any, event: Any, refcon: Any) -> Any:
                try:
                    event_type = int(event_type)
                    location = Quartz.CGEventGetLocation(event)
                    keycode = -1
                    if event_type == int(Quartz.kCGEventKeyDown):
                        keycode = int(
                            Quartz.CGEventGetIntegerValueField(
                                event, Quartz.kCGKeyboardEventKeycode
                            )
                        )
                    service._on_pointer_event(
                        event_type,
                        int(location.x),
                        int(location.y),
                        keycode,
                    )
                except Exception as exc:
                    _debug_log(f"LIVE TAP HANDLER ERROR: {exc}")
                return event

            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                handler,
                None,
            )
            if tap is None:
                _debug_log("LIVE TAP: event tap creation failed")
                return
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self._tap_callback_ref = handler
            self._tap = tap
            self._tap_source = source
        except Exception as exc:
            _debug_log(f"LIVE MOUSE MONITOR: {exc}")

    def _stop_tap(self) -> None:
        if self._tap is not None:
            try:
                import Quartz

                Quartz.CGEventTapEnable(self._tap, False)
                if self._tap_source is not None:
                    Quartz.CFRunLoopRemoveSource(
                        Quartz.CFRunLoopGetCurrent(),
                        self._tap_source,
                        Quartz.kCFRunLoopCommonModes,
                    )
            except Exception:
                pass
        self._tap = None
        self._tap_source = None
        self._tap_callback_ref = None
        self._hovered = None

    def _on_pointer_event(
        self, event_type: int, x: int, y: int, keycode: int = -1
    ) -> None:
        # Escape dismisses the marks.
        if event_type == 10 and keycode == 53:  # kCGEventKeyDown, Esc
            self._clear_marks()
            return
        pos = QPoint(x, y)
        if self._mark_is_word:
            if event_type == 5 and self._word_card is not None:
                if self._word_window_contains(pos):
                    if not self._word_card.isVisible():
                        self._word_card.show()
                elif self._word_card.isVisible() and not self._overlay.isVisible():
                    self._word_card.hide()
            return
        index = span_at_point(self._overlay_spans, pos)
        if event_type == 5:  # mouse moved
            now = time.monotonic()
            if now - self._last_hover_ts < 0.04:
                return
            self._last_hover_ts = now
            if index != self._hovered:
                _debug_log(f"LIVE HOVER: index={index} pos={x},{y}")
                self._hovered = index
                if index is None:
                    self._overlay.hide_popup()
                else:
                    self._overlay.show_popup(index)
        elif event_type == 1 and index is not None:  # left mouse down
            _debug_log(f"LIVE CLICK: index={index} pos={x},{y}")
            self._apply_overlay_mark(index)

    def _word_window_contains(self, pos: QPoint) -> bool:
        try:
            import ApplicationServices as AS

            pid = self._mark_target.get("pid")
            if not pid:
                return True
            el = AS.AXUIElementCreateApplication(pid)
            _, win = AS.AXUIElementCopyAttributeValue(
                el, AS.kAXFocusedWindowAttribute, None
            )
            if win is None:
                return True
            _, posv = AS.AXUIElementCopyAttributeValue(
                win, AS.kAXPositionAttribute, None
            )
            _, sizev = AS.AXUIElementCopyAttributeValue(
                win, AS.kAXSizeAttribute, None
            )
            ok1, p = AS.AXValueGetValue(posv, AS.kAXValueCGPointType, None)
            ok2, s = AS.AXValueGetValue(sizev, AS.kAXValueCGSizeType, None)
            if not (ok1 and ok2):
                return True
            return (
                p.x <= pos.x() <= p.x + s.width
                and p.y <= pos.y() <= p.y + s.height
            )
        except Exception:
            return True

    # --- apply ---

    def _apply_overlay_mark(self, overlay_index: int) -> None:
        if not (0 <= overlay_index < len(self._overlay_indices)):
            return
        span_index = self._overlay_indices[overlay_index]
        self._apply_mark(span_index)

    def _apply_mark(self, index: int) -> None:
        if not (0 <= index < len(self._marks)):
            return
        span = self._marks[index]
        if self._mark_is_word:
            from .word_integration import get_word_integration

            ok, message = get_word_integration().apply_live_edit(
                0, span.start, span.end, span.after
            )
        else:
            ok, message = self._editor.ax_replace_range(
                self._mark_target,
                span.start,
                span.end - span.start,
                span.after,
            )
        self.apply_done.emit(message)
        if ok:
            self._rerender_after_apply(index)

    def _rerender_after_apply(self, removed_index: int) -> None:
        """Drop the applied mark, shift later marks, and keep the rest."""
        removed = self._marks[removed_index]
        delta = len(removed.after) - (removed.end - removed.start)
        new_marks: list[EditSpan] = []
        for i, span in enumerate(self._marks):
            if i == removed_index:
                continue
            if span.start < removed.end and span.end > removed.start:
                continue  # overlapping mark is stale after the edit
            if span.start >= removed.end:
                span = EditSpan(
                    span.before,
                    span.after,
                    span.reason,
                    span.start + delta,
                    span.end + delta,
                )
            new_marks.append(span)
        self._marks = new_marks
        if not new_marks:
            self._clear_marks()
            return
        if self._mark_is_word:
            from .word_integration import get_word_integration

            get_word_integration().clear_live_underlines()
            self._render_word(new_marks)
        else:
            self._render_generic(new_marks)

    def _apply_all(self) -> None:
        """Apply every active suggestion directly, without opening the app."""
        if not self._marks:
            return
        if self._mark_is_word:
            from .word_integration import get_word_integration

            word = get_word_integration()
        else:
            word = None
        applied = 0
        delta = 0
        for span in sorted(list(self._marks), key=lambda s: s.start):
            rel_start = span.start + delta
            rel_end = span.end + delta
            try:
                if word is not None:
                    ok, _ = word.apply_live_edit(
                        0, rel_start, rel_end, span.after
                    )
                else:
                    ok, _ = self._editor.ax_replace_range(
                        self._mark_target,
                        rel_start,
                        rel_end - rel_start,
                        span.after,
                    )
            except Exception:
                ok = False
            if ok:
                applied += 1
                delta += len(span.after) - (span.end - span.start)
        self.apply_all_requested.emit()
        self.apply_done.emit(f"Applied {applied} suggestions.")
        self._clear_marks()

    # --- teardown ---

    def _clear_marks(self) -> None:
        self._marks = []
        self._mark_rects = []
        self._overlay_spans = []
        self._overlay_indices = []
        self._mark_target = {}
        self._stop_tap()
        self._overlay.hide_overlay()
        if self._word_card is not None:
            self._word_card.hide()
        if self._mark_is_word:
            try:
                from .word_integration import get_word_integration

                get_word_integration().clear_live_underlines()
            except Exception:
                pass
        self._mark_is_word = False
