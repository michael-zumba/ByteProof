#!/usr/bin/env python3
"""Record ByteProof's demonstration videos, with narration, from the real app.

ByteBook's films are made by driving its own pages in a browser. ByteProof is a
desktop application: there is no page to drive and no port to open, so the
clips are assembled the other way round. The shipped widgets - the suggestion
card, the live overlay and its popup, the toast, the settings dialog, the
updater - are built offscreen, rendered at twice their size, and composited
onto a 1600x900 canvas with a cursor, a caption, a title card and the
occasional note.

What is real, and what is staged, is worth being exact about, because a demo
that quietly invents behaviour is worse than no demo at all:

* Every screen is the application's own widget, laid out by its own code.
* Every change of state is made by calling the same method its own event
  handler calls - ``set_spans``, ``apply_all``, ``_on_local_download_done``,
  ``_refresh_license_tab`` - rather than by drawing a picture of the result.
* The *timing* is staged, and nothing is sent anywhere. Where a clip would
  otherwise wait for a language model, the answer is supplied by the plan.

So a clip shows what the software does. It does not show how long the software
takes, or which model answered, and no clip claims otherwise.

    python scripts/record_demo_videos.py --plan scripts/video_clips.py --list
    python scripts/record_demo_videos.py --plan scripts/video_clips.py \\
        --only 03-review-the-changes --out /tmp/try
    python scripts/record_demo_videos.py --plan scripts/video_clips.py --out demos

One clip before the set: the audit sheet (``<name>-audit.jpg``) is the only
cheap way to catch narration describing a screen the viewer is not looking at,
and a full set takes the better part of an hour.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# The canvas helpers - snapping a widget, drawing a shadow, the macOS pointer,
# the key caps, the palette - already exist and are used by the website clips.
# One copy, so a fix to the cursor lands in both.
import record_website_clips as rwc

CANVAS_W, CANVAS_H = 1600, 900
FPS = 30

# The canvas above is the design space: every coordinate in a plan is written
# in it, and every widget is laid out in it. The output can be larger, which is
# what the YouTube copy of a set is. Nothing in a plan changes between the two:
# the painter is scaled before anything is drawn, so text is rendered at the
# output size rather than enlarged afterwards.
OUT_SIZE = (CANVAS_W, CANVAS_H)
OUT_CRF = 20
OUT_CHANNELS = 1
RENARRATE = False


def output_size() -> tuple[int, int]:
    return OUT_SIZE


def output_scale() -> float:
    return OUT_SIZE[0] / CANVAS_W

DEFAULT_KOKORO = Path.home() / ".codex" / "tts" / "kokoro" / "venv" / "bin" / "python"
KOKORO_RUNNER = Path(__file__).resolve().parent / "kokoro_narrate.py"
KOKORO_VOICES = ("af_heart", "am_michael", "am_fenrir", "bf_emma", "bm_george")
KOKORO_SPEED = 1.0
CASTING_SEED = 20260924

# How long the picture holds after the narrator stops, so one step does not
# fall over the next. The title card gets longer: it is also where a viewer
# works out whether they are in the right place.
STEP_BREATH = 0.6
TITLE_BREATH = 1.0
CLOSING_BREATH = 1.2

# The application's own palette, from src/ui_theme.py. A clip has to look like
# the product it is selling, so these are not a designer's choice.
PRIMARY = "#1a3a2a"
PRIMARY_DEEP = "#143024"
PRIMARY_600 = "#1f5335"
PRIMARY_100 = "#d6e4db"
GOLD = "#c9a227"
PAPER = "#faf9f3"
INK = "#1f1e1a"
MUTED = "#6a6760"
HAIRLINE = "#e2ddd1"
SURFACE = "#fdfcf8"

SERIF = ("Iowan Old Style", "Palatino", "Georgia", "Times New Roman")
SANS = ("Inter", "Helvetica Neue", "Segoe UI", "Arial")
MONO = ("SF Mono", "Menlo", "Consolas", "Courier New")


def log(message: str) -> None:
    print(message, flush=True)


def run(command: list[str], timeout: int = 900, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )


# ---------------------------------------------------------------------------
# The plan's vocabulary
# ---------------------------------------------------------------------------


@dataclass
class Beat:
    """One step of a film: a screen, a sentence, and what is done on it.

    ``draw`` paints the picture and is given the seconds since the step began,
    which is how a plan animates a click or fades a panel in. ``cue`` runs a
    callable part-way through the step for a change of state, and the plan's
    own ``draw`` is expected to show the result from then on - the same shape
    the application uses, where a click calls a method and the widget repaints.

    Cursor waypoints, clicks and cues are given as fractions of the step, not
    as seconds. A step lasts as long as its sentence takes to read, which is
    not known until the voice has read it, and a pointer that arrives at 1.2
    seconds of a three second step and of an eight second step looks like two
    different pieces of software. The step's own length is handed to ``draw``
    once it is known.
    """

    caption: str
    say: object
    draw: Callable[[object, float, Beat], None]
    detail: str = ""
    note: dict | None = None
    cursor: Sequence[tuple[float, float, float]] = ()
    click: Sequence[float] = ()
    enter: Callable[[], None] | None = None
    cue: tuple[float, Callable[[], None]] | None = None
    ring: Callable[[], object] | None = None
    hold: float = STEP_BREATH
    seconds: float = 0.0


@dataclass
class Clip:
    name: str
    group: str
    card: dict
    end: dict
    say: object
    beats: list[Beat]
    poster_at: float | None = None
    cleanup: Callable[[], None] | None = None


def film(name: str, group: str, build: Callable[[], Clip]) -> dict:
    """One entry in a plan's ``FILMS`` list.

    The plan lists its films without building any widgets, so ``--list`` and
    the voice casting work without a Qt application and without paying for
    thirty offscreen windows that are about to be thrown away.
    """
    return {"name": name, "group": group, "build": build}


def check_clips(clips: Sequence[Clip]) -> None:
    """Refuse a set that cannot be recorded, in the way a plan can be wrong.

    Two mistakes are worth catching before an hour of rendering: a film whose
    narration does not have exactly one line per screen, and a script that
    contains an em dash. The second is not a style preference - it is the
    loudest tell that a machine wrote the words, and it is cheap to catch.
    """
    for clip in clips:
        if not clip.beats:
            raise SystemExit(f"{clip.name} has no steps")
        for index, beat in enumerate(clip.beats):
            if not beat.caption.strip():
                raise SystemExit(f"{clip.name}, step {index + 1}: no caption")
            if not (beat.say.get("text") if isinstance(beat.say, dict) else beat.say or "").strip():
                raise SystemExit(f"{clip.name}, step {index + 1}: nothing to say")
        lines = [clip.say, *[beat.say for beat in clip.beats], clip.end.get("strap", "")]
        for index, line in enumerate(lines):
            text = line["text"] if isinstance(line, dict) else line or ""
            where = "the title card" if index == 0 else (
                "the closing card" if index == len(lines) - 1 else f"step {index}"
            )
            if "\u2014" in text:
                raise SystemExit(
                    f"{clip.name}, {where}: an em dash. Rewrite the sentence:\n    {text}"
                )
            if not text.strip():
                raise SystemExit(f"{clip.name}, {where}: nothing to say")


# ---------------------------------------------------------------------------
# Words on the canvas
# ---------------------------------------------------------------------------


def qfont(families: Sequence[str], size: float, weight=None, spacing: float = 0.0):
    from PyQt6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(families))
    font.setPointSizeF(size)
    if weight is not None:
        font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def wrap(painter, text: str, font, width: float) -> list[str]:
    """Break a sentence into the lines that fit, without a text engine."""
    painter.save()
    painter.setFont(font)
    metrics = painter.fontMetrics()
    rows: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and metrics.horizontalAdvance(trial) > width:
            rows.append(line)
            line = word
        else:
            line = trial
    if line:
        rows.append(line)
    painter.restore()
    return rows


def draw_paragraph(painter, x: float, y: float, text: str, font, colour: str,
                   width: float, leading: float | None = None) -> float:
    """Draw wrapped text, and answer where the next line would go."""
    from PyQt6.QtGui import QColor

    painter.save()
    painter.setFont(font)
    painter.setPen(QColor(colour))
    step = leading or painter.fontMetrics().height() * 1.35
    for index, row in enumerate(wrap(painter, text, font, width)):
        painter.drawText(int(x), int(y + index * step), row)
    painter.restore()
    return y + len(wrap(painter, text, font, width)) * step


def draw_rule(painter, x: float, y: float, width: float, colour: str,
              thickness: float = 2.0) -> None:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor

    painter.save()
    painter.setPen(0)
    painter.setBrush(QColor(colour))
    painter.drawRect(QRectF(x, y, width, thickness))
    painter.restore()


def draw_logo_plate(painter, x: float, y: float, size: float = 88.0) -> None:
    """The mark on a cream plate.

    The drawing's ink is black and has to stay black - it is the owner's
    artwork and the same file the app icon comes from - so it goes on a plate
    rather than being recoloured for a dark card.
    """
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer

    path = Path(__file__).resolve().parent.parent / "logo" / "logo.svg"
    rwc.draw_shadow(painter, QRectF(x, y, size, size), 20.0, dy=8, blur=22, alpha=46)
    painter.save()
    painter.setPen(0)
    painter.setBrush(QColor(PAPER))
    painter.drawRoundedRect(QRectF(x, y, size, size), 20.0, 20.0)
    if path.exists():
        pixmap = QPixmap(int(size * 2), int(size * 2))
        pixmap.fill(QColor(0, 0, 0, 0))
        plate = QPainter(pixmap)
        plate.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(str(path)).render(
            plate, QRectF(size * 0.30, size * 0.30, size * 1.4, size * 1.4)
        )
        plate.end()
        painter.drawPixmap(int(x), int(y), int(size), int(size), pixmap)
    painter.restore()


# ---------------------------------------------------------------------------
# The furniture
# ---------------------------------------------------------------------------


def desk(painter) -> None:
    """The backdrop every clip is filmed against."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor

    painter.fillRect(QRectF(0, 0, CANVAS_W, CANVAS_H), QColor(rwc.PAGE_TOP))


@dataclass
class Window:
    """A real widget's picture, framed the way macOS frames it.

    The grab is of the client area, so the title bar is drawn here: traffic
    lights, the window's own title, a hairline under it. It sits inside a
    rounded, shadowed frame so a screenshot of a dialog reads as a window
    rather than as a floating slab.
    """

    title: str
    image: object
    x: float
    y: float
    radius: float = 14.0
    chrome: float = 40.0

    def rect(self):
        from PyQt6.QtCore import QRectF

        height = self.image.height() + (self.chrome if self.chrome else 0.0)
        return QRectF(self.x, self.y, self.image.width(), height)

    def draw(self, painter, alpha: float = 1.0, dy: float = 0.0) -> None:
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QColor, QPainterPath

        if alpha <= 0.01:
            return
        box = self.rect().translated(0.0, dy)
        painter.save()
        painter.setOpacity(alpha)
        rwc.draw_shadow(painter, box, self.radius + 2, dy=18, blur=36, alpha=48)

        path = QPainterPath()
        path.addRoundedRect(box, self.radius, self.radius)
        painter.setClipPath(path)
        painter.setPen(0)
        painter.setBrush(QColor(SURFACE))
        painter.drawRect(box)

        if self.chrome:
            painter.setBrush(QColor("#f1eee6"))
            painter.drawRect(QRectF(box.left(), box.top(), box.width(), self.chrome))
            painter.setBrush(QColor(HAIRLINE))
            painter.drawRect(
                QRectF(box.left(), box.top() + self.chrome - 1.0, box.width(), 1.0)
            )
            for index, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
                painter.setBrush(QColor(colour))
                painter.drawEllipse(
                    QRectF(box.left() + 16 + index * 20, box.top() + 14, 12, 12)
                )
            if self.title:
                painter.setPen(QColor(MUTED))
                painter.setFont(qfont(SANS, 12.5, None))
                painter.drawText(
                    QRectF(box.left(), box.top(), box.width(), self.chrome),
                    int(Qt.AlignmentFlag.AlignCenter),
                    self.title,
                )
            painter.setPen(QColor("#fdfcf8"))
            painter.setBrush(QColor(SURFACE))
            painter.drawRect(QRectF(box.left(), box.top() + self.chrome, 1, 1))
        top = box.top() + self.chrome
        painter.drawImage(int(box.left()), int(top), self.image)
        painter.restore()

        painter.save()
        painter.setOpacity(alpha)
        painter.setPen(QColor(0, 0, 0, 26))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, self.radius, self.radius)
        painter.restore()


def draw_card(painter, rect, radius: float = 14.0, fill: str = SURFACE,
              border: str | None = HAIRLINE, shadow: bool = True) -> None:
    from PyQt6.QtGui import QColor, QPen

    if shadow:
        rwc.draw_shadow(painter, rect, radius, dy=10, blur=26, alpha=30)
    painter.setPen(QPen(QColor(border), 1.0) if border else 0)
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(rect, radius, radius)


def window_frame(painter, box, title: str = "", chrome: float = 40.0,
                 radius: float = 14.0, fill: str = SURFACE):
    """A window around something the plan draws itself.

    A snapped widget already brings its own pixels; this is for the screens
    that have to be drawn - a document in Word, a mail window - so that they
    sit in the same frame as everything else rather than floating.
    """
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPainterPath

    painter.save()
    rwc.draw_shadow(painter, box, radius + 2, dy=18, blur=36, alpha=48)
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    painter.setClipPath(path)
    painter.setPen(0)
    painter.setBrush(QColor(fill))
    painter.drawRect(box)
    if chrome:
        painter.setBrush(QColor("#f1eee6"))
        painter.drawRect(QRectF(box.left(), box.top(), box.width(), chrome))
        painter.setBrush(QColor(HAIRLINE))
        painter.drawRect(QRectF(box.left(), box.top() + chrome - 1.0, box.width(), 1.0))
        for index, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            painter.setBrush(QColor(colour))
            painter.drawEllipse(QRectF(box.left() + 16 + index * 20, box.top() + 14, 12, 12))
        if title:
            painter.setPen(QColor(MUTED))
            painter.setFont(qfont(SANS, 12.5))
            painter.drawText(
                QRectF(box.left(), box.top(), box.width(), chrome),
                int(Qt.AlignmentFlag.AlignCenter),
                title,
            )
    painter.restore()
    return QRectF(box.left(), box.top() + chrome, box.width(), box.height() - chrome)


def draw_ring(painter, rect, t: float, colour: str = GOLD) -> None:
    """A ring around the control being used, breathing rather than flashing."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen

    pulse = 0.5 + 0.5 * math.sin(t * 2.4)
    grow = 5.0 + pulse * 2.5
    # Pen only, never a brush. `setBrush(0)` looks like "no brush" and is not:
    # zero is a colour, so it fills the ring's rectangle with black.
    for step, alpha in ((2.0 + grow, 42), (grow, 92), (0.0, 205)):
        painter.save()
        blend = QColor(colour)
        blend.setAlpha(int(alpha * (0.55 + 0.45 * pulse)))
        painter.setPen(QPen(blend, 2.4 if step else 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(rect).adjusted(-step, -step, step, step), 12.0 + step, 12.0 + step
        )
        painter.restore()


def draw_caption(painter, caption: str, detail: str, opacity: float = 1.0) -> None:
    """The lower third: what the viewer is looking at, and why it matters."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor

    if opacity <= 0.01:
        return
    caption_font = qfont(SERIF, 25.0)
    detail_font = qfont(SANS, 15.5)
    width = 880.0
    detail_rows = wrap(painter, detail, detail_font, width - 64.0) if detail else []
    height = 58.0 + (len(detail_rows) * 24.0 if detail_rows else 0.0)
    box = QRectF(64.0, CANVAS_H - 132.0 - (height - 74.0), width, height)

    painter.save()
    painter.setOpacity(opacity)
    rwc.draw_shadow(painter, box, 16.0, dy=12, blur=30, alpha=42)
    painter.setPen(0)
    painter.setBrush(QColor(255, 255, 255, 244))
    painter.drawRoundedRect(box, 16.0, 16.0)
    painter.setBrush(QColor(GOLD))
    painter.drawRoundedRect(QRectF(box.left(), box.top() + 14, 4.0, height - 28), 2.0, 2.0)

    painter.setPen(QColor(PRIMARY))
    painter.setFont(caption_font)
    painter.drawText(int(box.left() + 32), int(box.top() + 40), caption)
    if detail_rows:
        painter.setPen(QColor(MUTED))
        painter.setFont(detail_font)
        for index, row in enumerate(detail_rows):
            painter.drawText(int(box.left() + 32), int(box.top() + 72 + index * 24), row)
    painter.restore()


def draw_note(painter, note: dict, opacity: float = 1.0) -> None:
    """A short explanation in the bottom right, one per step at most."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor

    if opacity <= 0.01 or not note:
        return
    body_font = qfont(SANS, 14.0)
    width = 440.0
    rows = wrap(painter, note.get("body", ""), body_font, width - 56.0)
    height = 66.0 + len(rows) * 22.0
    box = QRectF(CANVAS_W - 64.0 - width, CANVAS_H - 132.0 - (height - 74.0), width, height)

    painter.save()
    painter.setOpacity(opacity)
    rwc.draw_shadow(painter, box, 16.0, dy=12, blur=30, alpha=38)
    fill = QColor(PRIMARY_DEEP)
    fill.setAlpha(246)
    painter.setPen(0)
    painter.setBrush(fill)
    painter.drawRoundedRect(box, 16.0, 16.0)
    painter.setBrush(QColor(GOLD))
    painter.drawRect(QRectF(box.left() + 28, box.top() + 22, 26.0, 2.0))
    painter.setPen(QColor(PAPER))
    painter.setFont(qfont(SANS, 14.5, None))
    painter.drawText(int(box.left() + 28), int(box.top() + 52), note.get("heading", ""))
    painter.setPen(QColor("#c8d6cd"))
    painter.setFont(body_font)
    for index, row in enumerate(rows):
        painter.drawText(int(box.left() + 28), int(box.top() + 78 + index * 22), row)
    painter.restore()


def draw_progress(painter, share: float) -> None:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor

    painter.save()
    painter.setPen(0)
    painter.setBrush(QColor(0, 0, 0, 22))
    painter.drawRect(QRectF(0, CANVAS_H - 4, CANVAS_W, 4))
    painter.setBrush(QColor(GOLD))
    painter.drawRect(QRectF(0, CANVAS_H - 4, CANVAS_W * max(0.0, min(1.0, share)), 4))
    painter.restore()


def draw_title_card(painter, brand: dict, card: dict, t: float) -> None:
    """The first thing a viewer sees, and the only flat dark screen in a set."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QLinearGradient

    settle = rwc.ease_out(rwc.between(t, 0.0, 0.7))
    gradient = QLinearGradient(0, 0, CANVAS_W, CANVAS_H)
    gradient.setColorAt(0.0, QColor(PRIMARY_DEEP))
    gradient.setColorAt(1.0, QColor(PRIMARY))
    painter.fillRect(QRectF(0, 0, CANVAS_W, CANVAS_H), gradient)

    painter.save()
    painter.setOpacity(settle)
    left = 132.0
    dy = (1.0 - settle) * 18.0
    draw_logo_plate(painter, left, 138.0 + dy, 92.0)

    painter.setPen(QColor(GOLD))
    painter.setFont(qfont(SANS, 12.5, None, spacing=2.6))
    painter.drawText(int(left), int(300 + dy), card.get("kicker", "").upper())

    title_font = qfont(SERIF, 62.0)
    rows = wrap(painter, card["title"], title_font, 1120.0)
    painter.setFont(title_font)
    painter.setPen(QColor(PAPER))
    for index, row in enumerate(rows):
        painter.drawText(int(left), int(378 + dy + index * 74), row)

    rule_y = 400 + dy + len(rows) * 74
    draw_rule(painter, left, rule_y, 84.0, GOLD, 3.0)

    strap_font = qfont(SANS, 20.0)
    rows = wrap(painter, card.get("strap", ""), strap_font, 900.0)
    painter.setFont(strap_font)
    painter.setPen(QColor("#d6e4db"))
    for index, row in enumerate(rows):
        painter.drawText(int(left), int(rule_y + 52 + index * 32), row)

    painter.setPen(QColor("#89a897"))
    painter.setFont(qfont(SANS, 13.0))
    painter.drawText(int(left), 792, card.get("foot", brand.get("site", "")))
    painter.setPen(QColor("#7f9c8b"))
    painter.setFont(qfont(SANS, 13.0))
    painter.drawText(
        QRectF(CANVAS_W - 552.0, 764.0, 420.0, 32.0),
        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
        card.get("series", ""),
    )
    painter.restore()


def draw_closing_card(painter, brand: dict, end: dict, t: float) -> None:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QLinearGradient

    settle = rwc.ease_out(rwc.between(t, 0.0, 0.7))
    gradient = QLinearGradient(0, 0, CANVAS_W, CANVAS_H)
    gradient.setColorAt(0.0, QColor(PRIMARY_DEEP))
    gradient.setColorAt(1.0, QColor(PRIMARY))
    painter.fillRect(QRectF(0, 0, CANVAS_W, CANVAS_H), gradient)

    painter.save()
    painter.setOpacity(settle)
    left = 132.0
    dy = (1.0 - settle) * 16.0
    painter.setPen(QColor(GOLD))
    painter.setFont(qfont(SANS, 12.5, None, spacing=2.6))
    painter.drawText(int(left), int(300 + dy), end.get("kicker", "").upper())

    title_font = qfont(SERIF, 54.0)
    rows = wrap(painter, end["title"], title_font, 1140.0)
    painter.setFont(title_font)
    painter.setPen(QColor(PAPER))
    for index, row in enumerate(rows):
        painter.drawText(int(left), int(374 + dy + index * 66), row)

    rule_y = 396 + dy + len(rows) * 66
    draw_rule(painter, left, rule_y, 84.0, GOLD, 3.0)

    strap_font = qfont(SANS, 21.0)
    rows = wrap(painter, end.get("strap", ""), strap_font, 980.0)
    painter.setFont(strap_font)
    painter.setPen(QColor("#d6e4db"))
    for index, row in enumerate(rows):
        painter.drawText(int(left), int(rule_y + 54 + index * 34), row)

    painter.setPen(QColor("#89a897"))
    painter.setFont(qfont(SANS, 13.0))
    painter.drawText(int(left), 792, brand.get("foot", brand.get("site", "")))
    painter.restore()


# ---------------------------------------------------------------------------
# The voice
# ---------------------------------------------------------------------------


def spoken(line) -> tuple[str, str]:
    """A narration line as (what is written, what is read out).

    They differ only where a word has to be spelled for the voice. The subtitle
    still has to say the right thing.
    """
    if isinstance(line, dict):
        return line["text"], line.get("spoken", line["text"])
    return line, line


def measure(ffmpeg: str, path: Path) -> float:
    """Seconds of audio in a file, without needing ffprobe."""
    result = run([ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"])
    match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if match:
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return 0.0


def which_kokoro() -> str | None:
    for candidate in (os.environ.get("KOKORO_PYTHON"), str(DEFAULT_KOKORO)):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def which_ffmpeg() -> str:
    """A full ffmpeg, not the one Playwright ships.

    The website clips use Playwright's bundled ffmpeg because all they need is
    a VP8 muxer for JPEG frames. That build has no libx264, no AAC and no WAV
    demuxer, so it cannot encode a narrated MP4 and it cannot read Kokoro's
    output. Asking it to try produces "Invalid data found", which is a
    confusing way to be told the wrong binary was chosen.
    """
    candidates = [
        os.environ.get("BYTEPROOF_DEMO_FFMPEG"),
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        encoders = run([candidate, "-hide_banner", "-encoders"], timeout=60).stdout
        if "libx264" in encoders:
            return candidate
    raise SystemExit(
        "no ffmpeg with libx264 was found. Install one with 'brew install ffmpeg', "
        "or point BYTEPROOF_DEMO_FFMPEG at a build that has it."
    )


def assign_voices(
    names: Sequence[str],
    voices: Sequence[str] = KOKORO_VOICES,
    seed: int = CASTING_SEED,
) -> dict[str, str]:
    """Give every clip a voice, spread evenly and shuffled.

    Two things matter. Every voice should carry about as many clips as the
    others, and two clips running back to back should not share one: these are
    meant to be watched in sequence, and the change of narrator is what stops
    that feeling like one long film.

    The seed is fixed so a re-run reproduces the casting exactly. An assignment
    that moved every time would leave a re-recorded clip sounding different
    from the clips around it.
    """
    import random

    rng = random.Random(seed)
    assignment: dict[str, str] = {}
    bag: list[str] = []
    previous = ""
    for name in names:
        if not bag:
            bag = list(voices)
            rng.shuffle(bag)
            if bag[0] == previous and len(bag) > 1:
                bag.append(bag.pop(0))
        previous = bag.pop(0)
        assignment[name] = previous
    return assignment


def narrate(lines: list, work: Path, voice: str, ffmpeg: str) -> list[dict]:
    """Read one clip's lines in a single Kokoro run, then normalise them.

    One process for the whole clip: the model takes a few seconds to load and a
    line takes under one, so a process per line would spend most of its life
    loading weights.

    A clip that has already been read is left alone. That is what lets the same
    set be rendered twice, once for the web and once for YouTube: the words and
    the pacing come back identical because they are the same audio files, and
    only the picture is drawn again. Pass --renarrate to read them afresh.
    """
    existing = [work / f"voice-{index:02d}.m4a" for index in range(len(lines))]
    if not RENARRATE and all(
        path.exists() and path.stat().st_size > 512 for path in existing
    ):
        log(f"     reusing the narration in {work}")
        return [
            {
                "text": spoken(line)[0],
                "path": path,
                "seconds": round(measure(ffmpeg, path), 3),
            }
            for line, path in zip(lines, existing, strict=True)
        ]

    interpreter = which_kokoro()
    if not interpreter:
        raise SystemExit(
            "the Kokoro environment was not found.\n"
            "  uv venv --python 3.12 ~/.codex/tts/kokoro/venv\n"
            "  uv pip install --python ~/.codex/tts/kokoro/venv/bin/python kokoro soundfile"
        )
    out_dir = work / "kokoro"
    out_dir.mkdir(parents=True, exist_ok=True)
    job = work / "kokoro-job.json"
    job.write_text(
        json.dumps(
            {
                "voice": voice,
                "speed": KOKORO_SPEED,
                "out_dir": str(out_dir),
                "lines": [
                    {"text": spoken(line)[0], "spoken": spoken(line)[1]} for line in lines
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # misaki, Kokoro's text front end, shells out to `uv` the first time it
    # needs its English model. `uv` will not act unless it can see which
    # environment it is working on, so it is told.
    environment = dict(os.environ, VIRTUAL_ENV=str(Path(interpreter).parent.parent))
    result = run([interpreter, str(KOKORO_RUNNER), str(job)], env=environment, timeout=900)
    if result.returncode != 0:
        raise SystemExit(
            f"Kokoro could not read the script:\n"
            f"{(result.stderr or result.stdout).strip()[-600:]}"
        )

    made = []
    for index, line in enumerate(lines):
        raw = out_dir / f"line-{index:02d}.wav"
        if not raw.exists():
            raise SystemExit(f"Kokoro produced no audio for line {index} of {work.name}")
        audio = work / f"voice-{index:02d}.m4a"
        outcome = run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "1", str(audio),
            ]
        )
        if not audio.exists():
            raise SystemExit(
                f"line {index} was read but could not be converted "
                f"(ffmpeg exit {outcome.returncode}):\n"
                f"{(outcome.stderr or '').strip()[-500:]}"
            )
        made.append(
            {"text": spoken(line)[0], "path": audio, "seconds": round(measure(ffmpeg, audio), 3)}
        )
    return made


def build_score(lines: list[dict], slots: list[dict], length: float, out: Path,
                ffmpeg: str) -> Path | None:
    """Lay every spoken line on the clock the picture is cut to."""
    inputs: list[str] = [
        "-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=44100:cl=mono"
    ]
    for line in lines:
        inputs += ["-i", str(line["path"])]
    filters, labels = [], ["[0:a]"]
    for index, slot in enumerate(slots):
        delay = max(0, round(slot["start"] * 1000))
        filters.append(f"[{index + 1}:a]adelay={delay}|{delay}[v{index}]")
        labels.append(f"[v{index}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]"
    )
    outcome = run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", ";".join(filters), "-map", "[aout]",
            "-t", f"{length:.3f}", "-c:a", "aac", "-b:a", "160k", str(out),
        ],
        timeout=600,
    )
    if outcome.returncode != 0:
        raise SystemExit(f"the narration could not be laid down: {outcome.stderr[:400]}")
    return out


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def timeline(clip: Clip, lines: list[dict]) -> tuple[list[dict], float]:
    """Place every line on the clock, and say how long the film runs.

    The picture waits for the sentence rather than the sentence being hurried
    to fit the picture, which is the only way a step reads as an explanation
    instead of a caption. Because the narration is written before the frames
    are rendered, the two cannot drift apart.
    """
    slots: list[dict] = []
    at = 0.0
    for index, line in enumerate(lines):
        if index == 0:
            hold, kind = TITLE_BREATH, "title"
        elif index == len(lines) - 1:
            hold, kind = CLOSING_BREATH, "closing"
        else:
            hold, kind = clip.beats[index - 1].hold, "step"
        end = at + line["seconds"] + hold
        slots.append(
            {
                "index": index,
                "start": at,
                "end": end,
                "text": line["text"],
                "kind": kind,
                "step": index - 1,
            }
        )
        at = end
    return slots, at


def slot_at(slots: list[dict], t: float) -> dict:
    for slot in slots:
        if t < slot["end"]:
            return slot
    return slots[-1]


def frame(clip: Clip, slots: list[dict], length: float, t: float):
    """One finished frame of the film."""
    from PyQt6.QtGui import QColor, QImage, QPainter

    slot = slot_at(slots, t)
    local = max(0.0, t - slot["start"])
    width, height = output_size()
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(rwc.PAGE_TOP))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # Plans are written in the logical canvas; a larger output is the same
    # picture drawn through a scale, so type stays sharp instead of being
    # enlarged afterwards. Smoothing keeps a snapped dialog from going soft.
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(output_scale(), output_scale())

    if slot["kind"] == "title":
        draw_title_card(painter, clip.card.get("brand", {}), clip.card, local)
        painter.end()
        return image
    if slot["kind"] == "closing":
        draw_closing_card(painter, clip.end.get("brand", clip.card.get("brand", {})),
                          clip.end, local)
        painter.end()
        return image

    beat = clip.beats[slot["step"]]
    beat.seconds = slot["end"] - slot["start"]
    desk(painter)
    beat.draw(painter, local, beat)

    if beat.ring is not None:
        rect = beat.ring()
        if rect is not None:
            draw_ring(painter, rect, local)

    if beat.cursor:
        span = max(1e-6, beat.seconds)
        pointer = rwc.Pointer(
            *[(at * span, x, y) for at, x, y in beat.cursor],
            clicks=[at * span for at in beat.click],
        )
        pointer.draw(painter, local)

    appear = rwc.ease_out(rwc.between(local, 0.05, 0.5))
    draw_note(painter, beat.note or {}, appear)
    draw_caption(painter, beat.caption, beat.detail, appear)
    draw_progress(painter, t / length if length else 0.0)
    painter.end()
    return image


def to_rgb24(image) -> bytes:
    """Raw bytes for ffmpeg, row by row: QImage pads its rows."""
    from PyQt6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = converted.width(), converted.height()
    stride = converted.bytesPerLine()
    buffer = converted.constBits()
    buffer.setsize(converted.sizeInBytes())
    raw = bytes(buffer)
    if stride == width * 3:
        return raw
    return b"".join(
        raw[y * stride:y * stride + width * 3] for y in range(height)
    )


# ---------------------------------------------------------------------------
# One film
# ---------------------------------------------------------------------------


def record_clip(clip: Clip, out_dir: Path, ffmpeg: str, voice: str,
                work_root: Path) -> dict:
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    out_dir.mkdir(parents=True, exist_ok=True)
    work = work_root / clip.name
    work.mkdir(parents=True, exist_ok=True)

    lines, slots, length = prepare(clip, work, voice, ffmpeg)

    score = build_score(lines, slots, length, work / "score.m4a", ffmpeg)
    video = out_dir / f"{clip.name}.mp4"
    poster = out_dir / f"{clip.name}.jpg"

    total = round(length * FPS)
    width, height = output_size()
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(FPS), "-i", "pipe:0",
        "-i", str(score),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(OUT_CRF),
        "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-ac", str(OUT_CHANNELS),
        "-shortest", "-r", str(FPS),
        str(video),
    ]
    stderr_path = work / "ffmpeg.log"
    with open(stderr_path, "w") as stderr_file:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr_file)
    assert process.stdin is not None

    app = QApplication.instance()
    if app is None:
        raise SystemExit("QApplication must exist before recording")

    # The cues belong to wall-clock positions, so they are fired by comparing
    # the frame's time rather than by a timer: rendering runs faster than
    # playback and a timer would fire late.
    pending = sorted(
        (cue for slot in slots for cue in slot.get("cues", [])), key=lambda pair: pair[0]
    )
    poster_frame = clip.poster_at
    if poster_frame is None:
        content = [slot for slot in slots if slot["kind"] == "step"]
        poster_frame = content[min(1, len(content) - 1)]["start"] + 0.8 if content else length * 0.5
    poster_delta = 1e9
    started = time.monotonic()
    for number in range(total):
        t = number / FPS
        while pending and pending[0][0] <= t:
            pending.pop(0)[1]()
        image = frame(clip, slots, length, t)
        process.stdin.write(to_rgb24(image))
        delta = abs(t - poster_frame)
        if delta < poster_delta:
            poster_delta = delta
            image.save(str(poster), "JPEG", 92)
        if number % 15 == 0:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        if process.poll() is not None:
            raise SystemExit(
                f"ffmpeg stopped early on {clip.name} (exit {process.returncode}):\n"
                f"{stderr_path.read_text(errors='replace')[-1200:]}"
            )
    process.stdin.close()
    process.wait()
    if process.returncode != 0:
        raise SystemExit(
            f"ffmpeg failed for {clip.name}:\n"
            f"{stderr_path.read_text(errors='replace')[-1500:]}"
        )

    script = [(slot["start"], slot["end"], slot["text"]) for slot in slots]
    write_transcript(script, out_dir / clip.name, clip.card["title"], clip.card.get("strap", ""))
    audit = write_audit(ffmpeg, video, script, out_dir, clip)
    if clip.cleanup is not None:
        clip.cleanup()
    return {
        "name": clip.name,
        "title": clip.card["title"],
        "strap": clip.card.get("strap", ""),
        "group": clip.group,
        "voice": voice,
        "seconds": round(length, 1),
        "size_mb": round(video.stat().st_size / 1_048_576, 2),
        "poster": poster.name,
        "transcript": f"{clip.name}.txt",
        "subtitles": f"{clip.name}.srt",
        "audit": audit,
        "recorded_in": round(time.monotonic() - started, 1),
        "lines": len(slots),
        # Chapter marks for an upload, from the captions that are already on
        # screen: YouTube wants the first one at zero and at least three.
        "chapters": [
            {
                "at": round(slot["start"], 1),
                "label": (
                    "Introduction" if slot["kind"] == "title"
                    else "What this shows" if slot["kind"] == "closing"
                    else clip.beats[slot["step"]].caption
                ),
            }
            for slot in slots
        ],
    }


def stamp(value: float, comma: bool = True) -> str:
    whole = max(0, int(value))
    fraction = round((value - int(value)) * 1000)
    if fraction >= 1000:
        whole, fraction = whole + 1, 0
    mark = "," if comma else "."
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}{mark}{fraction:03d}"


def prepare(clip: Clip, work: Path, voice: str, ffmpeg: str):
    """Read the script, then work out the clock it will be cut to."""
    lines = narrate(
        [clip.say, *[beat.say for beat in clip.beats], clip.end["strap"]],
        work, voice, ffmpeg,
    )
    slots, length = timeline(clip, lines)
    clip.card.setdefault("brand", {})
    clip.end.setdefault("brand", clip.card["brand"])
    for slot in slots:
        if slot["kind"] != "step":
            continue
        beat = clip.beats[slot["step"]]
        if beat.enter is not None:
            # Fired on the first frame of the step, before anything is drawn:
            # a plan uses this to put a page into the state the step is about.
            slot.setdefault("cues", []).append((slot["start"], beat.enter))
        if beat.cue is None:
            continue
        at = slot["start"] + (slot["end"] - slot["start"]) * beat.cue[0]
        slot.setdefault("cues", []).append((at, beat.cue[1]))
    return lines, slots, length


def probe_clip(clip: Clip, out_dir: Path, work: Path, voice: str, ffmpeg: str,
               moments: Sequence[float]) -> None:
    """Write one frame per asked-for moment, without encoding a film.

    Checking a picture by recording a fifty second clip and cutting frames out
    of it takes half a minute; this takes a second, which is the difference
    between fixing a layout and living with it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _, slots, length = prepare(clip, work, voice, ffmpeg)
    for moment in moments:
        t = min(max(0.0, moment), max(0.0, length - 0.05))
        slot = slot_at(slots, t)
        beat = clip.beats[slot["step"]] if slot["kind"] == "step" else None
        if beat is not None:
            beat.seconds = slot["end"] - slot["start"]
            # Every cue up to and including this step, so a still shows the
            # screen the step ends on rather than the one it begins on.
            for earlier in slots[: slot["index"] + 1]:
                for _at, action in earlier.get("cues", []):
                    action()
        image = frame(clip, slots, length, t)
        path = out_dir / f"probe-{clip.name}-{t:06.1f}.png"
        image.save(str(path))
        what = slot["kind"] if beat is None else f"step {slot['step'] + 1}"
        log(f"     {path.name}   ({what}, line starts {slot['start']:.1f}s)")
    log(f"     the film runs {length:.1f}s")


def write_transcript(found, base: Path, title: str, summary: str) -> None:
    """Two forms of the same thing: cues for an editor, a script to read."""
    base.with_suffix(".srt").write_text(
        "\n".join(
            f"{number}\n{stamp(start)} --> {stamp(end)}\n{text}\n"
            for number, (start, end, text) in enumerate(found, start=1)
        ),
        encoding="utf-8",
    )
    spoken_total = sum(end - start for start, end, _ in found)
    lines = [
        title,
        "=" * len(title),
        "",
        summary,
        "",
        f"Total spoken: {spoken_total:.1f} seconds over {len(found)} lines.",
        "Timecodes are from the start of the finished clip.",
        "",
    ]
    for start, _end, text in found:
        lines.append(f"[{stamp(start, comma=False)}]  {text}")
        lines.append("")
    base.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")


def write_audit(ffmpeg: str, video: Path, script, out_dir: Path, clip: Clip) -> str | None:
    """What is on screen when each sentence starts, laid out to be checked.

    Alignment is easy to get wrong and hard to notice, so it is not asserted
    here, it is shown: one frame taken just after each line begins, with the
    line underneath it. Work through this sheet and the question answers
    itself - is the narrator describing the screen the viewer is looking at?
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log(
            "    ! no Pillow, so no audit sheet was written. The sheet is how "
            "the narration is checked against the picture: install it with "
            "'venv/bin/python -m pip install pillow'."
        )
        return None

    frames_dir = out_dir / clip.name
    frames_dir.mkdir(parents=True, exist_ok=True)
    width, height = 760, 428
    pad, bar = 18, 78
    sheet = Image.new(
        "RGB", (width + pad * 2, (height + bar + pad) * len(script) + pad), "#faf9f3"
    )
    draw = ImageDraw.Draw(sheet)

    def font(size: int):
        for name in ("Helvetica.ttc", "HelveticaNeue.ttc", "Arial Unicode.ttf", "Geneva.ttf"):
            try:
                return ImageFont.truetype(f"/System/Library/Fonts/{name}", size)
            except OSError:
                continue
        return ImageFont.load_default()

    stamp_font, body_font = font(19), font(20)
    top = pad
    for number, (start, _end, text) in enumerate(script, start=1):
        # A beat into the line rather than exactly on it: the first frame of a
        # step is often still the previous screen.
        shot = frames_dir / f"cue-{number:02d}.jpg"
        run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start + 0.55:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", f"scale={width}:{height}:flags=lanczos", "-q:v", "4", str(shot),
            ],
            timeout=120,
        )
        if shot.exists():
            sheet.paste(Image.open(shot).convert("RGB"), (pad, top))
        label = stamp(start, comma=False)
        draw.text((pad, top + height + 10), label, fill=GOLD, font=stamp_font)
        offset = pad + draw.textlength(label + "   ", font=stamp_font)
        line, wrapped = "", []
        for word in text.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=body_font) > width + pad - offset:
                wrapped.append(line)
                line = word
            else:
                line = trial
        wrapped.append(line)
        for row, content in enumerate(wrapped[:2]):
            draw.text((offset, top + height + 10 + row * 26), content,
                      fill=INK, font=body_font)
        top += height + bar + pad

    sheet_path = out_dir / f"{clip.name}-audit.jpg"
    sheet.convert("RGB").save(sheet_path, quality=82)
    (frames_dir / "notes.txt").write_text(
        f"{clip.card['title']}\n{clip.card.get('strap', '')}\n\n"
        "One frame just after each spoken line begins.\n\n"
        + "\n".join(f"{stamp(start, comma=False)}  {text}" for start, _end, text in script)
        + "\n",
        encoding="utf-8",
    )
    return sheet_path.name


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------


def write_gallery(out: Path, results: list[dict], groups: dict[str, str], brand: dict) -> None:
    rows = []
    for group, label in groups.items():
        films = [item for item in results if item["group"] == group]
        if not films:
            continue
        rows.append(f'<h2>{label}</h2><div class="grid">')
        for item in films:
            rows.append(
                f'<figure><video src="{item["name"]}.mp4" poster="{item["poster"]}" '
                f'controls preload="metadata"></video>'
                f'<figcaption><b>{item["title"]}</b>'
                f'<span>{item["strap"]}</span>'
                f'<em>{item["seconds"]:g}s · {item["voice"]}</em>'
                f'</figcaption></figure>'
            )
        rows.append("</div>")
    page = f"""<!DOCTYPE html>
<html lang="en-NZ"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{brand.get('name', 'ByteProof')} demonstration videos</title>
<style>
  :root {{ --primary:#1a3a2a; --gold:#c9a227; --paper:#faf9f3; --ink:#1f1e1a;
           --muted:#6a6760; --border:#e2ddd1; --surface:#fdfcf8; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
          font:16px/1.55 Inter,-apple-system,system-ui,"Segoe UI",Roboto,sans-serif; }}
  header {{ background:var(--primary); color:#fff; padding:54px 6vw 46px; }}
  header h1 {{ font-family:Newsreader,"Iowan Old Style",Palatino,Georgia,serif;
               font-size:42px; font-weight:500; margin:0 0 12px; letter-spacing:-.01em; }}
  header p {{ margin:0; color:#d6e4db; max-width:760px; }}
  header .tag {{ color:var(--gold); letter-spacing:.2em; text-transform:uppercase;
                 font-size:12px; font-weight:600; margin-bottom:16px; }}
  main {{ padding:44px 6vw 80px; }}
  h2 {{ font-family:Newsreader,"Iowan Old Style",Palatino,Georgia,serif; font-weight:500;
        color:var(--primary); font-size:26px; margin:52px 0 20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:26px; }}
  figure {{ margin:0; background:var(--surface); border:1px solid var(--border);
            border-radius:12px; overflow:hidden; }}
  video {{ display:block; width:100%; background:#000; aspect-ratio:16/9; }}
  figcaption {{ padding:16px 18px 18px; }}
  figcaption b {{ display:block; font-family:Newsreader,"Iowan Old Style",Palatino,Georgia,serif;
                  font-size:19px; font-weight:500; color:var(--primary); margin-bottom:6px; }}
  figcaption span {{ display:block; color:var(--muted); font-size:14.5px; }}
  figcaption em {{ display:block; margin-top:10px; font-style:normal; font-size:12.5px;
                   color:var(--gold); letter-spacing:.08em; text-transform:uppercase; }}
</style></head><body>
<header>
  <div class="tag">{brand.get('name', 'ByteProof')} · demonstration videos</div>
  <h1>Every screen is the shipped application</h1>
  <p>Each clip is built from ByteProof's own widgets and narrates one job from
  start to finish. The narration is generated speech; the screens are not.</p>
</header>
<main>
{chr(10).join(rows)}
</main></body></html>"""
    (out / "index.html").write_text(page, encoding="utf-8")


def write_master_transcript(out: Path, results: list[dict], groups: dict[str, str]) -> None:
    blocks = [
        "# ByteProof demonstration videos: transcripts",
        "",
        "The narration for every clip, with the time it starts in the finished",
        "film. Each clip also has its own `.srt` (ready to import into an editor",
        "or a captioning tool) and `.txt` (the same script, laid out to read).",
        "",
        "Timecodes are `mm:ss.mmm` from the start of that clip.",
        "",
        "---",
        "",
    ]
    for group, label in groups.items():
        films = [item for item in results if item["group"] == group]
        if not films:
            continue
        blocks += [f"## {label}", ""]
        for item in films:
            blocks += [
                f"### {item['title']}  ·  `{item['name']}`",
                "",
                f"*{item['seconds']:g} seconds — voice: {item['voice']}*",
                "",
                item["strap"],
                "",
            ]
            path = out / item["transcript"]
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("[") and "]" in line:
                        when, _, words = line.partition("]")
                        blocks.append(f"- `{when[1:]}`  {words.strip()}")
                blocks.append("")
        blocks.append("")
    (out / "transcripts.md").write_text("\n".join(blocks), encoding="utf-8")


def write_readme(out: Path, results: list[dict], groups: dict[str, str], brand: dict,
                 plan_name: str) -> None:
    """What this folder is, for whoever opens it in six months."""
    rows = []
    for group, label in groups.items():
        films = [item for item in results if item["group"] == group]
        if not films:
            continue
        rows.append(f"\n### {label}\n")
        for item in films:
            rows.append(
                f"- **{item['title']}** · `clips/{item['name']}.mp4` · "
                f"{item['seconds']:g}s · voice `{item['voice']}`\n"
            )
    voices = sorted({item["voice"] for item in results})
    text = f"""# {brand.get('name', 'ByteProof')} demonstration videos

Filmed rather than mocked up. Every screen is the application's own widget,
laid out by its own code, and every change of state is made by calling the same
method the application's own event handler calls. What is staged is the timing,
and no clip makes a network request: where a clip would wait for a language
model, the answer is supplied by the plan.

So a clip shows what the software does. It does not show how long the software
takes, and the narration is generated speech rather than a person.

## What is in here

- `index.html` plays the whole set. Open it in a browser.
- `clips/<name>.mp4` is one film: {OUT_SIZE[0]}x{OUT_SIZE[1]}, H.264 and AAC.
- `clips/<name>.jpg` is a poster frame.
- `clips/<name>.srt` is the narration, timed, for an editor or a captioning
  tool. `clips/<name>.txt` is the same script laid out to read.
- `clips/<name>-audit.jpg` is every spoken line with the frame that plays under
  it. **Read this one** if a film is ever re-recorded: it is the only cheap way
  to catch narration describing a screen the viewer is not looking at.
- `upload-sheet.md` holds a title, a description and chapter marks for each
  clip, if the set is going to YouTube.
- `transcripts.md` is every script in one document.

## The other copy

This folder is the {OUT_SIZE[0]}x{OUT_SIZE[1]} copy. The narration is read once
and reused, so a second size is a re-render rather than a re-record: the words,
the pacing and the captions come back identical, and only the picture is drawn
again.

    python scripts/record_demo_videos.py --plan {plan_name} --out demos/youtube \\
        --size 1920x1080 --crf 18 --channels 2 --work <the work folder of the first run>

The website copy stays at {CANVAS_W}x{CANVAS_H}, which is the size the product
page was built around. 1920x1080 is the one to upload: 1080p at CRF 18, with
the narration spread to two channels because that is what a video service
expects.

## Re-recording

The scripts are the specification, so a set can be rebuilt after a release and
the narration, captions and timings come back the same:

    python scripts/record_demo_videos.py --plan {plan_name} --list
    python scripts/record_demo_videos.py --plan {plan_name} --only 02-proofread-a-selection --out /tmp/try
    python scripts/record_demo_videos.py --plan {plan_name} --out demos

Record one clip and read its audit sheet before running the set. Expect to fix
the parts of the picture that moved, which is what the audit sheet is for.

Two things have to be installed: ffmpeg, and the Kokoro environment that reads
the narration (`uv venv --python 3.12 ~/.codex/tts/kokoro/venv`, then
`uv pip install --python ~/.codex/tts/kokoro/venv/bin/python kokoro soundfile`).
Pillow is needed for the audit sheets. Kokoro is Apache-2.0, so the narration
can be published commercially; say that it is AI-generated when it is.

## The set

{len(results)} films, narrated by {len(voices)} voices ({', '.join('`' + v + '`' for v in voices)}),
dealt out by a fixed seed so a re-run gives every clip the voice it had before.
{"".join(rows)}
"""
    (out / "README.md").write_text(text, encoding="utf-8")


def write_upload_sheet(out: Path, results: list[dict], groups: dict[str, str],
                       brand: dict, size: tuple[int, int]) -> None:
    """Titles, descriptions and chapters, ready to paste into an upload form.

    Eighteen clips is eighteen sets of the same four fields, and copying them
    out of eighteen transcripts is the sort of job that ends with the wrong
    description under the wrong film.
    """
    def stamp_chapter(value: float) -> str:
        minutes = int(value) // 60
        seconds = int(value) % 60
        return f"{minutes:02d}:{seconds:02d}"

    blocks = [
        f"# {brand.get('name', 'ByteProof')} videos: upload sheet",
        "",
        f"The set at {size[0]}x{size[1]}, with a title, a description and",
        "chapters for each clip. The chapters are written from the captions",
        "that are already on screen, so they line up with what a viewer sees.",
        "",
        "Paste the title and description as they are. The last line of every",
        "description says the narration is generated speech, which is a",
        "disclosure worth keeping.",
        "",
        "---",
        "",
    ]
    for group, label in groups.items():
        films = [item for item in results if item["group"] == group]
        if not films:
            continue
        blocks += [f"## {label}", ""]
        for item in films:
            chapters = item.get("chapters") or []
            blocks += [
                f"### {item['title']}",
                "",
                (
                    f"**File** `clips/{item['name']}.mp4` · "
                    f"{item['seconds']:g}s · {size[0]}x{size[1]}"
                ),
                "",
                "**Title**",
                "",
                f"```\n{item['title']} | {brand.get('name', 'ByteProof')}\n```",
                "",
                "**Description**",
                "",
                "```",
                item["strap"],
                "",
                (
                    f"{brand.get('name', 'ByteProof')} proofreads the way you "
                    f"already write: select a passage, press the shortcut, and "
                    f"read the changes before any of them are made."
                ),
                "",
                brand.get("site", ""),
                "",
                (
                    "Narration is AI-generated speech. The screens are the "
                    "shipped application."
                ),
                "```",
                "",
            ]
            if len(chapters) >= 3:
                blocks += ["**Chapters**", "", "```"]
                blocks += [
                    f"{stamp_chapter(chapter['at'])} {chapter['label']}"
                    for chapter in chapters
                ]
                blocks += ["```", ""]
        blocks.append("")
    (out / "upload-sheet.md").write_text("\n".join(blocks), encoding="utf-8")


def read_manifest(out: Path) -> dict[str, dict]:
    """What has already been recorded into this folder.

    Re-recording one clip must not empty the gallery of the other thirty. The
    manifest is the folder's memory of them: the run reads it, replaces the
    clips it just made, and writes the gallery from the lot.
    """
    path = out / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    # Written as {name: clip}. A list is also accepted, because that is the
    # shape an older or hand-written manifest is likely to have.
    if isinstance(data, dict):
        return {
            str(name): item for name, item in data.items() if isinstance(item, dict)
        }
    if isinstance(data, list):
        return {
            str(item["name"]): item
            for item in data
            if isinstance(item, dict) and "name" in item
        }
    return {}


def write_manifest(out: Path, results: dict[str, dict]) -> None:
    (out / "manifest.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def load_plan(path: Path):
    spec = importlib.util.spec_from_file_location("video_clips", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"{path} is not a Python file")
    module = importlib.util.module_from_spec(spec)
    sys.modules["video_clips"] = module
    spec.loader.exec_module(module)
    for required in ("FILMS", "GROUPS"):
        if not hasattr(module, required):
            raise SystemExit(f"{path} has no {required}")
    return module


def main() -> int:
    global OUT_SIZE, OUT_CRF, OUT_CHANNELS, RENARRATE

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path, help="the film plan")
    parser.add_argument("--out", type=Path, default=ROOT / "demos")
    parser.add_argument("--only", action="append", default=[], help="a clip name (repeatable)")
    parser.add_argument("--group", action="append", default=[], help="a group name")
    parser.add_argument("--voice", help="one voice for every clip")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--size", default=f"{CANVAS_W}x{CANVAS_H}",
                        help="output size, e.g. 1920x1080 for a YouTube copy. "
                             "Plans are written in 1600x900 and are scaled.")
    parser.add_argument("--crf", type=int, default=OUT_CRF,
                        help="x264 quality, lower is better (default 20)")
    parser.add_argument("--channels", type=int, default=OUT_CHANNELS,
                        help="audio channels (2 for the YouTube copy)")
    parser.add_argument("--renarrate", action="store_true",
                        help="read the script again even if the work folder "
                             "already holds it")
    parser.add_argument("--probe", action="store_true",
                        help="write stills at --at instead of recording")
    parser.add_argument("--rebuild", action="store_true",
                        help="rewrite the gallery, README and transcripts from "
                             "what is already in --out, and record nothing")
    parser.add_argument("--at", default="", help="comma separated seconds, for --probe")
    parser.add_argument("--work", type=Path, default=None, help="where narration is built")
    args = parser.parse_args()

    try:
        width, height = (int(part) for part in args.size.lower().split("x"))
    except ValueError:
        raise SystemExit(f"--size wants WIDTHxHEIGHT, not {args.size!r}") from None
    if abs((width / height) - (CANVAS_W / CANVAS_H)) > 0.001:
        raise SystemExit(
            f"--size {args.size} is not {CANVAS_W}:{CANVAS_H}. The canvas is "
            f"fixed at that shape, so a different one would letterbox the film."
        )
    if width < CANVAS_W or width % 2 or height % 2:
        raise SystemExit(
            f"--size {args.size} must be at least {CANVAS_W}x{CANVAS_H} and even "
            f"in both directions, which is what yuv420p wants."
        )
    OUT_SIZE, OUT_CRF, OUT_CHANNELS = (width, height), args.crf, args.channels
    RENARRATE = args.renarrate

    plan = load_plan(args.plan.resolve())
    films = list(plan.FILMS)
    if args.only:
        wanted = set(args.only)
        films = [item for item in films if item["name"] in wanted]
        missing = wanted - {item["name"] for item in films}
        if missing:
            raise SystemExit(f"no such clip: {', '.join(sorted(missing))}")
    if args.group:
        wanted = set(args.group)
        films = [item for item in films if item["group"] in wanted]

    if args.list:
        for item in films:
            label = plan.GROUPS.get(item["group"], item["group"])
            print(f"{item['name']:<34} {label}")
        return 0

    if args.rebuild:
        # The templates are code, and code gets fixed after the films are made.
        # Rebuilding from the manifest means a wording change to the gallery
        # does not cost another hour of recording.
        recorded = read_manifest(args.out)
        everything = [recorded[item["name"]] for item in plan.FILMS
                      if item["name"] in recorded]
        if not everything:
            raise SystemExit(
                f"{args.out} has no manifest.json, so there is nothing to "
                f"rebuild from"
            )
        write_master_transcript(args.out, everything, plan.GROUPS)
        write_gallery(args.out, everything, plan.GROUPS, plan.BRAND)
        write_upload_sheet(args.out, everything, plan.GROUPS, plan.BRAND, OUT_SIZE)
        write_readme(args.out, everything, plan.GROUPS, plan.BRAND, str(
            args.plan.resolve().relative_to(ROOT)
            if str(args.plan.resolve()).startswith(str(ROOT)) else args.plan
        ))
        log(f"rebuilt the gallery from {len(everything)} recorded clips in {args.out}")
        return 0

    if not films:
        raise SystemExit("nothing selected")

    # Casting is worked out over the whole plan, not the subset being recorded,
    # so a clip keeps its voice whether it is recorded alone or with the set.
    casting = assign_voices([item["name"] for item in plan.FILMS])
    if args.voice:
        casting = {name: args.voice for name in casting}

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    rwc.apply_light_palette(app)

    ffmpeg = which_ffmpeg()
    work_root = args.work or Path(tempfile.mkdtemp(prefix="byteproof-demo-"))
    args.out.mkdir(parents=True, exist_ok=True)
    log(f"recording {len(films)} clip{'s' if len(films) != 1 else ''} into {args.out}")

    results: list[dict] = []
    started = time.monotonic()
    with rwc.quiet_app():
        for index, item in enumerate(films, start=1):
            voice = casting[item["name"]]
            log(f"\n{index:>2}. {item['name']}   ·   {voice}")
            clip = item["build"]()
            if clip.name != item["name"]:
                raise SystemExit(
                    f"the plan says {item['name']} but the clip calls itself {clip.name}"
                )
            check_clips([clip])
            if args.probe:
                moments = [
                    float(value) for value in args.at.split(",") if value.strip()
                ] or [0.5]
                probe_clip(clip, args.out / "probes", work_root / clip.name,
                           voice, ffmpeg, moments)
                continue
            result = record_clip(clip, args.out / "clips", ffmpeg, voice, work_root)
            results.append(result)
            log(
                f"     clips/{result['name']}.mp4  {result['seconds']:g}s  "
                f"{result['size_mb']} MB  ({result['recorded_in']:g}s to record)"
            )
            # Written after every clip, so a run that is stopped half way still
            # leaves a gallery that plays everything finished so far - and one
            # that only re-recorded a single clip leaves the rest of the set in
            # place, because the manifest remembers it.
            recorded = read_manifest(args.out)
            recorded[result["name"]] = result
            write_manifest(args.out, recorded)
            everything = [recorded[item["name"]] for item in plan.FILMS
                          if item["name"] in recorded]
            write_master_transcript(args.out, everything, plan.GROUPS)
            write_gallery(args.out, everything, plan.GROUPS, plan.BRAND)
            write_upload_sheet(args.out, everything, plan.GROUPS, plan.BRAND, OUT_SIZE)
            write_readme(args.out, everything, plan.GROUPS, plan.BRAND, str(
                args.plan.resolve().relative_to(ROOT)
                if str(args.plan.resolve()).startswith(str(ROOT)) else args.plan
            ))

    elapsed = (time.monotonic() - started) / 60.0
    total_mb = sum(item["size_mb"] for item in results)
    log(
        f"\n{len(results)} of {len(films)} films written to {args.out} "
        f"in {elapsed:.1f} minutes ({total_mb:.0f} MB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
