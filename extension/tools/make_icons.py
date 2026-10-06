"""Draw the extension icons (a lens over three dots) at 16, 32, 48 and 128 px. Run once; the PNGs are committed."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "icons"
INK, DOT, BG = (44, 93, 143, 255), (214, 92, 60, 255), (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    s = 8  # supersampling
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = n * 0.30
    cx, cy = n * 0.42, n * 0.42
    d.line([(cx + r * 0.72, cy + r * 0.72), (n * 0.90, n * 0.90)], fill=INK, width=int(n * 0.13))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BG, outline=INK, width=int(n * 0.09))
    dot = n * 0.055
    for i, dx in enumerate((-0.15, 0.0, 0.15)):
        x, y = cx + dx * n, cy + (0.05 if i != 1 else -0.05) * n
        d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=DOT if i == 1 else INK)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for size in (16, 32, 48, 128):
        draw(size).save(OUT / f"icon-{size}.png", optimize=True)
        print("icons/icon-%d.png" % size)
