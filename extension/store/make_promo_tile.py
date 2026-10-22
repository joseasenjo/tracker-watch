"""The Chrome Web Store small promotional tile (440x280), drawn from the extension's own icon and name.

    python extension/store/make_promo_tile.py

No photographs and no third-party artwork: the icon of the extension, the name and one plain sentence, on the same dark
blue as the website. Fonts are looked up on the computer (Georgia and Arial on Windows); if none is found the tile is not
written, so a tile with a wrong font is never produced by accident.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ICON = HERE.parent / "icons" / "icon-128.png"
OUT = HERE / "screenshots" / "promo-tile-440x280.png"
SIZE = (440, 280)
INK, SOFT, ACCENT = (230, 235, 243), (159, 176, 200), (110, 160, 255)
FONT_DIRS = ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts")


def font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for folder in FONT_DIRS:
        for name in names:
            path = Path(folder) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    raise SystemExit("no suitable font found: the tile was not written")


def main() -> int:
    title = font(("georgiab.ttf", "DejaVuSerif-Bold.ttf"), 30)
    text = font(("arial.ttf", "DejaVuSans.ttf"), 19)
    small = font(("arial.ttf", "DejaVuSans.ttf"), 15)
    image = Image.new("RGB", SIZE, (11, 18, 32))
    draw = ImageDraw.Draw(image)
    for y in range(SIZE[1]):  # a quiet vertical gradient, from the website's two dark blues
        t = y / SIZE[1]
        draw.line([(0, y), (SIZE[0], y)], fill=(int(11 + 9 * t), int(18 + 15 * t), int(32 + 29 * t)))
    plate = Image.new("RGBA", (112, 112), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle((0, 0, 111, 111), radius=22, fill=(255, 255, 255, 255))
    icon = Image.open(ICON).convert("RGBA").resize((92, 92))
    plate.alpha_composite(icon, (10, 10))
    image.paste(plate, (36, 40), plate)
    draw.text((166, 52), "Tracker Watch", font=title, fill=INK)
    draw.text((166, 90), "Lens", font=title, fill=ACCENT)
    draw.line([(36, 178), (404, 178)], fill=(38, 51, 74), width=1)
    draw.text((36, 194), "See who tracks you on the page", font=text, fill=INK)
    draw.text((36, 220), "you are reading, before you click.", font=text, fill=INK)
    draw.text((36, 250), "Free. No account. It sends nothing.", font=small, fill=SOFT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"{OUT} {image.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
