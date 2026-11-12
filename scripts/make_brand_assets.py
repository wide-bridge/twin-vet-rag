"""Web assets from the TwinVet app logo (the one registered on Kakao Map, 2026-11-12) and the site QR code.

    python -m scripts.make_brand_assets

Inputs (originals, not modified): brand/twinvet-app-icon-original.png (symbol only).
Outputs in frontend/public/brand/: twinvet-icon.png (512, transparent outside the symbol), favicon-64.png,
apple-touch-icon.png (180, white), qr-twinvet.png (https://twinvet.pawcheck.ai). Needs Pillow; QR needs segno
(dev-only, `pip install segno==1.6.6`).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "brand" / "twinvet-app-icon-original.png"
OUT = ROOT / "frontend" / "public" / "brand"
SITE = "https://twinvet.pawcheck.ai"
NAVY = "#102858"


def symbol() -> Image.Image:
    im = Image.open(SRC).convert("RGB")
    # background -> transparent by flood fill from the corners (the white dog face inside stays white)
    marker = (255, 0, 255)
    for xy in [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]:
        ImageDraw.floodfill(im, xy, marker, thresh=40)
    rgba = im.convert("RGBA")
    px = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            if px[x, y][:3] == marker:
                px[x, y] = (255, 255, 255, 0)
    bbox = rgba.getbbox()
    sym = rgba.crop(bbox)
    side = max(sym.size)
    pad = int(side * 0.04)
    canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (255, 255, 255, 0))
    canvas.paste(sym, ((canvas.width - sym.width) // 2, (canvas.height - sym.height) // 2), sym)
    return canvas


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    s = symbol()
    s.resize((512, 512), Image.LANCZOS).save(OUT / "twinvet-icon.png", optimize=True)
    s.resize((64, 64), Image.LANCZOS).save(OUT / "favicon-64.png", optimize=True)
    white = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
    small = s.resize((150, 150), Image.LANCZOS)
    white.paste(small, (15, 15), small)
    white.convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    try:
        import segno
    except ImportError:
        print("segno not installed: QR skipped")
    else:
        segno.make(SITE, error="m").save(OUT / "qr-twinvet.png", scale=8, border=2, dark=NAVY, light="#ffffff")
    for f in sorted(OUT.glob("*.png")):
        print(f.name, Image.open(f).size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
