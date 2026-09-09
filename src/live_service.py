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

import time
from typing import Any

from PyQt6.QtCore import QObject, QPoint, QRect, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor

from .generic_editing import _debug_log, get_generic_editor
from .live_overlay import LiveSuggestionPanel, apply_nonactivating_panel
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
        self._timer: QTimer | None = None
        self._panel: LiveSuggestionPanel | None = None
        self._pending: list[EditSpan] = []
        self._selection_text = ""
        self._selection_start = 0
        self._selection_target: dict[str, Any] = {}
        self._selection_is_word = False

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
        self._selection_text = text
        self._selection_target = target
        self._selection_start = (details.get("range") or (0, 0))[0]
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
        _debug_log(
            f"LIVE DONE: edits={len(spans)} "
            f"provider={result.get('meta', {}).get('provider')}"
        )
        self._cache.put(key, spans)
        self._show_result(spans)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._hide_panel()
        _debug_log(f"LIVE ERROR: {message}")
        self.preview_error.emit(message)

    # --- presentation ---

    def _show_result(self, spans: list[EditSpan]) -> None:
        self._pending = spans
        if not spans:
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
        if self._panel is not None:
            self._panel.hide()

    # --- apply ---

    def _apply_abs(self, abs_start: int, length: int, replacement: str) -> tuple[bool, str]:
        if self._selection_is_word:
            from .word_integration import get_word_integration

            return get_word_integration().apply_live_edit(
                0, abs_start, abs_start + length, replacement
            )
        return self._editor.ax_replace_range(
            self._selection_target, abs_start, length, replacement
        )

    def _apply_one(self, index: int) -> None:
        if not (0 <= index < len(self._pending)):
            return
        span = self._pending[index]
        ok, message = self._apply_abs(
            self._selection_start + span.start,
            span.end - span.start,
            span.after,
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
        panel = self._panel
        if panel is not None:
            panel.set_spans(remaining)
            panel.show()

    def _apply_all(self) -> None:
        if not self._pending:
            return
        applied = 0
        delta = 0
        for span in sorted(self._pending, key=lambda s: s.start):
            rel_start = span.start + delta
            rel_end = span.end + delta
            ok, _ = self._apply_abs(
                self._selection_start + rel_start,
                rel_end - rel_start,
                span.after,
            )
            if ok:
                applied += 1
                delta += len(span.after) - (span.end - span.start)
        self.apply_all_requested.emit()
        self.apply_done.emit(f"Applied {applied} suggestions.")
        self._hide_panel()
