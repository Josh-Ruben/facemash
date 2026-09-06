"""facemash menu-bar application.

The rumps event loop owns the main thread; the camera/MediaPipe work happens in
``FaceTouchDetector``'s background thread. Repeating ``rumps.Timer``s poll the
detector state and perform all UI updates (icon, menu labels, notifications and
the live preview window) on the main thread.
"""

from __future__ import annotations

import os
from pathlib import Path

import rumps

from facemash import __version__
from facemash.config import (
    CUE_CHOICES,
    HOLD_CHOICES,
    IDLE_TIMEOUT_CHOICES,
    NUDGE_INTERVAL_CHOICES,
    SENSITIVITIES,
    Config,
)
from facemash.detector import FaceTouchDetector
from facemash.preview import PreviewWindow, render_dashboard

ICONS = Path(__file__).parent / "resources" / "icons"
APP_ICON = str(ICONS / "app_icon.png")
# state -> (icon filename, template?)  template icons adapt to light/dark menu bar
STATE_ICONS = {
    "ok": ("ok.png", True),
    "alert": ("alert.png", False),  # colored, stands out
    "paused": ("paused.png", True),
    "idle": ("idle.png", True),
    "error": ("error.png", True),
}

UPDATE_INTERVAL = 0.2   # menu/icon refresh
PREVIEW_INTERVAL = 0.07  # ~14 fps preview refresh

SENSITIVITY_LABELS = {
    "low": "Low — only clear touches",
    "medium": "Medium — balanced",
    "high": "High — even light touches",
}
CUE_LABELS = {"sound": "Sound", "voice": "Voice", "both": "Sound + Voice"}


def _secs_label(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs} seconds"
    if secs == 60:
        return "1 minute"
    return f"{secs // 60} minutes"


def _header(text: str) -> rumps.MenuItem:
    item = rumps.MenuItem(text)
    item.set_callback(None)
    return item


class FacemashApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("facemash", quit_button=None)
        self.config = Config()
        self.detector = FaceTouchDetector(get_setting=self.config.get)
        self._icon_state = None
        self._set_state_icon("ok")
        self._preview_win = None
        self._auto_preview_done = False

        # --- info rows --------------------------------------------------
        self.status_item = rumps.MenuItem("Starting…")
        self.status_item.set_callback(None)
        self.count_item = rumps.MenuItem("Touches today: 0")
        self.count_item.set_callback(None)
        self.preview_item = rumps.MenuItem("Show camera preview", callback=self.toggle_preview)
        self.pause_item = rumps.MenuItem("Pause monitoring", callback=self.toggle_pause)

        # --- submenus ---------------------------------------------------
        self._sensitivity_items = {}
        sensitivity_menu = rumps.MenuItem("Sensitivity")
        for level in SENSITIVITIES:
            item = rumps.MenuItem(SENSITIVITY_LABELS[level], callback=self._cb("sensitivity", level))
            self._sensitivity_items[level] = item
            sensitivity_menu.add(item)

        self._hold_items = {}
        hold_menu = rumps.MenuItem("Hold time before alert")
        for secs in HOLD_CHOICES:
            item = rumps.MenuItem(f"{secs:g} seconds", callback=self._cb("hold_seconds", secs))
            self._hold_items[secs] = item
            hold_menu.add(item)

        self._nudge_items = {}
        nudge_menu = rumps.MenuItem("Time between nudges")
        for secs in NUDGE_INTERVAL_CHOICES:
            item = rumps.MenuItem(_secs_label(secs), callback=self._cb("nudge_interval_seconds", secs))
            self._nudge_items[secs] = item
            nudge_menu.add(item)

        self.chin_item = rumps.MenuItem("Ignore chin & beard area", callback=self.toggle_chin)

        self._idle_items = {}
        idle_menu = rumps.MenuItem("Camera when idle")
        for secs in IDLE_TIMEOUT_CHOICES:
            label = "Always on" if secs == 0 else f"Off after {_secs_label(secs)}"
            item = rumps.MenuItem(label, callback=self._cb_idle(secs))
            self._idle_items[secs] = item
            idle_menu.add(item)

        self._cue_items = {}
        cue_menu = rumps.MenuItem("Alert cue")
        for cue in CUE_CHOICES:
            item = rumps.MenuItem(CUE_LABELS[cue], callback=self._cb_cue(cue))
            self._cue_items[cue] = item
            cue_menu.add(item)

        self.menu = [
            self.status_item,
            self.count_item,
            None,
            self.preview_item,
            self.pause_item,
            None,
            _header("Detection"),
            sensitivity_menu,
            hold_menu,
            self.chin_item,
            None,
            _header("Alerts"),
            nudge_menu,
            cue_menu,
            None,
            _header("Privacy & power"),
            idle_menu,
            None,
            rumps.MenuItem("Reset today's count", callback=self.reset_count),
            rumps.MenuItem("About facemash", callback=self.about),
            None,
            rumps.MenuItem("Quit facemash", callback=self.quit_app),
        ]

        self._sync_checks()

        self.detector.set_paused(not self.config.get("monitoring"))
        self.detector.start()
        self._timer = rumps.Timer(self.update_ui, UPDATE_INTERVAL)
        self._timer.start()
        self._preview_timer = rumps.Timer(self.update_preview, PREVIEW_INTERVAL)
        self._preview_timer.start()

    # -- icon helper --------------------------------------------------------
    def _set_state_icon(self, state: str) -> None:
        if state == self._icon_state:
            return
        self._icon_state = state
        filename, template = STATE_ICONS[state]
        self._template = template  # render with the right mode in one update
        self.icon = str(ICONS / filename)

    # -- check-mark sync ----------------------------------------------------
    def _sync_checks(self) -> None:
        for level, item in self._sensitivity_items.items():
            item.state = 1 if level == self.config.get("sensitivity") else 0
        current_hold = float(self.config.get("hold_seconds"))
        for secs, item in self._hold_items.items():
            item.state = 1 if abs(secs - current_hold) < 1e-6 else 0
        current_nudge = int(self.config.get("nudge_interval_seconds"))
        for secs, item in self._nudge_items.items():
            item.state = 1 if secs == current_nudge else 0
        current_idle = int(self.config.get("idle_timeout_seconds")) if self.config.get("pause_when_idle") else 0
        for secs, item in self._idle_items.items():
            item.state = 1 if secs == current_idle else 0
        for cue, item in self._cue_items.items():
            item.state = 1 if cue == self.config.get("cue") else 0
        self.chin_item.state = 1 if self.config.get("ignore_chin") else 0
        self.pause_item.title = (
            "Resume monitoring" if not self.config.get("monitoring") else "Pause monitoring"
        )

    # -- callbacks ----------------------------------------------------------
    def _cb(self, key: str, value):
        def cb(_sender):
            self.config.set(key, value)
            self._sync_checks()
        return cb

    def _cb_idle(self, secs: int):
        def cb(_sender):
            self.config.set("pause_when_idle", secs != 0)
            if secs != 0:
                self.config.set("idle_timeout_seconds", secs)
            self._sync_checks()
        return cb

    def _cb_cue(self, cue: str):
        def cb(_sender):
            self.config.set("cue", cue)
            self._sync_checks()
            self.detector._play_cue()  # preview the chosen cue
        return cb

    def toggle_pause(self, _sender) -> None:
        monitoring = not self.config.get("monitoring")
        self.config.set("monitoring", monitoring)
        self.detector.set_paused(not monitoring)
        self._sync_checks()

    def toggle_chin(self, _sender) -> None:
        self.config.set("ignore_chin", not self.config.get("ignore_chin"))
        self._sync_checks()

    def reset_count(self, _sender) -> None:
        self.config.reset_today()

    # -- preview window -----------------------------------------------------
    def toggle_preview(self, _sender) -> None:
        d = self.detector
        if d.preview_active:
            self._close_preview()
            return
        try:
            if self._preview_win is None:
                self._preview_win = PreviewWindow()
            d.set_preview(True)
            self._preview_win.show(render_dashboard(d.get_preview_frame(), self._preview_info()))
            self.preview_item.title = "Hide camera preview"
            self.preview_item.state = 1
        except Exception as exc:
            d.set_preview(False)
            rumps.alert(title="facemash", message=f"Couldn't open the preview window:\n{exc}",
                        ok="OK", icon_path=APP_ICON)

    def _close_preview(self) -> None:
        self.detector.set_preview(False)
        if self._preview_win is not None:
            self._preview_win.hide()
        self.preview_item.title = "Show camera preview"
        self.preview_item.state = 0

    def _preview_info(self, frame=None) -> dict:
        d = self.detector
        if d.camera_error:
            state, status = "error", d.camera_error
        elif d.touching:
            state, status = "touching", "Hands on your face!"
        elif d.face_present:
            state, status = "watching", "Watching you"
        else:
            state, status = "noface", "No face detected"
        return {
            "state": state,
            "status_text": status,
            "today": self.config.today_count,
            "total": self.config.total_count,
            "sensitivity": self.config.get("sensitivity"),
            "hold": float(self.config.get("hold_seconds")),
            "fps": d.fps,
        }

    def update_preview(self, _timer) -> None:
        d = self.detector
        if not d.preview_active:
            return
        # the user closed the window with the red button
        if self._preview_win is not None and not self._preview_win.is_visible():
            self._close_preview()
            return
        try:
            self._preview_win.update(render_dashboard(d.get_preview_frame(), self._preview_info()))
        except Exception:
            pass

    # -- about / quit -------------------------------------------------------
    def about(self, _sender) -> None:
        rumps.alert(
            title=f"facemash {__version__}",
            message=(
    "Keep your hands off your face — for healthier skin.\n\n"
    "Facemash watches your webcam and gently nudges you when you "
    "touch your face. Everything runs locally; no video ever leaves "
    "your computer, and the camera turns off when you pause or step away."
),
            ok="Got it",
            icon_path=APP_ICON,
        )

    def quit_app(self, _sender) -> None:
        try:
            self._close_preview()
            self.detector.stop()
        finally:
            rumps.quit_application()

    # -- periodic UI update (main thread) -----------------------------------
    def update_ui(self, _timer) -> None:
        d = self.detector
        if not self.config.get("monitoring") and not d.preview_active:
            self._set_state_icon("paused")
            self.status_item.title = "Paused"
        elif d.camera_error:
            self._set_state_icon("error")
            self.status_item.title = d.camera_error
        elif d.auto_paused:
            self._set_state_icon("idle")
            self.status_item.title = "Idle — camera off (you stepped away)"
        elif d.touching:
            self._set_state_icon("alert")
            self.status_item.title = "Hands on your face!"
        else:
            self._set_state_icon("ok")
            self.status_item.title = (
                f"Watching · {d.fps:.0f} fps" if d.face_present else "Watching · no face detected"
            )

        if d.consume_alert():
            today, total = self.config.record_touch()
            try:
                rumps.notification(title="facemash", subtitle="Hands off your face!",
                                   message=f"{today} today · {total} all-time")
            except Exception:
                pass

        self.count_item.title = f"Touches today: {self.config.today_count}"

        # Optional: auto-open the preview once on launch (used for testing).
        if not self._auto_preview_done and os.environ.get("FACEMASH_OPEN_PREVIEW"):
            self._auto_preview_done = True
            self.toggle_preview(None)


def main() -> None:
    FacemashApp().run()


if __name__ == "__main__":
    main()
