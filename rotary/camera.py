"""Camera discovery, validation and a latest-frame capture thread.

Every connected camera is enumerated (Windows: Media Foundation first, then DirectShow;
macOS: AVFoundation; Linux: Video4Linux),
opened, and judged on what it actually delivers: live sensor frames (not a
static placeholder or black screen), measured frame rate and resolution.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:  # pragma: no cover
    pass

try:
    from cv2_enumerate_cameras import enumerate_cameras
except Exception:  # pragma: no cover
    enumerate_cameras = None

if sys.platform == "win32":
    BACKENDS = [(cv2.CAP_MSMF, "Media Foundation"), (cv2.CAP_DSHOW, "DirectShow")]
elif sys.platform == "darwin":
    BACKENDS = [(cv2.CAP_AVFOUNDATION, "AVFoundation")]
else:
    BACKENDS = [(cv2.CAP_V4L2, "Video4Linux")]
# 30 fps is all hand tracking uses, costs half the CPU of 60 to decode, and lets the sensor expose twice as long:
# brighter, less noisy frames in a dim room, where hands are most often missed.
REQUEST = (640, 480, 30)


@dataclass
class CameraInfo:
    index: int
    backend: int
    name: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    live: bool = False
    bright: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.name}|{self.backend}|{self.index}"

    @property
    def backend_name(self) -> str:
        return dict(BACKENDS).get(self.backend, str(self.backend))

    @property
    def score(self) -> float:
        px = min(self.width * self.height, 1280 * 720) / (640 * 480)
        s = min(self.fps, 60.0) * 5 + px * 20
        if self.live:
            s += 1000
        if self.bright > 8:
            s += 300
        if self.backend == cv2.CAP_MSMF:
            s += 5
        return s

    def label(self) -> str:
        if self.width:
            return f"{self.name} — {self.width}×{self.height} @ {self.fps:.0f} fps"
        return self.name


def _candidates() -> list[CameraInfo]:
    found: list[CameraInfo] = []
    if enumerate_cameras is not None:
        for backend, _ in BACKENDS:
            try:
                for c in enumerate_cameras(backend):
                    idx = c.index - backend if c.index >= backend else c.index  # strip api offset
                    found.append(CameraInfo(index=idx, backend=backend, name=c.name or f"Camera {idx}"))
            except Exception as e:  # pragma: no cover
                log.debug("enumerate %s failed: %s", backend, e)
    if not found:
        for backend, _ in BACKENDS:
            for i in range(6):
                found.append(CameraInfo(index=i, backend=backend, name=f"Camera {i}"))
    return found


def _open(info: CameraInfo, request: bool) -> cv2.VideoCapture | None:
    for idx, be in [(info.index, info.backend)]:
        cap = cv2.VideoCapture(idx, be)
        if cap.isOpened():
            if request:
                w, h, fps = REQUEST
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, fps)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            return cap
        cap.release()
    return None


def probe(info: CameraInfo, seconds: float = 2.0, keep_open: bool = False):
    """Open and measure a camera. Returns (info, cap|None) or (None, None)."""
    for request in (True, False):
        cap = _open(info, request)
        if cap is None:
            continue
        frames, t_first, n, prev, diffs, bright = 0, None, 0, None, [], 0.0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ok, f = cap.read()
            if not ok or f is None or f.size == 0:
                continue
            frames += 1
            if frames == 6:
                t_first, n = time.monotonic(), 0
            n += 1
            g = cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (64, 48), interpolation=cv2.INTER_AREA).astype(np.float32)
            bright = float(g.mean())
            if prev is not None:
                diffs.append(float(np.abs(g - prev).mean()))
            prev = g
            if frames >= 50:
                break
        if frames >= 8 and t_first is not None:
            dt = max(time.monotonic() - t_first, 1e-3)
            info.height, info.width = f.shape[:2]
            info.fps = (n - 1) / dt
            info.bright = bright
            # Real sensors always have some noise; placeholders are perfectly static.
            info.live = len(diffs) > 4 and float(np.median(diffs[-20:])) > 0.05
            log.info("probe %s [%s]: %dx%d %.1ffps live=%s bright=%.0f",
                     info.name, info.backend_name, info.width, info.height, info.fps, info.live, bright)
            if keep_open:
                return info, cap
            cap.release()
            return info, None
        cap.release()
    log.info("probe %s [%s]: no frames", info.name, info.backend_name)
    return None, None


def discover(prefer_name: str | None = None) -> list[CameraInfo]:
    """Find every working camera, best first. Same-named devices are probed once
    (Media Foundation first) and DirectShow is only tried if that fails."""
    good: list[CameraInfo] = []
    seen_ok: set[str] = set()
    for c in _candidates():
        if c.name in seen_ok and not c.name.startswith("Camera "):
            continue
        info, _ = probe(c, seconds=1.6)
        if info is not None:
            good.append(info)
            seen_ok.add(info.name)
    good.sort(key=lambda c: (prefer_name is not None and c.name == prefer_name, c.score), reverse=True)
    return good


class Capture(threading.Thread):
    """Reads continuously and keeps only the newest frame (no lag build-up)."""

    def __init__(self, cap: cv2.VideoCapture, info: CameraInfo):
        super().__init__(daemon=True, name="capture")
        self.cap, self.info = cap, info
        self._cond = threading.Condition()
        self._frame = None
        self._t = 0.0
        self._seq = 0
        self.failed = False
        self._stopping = False
        self._fps_n, self._fps_t, self.fps = 0, time.monotonic(), 0.0

    def run(self) -> None:
        last_ok = time.monotonic()
        while not self._stopping:
            ok, f = self.cap.read()
            now = time.monotonic()
            if ok and f is not None and f.size:
                last_ok = now
                with self._cond:
                    self._frame, self._t = f, now
                    self._seq += 1
                    self._cond.notify_all()
                self._fps_n += 1
                if now - self._fps_t >= 1.0:
                    self.fps = self._fps_n / (now - self._fps_t)
                    self._fps_n, self._fps_t = 0, now
            elif now - last_ok > 2.5:
                log.warning("camera %s stopped delivering frames", self.info.name)
                self.failed = True
                break
            else:
                time.sleep(0.005)
        with self._cond:
            self._cond.notify_all()
        self.cap.release()

    def latest(self, after_seq: int, timeout: float = 0.5):
        with self._cond:
            if self._seq <= after_seq and not self.failed:
                self._cond.wait(timeout)
            if self._seq <= after_seq:
                return after_seq, None, 0.0
            return self._seq, self._frame, self._t

    def stop(self) -> None:
        self._stopping = True


class Enhancer:
    """Adaptive gamma / CLAHE for dim or low-quality sensors, plus downscale of huge frames.

    boost (Low-light boost, which the user turns on) brightens harder and further up, always evens out local
    contrast (a face lit from behind, a hand in shadow) and smooths sensor noise in a very dark room. It only
    changes the picture RotaryUI tracks on; the camera itself is never reconfigured."""

    def __init__(self):
        self._n = 0
        self._lut = None
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._clahe_boost = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self._dark = False
        self._noisy = False
        self.gamma = 1.0
        self.light = -1.0           # mean brightness of the raw picture, 0-255 (-1: not measured yet)
        self.boost = False
        self._boosted = False

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w > 1280:
            s = 960 / w
            frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        boost = self.boost
        if self._n % 15 == 0 or boost != self._boosted:
            g = cv2.cvtColor(cv2.resize(frame, (80, 60), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            self.light = float(g.mean())
            m = max(self.light, 4.0)
            gamma = 1.0
            target, below, floor = (135, 115, 0.3) if boost else (115, 90, 0.4)
            if m < below:
                gamma = max(floor, min(1.0, np.log(target / 255) / np.log(m / 255)))
            if (abs(gamma - self.gamma) > 0.04 or (gamma == 1.0) != (self._lut is None)
                    or boost != self._boosted):
                self.gamma = gamma
                self._lut = None if gamma >= 0.99 else np.array(
                    [((i / 255.0) ** gamma) * 255 for i in range(256)], np.uint8)
            self._dark = m < 45
            self._noisy = boost and m < 35
            self._boosted = boost
        self._n += 1
        if self._noisy:
            frame = cv2.GaussianBlur(frame, (3, 3), 0)     # before brightening, which would magnify the grain
        if self._lut is not None:
            frame = cv2.LUT(frame, self._lut)
        if self._dark or boost:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            lab[:, :, 0] = (self._clahe_boost if boost else self._clahe).apply(lab[:, :, 0])
            frame = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        return frame
