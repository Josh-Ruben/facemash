"""Generate facelint's icons with Pillow.

Produces:
  * Menu-bar state icons (40x40, rendered at 20pt -> crisp on Retina):
      ok.png      black template, neutral face         (watching)
      alert.png   red, "uh-oh" face                    (hand on face)
      paused.png  black template, pause bars           (manually paused)
      idle.png    black template, sleeping face        (camera off / idle)
      error.png   black template, exclamation          (camera problem)
  * A 1024px app icon (app_icon.png) and an .icns bundle for a real .app.

Template icons are pure black on transparency so macOS tints them white in
dark menu bars automatically. The alert icon is colored (non-template) so it
visibly stands out.

Run:  python -m facelint.resources.make_icons
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ICONS_DIR = Path(__file__).parent / "icons"
SS = 4  # supersampling factor for anti-aliasing

BLACK = (0, 0, 0, 255)
RED = (235, 64, 52, 255)
TEAL = (20, 184, 166, 255)
WHITE = (255, 255, 255, 255)


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _finish(img: Image.Image, size: int, path: Path) -> None:
    img = img.resize((size, size), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _draw_face(d, S, color, *, mouth="smile", eyes="open", stroke=None):
    stroke = stroke or int(S * 0.075)
    m = int(S * 0.13)
    # head
    d.ellipse([m, m, S - m, S - m], outline=color, width=stroke)
    # eyes
    ey = int(S * 0.42)
    ex1, ex2 = int(S * 0.37), int(S * 0.63)
    if eyes == "open":
        r = int(S * 0.055)
        for ex in (ex1, ex2):
            d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color)
    else:  # closed -> short horizontal lines
        w = int(S * 0.08)
        for ex in (ex1, ex2):
            d.line([ex - w, ey, ex + w, ey], fill=color, width=int(stroke * 0.9))
    # mouth
    my = int(S * 0.63)
    mw = int(S * 0.16)
    if mouth == "smile":
        d.arc([int(S * 0.5) - mw, my - mw, int(S * 0.5) + mw, my + mw],
              start=25, end=155, fill=color, width=stroke)
    elif mouth == "flat":
        d.line([int(S * 0.5) - mw, my, int(S * 0.5) + mw, my], fill=color, width=stroke)
    elif mouth == "o":
        r = int(S * 0.07)
        cx = int(S * 0.5)
        d.ellipse([cx - r, my - r, cx + r, my + r], outline=color, width=int(stroke * 0.9))


def make_menu_icons(size: int = 40) -> None:
    S = size * SS

    img, d = _canvas(size)
    _draw_face(d, S, BLACK, mouth="smile", eyes="open")
    _finish(img, size, ICONS_DIR / "ok.png")

    img, d = _canvas(size)
    _draw_face(d, S, RED, mouth="o", eyes="open")
    _finish(img, size, ICONS_DIR / "alert.png")

    img, d = _canvas(size)
    _draw_face(d, S, BLACK, mouth="flat", eyes="closed")
    _finish(img, size, ICONS_DIR / "idle.png")

    # paused: two rounded bars
    img, d = _canvas(size)
    bw = int(S * 0.13)
    gap = int(S * 0.12)
    top, bot = int(S * 0.26), int(S * 0.74)
    cx = S // 2
    d.rounded_rectangle([cx - gap - bw, top, cx - gap, bot], radius=bw // 2, fill=BLACK)
    d.rounded_rectangle([cx + gap, top, cx + gap + bw, bot], radius=bw // 2, fill=BLACK)
    _finish(img, size, ICONS_DIR / "paused.png")

    # error: exclamation mark
    img, d = _canvas(size)
    bw = int(S * 0.12)
    cx = S // 2
    d.rounded_rectangle([cx - bw // 2, int(S * 0.22), cx + bw // 2, int(S * 0.60)], radius=bw // 2, fill=BLACK)
    r = int(S * 0.075)
    dy = int(S * 0.74)
    d.ellipse([cx - r, dy - r, cx + r, dy + r], fill=BLACK)
    _finish(img, size, ICONS_DIR / "error.png")


def make_app_icon() -> None:
    """A 1024px rounded-square app icon, plus an .icns for a real .app bundle."""
    S = 1024 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded-square background (macOS "squircle"-ish)
    pad = int(S * 0.08)
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.22), fill=TEAL)
    # white face centered, scaled within the tile
    face = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(face)
    _draw_face(fd, S, WHITE, mouth="smile", eyes="open", stroke=int(S * 0.055))
    # nudge face slightly smaller/centered
    face = face.resize((int(S * 0.74), int(S * 0.74)), Image.LANCZOS)
    img.alpha_composite(face, (int(S * 0.13), int(S * 0.13)))

    out = img.resize((1024, 1024), Image.LANCZOS)
    out.save(ICONS_DIR / "app_icon.png")

    # Build an .icns via iconutil if available (best quality / real .app icon).
    try:
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "facelint.iconset"
            iconset.mkdir()
            for px, names in {
                16: ["icon_16x16.png"], 32: ["icon_16x16@2x.png", "icon_32x32.png"],
                64: ["icon_32x32@2x.png"], 128: ["icon_128x128.png"],
                256: ["icon_128x128@2x.png", "icon_256x256.png"],
                512: ["icon_256x256@2x.png", "icon_512x512.png"],
                1024: ["icon_512x512@2x.png"],
            }.items():
                resized = out.resize((px, px), Image.LANCZOS)
                for n in names:
                    resized.save(iconset / n)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICONS_DIR / "facelint.icns")],
                           check=True)
    except Exception as exc:  # pragma: no cover
        print("iconutil unavailable, skipping .icns:", exc)


if __name__ == "__main__":
    make_menu_icons()
    make_app_icon()
    print("icons written to", ICONS_DIR)
