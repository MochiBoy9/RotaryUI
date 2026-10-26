"""The screen's edges glow while screen assist's AI works on an answer, like Siri on newer iPhones.

A click-through window over the whole screen that paints only a soft band of colour around its edge: the colours
(purple-blue by default) drift slowly around the screen and the band breathes. It fades in once the screenshot is
taken, so it's never in the picture, and fades out when the answer (or an error) comes back.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QConicalGradient, QPainter, QPen, QRegion
from PySide6.QtWidgets import QWidget

from .glass import Spring, exclude_from_capture, frame_ms, overlay_flags, qcolor

BAND = 46            # pixels: how far into the screen the glow reaches
# (pen width, opacity) from the soft haze furthest in to the bright line on the edge. Each stroke is centred on
# the screen's edge, so half of it shows, and they pile up: many thin, faint steps blend into a smooth fade.
STEPS = 24
LAYERS = tuple((2 * BAND * (1 - i / STEPS) + 3, 0.035 + 0.11 * (i / STEPS) ** 2) for i in range(STEPS)) + ((5, 0.9),)
TURN_S = 7.0         # seconds for the colours to go once around the screen
BREATHE_S = 2.6      # seconds for one breath of the band


def gradient(colors: list[str], center: QPointF, angle: float, alpha: float) -> QConicalGradient:
    """The colours spread evenly around the screen, back to the first so there's no seam."""
    g = QConicalGradient(center, angle)
    n = len(colors)
    for i, c in enumerate(colors):
        g.setColorAt(i / n, qcolor(c, alpha))
    g.setColorAt(1.0, qcolor(colors[0], alpha))
    return g


class ScreenGlow(QWidget):
    def __init__(self):
        super().__init__(None)
        overlay_flags(self)
        self.colors: list[str] = []
        self.reduce_motion = False
        self.vis = Spring(0.0, 90.0, 19.0)
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._excluded = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    @property
    def glowing(self) -> bool:
        return self.vis.target > 0.0

    def start(self, colors: list[str], screen=None, reduce_motion: bool = False) -> None:
        """Glow around `screen` (the one the screenshot came from) until stop(); nothing when `colors` is []."""
        if not colors:
            return
        self.colors = list(colors)
        self.reduce_motion = reduce_motion
        if screen is not None:
            if self.isVisible() and self.screen() is not screen:
                self.hide()
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
        if not self.isVisible():
            self.vis.snap(0.0)
            self._t0 = time.perf_counter()
            self.show()
            if not self._excluded:
                self._excluded = exclude_from_capture(self, True)     # another screenshot never sees it
        self.vis.target = 1.0
        self._timer.setInterval(max(frame_ms(self.screen()), 33))      # 30 a second is smooth for a slow drift
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        """Fade out (the answer is in)."""
        self.vis.target = 0.0
        if self.isVisible() and not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        self.vis.step(min(0.05, now - self._last))
        self._last = now
        if self.vis.target == 0.0 and self.vis.value < 0.01:
            self._timer.stop()
            self.hide()
            return
        self.update(self._band())

    def _band(self) -> QRegion:
        """Only the edge needs painting; the middle of the screen stays untouched."""
        r = self.rect()
        return QRegion(r).subtracted(QRegion(r.adjusted(BAND + 2, BAND + 2, -BAND - 2, -BAND - 2)))

    def paintEvent(self, _ev) -> None:
        if not self.colors:
            return
        t = time.perf_counter() - self._t0
        fade = max(0.0, min(1.0, self.vis.value))
        if self.reduce_motion:
            angle, breath = 90.0, 1.0
        else:
            angle = 90.0 - 360.0 * (t / TURN_S)
            breath = 0.82 + 0.18 * math.sin(2 * math.pi * t / BREATHE_S)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipRegion(self._band())
        r = QRectF(self.rect())
        center = r.center()
        radius = min(28.0, min(r.width(), r.height()) / 8)
        for width, alpha in LAYERS:
            brush = QBrush(gradient(self.colors, center, angle, alpha * fade * breath))
            pen = QPen(brush, width * (0.9 + 0.1 * breath))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
        p.end()

