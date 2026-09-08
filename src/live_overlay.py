"""Transparent overlay that draws dashed underlines and a hover popup."""

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .live_preview import UNDERLINE_COLOR_HEX, EditSpan

CARD_SHEET = (
    "QFrame { background: #FFFFFF; border: 1px solid #E8E4E0;"
    " border-radius: 12px; }"
)
TITLE_SHEET = "color: #44403C; font-size: 12px; font-weight: 700;"
OLD_SHEET = (
    "color: #B91C1C; background: #FEF2F2; border-radius: 6px;"
    " padding: 2px 7px; font-size: 12px;"
)
NEW_SHEET = (
    "color: #166534; background: #F0FDF4; border-radius: 6px;"
    " padding: 2px 7px; font-size: 12px; font-weight: 600;"
)
REASON_SHEET = "color: #78716C; font-size: 11px;"
PRIMARY_BUTTON = (
    "QPushButton { background: #1A3A2A; color: #FFFFFF;"
    " border: 1px solid #143024; border-radius: 8px; padding: 5px 14px;"
    " font-size: 12px; font-weight: 700; }"
    "QPushButton:hover { background: #143024; }"
)
SECONDARY_BUTTON = (
    "QPushButton { background: #EDF3EF; color: #143024;"
    " border: 1px solid #A9C7B3; border-radius: 8px; padding: 5px 14px;"
    " font-size: 12px; font-weight: 600; }"
    "QPushButton:hover { background: #D6E4DB; }"
)


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


def _add_shadow(widget: QWidget) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(24)
    effect.setOffset(0, 6)
    effect.setColor(QColor(0, 0, 0, 36))
    widget.setGraphicsEffect(effect)


def _clamp_rect(rect: QRect) -> QRect:
    """Keep a window inside the primary screen's available geometry."""
    app = QApplication.instance()
    if app is None:
        return rect
    screen = QApplication.primaryScreen()
    if screen is None:
        return rect
    area = screen.availableGeometry()
    x = max(area.left(), min(rect.x(), area.right() - rect.width()))
    y = max(area.top(), min(rect.y(), area.bottom() - rect.height()))
    return QRect(x, y, rect.width(), rect.height())


class _SuggestionPopup(QFrame):
    """A compact, premium single-edit card that never steals focus."""

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_SHEET)
        self.setMaximumWidth(400)
        _add_shadow(self)

    def set_span(self, index: int, span: OverlaySpan) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(6)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        header = QHBoxLayout()
        title = QLabel("Suggested changes")
        title.setStyleSheet(TITLE_SHEET)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { border: none; color: #A8A29E; font-size: 14px; }"
            "QPushButton:hover { color: #44403C; }"
        )
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        for style, text in popup_rows(span):
            label = QLabel()
            label.setWordWrap(True)
            label.setMaximumWidth(372)
            if style == "old":
                label.setText(f"<s>{text}</s>")
                label.setStyleSheet(OLD_SHEET)
            elif style == "new":
                label.setText(text)
                label.setStyleSheet(NEW_SHEET)
            else:
                label.setText(text)
                label.setStyleSheet(REASON_SHEET)
            layout.addWidget(label)

        buttons = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(PRIMARY_BUTTON)
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(
            lambda: self.apply_requested.emit(index)
        )
        apply_all_btn = QPushButton("Apply all")
        apply_all_btn.setStyleSheet(SECONDARY_BUTTON)
        apply_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_all_btn.clicked.connect(self.apply_all_requested.emit)
        buttons.addWidget(apply_btn)
        buttons.addWidget(apply_all_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.adjustSize()

    def place_near(self, anchor: QRect) -> None:
        """Position below the anchor, or above it when there is no room."""
        target = QRect(
            anchor.left(),
            anchor.bottom() + 8,
            self.width(),
            self.height(),
        )
        area = _clamp_rect(target)
        if area.y() != target.y():
            target.moveTop(max(0, anchor.top() - self.height() - 8))
            area = _clamp_rect(target)
        self.move(area.topLeft())


class LiveOverlay(QWidget):
    """A frameless, click-through window that hosts underlines."""

    hovered = pyqtSignal(int)
    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
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
        origin = self.geometry().topLeft()
        region = QRegion()
        for span in spans:
            local = span.rect.translated(-origin)
            region = region.united(QRegion(local.adjusted(-3, -4, 3, 4)))
        self.setMask(region)
        self.show()
        self.update()

    def hide_overlay(self) -> None:
        self._hide_popup()
        self._spans = []
        self.hide()
        self.dismissed.emit()

    def show_popup(self, index: int) -> None:
        """Show the hover popup for a span (called by the input monitor)."""
        self._hovered = index
        self._show_popup(index)
        self.hovered.emit(index)

    def hide_popup(self) -> None:
        self._hovered = None
        self._hide_popup()

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

    def _show_popup(self, index: int) -> None:
        self._hide_popup()
        if not (0 <= index < len(self._spans)):
            return
        span = self._spans[index]
        popup = _SuggestionPopup()
        popup.set_span(index, span)
        popup.apply_requested.connect(self.apply_requested)
        popup.apply_all_requested.connect(self.apply_all_requested)
        popup.dismissed.connect(self.hide_popup)
        popup.place_near(span.rect)
        popup.show()
        self._popup = popup

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup.deleteLater()
            self._popup = None


class WordSuggestionCard(QWidget):
    """A cursor-anchored card listing all Word edits in track-changes style.

    Word exposes no character bounds through Accessibility, so it gets native
    in-document underlines plus this card, which follows the pointer and stays
    available while the marks persist.
    """

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_SHEET)
        self.setMaximumWidth(440)
        _add_shadow(self)

    def set_spans(self, spans: list[EditSpan]) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(6)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        header = QHBoxLayout()
        title = QLabel("Suggested changes")
        title.setStyleSheet(TITLE_SHEET)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { border: none; color: #A8A29E; font-size: 14px; }"
            "QPushButton:hover { color: #44403C; }"
        )
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        for index, span in enumerate(spans):
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(6)
            old_label = QLabel(f"<s>{span.before}</s>")
            old_label.setStyleSheet(OLD_SHEET)
            old_label.setWordWrap(True)
            old_label.setMaximumWidth(150)
            arrow = QLabel("→")
            arrow.setStyleSheet("color: #A8A29E;")
            new_label = QLabel(span.after)
            new_label.setStyleSheet(NEW_SHEET)
            new_label.setWordWrap(True)
            new_label.setMaximumWidth(170)
            apply_btn = QPushButton("Apply")
            apply_btn.setStyleSheet(PRIMARY_BUTTON)
            apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_btn.clicked.connect(
                lambda _checked=False, i=index: self.apply_requested.emit(i)
            )
            row_layout.addWidget(old_label)
            row_layout.addWidget(arrow)
            row_layout.addWidget(new_label, 1)
            row_layout.addWidget(apply_btn)
            body_layout.addWidget(row)
            if span.reason:
                reason = QLabel(span.reason)
                reason.setStyleSheet(REASON_SHEET)
                reason.setWordWrap(True)
                body_layout.addWidget(reason)

        if len(spans) > 5:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(body)
            scroll.setMaximumHeight(360)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            layout.addWidget(scroll)
        else:
            layout.addWidget(body)

        buttons = QHBoxLayout()
        apply_all_btn = QPushButton("Apply all")
        apply_all_btn.setStyleSheet(PRIMARY_BUTTON)
        apply_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_all_btn.clicked.connect(self.apply_all_requested.emit)
        buttons.addWidget(apply_all_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.adjustSize()

    def place_near(self, point: QPoint) -> None:
        target = QRect(point.x() + 14, point.y() + 14, self.width(), self.height())
        self.move(_clamp_rect(target).topLeft())
