#!/usr/bin/env python3
"""Record the short clips used by the ByteProof product page.

The product page shows one clip per feature: two to four seconds of the real
application doing one thing, so a visitor can see the process instead of
reading about it. This script produces those clips from the shipped widgets -
the suggestion card, the live overlay, the settings pages, the toast - rather
than filming the screen, so every clip is sharp, cropped, and repeatable.

How it works
    Each clip is a small storyboard: a page, a widget, and a few timed beats
    (select the text, press the hotkey, the card appears, the change is
    applied). A frame loop grabs the live widgets, composites them onto the
    page with a cursor, and pipes the frames to ffmpeg, which writes a VP8
    WebM plus a poster frame - the same shape as the ByteBook clips.

    The widgets are real, but the *timing* is staged, and no network call is
    made: the local-model progress bar is fed synthetic byte counts and the
    activation result is supplied by this script. Nothing here touches the
    user's settings file, the licence file, or the running copy of ByteProof.

Usage
    python scripts/record_website_clips.py                  # every clip
    python scripts/record_website_clips.py card review      # named clips
    python scripts/record_website_clips.py --list
    python scripts/record_website_clips.py --frames card    # dump stills

    BYTEPROOF_FFMPEG  path to an ffmpeg with libvpx (defaults to the copy
                      Playwright ships, which the website clips already use).
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = Path(
    "/Users/zhangy6j/Python Projects/Personal/ByteMind Project/"
    "ByteMind_Website/assets/byteproof"
)

FPS = 30

# The application forces its own light palette on startup so Windows dark mode
# cannot turn unstyled panels dark. The recorder runs the same widgets without
# that startup path, so it applies the same palette here - copied from
# src/main.py on purpose, because a clip must look like the shipped app.
PALETTE = {
    "Window": "#F7F4F0",
    "WindowText": "#292524",
    "Base": "#FFFFFF",
    "AlternateBase": "#F1EDE8",
    "Text": "#292524",
    "Button": "#FFFFFF",
    "ButtonText": "#292524",
    "ToolTipBase": "#FFFFFF",
    "ToolTipText": "#292524",
    "Highlight": "#1A3A2A",
    "HighlightedText": "#FFFFFF",
}


def apply_light_palette(app) -> None:
    from PyQt6.QtGui import QColor, QPalette

    palette = QPalette()
    for role, value in PALETTE.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    app.setPalette(palette)

# Colours taken from the product page so a clip sits inside the page frame
# without a seam.
PAGE_TOP = "#f6f4ee"
PAGE_BOTTOM = "#eae6da"
SHEET = "#fffefb"
SHEET_EDGE = "#e4dfd2"
INK = "#26251f"
MUTED = "#6a6760"
SELECTION = "#b8d4f2"
UNDERLINE = "#d93025"


# --------------------------------------------------------------------------
# quiet construction: build widgets without any of the app's live behaviour
# --------------------------------------------------------------------------


class quiet_app:
    """Context manager that stops the window from wiring itself to the OS.

    ``ProofreaderApp`` starts hotkey monitors, a menu bar item, an
    Accessibility poll and startup network checks unless it believes it is
    running offscreen. It is not offscreen - it is rendering for real - so the
    platform name is faked while windows are built, and the few startup timers
    are parked. Nothing is written to disk, and the user's own ByteProof is
    untouched.
    """

    PATCHED = (
        "check_api_keys",
        "_sync_launch_at_login",
        "_check_trial_status_at_startup",
        "_validate_license_at_startup",
        "_run_cache_cleanup",
        "_check_for_app_updates",
        "_start_app_tracking",
        "_apply_activation_policy",
    )

    def __enter__(self):
        from PyQt6.QtWidgets import QApplication

        from src import gui as gui_mod

        self._gui = gui_mod
        self._platform_name = QApplication.platformName
        self._originals: dict[str, object] = {}

        def _fake_platform_name(*_args, **_kwargs) -> str:
            return "offscreen"

        QApplication.platformName = staticmethod(_fake_platform_name)  # type: ignore[assignment]
        for name in self.PATCHED:
            if hasattr(gui_mod.ProofreaderApp, name):
                self._originals[name] = getattr(gui_mod.ProofreaderApp, name)
                setattr(gui_mod.ProofreaderApp, name, lambda *a, **k: None)
        if hasattr(gui_mod, "register_url_scheme"):
            self._originals["register_url_scheme"] = gui_mod.register_url_scheme
            gui_mod.register_url_scheme = lambda *a, **k: None
        return self

    def __exit__(self, *exc) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.platformName = self._platform_name  # type: ignore[assignment]
        for name, original in self._originals.items():
            if name == "register_url_scheme":
                self._gui.register_url_scheme = original
            else:
                setattr(self._gui.ProofreaderApp, name, original)
        return None


def hide_widget(widget) -> None:
    """Keep a widget out of the user's way while still being able to grab it."""
    from PyQt6.QtCore import Qt

    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()


def flush() -> None:
    """Let Qt finish the work a state change posted.

    Rebuilding a panel calls ``deleteLater`` on the widgets it replaces and
    relayouts the new ones; both have to happen before the next frame is
    rendered, or the frame shows the previous state bleeding through.
    """
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def widget_center(widget, target, index: int = 0):
    """Canvas coordinates of the nth visible child matching ``target``."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QPushButton

    matches = []
    for child in widget.findChildren(QPushButton):
        if child.text() == target and child.isVisible():
            matches.append(child)
    if not matches:
        return None
    child = matches[min(index, len(matches) - 1)]
    point = child.mapTo(widget, QPoint(child.width() // 2, child.height() // 2))
    return point.x(), point.y()


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------


def smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def between(t: float, start: float, end: float) -> float:
    if end <= start:
        return 1.0 if t >= end else 0.0
    return max(0.0, min(1.0, (t - start) / (end - start)))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def draw_shadow(painter, rect, radius: float, dy: float = 10.0, blur: int = 26,
                alpha: int = 34) -> None:
    """A soft drop shadow, built from concentric rounded rectangles."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    steps = max(6, blur // 2)
    for i in range(steps, 0, -1):
        spread = blur * i / steps
        fade = (1.0 - i / steps) ** 1.6
        colour = QColor(28, 30, 24)
        colour.setAlpha(int(alpha * fade))
        painter.setBrush(colour)
        painter.drawRoundedRect(
            QRectF(
                rect.x() - spread, rect.y() - spread + dy,
                rect.width() + spread * 2, rect.height() + spread * 2,
            ),
            radius + spread * 0.6, radius + spread * 0.6,
        )
    painter.restore()


def rounded_image(image, radius: float):
    """Clip an image to rounded corners, keeping the alpha channel."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QImage, QPainter, QPainterPath

    if not radius:
        return image
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(image.rect()), radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


SNAP_SCALE = 2


def snap(widget, radius: float = 0.0, background=None):
    """Render a widget at 2x and return it at logical size.

    ``QWidget.grab()`` returns whatever the window server last painted, which
    smears the previous state across the new one for a widget that never
    appears on screen. Rendering into a fresh pixmap sidesteps that, and keeps
    the grab clean for a widget whose background is painted by the window
    rather than by a stylesheet (the suggestion card, for one).
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    width = max(1, int(round(widget.width())))
    height = max(1, int(round(widget.height())))
    fill = background
    if fill is None:
        if widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
            fill = QColor(0, 0, 0, 0)
        else:
            fill = widget.palette().window().color()
    image = QImage(width * SNAP_SCALE, height * SNAP_SCALE,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(fill)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(SNAP_SCALE, SNAP_SCALE)
    widget.render(painter)
    painter.end()
    image = image.scaled(
        width, height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if radius:
        image = rounded_image(image, radius)
    return image


def draw_cursor(painter, x: float, y: float, pressed: bool = False,
                scale: float = 1.0) -> None:
    """Draw a macOS-style pointer so a viewer can see where the click lands."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QPainterPath, QPen

    painter.save()
    painter.translate(x, y)
    painter.scale(scale, scale)
    painter.setRenderHint(painter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.moveTo(0.0, 0.0)
    path.lineTo(0.0, 16.5)
    path.lineTo(4.4, 12.6)
    path.lineTo(7.3, 19.2)
    path.lineTo(10.0, 17.9)
    path.lineTo(7.2, 11.5)
    path.lineTo(12.4, 11.2)
    path.closeSubpath()

    shadow = QPainterPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(20, 20, 20, 60))
    painter.translate(0.7, 1.1)
    painter.drawPath(shadow)
    painter.translate(-0.7, -1.1)

    edge = QColor(38, 38, 38) if pressed else QColor(60, 60, 60)
    painter.setPen(QPen(edge, 1.1))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPath(path)
    painter.restore()


def draw_key_chip(painter, x: float, y: float, keys: Sequence[str],
                  scale: float = 1.0) -> None:
    """The hotkey, drawn as key caps - 'the hand reaches for the keys'."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QPen

    painter.save()
    painter.translate(x, y)
    painter.scale(scale, scale)
    painter.setRenderHint(painter.RenderHint.Antialiasing)

    font = QFont()
    font.setPointSizeF(12.5)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    metrics = painter.fontMetrics()

    gap = 6.0
    pad_x = 12.0
    height = 32.0
    widths = [max(30.0, metrics.horizontalAdvance(k) + pad_x * 2) for k in keys]
    total = sum(widths) + gap * (len(keys) - 1)
    left = -total / 2.0

    for key, width in zip(keys, widths):
        rect = QRectF(left, 0.0, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(28, 28, 30, 210))
        painter.drawRoundedRect(rect.adjusted(0, 2.0, 0, 0), 8, 8)
        painter.setBrush(QColor(44, 44, 47))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, key)
        left += width + gap
    painter.restore()


# --------------------------------------------------------------------------
# the sample document
# --------------------------------------------------------------------------


@dataclass
class Token:
    text: str


class Sheet:
    """A plain page of text: select it, fix a word, watch it change."""

    def __init__(self, rect, words: Sequence[str], font_size: float = 21.0,
                 line_height: float = 34.0, padding: float = 36.0):
        from PyQt6.QtCore import QRectF

        self.rect = QRectF(*rect) if not isinstance(rect, QRectF) else rect
        self.tokens = [Token(word) for word in words]
        self.font_size = font_size
        self.line_height = line_height
        self.padding = padding
        self._layout: list[tuple[int, object]] = []

    @classmethod
    def from_text(cls, rect, text: str, **kwargs) -> "Sheet":
        return cls(rect, text.split(" "), **kwargs)

    def font(self):
        from PyQt6.QtGui import QFont

        font = QFont()
        font.setPointSizeF(self.font_size * 0.78)
        return font

    def replace(self, first: int, last: int, text: str) -> None:
        self.tokens[first:last + 1] = [Token(word) for word in text.split(" ")]

    def index_of(self, phrase: str) -> tuple[int, int]:
        """Token range for a phrase, e.g. 'in order to' -> (3, 5)."""
        words = phrase.split(" ")
        for start in range(len(self.tokens) - len(words) + 1):
            if all(
                self.tokens[start + i].text.strip(".,;:").lower()
                == words[i].strip(".,;:").lower()
                for i in range(len(words))
            ):
                return start, start + len(words) - 1
        raise ValueError(f"{phrase!r} is not in the sheet")

    def _measure(self):
        from PyQt6.QtGui import QFontMetricsF

        return QFontMetricsF(self.font())

    def layout(self) -> list[tuple[int, object]]:
        """Wrap the tokens; returns [(token index, QRectF)] in page space."""
        from PyQt6.QtCore import QRectF

        metrics = self._measure()
        space = metrics.horizontalAdvance(" ") * 1.02
        left = self.rect.left() + self.padding
        right = self.rect.right() - self.padding
        x = left
        y = self.rect.top() + self.padding
        placed: list[tuple[int, object]] = []
        for index, token in enumerate(self.tokens):
            width = metrics.horizontalAdvance(token.text)
            if x > left and x + width > right:
                x = left
                y += self.line_height
            placed.append((index, QRectF(x, y, width, self.line_height)))
            x += width + space
        self._layout = placed
        return placed

    def token_rect(self, index: int):
        from PyQt6.QtCore import QRectF

        layout = self._layout or self.layout()
        for token_index, rect in layout:
            if token_index == index:
                return rect
        return QRectF(0, 0, 0, 0)

    def selection_rect(self, first: int, last: int):
        from PyQt6.QtCore import QRectF

        self.layout()
        start = self.token_rect(first)
        end = self.token_rect(last)
        return QRectF(
            start.left() - 2.0, start.top() + 3.0,
            max(end.right(), start.right()) - start.left() + 4.0,
            start.height() - 6.0,
        )

    def bounds(self):
        """The rectangle the text actually occupies."""
        from PyQt6.QtCore import QRectF

        layout = self.layout()
        if not layout:
            return QRectF(self.rect.left(), self.rect.top(), 0, 0)
        left = min(rect.left() for _, rect in layout) - 2.0
        right = max(rect.right() for _, rect in layout) + 2.0
        top = min(rect.top() for _, rect in layout) + 3.0
        bottom = max(rect.bottom() for _, rect in layout) - 3.0
        return QRectF(left, top, right - left, bottom - top)

    def draw(self, painter, selection=None, fixes=None) -> None:
        """Paint the page.

        ``selection`` is ``(first, last, sweep)`` - the sweep runs 0 to 1 as
        the pointer drags across the sentence. ``fixes`` paints a token with a
        colour, which is how a word looks the moment it is replaced.
        """
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QColor

        layout = self.layout()
        painter.setFont(self.font())

        if selection is not None:
            first, last, sweep = selection
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SELECTION))
            for rect, share in self._selection_rows(first, last, sweep):
                painter.drawRoundedRect(rect, 3, 3)

        if fixes:
            for token_index, colour in fixes.items():
                rect = self.token_rect(token_index)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colour))
                painter.drawRoundedRect(rect.adjusted(-3, 5, 3, -5), 5, 5)

        painter.setPen(QColor(INK))
        for token_index, rect in layout:
            painter.drawText(
                QRectF(rect.left(), rect.top(), rect.width() + 3, rect.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self.tokens[token_index].text,
            )

    def _selection_rows(self, first: int, last: int, sweep: float):
        """One highlight per line the selection covers.

        A sentence that wraps needs a rectangle per line, and the sweep has to
        run across the whole selection rather than across one row, or a drag
        looks like it stops halfway through.
        """
        from PyQt6.QtCore import QRectF

        self.layout()
        chosen = [index for index in range(first, last + 1)
                  if index < len(self.tokens)]
        if not chosen:
            return []
        rows: list[list[int]] = []
        for index in chosen:
            rect = self.token_rect(index)
            if rows and abs(self.token_rect(rows[-1][-1]).top() - rect.top()) < 1.0:
                rows[-1].append(index)
            else:
                rows.append([index])
        budget = smooth(sweep) * len(chosen)
        out = []
        used = 0.0
        for row in rows:
            start = self.token_rect(row[0])
            end = self.token_rect(row[-1])
            if budget <= used:
                break
            share = min(1.0, (budget - used) / len(row))
            right = end.right() if share >= 1.0 else lerp(start.left(),
                                                          end.right(), share)
            used += len(row)
            out.append((
                QRectF(start.left() - 3.0, start.top() + 4.0,
                       max(0.0, right - start.left() + 6.0),
                       start.height() - 8.0),
                0.0,
            ))
        return out


# --------------------------------------------------------------------------
# clip plumbing
# --------------------------------------------------------------------------


@dataclass
class Clip:
    name: str
    width: int
    height: int
    duration: float
    render: Callable[[float, object], None]
    poster_at: float
    caption: str = ""
    beats: list[tuple[float, Callable[[], None]]] = field(default_factory=list)
    cleanup: Callable[[], None] | None = None

    def image(self, t: float):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter

        for at, action in list(self.beats):
            if t >= at:
                self.beats.remove((at, action))
                action()

        image = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height)
        gradient.setColorAt(0.0, QColor(PAGE_TOP))
        gradient.setColorAt(1.0, QColor(PAGE_BOTTOM))
        painter.fillRect(image.rect(), gradient)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.render(t, painter)
        painter.end()
        return image


def find_ffmpeg() -> str:
    override = os.environ.get("BYTEPROOF_FFMPEG")
    if override:
        return override
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if cache.is_dir():
        for candidate in sorted(cache.glob("ffmpeg-*/ffmpeg-mac"), reverse=True):
            if os.access(candidate, os.X_OK):
                return str(candidate)
    for name in ("ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        from shutil import which

        found = which(name) if "/" not in name else (name if Path(name).exists() else None)
        if found:
            return found
    raise SystemExit(
        "No ffmpeg found. Set BYTEPROOF_FFMPEG, or install one with libvpx."
    )


def encode(clip: Clip, out_dir: Path, ffmpeg: str, bitrate: str,
           frames_dir: Path | None = None) -> tuple[Path, Path]:
    from PyQt6.QtCore import QBuffer, QByteArray, QTimer
    from PyQt6.QtWidgets import QApplication

    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / f"{clip.name}.webm"
    poster_path = out_dir / f"{clip.name}.jpg"

    stderr_path = Path(tempfile.gettempdir()) / f"byteproof-clip-{clip.name}.log"
    stderr_file = open(stderr_path, "w")
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS),
        "-i", "pipe:0",
        "-c:v", "libvpx", "-b:v", bitrate, "-crf", "30",
        "-deadline", "good", "-cpu-used", "2",
        "-pix_fmt", "yuv420p",
        "-vf", f"scale={clip.width}:{clip.height}",
        str(video_path),
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stderr=stderr_file,
    )
    assert process.stdin is not None

    total_frames = int(round(clip.duration * FPS))
    state = {"frame": 0, "written": 0, "poster": None, "poster_delta": 1e9}
    started = time.monotonic()

    def write(image) -> None:
        buffer = QByteArray()
        device = QBuffer(buffer)
        device.open(QBuffer.OpenModeFlag.WriteOnly)
        image.save(device, "JPEG", 94)
        device.close()
        if process.poll() is not None:
            stderr_file.flush()
            log = stderr_path.read_text(errors="replace")
            raise SystemExit(
                f"ffmpeg stopped early on {clip.name} "
                f"(exit {process.returncode}):\n{log[-1500:]}"
            )
        process.stdin.write(bytes(buffer))  # type: ignore[union-attr]

    timer = QTimer()

    def tick() -> None:
        elapsed = time.monotonic() - started
        target = min(total_frames, int(math.floor(elapsed * FPS)) + 1)
        while state["written"] < target:
            t = state["written"] / FPS
            image = clip.image(t)
            write(image)
            if frames_dir is not None and state["written"] % max(1, int(FPS * 0.6)) == 0:
                image.save(str(frames_dir / f"{clip.name}-{state['written']:04d}.png"))
            delta = abs(t - clip.poster_at)
            if delta < state["poster_delta"]:
                state["poster_delta"] = delta
                state["poster"] = image.copy()
            state["written"] += 1
        if state["written"] >= total_frames:
            timer.stop()

    timer.timeout.connect(tick)
    timer.start(1000 // FPS)
    app = QApplication.instance()
    if app is None:
        raise SystemExit("QApplication must exist before encoding")
    # Pump the event loop by hand rather than calling exec(): a clip closes
    # its own popups and panels, and Qt would end the loop the moment the last
    # of them goes away.
    while state["written"] < total_frames:
        app.processEvents()
        time.sleep(0.002)
    timer.stop()

    process.stdin.close()  # type: ignore[union-attr]
    process.wait()
    stderr_file.close()
    if state["poster"] is not None:
        state["poster"].save(str(poster_path), "JPEG", 90)
    if clip.cleanup is not None:
        clip.cleanup()
    if process.returncode != 0:
        log = stderr_path.read_text(errors="replace")
        raise SystemExit(f"ffmpeg failed for {clip.name}:\n{log[-2000:]}")
    return video_path, poster_path


# --------------------------------------------------------------------------
# clips
# --------------------------------------------------------------------------


def _sheet(words: Sequence[str], x: float = 72.0, y: float = 118.0,
           width: float = 540.0, height: float = 452.0) -> Sheet:
    return Sheet((x, y, width, height), words)


def draw_sheet(painter, sheet: Sheet) -> None:
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen

    draw_shadow(painter, sheet.rect, 16.0, dy=14, blur=30, alpha=30)
    painter.setPen(QPen(QColor(SHEET_EDGE), 1.0))
    painter.setBrush(QColor(SHEET))
    painter.drawRoundedRect(sheet.rect, 16.0, 16.0)


class Pointer:
    """A pointer that walks a few waypoints and clicks on cue."""

    def __init__(self, *waypoints: tuple[float, float, float],
                 clicks: Sequence[float] = ()) -> None:
        self.waypoints = list(waypoints)
        self.clicks = list(clicks)

    def pos(self, t: float) -> tuple[float, float]:
        points = self.waypoints
        if not points:
            return 0.0, 0.0
        if t <= points[0][0]:
            return points[0][1], points[0][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:]):
            if t <= t1:
                k = ease_out(between(t, t0, t1))
                return lerp(x0, x1, k), lerp(y0, y1, k)
        return points[-1][1], points[-1][2]

    def pressed(self, t: float) -> bool:
        return any(0.0 <= t - click < 0.16 for click in self.clicks)

    def draw(self, painter, t: float) -> None:
        x, y = self.pos(t)
        draw_cursor(painter, x, y, pressed=self.pressed(t))


def flash_colour(t: float, at: float, hue: str = "green"):
    """A tint that fades out after a change lands."""
    from PyQt6.QtGui import QColor

    amount = 1.0 - between(t, at + 0.15, at + 0.95)
    colour = QColor("#E1F1E7") if hue == "green" else QColor("#F7DFDC")
    colour.setAlpha(int(240 * amount))
    return colour


CARD_RADIUS = 12.0


def build_card(spans=None, checking: bool = False):
    """A fresh suggestion card in the state asked for.

    A new card every time: the panel replaces its whole body between states,
    and the widgets it drops stay painted until Qt gets around to deleting
    them, which shows up as ghost text in a frame.
    """
    from src.live_overlay import WordSuggestionCard

    card = WordSuggestionCard()
    hide_widget(card)
    if checking:
        card.set_checking()
    else:
        card.set_spans(list(spans or []))
    flush()
    return card, snap(card, CARD_RADIUS)


SENTENCE = (
    "We utilise a range of methods in order to examine the relationship "
    "between governance and firm performance due to the fact that the data "
    "is limited."
).split(" ")

CHANGES = [
    ("utilise", "use", "Plainer word"),
    ("in order to", "to", "Wordy phrase removed"),
    ("due to the fact that", "because", "Wordy phrase removed"),
]


def clip_card() -> Clip:
    """Select the text, press the hotkey: the suggestions arrive."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QPainter

    sheet = _sheet(SENTENCE)
    first, last = 0, len(SENTENCE) - 1
    _, checking_image = build_card(checking=True)
    images: dict[str, object] = {"checking": checking_image, "spans": None}

    def show_spans() -> None:
        _, images["spans"] = build_card(_spans())

    def render(t: float, painter: QPainter) -> None:
        draw_sheet(painter, sheet)
        selection = (first, last, between(t, 0.3, 0.8)) if t > 0.28 else None
        sheet.draw(painter, selection=selection)

        text_bounds = sheet.bounds()
        if 0.85 < t < 2.3:
            scale = 0.88 + 0.12 * ease_out(between(t, 0.85, 1.08))
            painter.save()
            painter.setOpacity(between(t, 0.85, 1.02))
            draw_key_chip(painter, sheet.rect.left() + 150,
                          text_bounds.bottom() + 74, ["⌘", "⇧", "'"], scale)
            painter.restore()

        left, top = 692.0, 186.0
        if t > 1.35:
            amount = ease_out(between(t, 1.35, 1.62))
            image = images["spans"] or images["checking"]
            offset = (1.0 - amount) * 28.0
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, amount * 1.6)))
            draw_shadow(painter, QRectF(left, top + offset, image.width(),
                                        image.height()),
                        18.0, dy=12, blur=28, alpha=38)
            painter.drawImage(int(left), int(top + offset), image)
            painter.restore()

        # A pointer that drags across the sentence, then settles by the panel.
        cx = lerp(110.0, 660.0, ease_out(between(t, 0.18, 1.6)))
        cy = lerp(text_bounds.bottom() + 26, top + 104,
                  ease_out(between(t, 1.5, 2.7)))
        draw_cursor(painter, cx, cy)

    return Clip(
        name="demo-hotkey", width=1152, height=704, duration=3.4,
        render=render, poster_at=3.0,
        beats=[(2.3, show_spans)],
    )


def _spans():
    from src.live_preview import EditSpan

    return [
        EditSpan(before=before, after=after, reason=reason,
                 start=0, end=len(before))
        for before, after, reason in CHANGES
    ]


def clip_review() -> Clip:
    """Review the list, apply the one change you want."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QPainter

    sheet = _sheet(SENTENCE)
    spans = _spans()
    left, top = 672.0, 186.0
    card, first_image = build_card(spans)
    images: dict[str, object] = {"card": first_image}
    state: dict[str, object] = {"flash": None, "flash_at": 0.0}

    def render_card(rows) -> None:
        _, images["card"] = build_card(rows)

    first_target = widget_center(card, "Apply", 0) or (0, 0)

    def apply_first() -> None:
        first, last = sheet.index_of("utilise")
        sheet.replace(first, last, "use")
        state["flash"] = first
        state["flash_at"] = 1.5
        render_card(spans[1:])

    pointer = Pointer(
        (0.0, 1080.0, 620.0), (0.5, 980.0, 470.0),
        (1.25, left + first_target[0] + 6, top + first_target[1] + 6),
        (2.5, left + 150, top + 300),
        clicks=(1.42,),
    )

    def render(t: float, painter: QPainter) -> None:
        draw_sheet(painter, sheet)
        fixes = None
        if state["flash"] is not None:
            fixes = {state["flash"]: flash_colour(t, state["flash_at"])}
        sheet.draw(painter, fixes=fixes)
        image = images["card"]
        draw_shadow(painter, QRectF(left, top, image.width(), image.height()),
                    12.0, dy=12, blur=28, alpha=38)
        painter.drawImage(int(left), int(top), image)
        pointer.draw(painter, t)

    return Clip(
        name="demo-review", width=1152, height=704, duration=3.2,
        render=render, poster_at=3.0,
        beats=[(1.5, apply_first)],
    )


def clip_apply() -> Clip:
    """Apply every change, and undo if it was not what you wanted."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QPainter
    from src.gui import ToastNotification
    from src.live_overlay import UndoPill

    sheet = _sheet(SENTENCE)
    card, card_image = build_card(_spans())

    toast = ToastNotification()
    hide_widget(toast)
    pill = UndoPill()
    hide_widget(pill)
    pill_image = snap(pill, 16.0)

    left, top = 672.0, 176.0
    apply_target = widget_center(card, "Apply all", 0) or (0, 0)
    undo_at = (sheet.rect.left() + 120.0, sheet.rect.top() + 34.0)
    toast_home = ((1152.0 - toast.width()) / 2.0, 636.0)
    pill_home = (undo_at[0] + 118.0, undo_at[1] + 82.0)
    fixed_sentence = (
        "We use a range of methods to examine the relationship between "
        "governance and firm performance because the data is limited."
    )

    state: dict[str, object] = {
        "applied": False, "toast": None, "toast_hue": "success",
        "toast_at": 0.0,
    }

    def show_toast(message: str, kind: str, at: float) -> None:
        toast.complete(message, kind=kind, duration_ms=600000)
        toast.hide()
        state["toast"] = snap(toast, 18.0)
        state["toast_at"] = at

    def apply_all() -> None:
        first, last = 0, len(sheet.tokens) - 1
        sheet.replace(first, last, fixed_sentence)
        state["applied"] = True
        show_toast("Applied 3 changes to Word", "success", 1.2)

    def undo() -> None:
        sheet.tokens = [Token(word) for word in SENTENCE]
        state["applied"] = False
        show_toast("Change undone", "warning", 3.2)

    pointer = Pointer(
        (0.0, 1080.0, 620.0), (0.5, 900.0, 470.0),
        (1.05, left + apply_target[0], top + apply_target[1]),
        (2.4, undo_at[0] + 60, undo_at[1] + 40),
        (3.05, pill_home[0] + pill_image.width() / 2,
         pill_home[1] + pill_image.height() / 2),
        clicks=(1.2, 3.2),
    )

    def render(t: float, painter: QPainter) -> None:
        draw_sheet(painter, sheet)
        fixes = None
        if state["applied"]:
            fixes = {index: flash_colour(t, 1.2)
                     for index in _changed_tokens(sheet, ("use", "to", "because"))}
        elif state["toast_at"] > 3.0:
            fixes = {index: flash_colour(t, 3.2, hue="red")
                     for index in _changed_tokens(sheet, ("utilise", "in order to",
                                                          "due to the fact that"))}
        sheet.draw(painter, fixes=fixes)

        if t < 1.45:
            shrink = ease_out(between(t, 1.2, 1.45))
            painter.save()
            painter.setOpacity(1.0 - shrink)
            draw_shadow(painter, QRectF(left, top, card_image.width(),
                                        card_image.height()),
                        12.0, dy=12, blur=28, alpha=int(38 * (1 - shrink)))
            painter.drawImage(int(left), int(top - shrink * 14), card_image)
            painter.restore()

        if state["applied"] and 1.3 < t < 3.15:
            amount = ease_out(between(t, 1.3, 1.7))
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(int(pill_home[0]),
                              int(pill_home[1] + (1 - amount) * 18), pill_image)
            painter.restore()

        if state["toast"] is not None:
            amount = ease_out(between(t, state["toast_at"] + 0.12,
                                      state["toast_at"] + 0.4))
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(int(toast_home[0]),
                              int(toast_home[1] + (1 - amount) * 18),
                              state["toast"])
            painter.restore()
        pointer.draw(painter, t)

    return Clip(
        name="demo-apply", width=1152, height=704, duration=3.8,
        render=render, poster_at=2.2,
        beats=[(1.2, apply_all), (3.2, undo)],
    )


def _changed_tokens(sheet: Sheet, phrases: Sequence[str]) -> list[int]:
    """Every token index a phrase covers, so a highlight matches the text."""
    index = 0
    found: list[int] = []
    for phrase in phrases:
        try:
            first, last = sheet.index_of(phrase)
        except ValueError:
            continue
        found.extend(range(first, last + 1))
        index += 1
    return found


def clip_anywhere() -> Clip:
    """The same proofread in any other app: underlines, then one tap."""
    from PyQt6.QtCore import QRect, QRectF
    from PyQt6.QtGui import QPainter
    from src.live_overlay import LiveOverlay, OverlaySpan

    sheet = _sheet(SENTENCE)
    overlay = LiveOverlay()
    hide_widget(overlay)
    state: dict[str, object] = {"flash": None, "flash_at": 0.0}
    canvas = (1152, 704)

    def overlay_spans():
        sheet.layout()
        spans = []
        for phrase, after, reason in CHANGES:
            try:
                first, last = sheet.index_of(phrase)
            except ValueError:
                # Already fixed in this take: it keeps no underline.
                continue
            start = sheet.token_rect(first)
            end = sheet.token_rect(last)
            spans.append(OverlaySpan(
                before=phrase, after=after, reason=reason,
                rect=QRect(int(start.left()) - 2, int(start.top()),
                           int(end.right() - start.left()) + 4,
                           int(start.height()) - 6),
            ))
        return spans

    def paint_overlay(spans) -> None:
        overlay.set_spans(spans)
        # The shipped overlay masks itself to the underlines so it can be
        # click-through. For a recording the whole page is grabbed instead, so
        # the dashed lines land exactly where the text is.
        overlay.clearMask()
        overlay.setGeometry(0, 0, canvas[0], canvas[1])
        flush()
        state["overlay"] = snap(overlay)
        state["overlay_at"] = overlay.geometry().topLeft()

    spans = overlay_spans()
    paint_overlay(spans)

    def open_popup() -> None:
        from PyQt6.QtGui import QColor

        overlay.show_popup(1)
        flush()
        popup = overlay._popup
        if popup is None:
            return
        # The panel is transparent on screen (its stylesheet surface is never
        # painted), so the recorder gives it the surface colour the design
        # asks for - otherwise the card would be unreadable over the text.
        state["popup"] = snap(popup, 14.0, background=QColor("#FFFFFF"))
        state["popup_at"] = (spans[1].rect.left() + 4.0,
                             sheet.bounds().bottom() + 16.0)

    def apply_change() -> None:
        first, last = sheet.index_of("in order to")
        sheet.replace(first, last, "to")
        state["flash"] = first
        state["flash_at"] = 2.35
        overlay.hide_popup()
        state["popup"] = None
        paint_overlay(overlay_spans())

    pointer = Pointer(
        (0.0, 1080.0, 620.0), (0.6, 880.0, 470.0),
        (1.4, spans[1].rect.left() + 110.0, spans[1].rect.bottom() + 4.0),
        (2.0, spans[1].rect.left() + 96.0, sheet.bounds().bottom() + 88.0),
        (2.9, spans[1].rect.left() + 170.0, sheet.bounds().bottom() + 140.0),
        clicks=(1.6, 2.35),
    )

    def render(t: float, painter: QPainter) -> None:
        draw_sheet(painter, sheet)
        fixes = None
        if state["flash"] is not None:
            fixes = {state["flash"]: flash_colour(t, state["flash_at"])}
        sheet.draw(painter, fixes=fixes)

        if t > 0.7:
            amount = between(t, 0.7, 1.1)
            at = state["overlay_at"]
            painter.save()
            painter.setOpacity(amount)
            painter.drawImage(at.x(), at.y(), state["overlay"])
            painter.restore()

        popup = state.get("popup")
        if popup is not None:
            amount = ease_out(between(t, 1.62, 1.84))
            at = state["popup_at"]
            painter.save()
            painter.setOpacity(amount)
            draw_shadow(painter, QRectF(at[0], at[1] + (1 - amount) * 16,
                                        popup.width(), popup.height()),
                        18.0, dy=12, blur=28, alpha=42)
            painter.drawImage(int(at[0]), int(at[1] + (1 - amount) * 16), popup)
            painter.restore()
        pointer.draw(painter, t)

    return Clip(
        name="demo-anywhere", width=1152, height=704, duration=3.6,
        render=render, poster_at=3.4,
        beats=[(1.6, open_popup), (2.35, apply_change)],
    )


class SettingsStage:
    """A settings page on a cream backdrop, ready to be animated."""

    def __init__(self, page_row: int, dialog_size, canvas, name: str,
                 duration: float, poster_at: float) -> None:
        from src import settings as settings_mod
        from src.gui import SettingsDialog

        self.dialog = SettingsDialog(settings_mod.load_runtime_settings())
        hide_widget(self.dialog)
        self.dialog.resize(*dialog_size)
        self.dialog.sidebar.setCurrentRow(page_row)
        self.dialog.change_page(page_row)
        flush()
        self.canvas = canvas
        self.offset = (
            (canvas[0] - self.dialog.width()) / 2.0,
            (canvas[1] - self.dialog.height()) / 2.0,
        )
        self.image = snap(self.dialog, 14.0)

    def refresh(self) -> None:
        flush()
        self.image = snap(self.dialog, 14.0)

    def point(self, widget) -> tuple[float, float]:
        """Canvas coordinates of the centre of a widget inside the dialog."""
        from PyQt6.QtCore import QPoint

        if widget is None:
            return (self.canvas[0] / 2.0, self.canvas[1] / 2.0)
        centre = widget.mapTo(self.dialog,
                              QPoint(widget.width() // 2, widget.height() // 2))
        return (self.offset[0] + centre.x(), self.offset[1] + centre.y())

    def draw_dialog(self, painter) -> None:
        from PyQt6.QtCore import QRectF

        draw_shadow(painter, QRectF(self.offset[0], self.offset[1],
                                    self.image.width(), self.image.height()),
                    14.0, dy=16, blur=34, alpha=40)
        painter.drawImage(int(self.offset[0]), int(self.offset[1]), self.image)


def clip_connect() -> Clip:
    """Pick a provider: the local model needs no key, the cloud ones do."""
    from PyQt6.QtGui import QPainter
    from src.settings import LOCAL_MODEL_PROVIDER

    stage = SettingsStage(3, (900, 620), (1060, 740), "demo-connect", 3.2, 3.0)
    state: dict[str, object] = {}

    def button():
        return stage.dialog.provider_buttons.get(LOCAL_MODEL_PROVIDER)

    def activate() -> None:
        stage.dialog.set_active_provider(LOCAL_MODEL_PROVIDER)
        stage.refresh()

    def render(t: float, painter: QPainter) -> None:
        stage.draw_dialog(painter)
        target = stage.point(button())
        pointer = Pointer((0.0, 1010.0, 700.0), (0.45, 900.0, 560.0),
                          (1.0, target[0], target[1]), (2.9, target[0], target[1]),
                          clicks=(1.1,))
        pointer.draw(painter, t)

    return Clip(name="demo-connect", width=1060, height=740, duration=3.2,
                render=render, poster_at=3.0, beats=[(1.1, activate)])


def clip_local() -> Clip:
    """Download the local model once, then proofread offline."""
    from PyQt6.QtGui import QPainter
    from src import gui as gui_mod
    from src.local_model import get_model

    model_id = "qwen3-1.7b"
    model = get_model(model_id)
    total = int(model["size_bytes"])
    # The finished state has to survive _refresh_local_tab(), which asks the
    # disk whether the model is there. Nothing was downloaded - the progress
    # bar is staged - so the answer is staged too.
    real_is_installed = gui_mod.is_model_installed
    gui_mod.is_model_installed = lambda mid: mid == model_id or real_is_installed(mid)
    stage = SettingsStage(4, (900, 620), (1060, 740), "demo-local", 4.0, 3.8)
    state: dict[str, object] = {"started": False, "done": False}

    def restore() -> None:
        gui_mod.is_model_installed = real_is_installed

    def card_widget(name: str):
        widgets = stage.dialog.local_model_cards.get(model_id) or {}
        return widgets.get(name)

    def start_download() -> None:
        state["started"] = True
        stage.dialog._local_downloading = model_id
        for other, widgets in stage.dialog.local_model_cards.items():
            widgets["download"].setEnabled(other != model_id)
            if other == model_id:
                widgets["download"].setText("Downloading…")
        stage.dialog.local_status_label.setObjectName("SettingsValue")
        stage.dialog.local_status_label.setText(f"Preparing {model['name']}…")
        stage.refresh()

    def finish() -> None:
        state["done"] = True
        stage.dialog._on_local_download_done(model_id)
        stage.refresh()

    def render(t: float, painter: QPainter) -> None:
        if state["started"] and not state["done"]:
            share = between(t, 1.1, 3.15)
            done = int(total * min(1.0, share))
            if share < 1.0:
                stage.dialog._on_local_download_progress(done, total, "Downloading")
            else:
                stage.dialog._on_local_download_progress(total, total, "Verifying")
            stage.refresh()
        stage.draw_dialog(painter)
        target = stage.point(card_widget("download"))
        pointer = Pointer((0.0, 1010.0, 700.0), (0.45, 880.0, 540.0),
                          (0.95, target[0], target[1]), (3.9, target[0] + 40, target[1] + 60),
                          clicks=(1.05,))
        pointer.draw(painter, t)

    return Clip(name="demo-local", width=1060, height=740, duration=4.0,
                render=render, poster_at=3.8,
                beats=[(1.05, start_download), (3.3, finish)],
                cleanup=restore)


def clip_license() -> Clip:
    """Paste the key from the receipt, and the app unlocks."""
    from PyQt6.QtCore import QPoint, QRectF
    from PyQt6.QtGui import QPainter
    from PyQt6.QtWidgets import QApplication, QInputDialog
    from src import gui as gui_mod
    from src.gui import activation_prompt

    key = "polar_xxxxxxxxxxxxxxxxxxxx"
    holder: dict[str, object] = {}

    def unlicensed() -> dict:
        return {"status": "unlicensed"}

    original = gui_mod.get_license_info
    original_trial_start = gui_mod.ensure_trial_started
    original_trial_status = gui_mod.get_trial_status
    # A fresh install with a week left, not this machine's finished trial, and
    # no reading or writing of the licence files while the clip is recorded.
    gui_mod.get_license_info = unlicensed
    gui_mod.ensure_trial_started = lambda: 0.0
    gui_mod.get_trial_status = lambda _ts: {
        "in_trial": True, "days_left": 7, "trial_expired": False,
    }
    stage = SettingsStage(5, (900, 620), (1060, 740), "demo-license", 3.6, 3.4)
    holder["stage"] = stage

    def restore() -> None:
        gui_mod.get_license_info = original
        gui_mod.ensure_trial_started = original_trial_start
        gui_mod.get_trial_status = original_trial_status

    def open_prompt() -> None:
        title, label = activation_prompt()
        prompt = QInputDialog()
        prompt.setWindowTitle(title)
        prompt.setLabelText(label)
        prompt.setInputMode(QInputDialog.InputMode.TextInput)
        prompt.setOkButtonText("Activate")
        prompt.setCancelButtonText("Cancel")
        prompt.setTextValue(key)
        prompt.resize(440, 210)
        hide_widget(prompt)
        flush()
        holder["prompt"] = prompt
        holder["prompt_image"] = snap(prompt, 12.0)
        QApplication.processEvents()

    def activate() -> None:
        prompt = holder.get("prompt")
        if prompt is not None:
            prompt.close()
        holder["prompt_image"] = None
        gui_mod.get_license_info = lambda: {
            "status": "licensed",
            "provider": "polar",
            "key_display": "…7F2C",
            "email": "",
            "expiry": None,
            "activated_at": "",
        }
        stage.dialog._refresh_license_tab()
        stage.refresh()

    def render(t: float, painter: QPainter) -> None:
        stage.draw_dialog(painter)
        button_centre = stage.point(stage.dialog.btn_auto_activate)
        pointer = Pointer((0.0, 1010.0, 700.0), (0.5, 880.0, 540.0),
                          (1.05, button_centre[0], button_centre[1]),
                          (2.2, button_centre[0] + 90.0, button_centre[1] + 40.0),
                          (3.4, button_centre[0] + 90.0, button_centre[1] + 40.0),
                          clicks=(1.15,))
        pointer.draw(painter, t)

        image = holder.get("prompt_image")
        if image is None:
            return
        prompt = holder["prompt"]
        at = ((stage.canvas[0] - prompt.width()) / 2.0,
              (stage.canvas[1] - prompt.height()) / 2.0 - 30.0)
        amount = ease_out(between(t, 1.22, 1.44))
        painter.save()
        painter.setOpacity(amount)
        draw_shadow(painter, QRectF(at[0], at[1], image.width(), image.height()),
                    12.0, dy=14, blur=30, alpha=46)
        painter.drawImage(int(at[0]), int(at[1]), image)
        painter.restore()

    return Clip(name="demo-license", width=1060, height=740, duration=3.6,
                render=render, poster_at=3.4,
                beats=[(1.22, open_prompt), (2.2, activate)],
                cleanup=restore)


CLIPS: dict[str, Callable[[], Clip]] = {
    "card": clip_card,
    "review": clip_review,
    "apply": clip_apply,
    "anywhere": clip_anywhere,
    "connect": clip_connect,
    "local": clip_local,
    "license": clip_license,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="clips to record (default: all)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--frames", action="store_true",
                        help="also dump review stills")
    parser.add_argument("--bitrate", default="1100k")
    args = parser.parse_args()

    if args.list:
        for name in sorted(CLIPS):
            print(name)
        return 0

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_light_palette(app)
    ffmpeg = find_ffmpeg()
    names = args.names or sorted(CLIPS)
    frames_dir = Path(tempfile.gettempdir()) / "byteproof-clip-frames" if args.frames else None
    if frames_dir is not None:
        frames_dir.mkdir(parents=True, exist_ok=True)

    with quiet_app():
        for name in names:
            if name not in CLIPS:
                raise SystemExit(f"unknown clip {name!r} (try --list)")
            started = time.monotonic()
            clip = CLIPS[name]()
            print(f"recording {clip.name}…", flush=True)
            video, poster = encode(clip, args.out, ffmpeg, args.bitrate, frames_dir)
            size_kb = video.stat().st_size / 1024
            print(f"{clip.name:<16} {clip.width}x{clip.height} "
                  f"{clip.duration:.1f}s  {size_kb:6.0f} KB  "
                  f"({time.monotonic() - started:.1f}s to record)")
            print(f"                 poster {poster.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
