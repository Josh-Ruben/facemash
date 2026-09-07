"""Webcam face-touch detector.

Uses MediaPipe (Tasks API) to find the face bounding box + key landmarks and the
hand landmarks in each frame, then flags a "touch" when a hand intrudes into the
face region. Designed to be light on resources and gentle about false positives:

  * Sensitivity is a *signed* margin around the face: Low requires the hand to be
    clearly inside the face (inset); High reacts to hands near the edge.
  * An optional "ignore chin & beard" zone drops touches below the mouth line, so
    resting your chin or stroking your beard while thinking doesn't nag you.
  * The capture loop runs slowly (a few fps) while idle and only speeds up when a
    hand is actually in frame, skips the expensive hand model entirely when no
    face is present, and releases the camera when you pause or step away.

The geometry helpers at the top are pure (no MediaPipe / camera) so the core
logic can be unit-tested in isolation.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

# Quiet the very chatty native logging from MediaPipe / glog / TF-Lite before
# the libraries are imported.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("GLOG_logtostderr", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

# OpenCV's AVFoundation backend tries to request camera authorization from
# whatever thread first opens the device. That auth request needs the main
# run loop, so it fails when we open the camera from our background thread.
# Skipping it lets us open the device directly; macOS' TCC still presents the
# system permission prompt on first access.
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

# Hand-landmark indices (MediaPipe Hands topology).
FINGERTIPS = (4, 8, 12, 16, 20)
FINGER_JOINTS = (3, 6, 7, 10, 11, 14, 15, 18, 19)
ALL_HAND_POINTS = tuple(range(21))

# sensitivity -> (signed margin around face, hand points to test)
#   negative margin  => face region is shrunk; the hand must be clearly inside
#                       (fewer, surer alerts)
#   positive margin  => face region is grown; reacts to hands near the edge
SENSITIVITY_PROFILES = {
    "low": (-0.10, FINGERTIPS),
    "medium": (0.0, FINGERTIPS + FINGER_JOINTS),
    "high": (0.12, ALL_HAND_POINTS),
}

# When "ignore chin & beard" is on but no mouth landmark is available, drop the
# bottom this-fraction of the face box (covers chin/jaw/beard).
_CHIN_FALLBACK_FRACTION = 0.72

# How long a vanished face stays "valid" so that a hand covering the face still
# counts as a touch even though MediaPipe can no longer see the face.
FACE_MEMORY_SECONDS = 1.5

# Adaptive frame rate: scan slowly while nothing is happening, speed up when a
# hand is in frame so a real touch is caught promptly.
IDLE_FPS = 4
ACTIVE_FPS = 13
ACTIVE_LINGER_SECONDS = 2.0  # stay at ACTIVE_FPS this long after last hand sighting
PREVIEW_FPS = 15  # smoother feed while the preview window is open

PROCESS_WIDTH = 480  # downscale frames to this width before inference

# MediaPipe Hands skeleton, used to draw the hand overlay in the preview.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),            # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),            # index
    (5, 9), (9, 10), (10, 11), (11, 12),       # middle
    (9, 13), (13, 14), (14, 15), (15, 16),     # ring
    (13, 17), (17, 18), (18, 19), (19, 20),    # pinky
    (0, 17),                                   # palm
)


@dataclass
class BBox:
    """Normalized [0, 1] bounding box."""

    x: float
    y: float
    w: float
    h: float

    def expanded(self, margin: float) -> "BBox":
        """Grow (margin > 0) or shrink (margin < 0) the box, clamped to [0, 1]."""
        mx = self.w * margin
        my = self.h * margin
        x = min(max(0.0, self.x - mx), 1.0)
        y = min(max(0.0, self.y - my), 1.0)
        x2 = min(max(0.0, self.x + self.w + mx), 1.0)
        y2 = min(max(0.0, self.y + self.h + my), 1.0)
        return BBox(x, y, max(0.0, x2 - x), max(0.0, y2 - y))

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def touch_region(
    face: Optional[BBox], margin: float, bottom_limit: Optional[float] = None
) -> Optional[BBox]:
    """The active region a hand must enter to count as a touch.

    ``margin`` grows (>0) or shrinks (<0) the face box; ``bottom_limit`` (a
    normalized y) clips off everything below it (chin/jaw/beard).
    """
    if face is None:
        return None
    region = face.expanded(margin)
    if region.w <= 0 or region.h <= 0:
        return None
    if bottom_limit is not None:
        new_bottom = min(region.y + region.h, bottom_limit)
        region = BBox(region.x, region.y, region.w, max(0.0, new_bottom - region.y))
        if region.h <= 0:
            return None
    return region


def hands_touch_face(
    face: Optional[BBox],
    hands: Sequence[Sequence[tuple[float, float]]],
    margin: float,
    indices: Iterable[int],
    bottom_limit: Optional[float] = None,
) -> bool:
    """Return True if any selected landmark of any hand lies in the face region.

    ``hands`` is a list of hands, each a list of (x, y) normalized landmarks.
    """
    region = touch_region(face, margin, bottom_limit)
    if region is None:
        return False
    idx = tuple(indices)
    for hand in hands:
        for i in idx:
            if i < len(hand):
                px, py = hand[i]
                if region.contains(px, py):
                    return True
    return False


def draw_overlays(cv2, rgb, region, face_box, hands_pts, touching) -> None:
    """Draw the touch zone, face box and hand skeletons onto an RGB frame in place."""
    h, w = rgb.shape[:2]

    def to_px(box):
        return (int(box.x * w), int(box.y * h),
                int((box.x + box.w) * w), int((box.y + box.h) * h))

    # active touch zone: translucent teal fill + outline
    if region is not None and region.w > 0 and region.h > 0:
        x1, y1, x2, y2 = to_px(region)
        overlay = rgb.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (20, 184, 166), -1)
        cv2.addWeighted(overlay, 0.18, rgb, 0.82, 0, rgb)
        cv2.rectangle(rgb, (x1, y1), (x2, y2), (20, 184, 166), 1, cv2.LINE_AA)

    # face box: green normally, red while touching
    if face_box is not None:
        fx1, fy1, fx2, fy2 = to_px(face_box)
        color = (235, 64, 52) if touching else (94, 214, 128)
        cv2.rectangle(rgb, (fx1, fy1), (fx2, fy2), color, 2, cv2.LINE_AA)

    # hands: skeleton + landmarks
    for hand in hands_pts:
        pts = [(int(x * w), int(y * h)) for (x, y) in hand]
        for a, b in HAND_CONNECTIONS:
            if a < len(pts) and b < len(pts):
                cv2.line(rgb, pts[a], pts[b], (255, 255, 255), 1, cv2.LINE_AA)
        for i, p in enumerate(pts):
            c = (255, 209, 102) if i in FINGERTIPS else (120, 200, 255)
            cv2.circle(rgb, p, 3, c, -1, cv2.LINE_AA)


@dataclass
class _State:
    running: bool = False
    camera_on: bool = False
    camera_error: Optional[str] = None
    auto_paused: bool = False  # paused because the user is idle/away
    face_present: bool = False
    hands_present: bool = False
    touching: bool = False
    fps: float = 0.0
    touch_count: int = 0  # alerts fired this session
    _pending_alert: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)


class FaceTouchDetector:
    """Runs the camera + MediaPipe loop in a background thread."""

    def __init__(
        self,
        get_setting: Callable[[str], object],
        on_alert: Optional[Callable[[], None]] = None,
    ):
        self._get = get_setting
        self._on_alert = on_alert
        self._state = _State()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._paused = threading.Event()  # set => manually paused
        self._preview = threading.Event()  # set => preview window open
        self._preview_lock = threading.Lock()
        self._preview_frame = None  # latest annotated RGB frame (numpy)
        self._idle_checked_at = -1e9
        self._idle_cached = 0.0

    # -- public state (thread-safe reads) ----------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _read(self, attr: str):
        with self._state._lock:
            return getattr(self._state, attr)

    @property
    def camera_error(self) -> Optional[str]:
        return self._read("camera_error")

    @property
    def camera_on(self) -> bool:
        return self._read("camera_on")

    @property
    def auto_paused(self) -> bool:
        return self._read("auto_paused")

    @property
    def face_present(self) -> bool:
        return self._read("face_present")

    @property
    def touching(self) -> bool:
        return self._read("touching")

    @property
    def fps(self) -> float:
        return self._read("fps")

    def consume_alert(self) -> bool:
        """Return True once if a new alert fired since the last call."""
        with self._state._lock:
            if self._state._pending_alert:
                self._state._pending_alert = False
                return True
            return False

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="facemash-detector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)
        self._thread = None

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    # -- preview ------------------------------------------------------------
    def set_preview(self, on: bool) -> None:
        if on:
            self._preview.set()
        else:
            self._preview.clear()
            with self._preview_lock:
                self._preview_frame = None

    @property
    def preview_active(self) -> bool:
        return self._preview.is_set()

    def get_preview_frame(self):
        """Latest annotated RGB frame (numpy array) or None."""
        with self._preview_lock:
            return self._preview_frame

      # -- idle / should-monitor ---------------------------------------------
    def _idle_seconds(self) -> float:
        """Seconds since the last keyboard/mouse input."""
        now = time.monotonic()
        if now - self._idle_checked_at < 2.0:
            return self._idle_cached

        self._idle_checked_at = now

        if platform.system() == "Darwin":
            try:
                out = subprocess.run(
                    ["ioreg", "-c", "IOHIDSystem"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ).stdout

                m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)

                self._idle_cached = (
                    int(m.group(1)) / 1_000_000_000.0
                    if m
                    else 0.0
                )
            except Exception:
                self._idle_cached = 0.0

        elif platform.system() == "Windows":
            try:
                import ctypes

                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_uint),
                    ]

                info = LASTINPUTINFO()
                info.cbSize = ctypes.sizeof(LASTINPUTINFO)

                ctypes.windll.user32.GetLastInputInfo(
                    ctypes.byref(info)
                )

                millis = (
                    ctypes.windll.kernel32.GetTickCount()
                    - info.dwTime
                )
                self._idle_cached = millis / 1000.0

            except Exception:
                self._idle_cached = 0.0

        else:
            self._idle_cached = 0.0

        return self._idle_cached

    def _should_monitor(self) -> tuple[bool, bool]:
        """Return (monitor, auto_paused)."""
        if self._preview.is_set():
            # Keep the camera running so the preview always shows a live feed.
            return True, False
        if self._paused.is_set():
            return False, False
        if bool(self._get("pause_when_idle")):
            timeout = float(self._get("idle_timeout_seconds") or 0)
            if timeout > 0 and self._idle_seconds() >= timeout:
                return False, True
        return True, False

    # -- internals ----------------------------------------------------------
    def _set(self, **kw) -> None:
        with self._state._lock:
            for k, v in kw.items():
                setattr(self._state, k, v)

    def _fire_alert(self) -> None:
        with self._state._lock:
            self._state.touch_count += 1
            self._state._pending_alert = True
        self._play_cue()
        if self._on_alert is not None:
            try:
                self._on_alert()
            except Exception:
                pass

    def _play_cue(self) -> None:
        cue = str(self._get("cue") or "sound")
        sound = str(
            self._get("sound")
            or "/System/Library/Sounds/Funk.aiff"
        )

        system = platform.system()

        try:
            if system == "Darwin":
                if cue in ("sound", "both"):
                    if os.path.exists(sound):
                        subprocess.Popen(["afplay", sound])
                    else:
                        subprocess.Popen(
                            ["osascript", "-e", "beep"]
                        )

                if cue in ("voice", "both"):
                    subprocess.Popen(
                        ["say", "Hands off your face"]
                    )

            elif system == "Windows":
                if cue in ("sound", "both"):
                    import winsound
                    winsound.MessageBeep()

                if cue in ("voice", "both"):
                    import pyttsx3

                    engine = pyttsx3.init()
                    engine.say("Hands off your face")
                    engine.runAndWait()

        except Exception:
            pass

    def _open_camera(self, cv2):
        cam_index = int(self._get("camera_index") or 0)
        cap = cv2.VideoCapture(cam_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _run(self) -> None:
        # Imported lazily so that importing this module (e.g. for tests) does
        # not require the heavy native dependencies.
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            from facemash.models import ensure_models
        except Exception as exc:  # pragma: no cover - import-time failure
            self._set(camera_error=f"Missing dependency: {exc}")
            return

        try:
            models = ensure_models()
        except Exception as exc:
            self._set(camera_error=f"Could not download models: {exc}")
            return

        face_opts = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(models["face"])),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5,
        )
        hand_opts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(models["hand"])),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        cap = None
        last_face: Optional[BBox] = None
        last_mouth_y: Optional[float] = None
        last_face_time = 0.0
        touch_start: Optional[float] = None
        last_alert = 0.0
        active_until = 0.0
        frame_times: list[float] = []
        consecutive_failures = 0
        ts_ms = 0  # strictly-increasing timestamp required by VIDEO mode

        self._set(running=True)

        try:
            with vision.FaceDetector.create_from_options(face_opts) as face_det, \
                 vision.HandLandmarker.create_from_options(hand_opts) as hands_det:
                while not self._stop.is_set():
                    monitor, auto_paused = self._should_monitor()
                    if not monitor:
                        if cap is not None:
                            cap.release()
                            cap = None
                        touch_start = None
                        self._set(camera_on=False, auto_paused=auto_paused, touching=False,
                                  face_present=False, hands_present=False, fps=0.0)
                        self._stop.wait(0.5)
                        continue

                    if cap is None:
                        cap = self._open_camera(cv2)
                        if cap is None:
                            self._set(camera_error="Cannot open camera — check Camera permission "
                                                   "in System Settings › Privacy & Security.",
                                      camera_on=False)
                            self._stop.wait(2.0)
                            continue
                        self._set(camera_on=True, camera_error=None, auto_paused=False)

                    loop_start = time.monotonic()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        consecutive_failures += 1
                        if consecutive_failures > 30:
                            cap.release()
                            cap = None
                            self._set(camera_error="Lost camera stream; retrying…", camera_on=False)
                            self._stop.wait(1.0)
                            consecutive_failures = 0
                        else:
                            self._stop.wait(0.05)
                        continue
                    consecutive_failures = 0

                    now = time.monotonic()
                    ts_ms = max(ts_ms + 1, int(now * 1000))

                    # Downscale for speed; normalized coordinates are unaffected.
                    fh, fw = frame.shape[:2]
                    if fw > PROCESS_WIDTH:
                        scale = PROCESS_WIDTH / fw
                        frame = cv2.resize(frame, (PROCESS_WIDTH, int(round(fh * scale))))
                    # Mirror to a natural selfie view (detection is symmetric).
                    frame = cv2.flip(frame, 1)
                    h, w = frame.shape[:2]
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                    # ---- face (always; it's the cheap model) ----
                    face_res = face_det.detect_for_video(mp_image, ts_ms)
                    face_now: Optional[BBox] = None
                    mouth_y: Optional[float] = None
                    if face_res.detections:
                        det0 = face_res.detections[0]
                        bb = det0.bounding_box
                        face_now = BBox(bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h)
                        kp = getattr(det0, "keypoints", None)
                        if kp and len(kp) > 3:
                            mouth_y = kp[3].y  # BlazeFace keypoint 3 = mouth center
                        last_face, last_mouth_y, last_face_time = face_now, mouth_y, now

                    face_for_test = face_now
                    mouth_for_test = mouth_y
                    if face_for_test is None and last_face is not None and (now - last_face_time) <= FACE_MEMORY_SECONDS:
                        face_for_test = last_face
                        mouth_for_test = last_mouth_y

                    # ---- hands (only when a face exists -> nothing to touch otherwise) ----
                    hands_pts: list[list[tuple[float, float]]] = []
                    if face_for_test is not None:
                        hand_res = hands_det.detect_for_video(mp_image, ts_ms)
                        for hand in hand_res.hand_landmarks:
                            hands_pts.append([(lm.x, lm.y) for lm in hand])

                    # ---- chin/beard exclusion ----
                    bottom_limit = None
                    if face_for_test is not None and bool(self._get("ignore_chin")):
                        bottom_limit = (
                            mouth_for_test
                            if mouth_for_test is not None
                            else face_for_test.y + face_for_test.h * _CHIN_FALLBACK_FRACTION
                        )

                    margin, indices = SENSITIVITY_PROFILES.get(
                        str(self._get("sensitivity") or "medium"), SENSITIVITY_PROFILES["medium"]
                    )
                    touching = hands_touch_face(face_for_test, hands_pts, margin, indices, bottom_limit)

                    # ---- hold + nudge-interval logic ----
                    hold = float(self._get("hold_seconds") or 1.2)
                    interval = float(self._get("nudge_interval_seconds") or 30)
                    if touching:
                        if touch_start is None:
                            touch_start = now
                        elif (now - touch_start) >= hold and (now - last_alert) >= interval:
                            last_alert = now
                            self._fire_alert()
                    else:
                        touch_start = None

                    if hands_pts or touching:
                        active_until = now + ACTIVE_LINGER_SECONDS

                    # ---- preview annotation ----
                    if self._preview.is_set():
                        region = touch_region(face_for_test, margin, bottom_limit)
                        draw_overlays(cv2, rgb, region, face_for_test, hands_pts, touching)
                        with self._preview_lock:
                            self._preview_frame = rgb

                    # ---- fps bookkeeping ----
                    frame_times.append(now)
                    frame_times = [t for t in frame_times if now - t <= 1.0]
                    self._set(
                        face_present=face_now is not None,
                        hands_present=bool(hands_pts),
                        touching=touching,
                        fps=float(len(frame_times)),
                        camera_on=True,
                        auto_paused=False,
                    )

                    # ---- adaptive pacing ----
                    target_fps = ACTIVE_FPS if now < active_until else IDLE_FPS
                    if self._preview.is_set():
                        target_fps = max(target_fps, PREVIEW_FPS)
                    elapsed = time.monotonic() - loop_start
                    wait = (1.0 / target_fps) - elapsed
                    if wait > 0:
                        self._stop.wait(wait)
        finally:
            if cap is not None:
                cap.release()
            self._set(running=False, camera_on=False, touching=False,
                      face_present=False, hands_present=False, fps=0.0)
