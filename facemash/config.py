"""Persistent user configuration and daily stats for facelint.

Settings live in ``~/Library/Application Support/facelint/config.json`` so they
survive restarts and reinstalls.
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "facelint"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Sensitivity presets are interpreted by the detector. ``low`` only reacts to
# fingertips clearly inside the face; ``high`` reacts to any part of the hand
# entering an enlarged region around the face.
SENSITIVITIES = ("low", "medium", "high")
HOLD_CHOICES = (0.8, 1.2, 2.0, 3.0)
NUDGE_INTERVAL_CHOICES = (10, 30, 60, 120, 300)
# 0 == always on (never auto-pause on idle)
IDLE_TIMEOUT_CHOICES = (0, 30, 60, 120, 300)
CUE_CHOICES = ("sound", "voice", "both")

DEFAULTS: dict[str, Any] = {
    "monitoring": True,
    "sensitivity": "medium",
    "hold_seconds": 1.2,
    # Minimum gap between two nudges, so it never keeps nagging you.
    "nudge_interval_seconds": 30,
    # Ignore touches below the mouth line (chin / jaw / beard, common thinking
    # poses) so they don't trigger false alerts.
    "ignore_chin": True,
    # Turn the camera off when you've been idle this many seconds (0 = never).
    "pause_when_idle": True,
    "idle_timeout_seconds": 90,
    "cue": "sound",
    "sound": "/System/Library/Sounds/Funk.aiff",
    "camera_index": 0,
    "stats": {"date": "", "today": 0, "total": 0},
}


def _today() -> str:
    return _dt.date.today().isoformat()


class Config:
    """Thread-safe wrapper around the JSON config file."""

    def __init__(self, path: Path = CONFIG_PATH):
        self._path = path
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self._data["stats"] = dict(DEFAULTS["stats"])
        self.load()

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        existed = self._path.exists()
        with self._lock:
            try:
                raw = json.loads(self._path.read_text())
            except (OSError, ValueError):
                raw = {}
            for key, default in DEFAULTS.items():
                if key == "stats":
                    continue
                self._data[key] = raw.get(key, default)
            stats = raw.get("stats", {}) or {}
            self._data["stats"] = {
                "date": stats.get("date", ""),
                "today": int(stats.get("today", 0)),
                "total": int(stats.get("total", 0)),
            }
            self._roll_day_locked()
        if not existed:
            # Materialize the file on first run so users can find and edit it.
            self.save()

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self._path)

    # -- generic accessors --------------------------------------------------
    def get(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
        self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    # -- stats --------------------------------------------------------------
    def _roll_day_locked(self) -> None:
        today = _today()
        if self._data["stats"].get("date") != today:
            self._data["stats"]["date"] = today
            self._data["stats"]["today"] = 0

    def record_touch(self) -> tuple[int, int]:
        """Increment counters, return ``(today, total)``."""
        with self._lock:
            self._roll_day_locked()
            self._data["stats"]["today"] += 1
            self._data["stats"]["total"] += 1
            today = self._data["stats"]["today"]
            total = self._data["stats"]["total"]
        self.save()
        return today, total

    def reset_today(self) -> None:
        with self._lock:
            self._data["stats"]["date"] = _today()
            self._data["stats"]["today"] = 0
        self.save()

    @property
    def today_count(self) -> int:
        with self._lock:
            self._roll_day_locked()
            return self._data["stats"]["today"]

    @property
    def total_count(self) -> int:
        with self._lock:
            return self._data["stats"]["total"]
