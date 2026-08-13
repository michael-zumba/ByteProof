"""Generate Microsoft Store display art for ByteProof from the existing logo.

Outputs (all PNG, well under the 50 MB Store limit):
  packaging/windows/listing/PosterArt_9x16.png      1296 x 2304
  packaging/windows/listing/BoxArt_1x1.png          1080 x 1080
  packaging/windows/listing/SuperHeroArt_16x9.png   1920 x 1080 (no text)

Re-run whenever the logo or tagline changes:
  python3 packaging/windows/make_store_art.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent
LOGO = ROOT / "logo" / "logo.png"
OUT_DIR = ROOT / "packaging" / "windows" / "listing"

# ByteProof brand palette (from assets/hero-banner.svg and byteproof.css)
GREEN_900 = (14, 36, 25)      # #0E2419
GREEN_700 = (26, 58, 42)      # #1A3A2A
GREEN_600 = (31, 83, 53)      # #1F5335
GREEN_300 = (121, 168, 138)   # #79A88A
GREEN_200 = (169, 199, 179)   # #A9C7B3
CREAM = (250, 249, 243)       # #FAF9F3

FONT = "/System/Library/Fonts/SFNS.ttf"


def vgradient(width: int, height: int, stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    img = Image.new("RGB", (1, height))
    px = img.load()
    for y in range(height):
        t = y / (height - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                f = (t - p0) / (p1 - p0)
                px[0, y] = tuple(round(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                break
    return img.resize((width, height), Image.Resampling.BILINEAR)


def radial_glow(size: tuple[int, int], center: tuple[int, int], radius: int, alpha: int) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.ellipse(
        [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
        fill=(*GREEN_300, alpha),
    )
    return overlay.filter(ImageFilter.GaussianBlur(radius * 0.28))


def dot_pattern(size: tuple[int, int], spacing: int, radius: int, alpha: int) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(spacing // 2, size[1], spacing):
        for x in range(spacing // 2, size[0], spacing):
            d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(*CREAM, alpha))
    return overlay


def ring_overlay(size: tuple[int, int], rings: list[tuple[int, int, int, int]]) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for cx, cy, r, alpha in rings:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*CREAM, alpha), width=2)
    return overlay


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def centered_text(d: ImageDraw.ImageDraw, center: tuple[int, int], text: str, fnt: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    bbox = d.textbbox((0, 0), text, font=fnt)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    d.text((center[0] - w / 2 - bbox[0], center[1] - h / 2 - bbox[1]), text, font=fnt, fill=fill)


def pill(size: tuple[int, int], center: tuple[int, int], width: int, height: int, text: str, fnt: ImageFont.FreeTypeFont) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x0, y0 = center[0] - width // 2, center[1] - height // 2
    d.rounded_rectangle(
        [x0, y0, x0 + width, y0 + height],
        radius=height // 2,
        fill=(*CREAM, 16),
        outline=(*CREAM, 90),
        width=2,
    )
    centered_text(d, center, text, fnt, CREAM)
    return overlay


def compose(width: int, height: int, logo_size: int, logo_pos: tuple[int, int], text: bool) -> Image.Image:
    base = vgradient(width, height, [(0.0, GREEN_900), (0.55, GREEN_700), (1.0, GREEN_600)]).convert("RGBA")
    base = Image.alpha_composite(base, radial_glow((width, height), (width // 2, int(height * 0.36)), int(width * 0.62), 74))
    base = Image.alpha_composite(base, dot_pattern((width, height), max(52, width // 28), max(3, width // 420), 26))
    base = Image.alpha_composite(base, ring_overlay((width, height), [
        (int(width * 0.92), int(height * 0.07), int(width * 0.17), 20),
        (int(width * 0.92), int(height * 0.07), int(width * 0.23), 12),
        (int(width * 0.07), int(height * 0.93), int(width * 0.14), 18),
        (int(width * 0.07), int(height * 0.93), int(width * 0.20), 10),
    ]))

    logo = Image.open(LOGO).convert("RGBA").resize((logo_size, logo_size), Image.Resampling.LANCZOS)
    base.alpha_composite(logo, logo_pos)

    d = ImageDraw.Draw(base)
    if text:
        title_fnt = font(int(width * 0.125))
        tag_fnt = font(int(width * 0.032))
        title_cy = logo_pos[1] + logo_size + int(title_fnt.size * 0.95)
        centered_text(d, (width // 2, title_cy), "ByteProof", title_fnt, CREAM)
        tagline = "AI proofreading for Microsoft Word"
        tagline2 = "tracked changes, comments & polish anywhere"
        tag1_cy = title_cy + int(tag_fnt.size * 1.4)
        tag2_cy = tag1_cy + int(tag_fnt.size * 1.25)
        centered_text(d, (width // 2, tag1_cy), tagline, tag_fnt, GREEN_200)
        centered_text(d, (width // 2, tag2_cy), tagline2, tag_fnt, GREEN_200)
    return base


def poster() -> Image.Image:
    width, height = 1296, 2304
    logo_size = 420
    logo_x = (width - logo_size) // 2
    logo_y = 430
    img = compose(width, height, logo_size, (logo_x, logo_y), text=True)
    d = ImageDraw.Draw(img)
    pill_fnt = font(34)
    labels = ["Private local AI", "Word tracked changes", "Bring your own key"]
    pw, ph = 360, 84
    y = 1240
    for label in labels:
        img = Image.alpha_composite(img, pill((width, height), (width // 2, y), pw, ph, label, pill_fnt))
        y += ph + 22
    del d
    return img.convert("RGB")


def box_art() -> Image.Image:
    width, height = 1080, 1080
    logo_size = 270
    logo_x = (width - logo_size) // 2
    logo_y = 190
    return compose(width, height, logo_size, (logo_x, logo_y), text=True).convert("RGB")


def hero_art() -> Image.Image:
    width, height = 1920, 1080
    logo_size = 380
    logo_x = (width - logo_size) // 2
    logo_y = (height - logo_size) // 2
    return compose(width, height, logo_size, (logo_x, logo_y), text=False).convert("RGB")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("PosterArt_9x16.png", poster()),
        ("BoxArt_1x1.png", box_art()),
        ("SuperHeroArt_16x9.png", hero_art()),
    ]
    for name, img in jobs:
        path = OUT_DIR / name
        img.save(path, "PNG")
        print(f"{path.name}: {img.size[0]}x{img.size[1]}  {path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
