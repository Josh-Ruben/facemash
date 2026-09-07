"""Windows system-tray application for facemash."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pystray
from PIL import Image

from facemash.config import Config
from facemash.detector import FaceTouchDetector


ICONS = Path(__file__).parent / "resources" / "icons"
APP_ICON = ICONS / "app_icon.png"

UPDATE_INTERVAL = 0.2


class FacemashWindowsApp:
    def __init__(self) -> None:
        self.config = Config()
        self.detector = FaceTouchDetector(
            get_setting=self.config.get
        )

        self.icon = pystray.Icon(
            "facemash",
            self._load_icon(),
            "facemash",
            self._create_menu(),
        )

        self._running = True

    def _load_icon(self):
        return Image.open(APP_ICON)

    def _create_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                "Status: Starting...",
                None,
                enabled=False,
            ),

            pystray.Menu.SEPARATOR,

            pystray.MenuItem(
                lambda item: (
                    "Resume monitoring"
                    if not self.config.get("monitoring")
                    else "Pause monitoring"
                ),
                self.toggle_pause,
            ),

            pystray.MenuItem(
                lambda item: (
                    f"Touches today: {self.config.today_count}"
                ),
                None,
                enabled=False,
            ),

            pystray.Menu.SEPARATOR,

            pystray.MenuItem(
                "Reset today's count",
                self.reset_count,
            ),

            pystray.Menu.SEPARATOR,

            pystray.MenuItem(
                "Quit facemash",
                self.quit_app,
            ),
        )

    def toggle_pause(self, icon, item) -> None:
        monitoring = not self.config.get("monitoring")

        self.config.set(
            "monitoring",
            monitoring,
        )

        self.detector.set_paused(
            not monitoring
        )

        self.icon.update_menu()

    def reset_count(self, icon, item) -> None:
        self.config.reset_today()
        self.icon.update_menu()

    def quit_app(self, icon, item) -> None:
        self._running = False

        try:
            self.detector.stop()
        finally:
            self.icon.stop()

    def _update_loop(self) -> None:
        while self._running:
            try:
                if self.detector.consume_alert():
                    self.config.record_touch()

                self.icon.update_menu()

            except Exception:
                pass

            time.sleep(UPDATE_INTERVAL)

    def run(self) -> None:
        self.detector.set_paused(
            not self.config.get("monitoring")
        )

        self.detector.start()

        update_thread = threading.Thread(
            target=self._update_loop,
            daemon=True,
        )

        update_thread.start()

        self.icon.run()


def main() -> None:
    app = FacemashWindowsApp()
    app.run()


if __name__ == "__main__":
    main()
