# facemash

A macOS menu bar app that watches your webcam and nudges you when you touch your face, helping you break the habit for healthier skin.

Everything runs locally. No video ever leaves your Mac.

![platform](https://img.shields.io/badge/platform-macOS-black)
![python](https://img.shields.io/badge/python-3.9%20to%203.12-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

## Install

```sh
pip install facemash-ruben
facemash
```

On first launch, macOS asks for Camera permission. Approve it under System Settings > Privacy & Security > Camera, then relaunch.

## Features

- Real time face and hand tracking with MediaPipe.
- Configurable sensitivity, hold time, and minimum time between nudges.
- Ignores the chin and beard area, so resting your chin or stroking your beard while thinking does not raise a false alert. Detection is shape based, not colour based, so it works for any skin or beard colour.
- Sound and optional spoken cues, plus a desktop notification and a daily counter.
- Live preview window with on screen detection overlays for the face box, hand skeletons, and the active touch zone.
- Turns the camera off, including the indicator light, when you pause or step away from the keyboard.
- Light on resources: roughly 9% of one CPU core while actively monitoring, and close to zero when idle.

## Usage

Click the menu bar icon to open the menu.

| Section | Options |
| --- | --- |
| Top | Show camera preview, Pause or resume monitoring |
| Detection | Sensitivity, Hold time, Ignore chin and beard area |
| Alerts | Time between nudges, Alert cue (sound, voice, or both) |
| Privacy and power | Turn the camera off after a chosen period of inactivity |

Settings and your daily count are stored in ~/Library/Application Support/facemash/config.json.

## How it works

MediaPipe locates your face and hands in each frame. A touch is flagged when a hand stays inside the face region for longer than the hold time. Sensitivity adjusts that region: Low requires a clear touch, High also reacts to hands near the edge. The chin and beard zone below the mouth can be excluded to avoid common thinking poses.

To stay light, the capture loop runs at a few frames per second while idle, speeds up only when a hand is in frame, skips the hand model when no face is present, and releases the camera when you pause or step away.

## Privacy

facelint never records, stores, or transmits anything. Frames are processed in memory and discarded immediately. The only network access is a one time download of the MediaPipe model files, about 8 MB, into `~/Library/Application Support/facemash/models` on first run.

## Development

```sh
git clone https://github.com/Josh-Ruben/facemash
cd facemash
uv venv --python 3.12
uv pip install -e .
python -m tests.test_geometry   # pure logic tests, no camera needed
facemash
```

MediaPipe publishes wheels for Python 3.9 to 3.12, so facemash targets that range.

## License

MIT. See [LICENSE](LICENSE).
