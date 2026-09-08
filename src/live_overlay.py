"""Transparent overlay that draws dashed underlines and a hover popup."""

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .live_preview import UNDERLINE_COLOR_HEX, EditSpan


@dataclass(frozen=True)
class OverlaySpan:
    before: str
    after: str
    reason: str
    rect: QRect


def span_at_point(spans: Sequence[OverlaySpan], pos: QPoint) -> int | None:
    """Return the index of the span whose padded rect contains pos."""
    for index, span in enumerate(spans):
        if span.rect.adjusted(-3, -4, 3, 4).contains(pos):
            return index
    return None


def popup_rows(span: OverlaySpan) -> list[tuple[str, str]]:
    """Build (style, text) rows for the track-changes-style popup."""
    rows: list[tuple[str, str]] = []
    if span.before:
        rows.append(("old", span.before))
    if span.after:
        rows.append(("new", span.after))
    if span.reason:
        rows.append(("reason", span.reason))
    return rows


class _SuggestionPopup(QFrame):
    """A small frameless card that never steals focus."""

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #E8E4E0;"
            " border-radius: 10px; }"
        )

    def set_span(self, index: int, span: OverlaySpan) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(4)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for style, text in popup_rows(span):
            label = QLabel()
            label.setWordWrap(True)
            if style == "old":
                label.setText(f"<s style='color:#B91C1C'>{text}</s>")
                label.setStyleSheet("color:#B91C1C; font-size:12px;")
            elif style == "new":
                label.setText(f"<span style='color:#166534'>{text}</span>")
                label.setStyleSheet(
                    "color:#166534; font-size:12px; font-weight:600;"
                )
            else:
                label.setText(text)
                label.setStyleSheet("color:#78716C; font-size:11px;")
            layout.addWidget(label)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(
            lambda: self.apply_requested.emit(index)
        )
        layout.addWidget(apply_btn)
        apply_all_btn = QPushButton("Apply all")
        apply_all_btn.clicked.connect(self.apply_all_requested.emit)
        layout.addWidget(apply_all_btn)
        self.adjustSize()


class LiveOverlay(QWidget):
    """A frameless, mostly click-through window that hosts underlines."""

    hovered = pyqtSignal(int)
    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._spans: list[OverlaySpan] = []
        self._hovered: int | None = None
        self._popup: _SuggestionPopup | None = None

    def set_spans(self, spans: list[OverlaySpan]) -> None:
        self._spans = spans
        self._hide_popup()
        if not spans:
            self.hide()
            return
        union = spans[0].rect
        for span in spans[1:]:
            union = union.united(span.rect)
        self.setGeometry(union.adjusted(-40, -40, 40, 40))
        region = QRegion()
        for span in spans:
            region = region.united(QRegion(span.rect.adjusted(-3, -4, 3, 4)))
        self.setMask(region)
        self.show()
        self.update()

    def hide_overlay(self) -> None:
        self._hide_popup()
        self._spans = []
        self.hide()
        self.dismissed.emit()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        pen = QPen(QColor(UNDERLINE_COLOR_HEX))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        origin = self.geometry().topLeft()
        for span in self._spans:
            rect = span.rect.translated(-origin)
            painter.drawLine(
                rect.left(), rect.bottom() - 2, rect.right(), rect.bottom() - 2
            )
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = span_at_point(
            self._spans,
            event.position().toPoint() + self.geometry().topLeft(),
        )
        if index != self._hovered:
            self._hovered = index
            if index is not None:
                self._show_popup(index)
                self.hovered.emit(index)
            else:
                self._hide_popup()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = None
        self._hide_popup()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        index = span_at_point(
            self._spans,
            event.position().toPoint() + self.geometry().topLeft(),
        )
        if index is not None:
            self.apply_requested.emit(index)
        super().mouseReleaseEvent(event)

    def _show_popup(self, index: int) -> None:
        self._hide_popup()
        span = self._spans[index]
        popup = _SuggestionPopup()
        popup.set_span(index, span)
        popup.apply_requested.connect(self.apply_requested)
        popup.apply_all_requested.connect(self.apply_all_requested)
        x = span.rect.left()
        y = span.rect.bottom() + 6
        popup.move(x, y)
        popup.show()
        self._popup = popup

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None


class WordSuggestionCard(QWidget):
    """A cursor-anchored card listing all Word edits in track-changes style.

    Word does not expose character bounds through the Accessibility API, so
    the per-word hover popup cannot be hit-tested there. Instead, Word gets
    native in-document underlines plus this card, which appears next to the
    pointer as soon as the preview finishes.
    """

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "WordSuggestionCard { background: #FFFFFF;"
            " border: 1px solid #E8E4E0; border-radius: 10px; }"
        )
        self._span_rows: dict[int, QFrame] = {}

    def set_spans(self, spans: list[EditSpan]) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(6)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._span_rows = {}

        header = QHBoxLayout()
        title = QLabel("Live suggestions")
        title.setStyleSheet("color:#44403C; font-size:12px; font-weight:700;")
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        for index, span in enumerate(spans):
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            old_label = QLabel(f"<s style='color:#B91C1C'>{span.before}</s>")
            old_label.setStyleSheet("color:#B91C1C; font-size:12px;")
            arrow_label = QLabel("→")
            new_label = QLabel(
                f"<span style='color:#166534'>{span.after}</span>"
            )
            new_label.setStyleSheet(
                "color:#166534; font-size:12px; font-weight:600;"
            )
            reason_label = QLabel(span.reason)
            reason_label.setStyleSheet("color:#78716C; font-size:11px;")
            apply_btn = QPushButton("Apply")
            apply_btn.clicked.connect(
                lambda _checked=False, i=index: self.apply_requested.emit(i)
            )
            row_layout.addWidget(old_label)
            row_layout.addWidget(arrow_label)
            row_layout.addWidget(new_label, 1)
            if span.reason:
                row_layout.addWidget(reason_label)
            row_layout.addWidget(apply_btn)
            layout.addWidget(row)
            self._span_rows[index] = row

        apply_all_btn = QPushButton("Apply all")
        apply_all_btn.clicked.connect(self.apply_all_requested.emit)
        layout.addWidget(apply_all_btn)
        self.adjustSize()
