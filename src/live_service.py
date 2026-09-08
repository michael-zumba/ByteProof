"""Orchestrates live-preview sampling, provider calls, rendering, and apply."""

import threading
import time
from typing import Any

from PyQt6.QtCore import QObject, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor

from .generic_editing import get_generic_editor
from .live_overlay import LiveOverlay, OverlaySpan, WordSuggestionCard
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
        self._selection_start = 0
        self._selection_text = ""
        self._selected_target: dict[str, Any] = {}
        self._spans: list[EditSpan] = []
        self._is_word = False
        self._seen_text = ""
        self._changed_at = 0.0
        self._previewed_text = ""
        self._worker: PreviewWorker | None = None
        self._timer: QTimer | None = None
        self._word_card: WordSuggestionCard | None = None
        self._overlay.apply_requested.connect(self._apply_index)
        self._overlay.apply_all_requested.connect(self.apply_all_requested)

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
        self._clear_preview()

    def refresh_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._fingerprint = settings_fingerprint(settings)
        if not settings.get("live_preview", {}).get("enabled", True):
            self._clear_preview()

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
            self._clear_preview()
            return
        self._is_word = bool(getattr(self._editor, "is_word", lambda _t: False)(target))
        permission_ok, _ = self._editor.permission_status()
        if self._is_word:
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
        if text != self._seen_text:
            self._seen_text = text
            self._changed_at = now
            if self._spans:
                self._clear_preview()
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
            return
        self._selection_text = text
        self._selected_target = target
        self._selection_start = (details.get("range") or (0, 0))[0]
        self._previewed_text = text
        key = preview_cache_key(
            str(target.get("bundle_id", "")),
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
            self._fingerprint,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._spans = cached
            self._render(cached)
            return
        self._spawn_preview(target, text, details, key)

    def _delay(self) -> int:
        return int(
            self._settings.get("live_preview", {}).get(
                "delay_ms", DEFAULT_DELAY_MS
            )
        )

    def _spawn_preview(
        self,
        target: dict[str, Any],
        text: str,
        details: dict[str, Any],
        key: str,
    ) -> None:
        self._worker = PreviewWorker(
            self._settings,
            target,
            text,
            details.get("context_before", ""),
            details.get("context_after", ""),
        )
        self._worker.done.connect(
            lambda result, k=key, t=text: self._on_done(result, k, t)
        )
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, result: dict[str, Any], key: str, text: str) -> None:
        self._worker = None
        if text != self._seen_text:
            return
        spans = map_edits_to_ranges(text, result.get("edits") or [])
        self._cache.put(key, spans)
        self._spans = spans
        self._render(spans)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._clear_preview()
        self.preview_error.emit(message)

    def _render(self, spans: list[EditSpan]) -> None:
        if not spans:
            self._clear_preview()
            return
        if getattr(self, "_is_word", False):
            self._render_word(spans)
            return
        overlay_spans: list[OverlaySpan] = []
        for span in spans:
            bounds = self._editor.ax_bounds_for_range(
                self._selected_target,
                self._selection_start + span.start,
                span.end - span.start,
            )
            rect = self._rect_from_bounds(bounds)
            if not rect.isNull():
                overlay_spans.append(
                    OverlaySpan(span.before, span.after, span.reason, rect)
                )
        self._overlay.set_spans(overlay_spans)

    def _render_word(self, spans: list[EditSpan]) -> None:
        from .word_integration import get_word_integration

        word = get_word_integration()
        try:
            word.clear_live_underlines()
            for span in spans:
                word.set_live_underline(
                    self._selection_start, span.start, span.end, True
                )
        except Exception:
            pass
        if self._word_card is None:
            self._word_card = WordSuggestionCard()
            self._word_card.apply_requested.connect(self._apply_index)
            self._word_card.apply_all_requested.connect(self.apply_all_requested)
            self._word_card.dismissed.connect(self._clear_preview)
        self._word_card.set_spans(spans)
        cursor = QCursor.pos()
        self._word_card.move(cursor.x() + 12, cursor.y() + 12)
        self._word_card.show()

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

    def _apply_index(self, index: int) -> None:
        if not (0 <= index < len(self._spans)):
            return
        self._apply_span(self._spans[index])

    def _apply_span(self, span: EditSpan) -> None:
        if getattr(self, "_is_word", False):
            from .word_integration import get_word_integration

            ok, message = get_word_integration().apply_live_edit(
                self._selection_start, span.start, span.end, span.after
            )
        else:
            ok, message = self._editor.ax_replace_range(
                self._selected_target,
                self._selection_start + span.start,
                span.end - span.start,
                span.after,
            )
        self.apply_done.emit(message)
        if ok:
            self._previewed_text = ""
            self._clear_preview()

    def _clear_preview(self) -> None:
        self._spans = []
        self._overlay.hide_overlay()
        if self._word_card is not None:
            self._word_card.hide()
        if getattr(self, "_is_word", False):
            try:
                from .word_integration import get_word_integration

                get_word_integration().clear_live_underlines()
            except Exception:
                pass
