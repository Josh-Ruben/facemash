"""Download & cache the MediaPipe Tasks model bundles.

The arm64 MediaPipe wheel ships only the Tasks runtime, not the model assets, so
we fetch the two small models we need from Google's public model storage on
first run and cache them under Application Support.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from facemash.config import CONFIG_DIR

MODELS_DIR = CONFIG_DIR / "models"

# (filename, url, minimum plausible size in bytes for a sanity check)
_MODELS = {
    "face": (
        "blaze_face_short_range.tflite",
        "https://storage.googleapis.com/mediapipe-models/face_detector/"
        "blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
        100_000,
    ),
    "hand": (
        "hand_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
        "hand_landmarker/float16/1/hand_landmarker.task",
        1_000_000,
    ),
}


def _fetch(name: str) -> Path:
    filename, url, min_size = _MODELS[name]
    dest = MODELS_DIR / filename
    if dest.exists() and dest.stat().st_size >= min_size:
        return dest
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
        fh.write(resp.read())
    if tmp.stat().st_size < min_size:
        tmp.unlink(missing_ok=True)
        raise OSError(f"Downloaded {filename} looks too small; download may have failed.")
    tmp.replace(dest)
    return dest


def ensure_models() -> dict[str, Path]:
    """Return paths to the face and hand models, downloading them if needed."""
    return {name: _fetch(name) for name in _MODELS}


def models_present() -> bool:
    for filename, _url, min_size in _MODELS.values():
        p = MODELS_DIR / filename
        if not (p.exists() and p.stat().st_size >= min_size):
            return False
    return True
