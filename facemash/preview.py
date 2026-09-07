"""Premium live preview: a native window showing the webcam with MediaPipe
overlays (face box, the active "touch zone", and hand skeletons) plus a clean
status/stats dashboard.

The dashboard image is composited with Pillow and displayed using Tkinter,
which works on both macOS and Windows.
"""

from __future__ import annotations

import io
import platform
from functools import lru_cache
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

import tkinter as tk


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

PAD = 18
CAM_W = 480


@lru_cache(maxsize=16)
def _font(size: int):
    """Load a suitable system font on macOS or Windows."""

    font_paths = [
        # macOS
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",

        # Windows
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    return ImageFont.load_default()


def _text(d, xy, s, size, fill, *, bold=False, anchor="la"):
    d.text(
        xy,
        s,
        font=_font(size),
        fill=fill,
        anchor=anchor,
        stroke_width=1 if bold else 0,
        stroke_fill=fill,
    )


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

    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1],
        radius=radius,
        fill=fill,
    )

    return img


def render_dashboard(
    camera_rgb: Optional[np.ndarray],
    info: dict,
) -> Image.Image:
    """Compose the full preview image."""

    state = info.get("state", "watching")
    accent, pill_text = _STATE_STYLE.get(
        state,
        _STATE_STYLE["watching"],
    )

    cam_h = 270

    if camera_rgb is not None:
        ch, cw = camera_rgb.shape[:2]
        cam_h = max(
            160,
            int(round(CAM_W * ch / cw)),
        )

    header_h = 52
    footer_h = 96

    W = CAM_W + PAD * 2
    H = header_h + cam_h + PAD + footer_h + PAD

    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    # ---- header ------------------------------------------------------------

    _text(
        d,
        (PAD, 16),
        "facemash",
        21,
        TEXT,
        bold=True,
    )

    _text(
        d,
        (PAD + 110, 22),
        "live preview",
        13,
        MUTED,
    )

    d.ellipse(
        [
            W - PAD - 150,
            22,
            W - PAD - 140,
            32,
        ],
        fill=accent,
    )

    _text(
        d,
        (W - PAD - 132, 18),
        info.get("status_text", ""),
        13,
        MUTED,
        anchor="la",
    )

    # ---- camera card -------------------------------------------------------

    cam_x = PAD
    cam_y = header_h

    card = _rounded(
        (CAM_W, cam_h),
        14,
        CARD,
    )

    canvas.paste(
        card,
        (cam_x, cam_y),
        card,
    )

    if camera_rgb is not None:

        cam = Image.fromarray(
            camera_rgb,
            "RGB",
        ).resize((CAM_W, cam_h))

        mask = Image.new(
            "L",
            (CAM_W, cam_h),
            0,
        )

        ImageDraw.Draw(mask).rounded_rectangle(
            [
                0,
                0,
                CAM_W - 1,
                cam_h - 1,
            ],
            radius=14,
            fill=255,
        )

        canvas.paste(
            cam,
            (cam_x, cam_y),
            mask,
        )

        # ---- status pill ---------------------------------------------------

        pill_w = 14 + len(pill_text) * 8

        pill = _rounded(
            (pill_w, 26),
            13,
            accent,
        )

        canvas.paste(
            pill,
            (
                cam_x + 12,
                cam_y + 12,
            ),
            pill,
        )

        _text(
            d,
            (
                cam_x + 12 + pill_w / 2,
                cam_y + 12 + 13,
            ),
            pill_text,
            12,
            (10, 12, 16),
            bold=True,
            anchor="mm",
        )

    else:

        msg = info.get(
            "status_text",
            "Camera off",
        )

        _text(
            d,
            (
                cam_x + CAM_W / 2,
                cam_y + cam_h / 2 - 16,
            ),
            "◉",
            30,
            MUTED,
            anchor="mm",
        )

        _text(
            d,
            (
                cam_x + CAM_W / 2,
                cam_y + cam_h / 2 + 14,
            ),
            msg,
            14,
            MUTED,
            anchor="mm",
        )

    # ---- footer stat chips -------------------------------------------------

    chips = [
        (
            "TOUCHES TODAY",
            str(info.get("today", 0)),
            accent,
        ),
        (
            "SENSITIVITY",
            str(
                info.get(
                    "sensitivity",
                    "—",
                )
            ).title(),
            TEXT,
        ),
        (
            "HOLD",
            f"{info.get('hold', 0):g}s",
            TEXT,
        ),
        (
            "FPS",
            f"{info.get('fps', 0):.0f}",
            TEXT,
        ),
    ]

    fy = header_h + cam_h + PAD
    gap = 10

    chip_w = (
        CAM_W - gap * (len(chips) - 1)
    ) / len(chips)

    for i, (
        label,
        value,
        value_color,
    ) in enumerate(chips):

        cx = PAD + i * (
            chip_w + gap
        )

        chip = _rounded(
            (
                int(chip_w),
                footer_h,
            ),
            12,
            CARD2,
        )

        canvas.paste(
            chip,
            (
                int(cx),
                fy,
            ),
            chip,
        )

        _text(
            d,
            (
                cx + 14,
                fy + 16,
            ),
            label,
            10,
            MUTED,
        )

        _text(
            d,
            (
                cx + 14,
                fy + 38,
            ),
            value,
            26,
            value_color,
            bold=True,
        )

    return canvas


class PreviewWindow:
    """Cross-platform preview window using Tkinter."""

    def __init__(self) -> None:

        self._root = None
        self._label = None
        self._photo = None
        self._visible = False

    def _ensure(self):

        if self._root is not None:
            return

        self._root = tk.Tk()

        self._root.title(
            "facemash — preview"
        )

        self._root.configure(
            bg="#0d1016"
        )

        self._root.protocol(
            "WM_DELETE_WINDOW",
            self.hide,
        )

        self._label = tk.Label(
            self._root,
            bg="#0d1016",
            bd=0,
        )

        self._label.pack()

        self._root.withdraw()

    def _set_image(
        self,
        img: Image.Image,
    ):

        self._photo = ImageTk.PhotoImage(
            img
        )

        self._label.configure(
            image=self._photo
        )

    def show(
        self,
        img: Image.Image,
    ) -> None:

        self._ensure()

        self._set_image(img)

        self._root.deiconify()

        self._root.lift()

        self._visible = True

        self._root.update_idletasks()

        self._root.update()

    def update(
        self,
        img: Image.Image,
    ) -> None:

        if (
            self._root is not None
            and self._visible
        ):

            self._set_image(img)

            try:
                self._root.update_idletasks()
                self._root.update()
            except tk.TclError:
                self._visible = False

    def is_visible(self) -> bool:

        if self._root is None:
            return False

        try:
            return (
                self._visible
                and self._root.winfo_viewable()
            )
        except tk.TclError:
            return False

    def hide(self) -> None:

        if self._root is not None:

            try:
                self._root.withdraw()
            except tk.TclError:
                pass

        self._visible = False
