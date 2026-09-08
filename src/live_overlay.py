"""Transparent overlay that draws dashed underlines and a hover popup."""

import difflib
import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .live_preview import UNDERLINE_COLOR_HEX, EditSpan

# Bright, Google-inspired surfaces and typography.
SURFACE_SHEET = (
    "QFrame { background: #FFFFFF; border: 1px solid #E8EAED;"
    " border-radius: 16px; }"
)
TITLE_SHEET = "color: #202124; font-size: 14px; font-weight: 600;"
DIFF_SHEET = "color: #202124; font-size: 14px;"
REASON_SHEET = "color: #5F6368; font-size: 12px;"
PRIMARY_BUTTON = (
    "QPushButton { background: #1A73E8; color: #FFFFFF; border: none;"
    " border-radius: 20px; padding: 7px 20px; font-size: 14px;"
    " font-weight: 500; }"
    "QPushButton:hover { background: #1765CC; }"
)
SECONDARY_BUTTON = (
    "QPushButton { background: #FFFFFF; color: #1A73E8;"
    " border: 1px solid #DADCE0; border-radius: 20px; padding: 7px 20px;"
    " font-size: 14px; font-weight: 500; }"
    "QPushButton:hover { background: #F8F9FA; }"
)
CLOSE_BUTTON = (
    "QPushButton { border: none; color: #5F6368; font-size: 16px;"
    " background: transparent; border-radius: 12px; }"
    "QPushButton:hover { background: #F1F3F4; color: #202124; }"
)

OLD_STYLE = "color:#C5221F; background:#FCE8E6;"
NEW_STYLE = "color:#137333; background:#E6F4EA; font-weight:500;"


@dataclass(frozen=True)
class OverlaySpan:
    before: str
    after: str
    reason: str
    rect: QRect


def span_at_point(spans: Sequence[OverlaySpan], pos: QPoint) -> int | None:
    """Return the index of the span whose padded rect contains pos."""
    for index, span in enumerate(spans):
        if span.rect.adjusted(-4, -8, 4, 8).contains(pos):
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


def diff_html(before: str, after: str, context: int = 24) -> str:
    """Render a pinpoint word/character diff, unchanged text left normal."""

    def escape(text: str) -> str:
        return html.escape(text).replace("\n", " ")

    before_tokens = re.findall(r"\S+|\s+", before)
    after_tokens = re.findall(r"\S+|\s+", after)
    matcher = difflib.SequenceMatcher(None, before_tokens, after_tokens)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old = "".join(before_tokens[i1:i2])
        new = "".join(after_tokens[j1:j2])
        if tag == "equal":
            segment = escape(old)
            if len(segment) > context * 2:
                segment = segment[:context] + "…" + segment[-context:]
            if segment:
                parts.append(
                    f"<span style='color:#202124'>{segment}</span>"
                )
        elif tag == "delete":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
        elif tag == "insert":
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
        elif tag == "replace":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
    return "".join(parts) or escape(after)


def clear_layout(layout: QLayout) -> None:
    """Remove every item, including nested layouts, from a layout."""
    while layout.count():
        item = layout.takeAt(0)
        sub = item.layout()
        if sub is not None:
            clear_layout(sub)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


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
    """A compact, pinpoint single-edit card that never steals focus."""

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(SURFACE_SHEET)
        self.setMinimumWidth(280)
        self.setMaximumWidth(420)

    def set_span(self, index: int, span: OverlaySpan) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(10)
        else:
            clear_layout(layout)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Suggested changes")
        title.setStyleSheet(TITLE_SHEET)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(CLOSE_BUTTON)
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        diff_label = QLabel(diff_html(span.before, span.after))
        diff_label.setStyleSheet(DIFF_SHEET)
        diff_label.setWordWrap(True)
        diff_label.setMaximumWidth(384)
        diff_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(diff_label)

        if span.reason:
            reason = QLabel(span.reason)
            reason.setStyleSheet(REASON_SHEET)
            reason.setWordWrap(True)
            layout.addWidget(reason)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
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
        target = QRect(
            anchor.left(),
            anchor.bottom() + 10,
            self.width(),
            self.height(),
        )
        area = _clamp_rect(target)
        if area.y() != target.y():
            target.moveTop(max(0, anchor.top() - self.height() - 10))
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
        hovered = self._hovered
        self._spans = spans
        if not spans:
            self._hide_popup()
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
        if (
            hovered is not None
            and hovered < len(spans)
            and self._popup is not None
        ):
            self._popup.place_near(spans[hovered].rect)

    def hide_overlay(self) -> None:
        self._hide_popup()
        self._spans = []
        self.hide()
        self.dismissed.emit()

    def show_popup(self, index: int) -> None:
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
    """A cursor-anchored card listing all Word edits in track-changes style."""

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(SURFACE_SHEET)
        self.setMinimumWidth(320)
        self.setMaximumWidth(480)

    def set_spans(self, spans: list[EditSpan]) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(10)
        else:
            clear_layout(layout)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Suggested changes")
        title.setStyleSheet(TITLE_SHEET)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(CLOSE_BUTTON)
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        for index, span in enumerate(spans):
            row = QHBoxLayout()
            row.setSpacing(10)
            diff_label = QLabel(diff_html(span.before, span.after))
            diff_label.setStyleSheet(DIFF_SHEET)
            diff_label.setWordWrap(True)
            diff_label.setMaximumWidth(320)
            diff_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            apply_btn = QPushButton("Apply")
            apply_btn.setStyleSheet(PRIMARY_BUTTON)
            apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_btn.clicked.connect(
                lambda _checked=False, i=index: self.apply_requested.emit(i)
            )
            row.addWidget(diff_label, 1)
            row.addWidget(apply_btn, 0, Qt.AlignmentFlag.AlignTop)
            body_layout.addLayout(row)
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
            scroll.setStyleSheet(
                "QScrollArea { border: none; background: transparent; }"
            )
            layout.addWidget(scroll)
        else:
            layout.addWidget(body)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        apply_all_btn = QPushButton("Apply all")
        apply_all_btn.setStyleSheet(PRIMARY_BUTTON)
        apply_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_all_btn.clicked.connect(self.apply_all_requested.emit)
        buttons.addWidget(apply_all_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.adjustSize()

    def place_near(self, point: QPoint) -> None:
        target = QRect(
            point.x() + 14, point.y() + 14, self.width(), self.height()
        )
        self.move(_clamp_rect(target).topLeft())
