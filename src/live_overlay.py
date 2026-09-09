"""Transparent overlay that draws dashed underlines and a hover popup."""

import difflib
import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QEasingCurve, QPoint, QRect, Qt, QVariantAnimation, pyqtSignal
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
    "QFrame { background: #FFFFFF; border: 1px solid #E3E6EB;"
    " border-radius: 20px; }"
)
TITLE_SHEET = "color: #202124; font-size: 14px; font-weight: 600;"
DIFF_SHEET = "color: #202124; font-size: 14px;"
REASON_SHEET = "color: #5F6368; font-size: 12px;"
DIFF_BOX = (
    "QFrame { background: #F7F8FA; border: 1px solid #EDF0F4;"
    " border-radius: 12px; }"
)
PRIMARY_BUTTON = (
    "QPushButton { background: #1A73E8; color: #FFFFFF; border: none;"
    " border-radius: 20px; padding: 7px 20px; font-size: 14px;"
    " font-weight: 500; }"
    "QPushButton:hover { background: #1765CC; }"
    "QPushButton:pressed { background: #1256A8; }"
)
SECONDARY_BUTTON = (
    "QPushButton { background: #FFFFFF; color: #1A73E8;"
    " border: 1px solid #DADCE0; border-radius: 20px; padding: 7px 20px;"
    " font-size: 14px; font-weight: 500; }"
    "QPushButton:hover { background: #F8F9FA; }"
    "QPushButton:pressed { background: #EDF0F4; }"
)
CLOSE_BUTTON = (
    "QPushButton { border: none; color: #5F6368; font-size: 16px;"
    " background: transparent; border-radius: 13px; }"
    "QPushButton:hover { background: #F1F3F4; color: #202124; }"
    "QPushButton:pressed { background: #E4E7EB; color: #202124; }"
)

OLD_STYLE = "color:#C5221F; background:#FCE8E6;"
NEW_STYLE = "color:#137333; background:#E6F4EA; font-weight:500;"
ARROW_STYLE = "color:#9AA0A6;"
CONTEXT_STYLE = "color:#5F6368;"
DIVIDER_SHEET = "QFrame { background: #F1F3F5; border: none; }"
REASON_CHIP = (
    "color: #5F6368; font-size: 11px; background: #F1F3F4;"
    " border-radius: 9px; padding: 2px 8px;"
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
                    f"<span style='{CONTEXT_STYLE}'>{segment}</span>"
                )
        elif tag == "delete":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
        elif tag == "insert":
            parts.append(f"<span style='{NEW_STYLE}'>{escape(new)}</span>")
        elif tag == "replace":
            parts.append(f"<s style='{OLD_STYLE}'>{escape(old)}</s>")
            parts.append(f"<span style='{ARROW_STYLE}'> → </span>")
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


def _screen_at(point: QPoint):
    """Return the QScreen containing point (best effort), else None."""
    try:
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return None
        for screen in app.screens():
            if screen.geometry().contains(point):
                return screen
        return app.screenAt(point)
    except Exception:
        return None


def _clamp_rect(rect: QRect, anchor: QPoint | None = None) -> QRect:
    """Keep a window inside the screen that owns the anchor point.

    The suggestion panel must follow the display the user is working on, not
    jump to the primary screen when the selection lives on a secondary one.
    """
    app = QApplication.instance()
    if app is None:
        return rect
    point = anchor if anchor is not None else rect.center()
    screen = _screen_at(point)
    if screen is None:
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
        area = _clamp_rect(target, anchor.topLeft())
        if area.y() != target.y():
            target.moveTop(max(0, anchor.top() - self.height() - 10))
            area = _clamp_rect(target, anchor.topLeft())
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

    def popup_contains(self, pos: QPoint) -> bool:
        """Whether pos is on (or near) the open popup card."""
        if self._popup is None or not self._popup.isVisible():
            return False
        return self._popup.geometry().adjusted(-16, -28, 16, 28).contains(pos)

    def paintEvent(self, event) -> None:
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


def _hairline() -> QFrame:
    """A 1px divider matching the card's border colour."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(DIVIDER_SHEET)
    return line


class WordSuggestionCard(QWidget):
    """A floating suggestion card that can be dragged by its header.

    The window stays fully opaque (translucent tool windows composite as
    black rectangles next to normal windows on some macOS versions), so all
    depth comes from the rounded surface, soft borders, and the pop-in
    animation instead of a drop shadow.
    """

    apply_requested = pyqtSignal(int)
    apply_all_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(SURFACE_SHEET)
        self.setMinimumWidth(320)
        self.setMaximumWidth(480)
        self._header_widget: QWidget | None = None
        self._dragging = False
        self._drag_offset = QPoint()
        self._pop_anim = None
        self._pop_start: QRect | None = None
        self._pop_target: QRect | None = None

    # --- drag-to-move (header only) ---

    def _header_zone(self) -> QRect:
        header = self._header_widget
        if header is None:
            return QRect()
        return QRect(header.mapTo(self, QPoint(0, 0)), header.size())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            zone = self._header_zone()
            if zone.contains(pos) and not zone.isEmpty():
                self._dragging = True
                self._drag_offset = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            # Keep the manually placed panel inside the screen it sits on.
            self.move(_clamp_rect(self.frameGeometry(), self.pos()).topLeft())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # --- pop-in animation ---

    def pop_in(self) -> None:
        """Fade in and grow slightly from the panel's top-left corner."""
        self.stop_pop()
        target = self.geometry()
        self._pop_start = QRect(
            target.left() + int(target.width() * 0.08),
            target.top() + int(target.height() * 0.10),
            int(target.width() * 0.84),
            int(target.height() * 0.80),
        )
        self._pop_target = target
        try:
            self.setWindowOpacity(0.0)
            self.setGeometry(self._pop_start)
            anim = QVariantAnimation(self)
            anim.setDuration(170)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.valueChanged.connect(self._on_pop_frame)
            anim.finished.connect(self._on_pop_finished)
            self._pop_anim = anim
            anim.start()
        except Exception:
            self.setWindowOpacity(1.0)
            self.setGeometry(target)

    def stop_pop(self) -> None:
        anim = self._pop_anim
        if anim is not None:
            anim.stop()
            self._pop_anim = None
        self._pop_start = None
        self._pop_target = None
        self.setWindowOpacity(1.0)

    def _on_pop_frame(self, value: float) -> None:
        start = self._pop_start
        target = self._pop_target
        if start is None or target is None:
            return
        rect = QRect(
            round(start.x() + (target.x() - start.x()) * value),
            round(start.y() + (target.y() - start.y()) * value),
            round(start.width() + (target.width() - start.width()) * value),
            round(start.height() + (target.height() - start.height()) * value),
        )
        self.setGeometry(rect)
        self.setWindowOpacity(float(value))

    def _on_pop_finished(self) -> None:
        anim = self._pop_anim
        self._pop_anim = None
        if anim is not None:
            anim.deleteLater()
        target = self._pop_target
        self._pop_start = None
        self._pop_target = None
        self.setWindowOpacity(1.0)
        if target is not None:
            self.setGeometry(target)

    def set_spans(self, spans: list[EditSpan]) -> None:
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 14, 18, 16)
            layout.setSpacing(0)
        else:
            clear_layout(layout)

        header_widget = QWidget()
        header_widget.setCursor(Qt.CursorShape.OpenHandCursor)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 2, 0, 2)
        header.setSpacing(8)
        title_text = "Suggested changes"
        if len(spans) > 1:
            title_text = f"Suggested changes ({len(spans)})"
        title = QLabel(title_text)
        title.setStyleSheet(TITLE_SHEET)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(CLOSE_BUTTON)
        close_btn.setToolTip("Close (Esc)")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.dismissed.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        self._header_widget = header_widget
        layout.addWidget(header_widget)
        layout.addWidget(_hairline())
        layout.addSpacing(10)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        for index, span in enumerate(spans):
            if index:
                body_layout.addWidget(_hairline())
                body_layout.addSpacing(4)
            row = QHBoxLayout()
            row.setSpacing(10)
            diff_box = QFrame()
            diff_box.setStyleSheet(DIFF_BOX)
            diff_box_layout = QVBoxLayout(diff_box)
            diff_box_layout.setContentsMargins(10, 7, 10, 7)
            diff_label = QLabel(diff_html(span.before, span.after))
            diff_label.setStyleSheet(DIFF_SHEET)
            diff_label.setWordWrap(True)
            diff_label.setMaximumWidth(300)
            diff_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            diff_box_layout.addWidget(diff_label)
            apply_btn = QPushButton("Apply")
            apply_btn.setStyleSheet(PRIMARY_BUTTON)
            apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            apply_btn.clicked.connect(
                lambda _checked=False, i=index: self.apply_requested.emit(i)
            )
            row.addWidget(diff_box, 1)
            row.addWidget(apply_btn, 0, Qt.AlignmentFlag.AlignTop)
            body_layout.addLayout(row)
            if span.reason:
                reason = QLabel(span.reason)
                reason.setStyleSheet(REASON_CHIP)
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

        layout.addSpacing(10)
        layout.addWidget(_hairline())
        layout.addSpacing(10)
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
        self.move(_clamp_rect(target, point).topLeft())


LiveSuggestionPanel = WordSuggestionCard


def apply_nonactivating_panel(widget: QWidget) -> None:
    """Best-effort: make a Qt window a true non-activating macOS panel."""
    try:
        app = QApplication.instance()
        if app is None or "offscreen" in app.platformName():
            return

        import AppKit
        import objc

        view = widget.winId()
        nsview = objc.objc_object(c_void_p=int(view))
        window = nsview.window()
        if window is None:
            return
        mask = int(window.styleMask())
        window.setStyleMask_(
            mask | int(AppKit.NSWindowStyleMaskNonactivatingPanel)
        )
        window.setHidesOnDeactivate_(False)
        window.setLevel_(int(AppKit.NSFloatingWindowLevel))
        behavior = int(window.collectionBehavior())
        window.setCollectionBehavior_(
            behavior | int(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces)
        )
    except Exception:
        pass
