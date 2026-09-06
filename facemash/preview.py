"""Premium live preview: a native window showing the webcam with MediaPipe
overlays (face box, the active "touch zone", and hand skeletons) plus a clean
status/stats dashboard.

The dashboard image is composited with Pillow so we get nice typography; it is
then handed to a small AppKit ``NSWindow`` for display on the main thread.
"""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- palette -----------------------------------------------------------------
BG = (13, 16, 22)
CARD = (24, 29, 38)
CARD2 = (31, 37, 48)
TEXT = (237, 240, 245)
MUTED = (150, 160, 175)
TEAL = (20, 184, 166)
RED = (235, 80, 68)
GREEN = (94, 214, 128)
AMBER = (245, 184, 84)

_FONT_PATH = "/System/Library/Fonts/SFNS.ttf"
_FONT_FALLBACK = "/System/Library/Fonts/Helvetica.ttc"

PAD = 18
CAM_W = 480


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (_FONT_PATH, _FONT_FALLBACK):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text(d, xy, s, size, fill, *, bold=False, anchor="la"):
    d.text(xy, s, font=_font(size), fill=fill, anchor=anchor,
           stroke_width=1 if bold else 0, stroke_fill=fill)


_STATE_STYLE = {
    "touching": (RED, "HANDS ON FACE"),
    "watching": (TEAL, "WATCHING"),
    "noface": (MUTED, "NO FACE"),
    "paused": (AMBER, "PAUSED"),
    "idle": (AMBER, "IDLE — CAMERA OFF"),
    "error": (RED, "CAMERA ERROR"),
}


def _rounded(size, radius, fill):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=fill)
    return img


def render_dashboard(camera_rgb: Optional[np.ndarray], info: dict) -> Image.Image:
    """Compose the full preview image. ``info`` carries state + stats."""
    state = info.get("state", "watching")
    accent, pill_text = _STATE_STYLE.get(state, _STATE_STYLE["watching"])

    cam_h = 270
    if camera_rgb is not None:
        ch, cw = camera_rgb.shape[:2]
        cam_h = max(160, int(round(CAM_W * ch / cw)))

    header_h = 52
    footer_h = 96
    W = CAM_W + PAD * 2
    H = header_h + cam_h + PAD + footer_h + PAD

    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    # ---- header ----
    _text(d, (PAD, 16), "facelint", 21, TEXT, bold=True)
    _text(d, (PAD + 92, 22), "live preview", 13, MUTED)
    # accent dot + status on the right
    d.ellipse([W - PAD - 150, 22, W - PAD - 140, 32], fill=accent)
    _text(d, (W - PAD - 132, 18), info.get("status_text", ""), 13, MUTED, anchor="la")

    # ---- camera card ----
    cam_x, cam_y = PAD, header_h
    card = _rounded((CAM_W, cam_h), 14, CARD)
    canvas.paste(card, (cam_x, cam_y), card)

    if camera_rgb is not None:
        cam = Image.fromarray(camera_rgb, "RGB").resize((CAM_W, cam_h))
        mask = Image.new("L", (CAM_W, cam_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, CAM_W - 1, cam_h - 1], radius=14, fill=255)
        canvas.paste(cam, (cam_x, cam_y), mask)
        # status pill (top-left, inside the camera)
        pill_w = 14 + len(pill_text) * 8
        pill = _rounded((pill_w, 26), 13, accent)
        canvas.paste(pill, (cam_x + 12, cam_y + 12), pill)
        _text(d, (cam_x + 12 + pill_w / 2, cam_y + 12 + 13), pill_text, 12, (10, 12, 16), bold=True, anchor="mm")
    else:
        msg = info.get("status_text", "Camera off")
        _text(d, (cam_x + CAM_W / 2, cam_y + cam_h / 2 - 16), "◉", 30, MUTED, anchor="mm")
        _text(d, (cam_x + CAM_W / 2, cam_y + cam_h / 2 + 14), msg, 14, MUTED, anchor="mm")

    # ---- footer stat chips ----
    chips = [
        ("TOUCHES TODAY", str(info.get("today", 0)), accent),
        ("SENSITIVITY", str(info.get("sensitivity", "—")).title(), TEXT),
        ("HOLD", f"{info.get('hold', 0):g}s", TEXT),
        ("FPS", f"{info.get('fps', 0):.0f}", TEXT),
    ]
    fy = header_h + cam_h + PAD
    gap = 10
    cw = (CAM_W - gap * (len(chips) - 1)) / len(chips)
    for i, (label, value, vcolor) in enumerate(chips):
        cx = PAD + i * (cw + gap)
        chip = _rounded((int(cw), footer_h), 12, CARD2)
        canvas.paste(chip, (int(cx), fy), chip)
        _text(d, (cx + 14, fy + 16), label, 10, MUTED)
        _text(d, (cx + 14, fy + 38), value, 26, vcolor, bold=True)

    return canvas


def pil_to_nsimage(img: Image.Image):
    from AppKit import NSImage
    from Foundation import NSData

    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    return NSImage.alloc().initWithData_(data)


class PreviewWindow:
    """A lightweight AppKit window that displays the dashboard image."""

    def __init__(self) -> None:
        self._window = None
        self._imageview = None
        self._size = None

    def _ensure(self, w: int, h: int) -> None:
        from AppKit import (
            NSWindow, NSImageView, NSMakeRect, NSBackingStoreBuffered,
            NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable, NSImageScaleAxesIndependently,
            NSColor,
        )

        if self._window is not None:
            if self._size != (w, h):
                from AppKit import NSSize
                self._window.setContentSize_(NSSize(w, h))
                self._imageview.setFrame_(NSMakeRect(0, 0, w, h))
                self._size = (w, h)
            return

        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
        win.setTitle_("facelint — preview")
        win.setReleasedWhenClosed_(False)
        win.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.06, 0.086, 1.0))
        iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        iv.setImageScaling_(NSImageScaleAxesIndependently)
        win.setContentView_(iv)
        win.center()
        self._window, self._imageview, self._size = win, iv, (w, h)

    def show(self, img: Image.Image) -> None:
        self._ensure(img.width, img.height)
        self._imageview.setImage_(pil_to_nsimage(img))
        self._window.makeKeyAndOrderFront_(None)

    def update(self, img: Image.Image) -> None:
        if self._window is not None and self._window.isVisible():
            self._ensure(img.width, img.height)
            self._imageview.setImage_(pil_to_nsimage(img))

    def is_visible(self) -> bool:
        return self._window is not None and bool(self._window.isVisible())

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)
