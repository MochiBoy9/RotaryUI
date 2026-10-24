"""Background thread: camera lifecycle + MediaPipe hand landmarks."""
from __future__ import annotations

import importlib.abc
import importlib.util
import logging
import sys
import time
import types
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from . import DATA_DIR, ROOT
from .gestures import HandFrame

# OpenCV and MediaPipe are heavy: they load inside the tracking thread, and only when the camera is used.

log = logging.getLogger(__name__)

MODEL_PATH = ROOT / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/latest/hand_landmarker.task")
FACE_PATH = ROOT / "models" / "blaze_face_short_range.tflite"
FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
            "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
ACTIVE_GAP = 0.026        # seconds between tracked frames with a hand about: ~30 fps (a 60 fps camera skips half)
IDLE_GAP = 0.075          # …and with nobody around: ~12 fps, still quick to notice a hand coming up


@dataclass
class TrackResult:
    t: float
    hands: list[HandFrame]
    width: int
    height: int
    preview: QImage | None = None
    infer_ms: float = 0.0
    faces: list[tuple[float, float, float, float]] = field(default_factory=list)  # normalised x0, y0, x1, y1


@dataclass
class CameraStatus:
    state: str                    # searching | running | none | error
    camera: CameraInfo | None = None
    cameras: list[CameraInfo] = field(default_factory=list)
    capture_fps: float = 0.0
    infer_fps: float = 0.0
    message: str = ""


def _load_model(path=MODEL_PATH, url=MODEL_URL) -> bytes:
    if not path.exists():
        path = DATA_DIR / "models" / path.name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        import urllib.request

        from .assist import _ssl_context
        log.info("downloading model from %s", url)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=30, context=_ssl_context()) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(path)
    return path.read_bytes()


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """MediaPipe imports its drawing helpers (matplotlib) and audio recorder (sounddevice) eagerly.
    We use neither, and the packaged app leaves those libraries out to stay small — so hand MediaPipe
    empty stand-ins for them instead."""
    NAMES = ("mediapipe.tasks.python.vision.drawing_utils", "mediapipe.tasks.python.vision.drawing_styles",
             "sounddevice")

    def find_spec(self, name, path, target=None):
        return importlib.util.spec_from_loader(name, self) if name in self.NAMES else None

    def create_module(self, spec):
        return types.ModuleType(spec.name)

    def exec_module(self, module):
        pass


def _install_stubs() -> None:
    if not any(isinstance(f, _StubFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _StubFinder())


def _make_face_detector():
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    try:
        opts = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=_load_model(FACE_PATH, FACE_URL)),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5,
        )
        return vision.FaceDetector.create_from_options(opts)
    except Exception:
        log.exception("face detector unavailable — the near-face filter is off")
        return None


def _make_landmarker():
    _install_stubs()
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opts = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_buffer=_load_model()),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        # A little below MediaPipe's defaults: a hand in dim light, side-on or moving fast scores lower, and a
        # missed hand stalls a gesture where a doubtful one is still checked by the gesture rules.
        min_hand_detection_confidence=0.42,
        # A hand already found keeps being tracked more readily still: against a face (skin on skin) the
        # per-frame scores dip, and dropping the hand there would block gestures in front of the face.
        min_hand_presence_confidence=0.32,
        min_tracking_confidence=0.32,
    )
    return mp, vision.HandLandmarker.create_from_options(opts)


class TrackerThread(QThread):
    result = Signal(object)        # TrackResult
    status = Signal(object)        # CameraStatus

    def __init__(self, prefer_camera: str | None = None, parent=None):
        super().__init__(parent)
        self.prefer_camera = prefer_camera
        self.want_preview = False
        self._stopping = False
        self._request_key: str | None = None
        self._request_rescan = False
        self.cameras: list[CameraInfo] = []
        self.current: CameraInfo | None = None
        self.paused = False

    # -- called from the UI thread
    def stop(self):
        self._stopping = True

    def set_paused(self, paused: bool):
        """Paused = camera released (light off) and no tracking."""
        self.paused = paused

    def select_camera(self, key: str):
        self._request_key = key

    def rescan(self):
        self._request_rescan = True

    # -- thread body
    def run(self):
        from .camera import Capture, Enhancer, discover
        try:
            mp, landmarker = _make_landmarker()
        except Exception as e:
            log.exception("could not start hand tracking")
            self.status.emit(CameraStatus("error", message=f"Hand model failed to load: {e}"))
            return

        face_det = _make_face_detector()
        faces_box = {"faces": [], "n": 0}
        capture: Capture | None = None
        enhancer = Enhancer()
        last_seq = 0
        n_inf, t_inf, infer_fps = 0, time.monotonic(), 0.0
        next_scan = 0.0
        fails = 0
        last_hand = time.monotonic()
        last_t = 0.0
        last_err = 0.0

        while not self._stopping:
            # ---- paused: let go of the camera entirely
            if self.paused:
                if capture is not None:
                    capture.stop()
                    capture.join(1.0)
                    capture = None
                    self.current = None
                    self.status.emit(CameraStatus("paused", cameras=self.cameras, message="Paused — camera off"))
                time.sleep(0.2)
                continue
            # ---- (re)acquire a camera
            want_switch = self._request_key is not None or self._request_rescan
            if capture is None or capture.failed or want_switch:
                if capture is not None and (capture.failed or want_switch):
                    capture.stop()
                    capture.join(1.0)
                    capture = None
                if time.monotonic() < next_scan and not want_switch:
                    time.sleep(0.1)
                    continue
                key, self._request_key = self._request_key, None
                rescan, self._request_rescan = self._request_rescan, False
                self.status.emit(CameraStatus("searching", cameras=self.cameras, message="Looking for cameras…"))
                capture = None
                if not rescan and not key and self.prefer_camera and fails == 0:
                    capture = self._open_preferred()          # fast path: straight to the usual camera
                if capture is None:
                    if rescan or not self.cameras or key is None:
                        self.cameras = discover(self.prefer_camera)
                    order = list(self.cameras)
                    if key:
                        order.sort(key=lambda c: c.key != key)
                    capture = self._open_first(order)
                if capture is None:
                    self.current = None
                    self.cameras = []
                    fails += 1
                    wait = min(30.0, 3.0 * 2 ** min(fails - 1, 4))   # 3, 6, 12, 24, 30 s
                    self.status.emit(CameraStatus("none", message="No camera found — plug one in"))
                    next_scan = time.monotonic() + wait
                    continue
                fails = 0
                self.current = capture.info
                self.prefer_camera = capture.info.name
                last_seq = 0
                self.status.emit(CameraStatus("running", camera=capture.info, cameras=self.cameras))

            # ---- newest frame → landmarks
            last_seq, frame, t_cap = capture.latest(last_seq, timeout=0.5)
            if frame is None:
                continue
            # Hand tracking runs on the CPU (MediaPipe's Windows build has no GPU path), so it looks at no more
            # frames than gestures need: up to 30 a second with a hand about, 12 once nobody's been seen for a while.
            idle = time.monotonic() - last_hand > 6.0 and not self.want_preview
            if t_cap - last_t < (IDLE_GAP if idle else ACTIVE_GAP):
                continue
            last_t = t_cap
            try:
                n_hands = self._process(mp, landmarker, face_det, enhancer, frame, t_cap, capture, faces_box)
            except Exception:
                if time.monotonic() - last_err > 5.0:
                    log.exception("frame processing failed")
                    last_err = time.monotonic()
                continue
            if n_hands:
                last_hand = time.monotonic()

            n_inf += 1
            now = time.monotonic()
            if now - t_inf >= 1.0:
                infer_fps = n_inf / (now - t_inf)
                n_inf, t_inf = 0, now
                self.status.emit(CameraStatus("running", camera=capture.info, cameras=self.cameras,
                                              capture_fps=capture.fps, infer_fps=infer_fps))

        if capture is not None:
            capture.stop()
            capture.join(1.0)
        landmarker.close()
        if face_det is not None:
            face_det.close()

    def _process(self, mp, landmarker, face_det, enhancer, frame, t_cap, capture, faces_box) -> int:
        import cv2
        frame = cv2.flip(frame, 1)                   # selfie / mirror view
        frame = enhancer(frame)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        ts = max(int(t_cap * 1000), self._last_ts + 1)
        self._last_ts = ts
        t0 = time.perf_counter()
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)     # shared by both models
        res = landmarker.detect_for_video(image, ts)
        infer_ms = (time.perf_counter() - t0) * 1000

        h, w = rgb.shape[:2]
        # Faces: only needed when a hand is visible; every 3rd frame is plenty.
        if face_det is not None and res.hand_landmarks:
            faces_box["n"] += 1
            if faces_box["n"] % 3 == 1:
                try:
                    fr = face_det.detect_for_video(image, ts)
                    faces_box["faces"] = [(d.bounding_box.origin_x / w, d.bounding_box.origin_y / h,
                              (d.bounding_box.origin_x + d.bounding_box.width) / w,
                              (d.bounding_box.origin_y + d.bounding_box.height) / h) for d in fr.detections]
                except Exception:
                    log.debug("face detection failed", exc_info=True)
        elif not res.hand_landmarks:
            faces_box["n"] = 0
        hands = []
        for lms, wlms, hd in zip(res.hand_landmarks, res.hand_world_landmarks, res.handedness):
            hands.append(HandFrame(
                landmarks=np.array([(p.x, p.y, p.z) for p in lms], np.float32),
                world=np.array([(p.x, p.y, p.z) for p in wlms], np.float32),
                handedness=hd[0].category_name,
                score=float(hd[0].score),
                aspect=w / h,
            ))

        preview = None
        if self.want_preview:
            small = cv2.resize(rgb, (480, int(480 * h / w)), interpolation=cv2.INTER_AREA)
            preview = QImage(small.data, small.shape[1], small.shape[0], small.strides[0],
                             QImage.Format.Format_RGB888).copy()
        self.result.emit(TrackResult(t_cap, hands, w, h, preview, infer_ms, list(faces_box["faces"])))
        return len(hands)

    _last_ts = 0

    def _open_preferred(self) -> Capture | None:
        """Open the camera used last time without probing every device first."""
        from .camera import Capture, _candidates, probe
        for info in _candidates():
            if info.name == self.prefer_camera:
                got, cap = probe(info, seconds=1.0, keep_open=True)
                if got is not None and cap is not None:
                    log.info("using camera %s (fast start)", got.label())
                    self.cameras = [got]
                    c = Capture(cap, got)
                    c.start()
                    return c
        return None

    def _open_first(self, order: list[CameraInfo]) -> Capture | None:
        from .camera import Capture, probe
        for info in order:
            if self._stopping:
                return None
            got, cap = probe(info, seconds=1.2, keep_open=True)
            if got is not None and cap is not None:
                log.info("using camera %s", got.label())
                c = Capture(cap, got)
                c.start()
                return c
        return None
