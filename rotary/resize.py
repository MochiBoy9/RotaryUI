"""Two-hand window resizing: pinch with both hands, then pull them apart or push them together.

Hands side by side change the width, one above the other the height, and hands at opposite corners
(diagonal) change both, like dragging a window by its corner. Only the window in front is touched, and
only while it is windowed (not maximised, minimised or full screen).

Pure Python + numpy apart from the worker thread: `TwoHandResize` turns tracked hands into
start / scale / end events, `target_rect` turns a scale into a window rectangle, and `WindowSizer`
applies it off the UI thread through the platform's `active_window` / `set_window_rect`
(winapi.py on Windows, posix.py elsewhere).
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, NamedTuple

from .gestures import HandFeatures, OneEuro

log = logging.getLogger(__name__)


class Rect(NamedTuple):
    x: int
    y: int
    w: int
    h: int


@dataclass
class ActiveWindow:
    handle: object         # HWND on Windows, an AXUIElement on macOS, an X11 window id on Linux
    rect: Rect             # where it is now, in the same units set_window_rect takes
    work: Rect             # the usable part of its screen (no taskbar / Dock / panels)
    title: str = ""


PINCH_ON = 0.36            # thumb-to-index gap (palm lengths) that counts as a pinch
PINCH_OFF = 0.52          # ...and the gap that lets go
HOLD_SECONDS = 0.25        # both hands pinch this long before the window follows
LOST_SECONDS = 0.35        # a hand out of sight this long ends the resize
MIN_SPAN = 0.10            # hands closer than this (image heights) along an axis can't steer that axis
DEAD_ZONE = 0.03           # spread changes smaller than this don't move the window
AXIS_DEGREES = 28.0        # hands within this of level (or of upright) change just one side
MAX_SCALE = 4.0


def pinch_point(f: HandFeatures) -> tuple[float, float]:
    """Where the pinch is: the index fingertip (it meets the thumb), image-height units."""
    return f.tip


def axis_for(dx: float, dy: float) -> str:
    """'x' for hands side by side, 'y' for one above the other, 'xy' for opposite corners."""
    ang = math.degrees(math.atan2(abs(dy), abs(dx)))
    if ang <= AXIS_DEGREES:
        return "x"
    if ang >= 90 - AXIS_DEGREES:
        return "y"
    return "xy"


class TwoHandResize:
    """Watches every tracked hand and reports a two-hand pinch-and-stretch.

    update() returns events: ("resize_start", axis), ("resize", (sx, sy)) whenever the scale changes,
    and ("resize_end", reason). sx / sy are relative to the hands' spread when the resize began."""

    def __init__(self):
        self.enabled = True
        self.state = "idle"            # idle | holding | active
        self.axis = ""
        self.scale = (1.0, 1.0)
        self._pinched: dict[int, bool] = {0: False, 1: False}
        self._since = 0.0
        self._last_both = 0.0
        self._span0 = (1.0, 1.0)
        self._fx, self._fy = OneEuro(1.2, 0.8), OneEuro(1.2, 0.8)
        self._need_release = False

    @property
    def busy(self) -> bool:
        """Both hands are pinching (about to resize, or resizing): keep the rotary out of it."""
        return self.state != "idle"

    def cancel(self) -> list:
        if self.state == "active":
            self._reset()
            self._need_release = True
            return [("resize_end", "cancelled")]
        self._reset()
        return []

    def _reset(self) -> None:
        self.state = "idle"
        self.axis = ""
        self.scale = (1.0, 1.0)
        self._fx.reset()
        self._fy.reset()

    @staticmethod
    def _pair(hands: list[HandFeatures]) -> list[HandFeatures] | None:
        """The two hands to use, left one first (as the user sees it), or None."""
        ok = [f for f in hands if not f.at_face and f.grip < 0.65]
        if len(ok) < 2:
            return None
        ok.sort(key=lambda f: f.scale, reverse=True)
        a, b = ok[0], ok[1]
        # Two detections of the same hand, or hands crossed over each other: not a stretch.
        if math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1]) < 0.9 * max(a.scale, b.scale):
            return None
        return sorted((a, b), key=lambda f: f.center[0])

    def _pinching(self, i: int, f: HandFeatures) -> bool:
        was = self._pinched[i]
        now = f.pinch < (PINCH_OFF if was else PINCH_ON)
        self._pinched[i] = now
        return now

    def update(self, hands: list[HandFeatures], now: float) -> list:
        events: list = []
        pair = self._pair(hands) if self.enabled else None
        both = False
        if pair is not None:
            p0, p1 = self._pinching(0, pair[0]), self._pinching(1, pair[1])
            both = p0 and p1
            if not p0 and not p1:
                self._need_release = False
        else:
            self._pinched = {0: False, 1: False}
            self._need_release = False

        if both:
            self._last_both = now
        if self.state == "active":
            if not both:
                if pair is not None or now - self._last_both > LOST_SECONDS:
                    self._reset()
                    self._need_release = True
                    events.append(("resize_end", "released" if pair is not None else "lost"))
                return events
            events += self._track(pair, now)
            return events

        if not both or self._need_release:
            self.state = "idle"
            return events
        if self.state == "idle":
            self.state = "holding"
            self._since = now
        if now - self._since >= HOLD_SECONDS:
            (x0, y0), (x1, y1) = pinch_point(pair[0]), pinch_point(pair[1])
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            self.axis = axis_for(dx, dy)
            if (self.axis in ("x", "xy") and dx < MIN_SPAN) or (self.axis in ("y", "xy") and dy < MIN_SPAN):
                return events          # too close together to steer; keep waiting
            self._fx.reset()
            self._fy.reset()
            self._span0 = (self._fx(dx, now), self._fy(dy, now))
            self.state = "active"
            self.scale = (1.0, 1.0)
            events.append(("resize_start", self.axis))
        return events

    def _track(self, pair: list[HandFeatures], now: float) -> list:
        (x0, y0), (x1, y1) = pinch_point(pair[0]), pinch_point(pair[1])
        dx, dy = self._fx(abs(x1 - x0), now), self._fy(abs(y1 - y0), now)
        sx = dx / max(self._span0[0], 1e-6) if self.axis in ("x", "xy") else 1.0
        sy = dy / max(self._span0[1], 1e-6) if self.axis in ("y", "xy") else 1.0
        sx = 1.0 if abs(sx - 1) < DEAD_ZONE else min(MAX_SCALE, max(1 / MAX_SCALE, sx))
        sy = 1.0 if abs(sy - 1) < DEAD_ZONE else min(MAX_SCALE, max(1 / MAX_SCALE, sy))
        if abs(sx - self.scale[0]) < 0.004 and abs(sy - self.scale[1]) < 0.004:
            return []
        self.scale = (sx, sy)
        return [("resize", self.scale)]


def target_rect(start: Rect, sx: float, sy: float, work: Rect | None, min_w: int = 320, min_h: int = 200) -> Rect:
    """The window scaled about its centre, no smaller than a usable minimum, no bigger than its screen's
    work area, and kept on that screen."""
    w = max(min(start.w, min_w), round(start.w * sx))
    h = max(min(start.h, min_h), round(start.h * sy))
    if work is not None and work.w > 0 and work.h > 0:
        w, h = min(w, max(work.w, start.w)), min(h, max(work.h, start.h))
    cx, cy = start.x + start.w / 2, start.y + start.h / 2
    x, y = round(cx - w / 2), round(cy - h / 2)
    if work is not None and work.w > 0 and work.h > 0 and w <= work.w and h <= work.h:
        x = min(max(x, work.x), work.x + work.w - w)
        y = min(max(y, work.y), work.y + work.h - h)
    return Rect(int(x), int(y), int(w), int(h))


class WindowSizer:
    """Applies resize events to the window in front, on its own thread so a slow window system never
    holds up hand tracking. Only the latest size is ever applied; older ones are dropped."""

    def __init__(self, active_window: Callable[[], ActiveWindow | None],
                 set_window_rect: Callable[[object, Rect], bool],
                 on_started: Callable[[ActiveWindow | None], None] | None = None):
        self._active = active_window
        self._set = set_window_rect
        self._on_started = on_started
        self._cv = threading.Condition()
        self._jobs: list = []            # ("begin",) / ("scale", sx, sy) / ("end",); scale jobs are coalesced
        self._thread: threading.Thread | None = None
        self.window: ActiveWindow | None = None

    def begin(self) -> None:
        self._post(("begin",))

    def scale(self, sx: float, sy: float) -> None:
        with self._cv:
            self._jobs = [j for j in self._jobs if j[0] != "scale"]
        self._post(("scale", sx, sy))

    def end(self) -> None:
        self._post(("end",))

    def _post(self, job) -> None:
        with self._cv:
            self._jobs.append(job)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="resize")
                self._thread.start()
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._jobs and not self._cv.wait(5.0):
                    if not self._jobs:
                        self._thread = None
                        return
                if not self._jobs:
                    continue
                job = self._jobs.pop(0)
            try:
                self._do(job)
            except Exception:
                log.debug("window resize step failed", exc_info=True)

    def _do(self, job) -> None:
        if job[0] == "begin":
            self.window = self._active()
            if self.window is not None:
                log.info("resizing %r from %s", self.window.title, tuple(self.window.rect))
            if self._on_started is not None:
                self._on_started(self.window)
        elif job[0] == "scale" and self.window is not None:
            win = self.window
            self._set(win.handle, target_rect(win.rect, job[1], job[2], win.work))
        elif job[0] == "end":
            self.window = None
