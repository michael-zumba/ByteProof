#!/usr/bin/env python3
"""Build every raster icon from the vector sources in ``logo/``.

The app icon carries two drawings on purpose. At 64px and up it is the mark
itself (``logo/app-icon.svg``); at 16 and 32 pixels its strokes are thinner
than a pixel and its hollow nodes collapse into grey mush, so those entries
use the same mark filled solid. The silhouette is derived from the artwork by
filling its enclosed areas - the outline is the logo's own - so no drawing is
invented here.

  logo/logo.png                1024, the window icon and store art
  logo/app_icon.png            1024, kept in step with logo.png
  logo/logo.icns               macOS, mixed artwork per size
  logo/logo.ico                Windows, mixed artwork per size
  packaging/windows/Assets/    Microsoft Store / MSIX tiles

Run from anywhere with the project's virtualenv:

    ./venv/bin/python scripts/build_app_icons.py
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETAIL = ROOT / "logo" / "app-icon.svg"
MARK = ROOT / "logo" / "logo.svg"
INK = "#1a3a2a"
PLATE_GREY = "#e6e1d6"

# Small entries are the solid form; anything 64px and up is the line art.
ICONSET = [
    ("icon_16x16.png", 16, "small"),
    ("icon_16x16@2x.png", 32, "small"),
    ("icon_32x32.png", 32, "small"),
    ("icon_32x32@2x.png", 64, DETAIL),
    ("icon_128x128.png", 128, DETAIL),
    ("icon_128x128@2x.png", 256, DETAIL),
    ("icon_256x256.png", 256, DETAIL),
    ("icon_256x256@2x.png", 512, DETAIL),
    ("icon_512x512.png", 512, DETAIL),
    ("icon_512x512@2x.png", 1024, DETAIL),
]

ICO_SIZES = [(16, "small"), (24, "small"), (32, "small"), (48, "small"),
             (64, DETAIL), (128, DETAIL), (256, DETAIL)]

STORE_TILES = [
    ("Square44x44Logo.png", 44, "small"),
    ("Square150x150Logo.png", 150, DETAIL),
    ("StoreLogo.png", 50, "small"),
]

# The small form sits a little larger on its plate: at 16 pixels the mark needs
# the extra weight, which is also what system icons do.
SMALL_PLATE = 940.0
SMALL_PLATE_RADIUS = 211.5
SMALL_MARK_SHARE = 0.74
# Pixels of growth applied to the outline before filling it, at the mask's own
# resolution: enough to close the artwork's open hemisphere, small enough that
# the fissure between the hemispheres stays open.
GROWTH = 4

_silhouettes: dict[int, object] = {}


def render_image(svg: Path, size: int):
    """Rasterise one SVG at one square size, keeping its aspect ratio."""
    from PyQt6.QtCore import QByteArray, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg.read_bytes()))
    if not renderer.isValid():
        raise SystemExit(f"{svg} is not a valid SVG")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    renderer.render(painter)
    painter.end()
    return image


def ink_bounds(image) -> tuple[int, int, int, int]:
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 120:
                left, top = min(left, x), min(top, y)
                right, bottom = max(right, x), max(bottom, y)
    if right < left:
        raise SystemExit("the mark rendered empty")
    return left, top, right, bottom


def silhouette(px: int = 320):
    """The mark with its enclosed areas filled: the 16/32px form.

    Cutting the small sizes from the line art is what made them a smudge, and
    a redrawn "simple" mark would no longer be the owner's logo. Filling the
    artwork's own outline keeps the shape and drops only what cannot survive
    at that size.

    Two details keep this honest. The artwork's right hemisphere is drawn open
    at the top - its inside is not enclosed - so the outline is grown by a few
    pixels before the fill, which closes both that opening and the hairline
    gaps anti-aliasing leaves in a traced contour; the fissure between the
    hemispheres is wider than that growth and survives. The result is then
    checked, so a leak is an error rather than a shattered icon.
    """
    cached = _silhouettes.get(px)
    if cached is not None:
        return cached

    from PyQt6.QtGui import QColor, QImage

    mark = render_image(MARK, px)
    ink = [
        [mark.pixelColor(x, y).alpha() > 32 for x in range(px)] for y in range(px)
    ]
    for _ in range(GROWTH):
        grown = [row[:] for row in ink]
        for y in range(px):
            for x in range(px):
                if ink[y][x]:
                    continue
                if (
                    (x and ink[y][x - 1])
                    or (x + 1 < px and ink[y][x + 1])
                    or (y and ink[y - 1][x])
                    or (y + 1 < px and ink[y + 1][x])
                ):
                    grown[y][x] = True
        ink = grown

    outside = [[False] * px for _ in range(px)]
    stack: list[tuple[int, int]] = []

    def push(x: int, y: int) -> None:
        if not ink[y][x] and not outside[y][x]:
            outside[y][x] = True
            stack.append((x, y))

    for i in range(px):
        push(i, 0)
        push(i, px - 1)
        push(0, i)
        push(px - 1, i)
    while stack:
        x, y = stack.pop()
        if x + 1 < px:
            push(x + 1, y)
        if x:
            push(x - 1, y)
        if y + 1 < px:
            push(x, y + 1)
        if y:
            push(x, y - 1)

    solid = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    solid.fill(QColor(0, 0, 0, 0))
    colour = QColor(INK)
    ink_pixels = filled_pixels = 0
    for y in range(px):
        for x in range(px):
            if ink[y][x] or not outside[y][x]:
                solid.setPixelColor(x, y, colour)
                filled_pixels += 1
            if ink[y][x]:
                ink_pixels += 1
    if filled_pixels < ink_pixels * 1.15:
        raise SystemExit(
            "the mark's silhouette came out with holes in it: the flood fill "
            "escaped through a gap in the outline"
        )
    _silhouettes[px] = solid
    return solid


def small_icon(size: int):
    """A plate carrying the solid mark, for the sizes where line art dies."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen

    canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    scale = size / 1024.0
    left = (1024.0 - SMALL_PLATE) / 2.0

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setBrush(QColor("#ffffff"))
    painter.setPen(QPen(QColor(PLATE_GREY), max(1.5 * scale, 1.0)))
    plate = QRectF(left * scale, left * scale, SMALL_PLATE * scale, SMALL_PLATE * scale)
    painter.drawRoundedRect(plate, SMALL_PLATE_RADIUS * scale, SMALL_PLATE_RADIUS * scale)

    shape = silhouette()
    bl, bt, br, bb = ink_bounds(shape)
    ink_w, ink_h = br - bl + 1, bb - bt + 1
    target_w = SMALL_PLATE * SMALL_MARK_SHARE * scale
    target_h = target_w * ink_h / ink_w
    painter.drawImage(
        QRectF(
            (size - target_w) / 2.0,
            (size - target_h) / 2.0,
            target_w,
            target_h,
        ),
        shape,
        QRectF(bl, bt, ink_w, ink_h),
    )
    painter.end()
    return canvas


def write_icon(source, size: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    image = small_icon(size) if source == "small" else render_image(source, size)
    if not image.save(str(out), "PNG"):
        raise SystemExit(f"could not write {out}")


def write_ico(entries: list[tuple[int, object]], out: Path) -> None:
    """Write a PNG-compressed .ico. Qt can read ICO but not write one, and the
    container is a directory of PNGs with a short header."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        payloads: list[tuple[int, bytes]] = []
        for size, source in entries:
            png = Path(tmp) / f"{size}.png"
            write_icon(source, size, png)
            payloads.append((size, png.read_bytes()))

        header = struct.pack("<HHH", 0, 1, len(payloads))
        offset = len(header) + 16 * len(payloads)
        directory = b""
        for size, data in payloads:
            directory += struct.pack(
                "<BBBBHHII",
                0 if size >= 256 else size,
                0 if size >= 256 else size,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
            offset += len(data)
        out.write_bytes(header + directory + b"".join(d for _, d in payloads))


def build_icns(icns_out: Path) -> None:
    """Compose an .iconset and let iconutil pack it (macOS only)."""
    import shutil
    import tempfile

    if shutil.which("iconutil") is None:
        print("iconutil not found: skipping logo.icns (macOS only)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "ByteProof.iconset"
        iconset.mkdir()
        for name, size, source in ICONSET:
            write_icon(source, size, iconset / name)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_out)],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    logo_png = ROOT / "logo" / "logo.png"
    write_icon(DETAIL, 1024, logo_png)
    write_icon(DETAIL, 1024, ROOT / "logo" / "app_icon.png")
    print(f"wrote {logo_png.relative_to(ROOT)} and app_icon.png (1024)")

    write_ico(ICO_SIZES, ROOT / "logo" / "logo.ico")
    print("wrote logo/logo.ico (16, 24, 32, 48 solid; 64+ line art)")

    build_icns(ROOT / "logo" / "logo.icns")
    if (ROOT / "logo" / "logo.icns").exists():
        print("wrote logo/logo.icns (16, 32 solid; 64+ line art)")

    tiles = ROOT / "packaging" / "windows" / "Assets"
    for name, size, source in STORE_TILES:
        write_icon(source, size, tiles / name)
    print(f"wrote {len(STORE_TILES)} store tiles in packaging/windows/Assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
