"""The rotary: a liquid-glass ring of items that turns with the user's hand."""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QLocale, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QTransform)
from PySide6.QtWidgets import QWidget

from ..actions import Item
from ..gestures import EngineSnapshot, Phase
from . import liquid
from .glass import (DISPLAY, ICONS, TEXT, Spring, angle_point, app_icon, black, circle, ease_out_cubic,
                    exclude_from_capture, font, frame_ms, glass_disc, grab_behind, no_activate, overlay_flags,
                    qcolor, refuse_activation, smoothstep, soft_shadow, stroke_rim, white)


def _wrap180(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, 1.0)


def _canvas(w: float, h: float, dpr: float) -> QPixmap:
    pm = QPixmap(max(1, int(math.ceil(w * dpr))), max(1, int(math.ceil(h * dpr))))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def _hq(p: QPainter) -> None:
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                     | QPainter.RenderHint.TextAntialiasing)


# ------------------------------------------------------------------ the clock under the ring
def dial_time(t: time.struct_time, h24: bool) -> str:
    """'9:41 AM', or '21:41' as 24-hour time."""
    if h24:
        return f"{t.tm_hour:02d}:{t.tm_min:02d}"
    return f"{(t.tm_hour % 12) or 12}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


def dial_date(t: time.struct_time) -> str:
    """'Fri, Oct 23'"""
    return f"{time.strftime('%a', t)}, {time.strftime('%b', t)} {t.tm_mday}"


def uses_fahrenheit() -> bool:
    return QLocale().measurementSystem() == QLocale.MeasurementSystem.ImperialUSSystem


def _cloud(cx: float, cy: float, k: float) -> QPainterPath:
    """A cloud about 0.8·k wide, centred on (cx, cy)."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(cx - 0.38 * k, cy - 0.02 * k, 0.76 * k, 0.26 * k), 0.13 * k, 0.13 * k)
    for x, y, r in ((-0.15, 0.0, 0.17), (0.09, -0.06, 0.23)):
        c = QPainterPath()
        c.addEllipse(QPointF(cx + x * k, cy + y * k), r * k, r * k)
        path = path.united(c)
    return path


def _sun(p: QPainter, c: QPointF, k: float, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(c, 0.2 * k, 0.2 * k)
    p.setPen(QPen(color, 0.075 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(QPointF(c.x() + math.cos(a) * 0.31 * k, c.y() + math.sin(a) * 0.31 * k),
                   QPointF(c.x() + math.cos(a) * 0.43 * k, c.y() + math.sin(a) * 0.43 * k))


def _moon(p: QPainter, c: QPointF, k: float, color: QColor) -> None:
    disc = QPainterPath()
    disc.addEllipse(c, 0.32 * k, 0.32 * k)
    bite = QPainterPath()
    bite.addEllipse(QPointF(c.x() + 0.2 * k, c.y() - 0.14 * k), 0.27 * k, 0.27 * k)
    p.fillPath(disc.subtracted(bite), color)


def draw_weather(p: QPainter, kind: str, c: QPointF, size: float, night: bool = False) -> None:
    """A small weather icon (clear, partly, cloudy, fog, rain, snow, storm) filling a `size` square at `c`."""
    k = size
    sun_c, moon_c = QColor("#FFD60A"), QColor("#E5E5EA")
    cloud_c, drop_c = white(0.95), QColor("#64D2FF")
    p.save()
    if kind == "clear":
        (_moon if night else _sun)(p, c, k, moon_c if night else sun_c)
    elif kind == "partly":
        body = QPointF(c.x() - 0.16 * k, c.y() - 0.16 * k)
        area = QPainterPath()
        area.addRect(QRectF(c.x() - k, c.y() - k, 2 * k, 2 * k))
        behind = area.subtracted(_cloud(c.x() + 0.08 * k, c.y() + 0.1 * k, k * 0.9))      # the sun peeks out
        p.setClipPath(behind, Qt.ClipOperation.IntersectClip)
        if night:
            _moon(p, body, k * 0.8, moon_c)
        else:
            _sun(p, body, k * 0.75, sun_c)
        p.setClipping(False)
        p.fillPath(_cloud(c.x() + 0.08 * k, c.y() + 0.1 * k, k * 0.78), cloud_c)
    elif kind == "fog":
        p.setPen(QPen(white(0.85), 0.08 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y, x0, x1 in ((-0.22, -0.34, 0.26), (0.0, -0.26, 0.38), (0.22, -0.38, 0.2)):
            p.drawLine(QPointF(c.x() + x0 * k, c.y() + y * k), QPointF(c.x() + x1 * k, c.y() + y * k))
    elif kind in ("rain", "snow", "storm"):
        p.fillPath(_cloud(c.x(), c.y() - 0.12 * k, k), cloud_c)
        if kind == "rain":
            p.setPen(QPen(drop_c, 0.07 * k, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for x in (-0.2, 0.0, 0.2):
                p.drawLine(QPointF(c.x() + x * k, c.y() + 0.24 * k), QPointF(c.x() + (x - 0.06) * k, c.y() + 0.42 * k))
        elif kind == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(white(0.95))
            for x, y in ((-0.2, 0.3), (0.0, 0.42), (0.2, 0.3)):
                p.drawEllipse(QPointF(c.x() + x * k, c.y() + y * k), 0.05 * k, 0.05 * k)
        else:
            bolt = QPolygonF([QPointF(c.x() + x * k, c.y() + y * k) for x, y in
                              ((0.04, 0.12), (-0.12, 0.32), (0.0, 0.32), (-0.06, 0.48), (0.14, 0.26), (0.02, 0.26),
                               (0.08, 0.12))])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sun_c)
            p.drawPolygon(bolt)
    else:                                   # cloudy
        p.fillPath(_cloud(c.x(), c.y() + 0.02 * k, k), cloud_c)
    p.restore()


class DialPainter:
    """All dial drawing + animation state. Window-agnostic so it can render offscreen.

    Anything that doesn't move while the dial is open (the refracting glass ring and centre disc,
    icon platters, colour glows, the lens' glass) is rendered once into cached pixmaps; each frame
    only composites those and draws what turns, plus the light that moves with it."""

    MARGIN = 84
    PLATTER_R = 27 * 1.3
    PLATTER_PAD = 14

    def __init__(self, items: list[Item], size: int = 600):
        self.items = items
        self.D = size
        self.state = EngineSnapshot()
        self._backdrop: liquid.Backdrop | None = None
        self._backdrop_ver = 0
        self.appear = Spring(0.0, 190.0, 20.0)     # a touch of overshoot
        self.rot = Spring(0.0, 520.0, 36.0)        # snappy follow of the hand, a little click of overshoot
        self.title = Spring(1.0, 240.0, 30.0)
        self.grip_vis = Spring(0.0, 260.0, 30.0)
        self.tracked_vis = Spring(1.0, 200.0, 28.0)
        self.point_vis = Spring(0.0, 240.0, 28.0)
        self.push_vis = Spring(0.0, 420.0, 34.0)
        self.px = Spring(0.0, 300.0, 30.0)
        self.py = Spring(0.0, 300.0, 30.0)
        self.tint = QColor(items[0].tint if items else "#0A84FF")
        self.open_t = 0.0
        self.exec_t: float | None = None
        self.exiting = False
        self._title_dir = 1.0
        self._prev_sel = 0
        self._cur_sel = 0
        self._cache_key = None
        self.text_scale = 1.0
        self.high_contrast = False
        self.reduce_motion = False
        self.controls: dict = {}       # gesture settings, so hints only mention what's switched on
        self.crumb = ""                # app name while inside a Quick nav ring
        self.keyboard = False          # opened from the keyboard shortcut
        self.level_kick = Spring(0.0, 260.0, 22.0)
        self.turned = 0.0              # degrees the primary ring has turned since it opened (360° gauge)
        self.gauge_vis = Spring(0.0, 200.0, 26.0)
        self._gauge_rot = 0.0
        self._appear_kc = (190.0, 20.0)
        # ring modes: "main" (items), "slides" (a cut per slide) or "video" (a full turn = the whole video)
        self.mode = "main"
        self.info: dict = {}           # what the mode ring says: title / sub / top, known, duration, edge
        self.volume_level = 0.0        # the volume slider: last level (0..1, or -1 when unknown)
        self.volume_until = 0.0
        self.volume_vis = Spring(0.0, 260.0, 30.0)
        self.volume_hot = False        # the mouse is over the volume button
        self.button_vis = Spring(0.0, 240.0, 26.0)   # the volume button (mouse / keyboard only)
        self.hot_vis = Spring(0.0, 320.0, 28.0)      # …lifting under the mouse
        self._pieces: dict = {}        # cached glass for the clock, the button and the slider
        self._canvas_wh = (float(self.size), float(self.size))
        self.clock_mode = "12h"        # the clock under the ring: 12h | 24h | off
        self.weather = None            # weather.Weather, or None to leave the icon out
        self.fahrenheit = uses_fahrenheit()
        self._crumb_rect: QRectF | None = None   # the "‹ App" breadcrumb, clickable from the mouse
        self.invalidate()

    def set_accessibility(self, text_scale: float = 1.0, high_contrast: bool = False,
                          reduce_motion: bool = False) -> None:
        if high_contrast != self.high_contrast:
            self._cache_key = None
        self.text_scale = max(0.8, min(1.8, float(text_scale)))
        self.high_contrast = bool(high_contrast)
        self.reduce_motion = bool(reduce_motion)
        if reduce_motion:          # critically damped: no overshoot, no wobble
            self.rot.k, self.rot.c = 520.0, 46.0
            self._appear_kc = (320.0, 36.0)
        else:
            self.rot.k, self.rot.c = 520.0, 36.0
            self._appear_kc = (190.0, 20.0)

    def set_mode(self, mode: str, items: list[Item], info: dict, state: EngineSnapshot) -> None:
        """Switch between the item ring and a slides / video ring, with the same short zoom as Quick nav."""
        self.mode = mode
        self.info = dict(info)
        self.set_level(items, "", state)
        if mode == "video":
            self.level_kick.snap(0.0)          # the video ring takes over the main one in place
        elif mode == "volume" and not self.reduce_motion:
            # It turns itself to where the volume already is: from 0° round to the level.
            self.rot.snap(0.0)
            self.rot.target = state.position

    def show_volume(self, level: float, seconds: float = 1.6) -> None:
        self.volume_level = level
        self.volume_until = time.monotonic() + seconds

    def set_level(self, items: list[Item], crumb: str, state: EngineSnapshot) -> None:
        """Swap to another ring (Quick nav in / Back out) with a short zoom."""
        self.items = items
        self.crumb = crumb
        self._cache_key = None
        self.state = state
        self.rot.snap(state.position)
        self._gauge_rot = state.position
        self._cur_sel = self._prev_sel = state.selected
        self.title.snap(1.0)
        self.tint = QColor(items[state.selected % max(1, len(items))].tint)
        self.exec_t = None
        self.exiting = False
        self.appear.target = 1.0
        if self.reduce_motion:
            self.open_t = 1.0
        else:
            self.open_t = 0.0
            self.level_kick.snap(1.0)
            self.level_kick.target = 0.0

    # ------------------------------------------------------------- state
    @property
    def size(self) -> int:
        return self.D + 2 * self.MARGIN

    @property
    def backdrop(self) -> liquid.Backdrop | None:
        return self._backdrop

    @backdrop.setter
    def backdrop(self, bd: liquid.Backdrop | None) -> None:
        self._backdrop = bd
        self._backdrop_ver += 1

    def invalidate(self) -> None:
        self._static: QPixmap | None = None
        self._lens_brush: QBrush | None = None
        self._platters: dict = {}
        self._glows: dict = {}
        self._fonts: dict = {}

    def set_items(self, items: list[Item]) -> None:
        self.items = items
        self._cur_sel = self._prev_sel = 0
        self._cache_key = None

    def set_size(self, size: int) -> None:
        self.D = int(size)
        self._cache_key = None

    def begin(self, state: EngineSnapshot) -> None:
        self.state = state
        self.appear.snap(0.0)
        self.appear.target = 1.0
        self.appear.k, self.appear.c = self._appear_kc
        self.rot.snap(state.position)
        self._gauge_rot = state.position
        self.turned = 0.0
        self.gauge_vis.snap(0.0)
        self.title.snap(1.0)
        self._cur_sel = self._prev_sel = state.selected
        self.tint = QColor(self.items[state.selected].tint)
        self.open_t = 0.0
        self.exec_t = None
        self.exiting = False

    def set_state(self, state: EngineSnapshot) -> None:
        n = max(1, len(self.items))
        if state.selected != self._cur_sel and state.phase == Phase.OPEN:
            diff = (state.selected - self._cur_sel) % n
            self._title_dir = 1.0 if diff <= n / 2 else -1.0
            self._prev_sel, self._cur_sel = self._cur_sel, state.selected
            self.title.snap(0.0)
            self.title.target = 1.0
        self.state = state
        if state.phase == Phase.OPEN:
            sel = round(state.position)
            if self.mode == "video":
                self.rot.target = state.position        # continuous: the dial is the timeline
            elif state.grip_mode:
                # The engine already strains the knob against its detent: follow it as is.
                self.rot.target = state.position
            else:
                # Magnetic detent: the ring leans toward the hand, then snaps to the item.
                self.rot.target = sel + (state.position - sel) * 0.42
        self.grip_vis.target = 1.0 if state.gripping else 0.0
        self.tracked_vis.target = 1.0 if (state.hand_present and state.facing) else 0.0
        self.point_vis.target = 1.0 if state.pointing else 0.0
        self.push_vis.target = state.push if state.pointing else 0.0
        self.px.target = max(-1.0, min(1.0, state.pointer[0] / 1.6))
        self.py.target = max(-1.0, min(1.0, state.pointer[1] / 1.6))

    def close(self) -> None:
        self.appear.target = 0.0

    def execute(self) -> None:
        self.exec_t = 0.0

    def tick(self, dt: float) -> bool:
        """Advance animations. Returns False once fully closed."""
        dt = min(dt, 1 / 30)
        self.open_t += dt
        if self.exec_t is not None:
            self.exec_t += dt
            if self.exec_t > 0.34 and self.appear.target != 0.0:
                self.exiting = True
                self.appear.target = 0.0
                self.appear.k, self.appear.c = 120.0, 22.0
        self.volume_vis.target = 1.0 if (time.monotonic() < self.volume_until and self.mode != "volume") else 0.0
        # From the mouse or the keyboard a button stands in for the two-finger slider.
        self.button_vis.target = 1.0 if self.keyboard else 0.0
        self.hot_vis.target = 1.0 if self.volume_hot else 0.0
        for s in self._springs():
            s.step(dt)
        if self.items:
            # Ring turned clockwise = positive degrees. Only the primary ring keeps count.
            if not self.crumb and self.mode == "main":
                self.turned += (self._gauge_rot - self.rot.value) * 360.0 / len(self.items)
            self._gauge_rot = self.rot.value
        self.gauge_vis.target = 1.0 if (not self.crumb and self.mode == "main" and abs(self.turned) > 0.5) else 0.0
        if self.items:
            target = QColor(self.items[self.state.selected % len(self.items)].tint)
            self.tint = _mix(self.tint, target, min(1.0, dt * 10))
        if self.appear.target == 0.0 and self.appear.value < 0.012 and abs(self.appear.velocity) < 0.5:
            return False
        return True

    def _springs(self) -> tuple[Spring, ...]:
        return (self.appear, self.rot, self.title, self.grip_vis, self.tracked_vis, self.point_vis, self.push_vis,
                self.px, self.py, self.level_kick, self.gauge_vis, self.volume_vis, self.button_vis, self.hot_vis)

    def visual_key(self) -> tuple:
        """Everything a frame shows. While it stays the same the window isn't painted again: a rotary held
        still costs next to nothing, however long it stays open."""
        t, st = self.tint, self.state
        # only what the dial draws from the hand: jitter in the rest (facing, grip strength…) changes nothing here
        hand = (st.phase, st.hand_present, st.facing, st.gripping, st.pointing, st.grip_mode, st.interacted,
                st.selected, st.scrolling, st.sliding, round(st.position, 3), round(st.dwell, 3), round(st.push, 3))
        return (tuple(round(s.value, 4) for s in self._springs()), (t.red(), t.green(), t.blue()),
                self.exec_t, self.exiting, round(self.open_t, 3) if self.open_t < 2.0 else None, hand,
                self.mode, sorted(self.info.items(), key=lambda kv: kv[0]), self.crumb, self.keyboard,
                self.volume_level, self.volume_hot, self._backdrop_ver, self._cache_key, self.weather,
                [(it.name, it.subtitle, it.glyph, it.tint, it.icon) for it in self.items],
                sorted(self.controls.items(), key=lambda kv: kv[0]), self.clock_mode, self.text_scale, self.high_contrast,
                self.reduce_motion, time.localtime().tm_min if self.clock_mode != "off" else 0)

    # ------------------------------------------------------------- geometry & caches
    def _geom(self):
        R = self.D / 2 - 8
        Rin = R * 0.585
        Rmid = (R + Rin) / 2
        rc = Rin - 13
        # The glass drop at 12 o'clock: just big enough for the chosen icon, smaller still on the slides /
        # video / volume rings, which have no icon under it.
        lens_r = ((R - Rin) / 2 - 5) * (0.84 if self.mode == "main" else 0.62)
        return R, Rin, Rmid, rc, lens_r

    def _ensure_cache(self, W: float, H: float, dpr: float) -> None:
        key = (W, H, dpr, self._backdrop_ver, len(self.items), id(self.items))
        if key != self._cache_key:
            self.invalidate()
            self._cache_key = key
        if self._static is None:
            self._static = self._render_static(W, H, dpr)
            self._lens_brush = self._render_lens(W, H)

    def prepare(self, W: float, H: float, dpr: float) -> None:
        """Build the cached layers now (before the window shows) so the first frame doesn't stall."""
        self._ensure_cache(W, H, dpr)

    def prewarm(self, dpr: float) -> None:
        """Work out the glass geometry once at idle time, so even the first opening is instant."""
        W = H = self.size
        gw = max(2, int(W * dpr) // 2)
        saved, saved_ver = self._backdrop, self._backdrop_ver
        self._backdrop = liquid.Backdrop(np.zeros((gw, gw, 3), np.float32), gw / W, 0.3)
        self._render_static(W, H, dpr)
        self._render_lens(W, H)
        self._backdrop, self._backdrop_ver = saved, saved_ver
        self._cache_key = None

    def _ring_paths(self) -> tuple[QPainterPath, QPainterPath]:
        R, Rin, Rmid, rc, lens_r = self._geom()
        ring = QPainterPath()
        ring.addEllipse(QPointF(0, 0), R, R)
        ring.addEllipse(QPointF(0, 0), Rin, Rin)
        ring.setFillRule(Qt.FillRule.OddEvenFill)
        return ring, circle(0, 0, rc)

    def _render_static(self, W: float, H: float, dpr: float) -> QPixmap:
        """Shadow, then the glass ring and centre disc: refracted backdrop, tint, sheen."""
        R, Rin, Rmid, rc, lens_r = self._geom()
        pm = _canvas(W, H, dpr)
        p = QPainter(pm)
        _hq(p)
        p.translate(W / 2, H / 2)
        O = QPointF(0, 0)
        hc = self.high_contrast
        bd = self._backdrop
        ring, disc = self._ring_paths()

        soft_shadow(p, O, R, 50, 0.22, dy=16)
        soft_shadow(p, O, rc, 10, 0.10, dy=4)
        lum = 0.5
        if bd is not None and not hc:
            img = liquid.render(bd, [liquid.Annulus(0, 0, Rin, R, bevel=(R - Rin) * 0.36, bend=0.8, magnify=1.03),
                                     liquid.Circle(0, 0, rc, bevel=rc * 0.24, bend=0.75, magnify=1.07)],
                                origin=(-W / 2, -H / 2))
            b = liquid.brush(img, bd.f, (-W / 2, -H / 2))
            p.fillPath(ring, b)
            p.fillPath(disc, b)
            lum = bd.lum
        # Slightly see-through: a light tint that darkens only as much as the backdrop's brightness needs.
        p.fillPath(ring, QColor(10, 10, 14, 205) if hc else qcolor("#0e0e14", liquid.tint_alpha(lum, 0.12, 0.36)))
        lg = QLinearGradient(0, -R, 0, R)
        lg.setColorAt(0.0, white(0.13))
        lg.setColorAt(0.5, white(0.03))
        lg.setColorAt(1.0, white(0.07))
        p.fillPath(ring, lg)
        p.strokePath(circle(0, 0, R + 0.8), QPen(black(0.18), 1.0))
        p.fillPath(disc, QColor(6, 6, 10, 230) if hc else qcolor("#0c0c12", liquid.tint_alpha(lum, 0.30, 0.56)))
        glass_disc(p, O, rc, QColor(0, 0, 0, 0), sheen=0.6)
        stroke_rim(p, circle(0, 0, R), O, 1.4, 1.0)
        stroke_rim(p, circle(0, 0, Rin), O, 1.1, 0.7, light_deg=305)
        stroke_rim(p, disc, O, 1.3, 0.85)
        p.end()
        return pm

    def _render_lens(self, W: float, H: float) -> QBrush | None:
        """The selection lens: a thick magnifying drop sitting on the ring at 12 o'clock."""
        bd = self._backdrop
        if bd is None or self.high_contrast:
            return None
        R, Rin, Rmid, rc, lens_r = self._geom()
        img = liquid.render(bd, [liquid.Circle(0, -Rmid, lens_r, bevel=lens_r * 0.6, bend=0.7, magnify=1.22)],
                            origin=(-W / 2, -H / 2), dispersion=0.1, highlight=0.8)
        return liquid.brush(img, bd.f, (-W / 2, -H / 2))

    def _glow(self, tint_name: str, dpr: float) -> QPixmap:
        key = (tint_name, dpr)
        pm = self._glows.get(key)
        if pm is None:
            R = self._geom()[0]
            G = R + 90
            tint = QColor(tint_name)
            pm = _canvas(2 * G, 2 * G, dpr)
            p = QPainter(pm)
            _hq(p)
            p.translate(G, G)
            g = QRadialGradient(QPointF(0, 0), G)
            k = R / G
            g.setColorAt(k * 0.86, qcolor(tint, 0.0))
            g.setColorAt(k, qcolor(tint, 0.26))
            g.setColorAt(1.0, qcolor(tint, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(0, 0), G, G)
            p.end()
            self._glows[key] = pm
        return pm

    def _platter_pm(self, i: int, dpr: float) -> QPixmap:
        it = self.items[i]
        key = (i, dpr, it.icon, it.glyph, it.tint, it.kind, self.high_contrast)
        pm = self._platters.get(key)
        if pm is None:
            r = self.PLATTER_R
            box = 2 * (r + self.PLATTER_PAD)
            pm = _canvas(box, box, dpr)
            p = QPainter(pm)
            _hq(p)
            self._draw_platter(p, QPointF(box / 2, box / 2), r, it)
            self._decorate(p, QPointF(box / 2, box / 2), r, it)
            p.end()
            self._platters[key] = pm
        return pm

    # ------------------------------------------------------------- paint
    def paint(self, p: QPainter, W: float, H: float) -> None:
        if not self.items:
            return
        _hq(p)
        a = self.appear.value
        opacity = max(0.0, min(1.0, a * 1.25))
        if opacity <= 0.002:
            return
        dev = p.device()
        dpr = dev.devicePixelRatioF() if dev is not None else 1.0
        self._ensure_cache(W, H, dpr)
        self._canvas_wh = (float(W), float(H))

        scale = 1.0 + 0.08 * (1.0 - a) if self.exiting else 0.84 + 0.16 * a
        if self.reduce_motion:
            scale = 1.0
        scale *= 1.0 - 0.07 * self.level_kick.value
        if abs(scale - 1.0) < 0.004:
            scale = 1.0           # pixel-aligned: the cached layers blit straight through, no resampling
        p.setTransform(QTransform().translate(W / 2, H / 2).scale(scale, scale) * p.transform())

        n = len(self.items)
        R, Rin, Rmid, rc, lens_r = self._geom()
        step = 360.0 / n
        st = self.state
        O = QPointF(0, 0)

        # ---- colour glow of the selected item (cross-faded), then the static glass body
        t = max(0.0, min(1.0, self.title.value))
        G = R + 90
        grect = QRectF(-G, -G, 2 * G, 2 * G)
        if t < 1.0:
            p.setOpacity(opacity * (1 - t))
            p.drawPixmap(grect, self._glow(self.items[self._prev_sel % n].tint, dpr), QRectF())
        p.setOpacity(opacity * t)
        p.drawPixmap(grect, self._glow(self.items[self._cur_sel % n].tint, dpr), QRectF())
        p.setOpacity(opacity)
        p.drawPixmap(QRectF(-W / 2, -H / 2, W, H), self._static, QRectF())
        self._light(p, R, Rin, rc)
        if self.mode != "main":
            self._paint_mode(p, n, step, opacity)
            self._clock(p, R)
            self._volume(p, R)
            self._volume_btn(p)
            return

        # ---- bezel ticks that turn with the hand
        rot_deg = -self.rot.value * step
        dim = QPainterPath()
        for j in range(120):
            ang = j * 3.0 + rot_deg
            d = abs(_wrap180(ang))
            if d < 42:
                self._tick(p, ang, R - 9, 4.5, 0.13 + 0.55 * (1 - d / 42), 1.0)
            else:
                a_ = math.radians(ang)
                s_, c_ = math.sin(a_), -math.cos(a_)
                dim.moveTo(s_ * (R - 13.5), c_ * (R - 13.5))
                dim.lineTo(s_ * (R - 9), c_ * (R - 9))
        p.strokePath(dim, QPen(white(0.13), 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(n):
            ang = (i + 0.5) * step + rot_deg
            d = abs(_wrap180(ang))
            self._tick(p, ang, R - 9, 9.0, 0.28 + 0.5 * max(0.0, 1 - d / 42), 1.4)

        self._gauge(p, R)

        # ---- the lens (fixed at 12 o'clock): body first, highlights after the icons
        lens, Lc = self._lens_body(p, Rmid, lens_r)

        # ---- items around the ring (cached platters, scaled under the lens)
        sel = st.selected
        r0 = self.PLATTER_R
        box0 = 2 * (r0 + self.PLATTER_PAD)
        for i in range(n):
            rel = _wrap180((i - self.rot.value) * step)
            pos = angle_point(Rmid, rel)
            f = smoothstep(max(0.0, 1 - abs(rel) / step))
            order = min((i - sel) % n, (sel - i) % n)
            si = 1.0 if self.reduce_motion else ease_out_cubic((self.open_t - 0.035 * order) / 0.42)
            if si <= 0:
                continue
            s = (1 + 0.30 * f) * (0.55 + 0.45 * si)
            bw = box0 * (27 * s / r0)
            p.setOpacity(opacity * (0.70 + 0.30 * f) * si)
            p.drawPixmap(QRectF(pos.x() - bw / 2, pos.y() - bw / 2, bw, bw), self._platter_pm(i, dpr), QRectF())
        p.setOpacity(opacity)

        # ---- lens highlights over the icons (so they read as "inside" the glass)
        self._lens_highlights(p, lens, Lc, lens_r)
        pv = self.push_vis.value
        if pv > 0.01 and self.exec_t is None:
            r = lens_r + 7
            rect = QRectF(Lc.x() - r, Lc.y() - r, 2 * r, 2 * r)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white(0.16 * self.point_vis.value), 3.0))
            p.drawEllipse(rect)
            p.setPen(QPen(qcolor(self.tint.lighter(135), 0.95), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-360 * 16 * min(1.0, pv)))
        if self.exec_t is not None and self.exec_t < 0.6 and not self.reduce_motion:
            tt = self.exec_t / 0.6
            rr = lens_r * (1 + 1.6 * ease_out_cubic(tt))
            p.strokePath(circle(Lc.x(), Lc.y(), rr), QPen(qcolor(self.tint, 0.85 * (1 - tt)), 2.0))

        # ---- grip glow around the centre disc
        g = self.grip_vis.value
        if g > 0.01:
            gg = QRadialGradient(O, rc + 26)
            kk = rc / (rc + 26)
            gg.setColorAt(kk * 0.9, qcolor(self.tint, 0.0))
            gg.setColorAt(kk, qcolor(self.tint, 0.45 * g))
            gg.setColorAt(1.0, qcolor(self.tint, 0.0))
            band = QPainterPath()
            band.addEllipse(O, rc + 26, rc + 26)
            band.addEllipse(O, rc * 0.88, rc * 0.88)
            band.setFillRule(Qt.FillRule.OddEvenFill)
            p.fillPath(band, gg)
            p.strokePath(circle(0, 0, rc + 1.5), QPen(qcolor(self.tint.lighter(130), 0.95 * g), 2.4))

        # ---- dwell progress
        if st.dwell > 0.001 and self.exec_t is None:
            r = rc + 9
            rect = QRectF(-r, -r, 2 * r, 2 * r)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white(0.14), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(rect)
            p.setPen(QPen(white(0.95), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-360 * 16 * st.dwell))

        # ---- finger mode: a small glass fingertip that follows the index finger
        pv = self.point_vis.value
        if pv > 0.01:
            cpos = QPointF(self.px.value * rc * 0.55, self.py.value * rc * 0.55)
            cr = 9.0 * (0.6 + 0.4 * pv)
            p.save()
            p.setOpacity(p.opacity() * pv)
            soft_shadow(p, cpos, cr, 6, 0.25, dy=2)
            dot = circle(cpos.x(), cpos.y(), cr)
            p.fillPath(dot, qcolor(self.tint, 0.55))
            glass_disc(p, cpos, cr, QColor(255, 255, 255, 40), sheen=1.4)
            stroke_rim(p, dot, cpos, 1.2, 1.0)
            p.restore()

        self._texts(p, rc)
        self._clock(p, R)
        self._volume(p, R)
        self._volume_btn(p)

    def _lens_body(self, p: QPainter, Rmid: float, lens_r: float) -> tuple[QPainterPath, QPointF]:
        v = self.rot.velocity
        sx = 1.0 if self.reduce_motion else 1 + min(abs(v) * 0.055, 0.38)
        sy = 1.0 if self.reduce_motion else 1 - min(abs(v) * 0.028, 0.14)
        pulse = 1.0 - 0.07 * max(0.0, self.push_vis.value)          # pressed in by a finger push
        if self.exec_t is not None:
            pulse = 1 + 0.2 * math.sin(math.pi * min(self.exec_t / 0.3, 1.0))
        Lc = QPointF(0, -Rmid)
        lens = QPainterPath()
        lens.addEllipse(Lc, lens_r * sx * pulse, lens_r * sy * pulse)
        soft_shadow(p, Lc, lens_r * sy, 12, 0.20, dy=6)
        if self._lens_brush is not None:
            p.fillPath(lens, self._lens_brush)
        p.fillPath(lens, qcolor(self.tint, 0.18))
        p.fillPath(lens, white(0.05))
        le = QRadialGradient(Lc, lens_r * max(sx, sy) * pulse)
        le.setColorAt(0.70, white(0.0))
        le.setColorAt(1.0, white(0.24))
        p.fillPath(lens, le)
        return lens, Lc

    def _lens_highlights(self, p: QPainter, lens: QPainterPath, Lc: QPointF, lens_r: float) -> None:
        p.save()
        p.setClipPath(lens)
        hl = QRadialGradient(QPointF(Lc.x() - lens_r * 0.35, Lc.y() - lens_r * 0.62), lens_r * 0.95)
        hl.setColorAt(0.0, white(0.34))
        hl.setColorAt(1.0, white(0.0))
        p.fillPath(lens, hl)
        bl = QRadialGradient(QPointF(Lc.x() + lens_r * 0.3, Lc.y() + lens_r * 0.9), lens_r * 0.8)
        bl.setColorAt(0.0, white(0.16))
        bl.setColorAt(1.0, white(0.0))
        p.fillPath(lens, bl)
        if self.exec_t is not None:
            p.fillPath(lens, white(max(0.0, 0.55 * (1 - self.exec_t / 0.35))))
        p.restore()
        stroke_rim(p, lens, Lc, 1.6, 1.0)

    # ------------------------------------------------------------- slides / video rings
    def _band(self, p: QPainter, a0: float, span: float, r0: float, r1: float, color: QColor) -> None:
        """Fill the part of the ring between radii r0..r1 from a0 (clockwise from 12 o'clock) for `span` degrees."""
        if abs(span) < 0.05:
            return
        path = QPainterPath()
        q0, q1 = QRectF(-r1, -r1, 2 * r1, 2 * r1), QRectF(-r0, -r0, 2 * r0, 2 * r0)
        path.arcMoveTo(q0, 90 - a0)
        path.arcTo(q0, 90 - a0, -span)
        path.arcTo(q1, 90 - a0 - span, span)
        path.closeSubpath()
        p.fillPath(path, color)

    def _cut(self, p: QPainter, deg: float, r0: float, r1: float, alpha: float, width: float) -> None:
        a = math.radians(deg)
        s_, c_ = math.sin(a), -math.cos(a)
        p.setPen(QPen(white(alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(s_ * r0, c_ * r0), QPointF(s_ * r1, c_ * r1))

    def _paint_mode(self, p: QPainter, n: int, step: float, opacity: float) -> None:
        """A slides ring (a cut between every two slides, no icons), a video ring (a full turn is the whole
        video, the played part lit) or a volume ring (a full turn is silent to full, the level lit), with the lens
        on top and the slide / time / level in the middle."""
        R, Rin, Rmid, rc, lens_r = self._geom()
        st = self.state
        r0, r1 = Rin + 5, R - 5
        rot_deg = -self.rot.value * step
        info = self.info
        if self.mode == "slides":
            # the slide under the lens, lit
            self._band(p, st.selected * step + rot_deg - step / 2, step, r0, r1, qcolor(self.tint, 0.30))
            every = max(1, math.ceil(3.0 / step))          # very many slides: not every cut, or it's a blur
            for i in range(n):
                ang = (i - 0.5) * step + rot_deg
                d = abs(_wrap180(ang))
                edge = info.get("known") and i == 0
                if edge:                                     # the start / end: a firm stop
                    self._cut(p, ang, r0 - 3, r1 + 3, 0.95, 3.2)
                elif i % every == 0:
                    major = info.get("known") and (i % 10 == 0 or (n <= 40 and i % 5 == 0))
                    self._cut(p, ang, r0 + (0 if major else 6), r1 - (0 if major else 6),
                              0.22 + 0.5 * max(0.0, 1 - d / 70), 2.0 if major else 1.4)
        elif self.mode == "volume":
            # Discreet: fine ticks turning with the ring, and the level as the thin 0–360° gauge outside it.
            for j in range(n + 1):
                ang = j * step + rot_deg
                d = abs(_wrap180(ang))
                major = j % 5 == 0
                self._cut(p, ang, r1 - (9 if major else 5), r1, 0.14 + 0.45 * max(0.0, 1 - d / 60),
                          1.4 if major else 1.0)
            self._level_gauge(p, R, max(0.0, min(1.0, self.rot.value / max(1, n))))
        else:
            frac = self.rot.value / max(1, n)
            full = info.get("duration", 0) > 0
            if full:
                self._band(p, rot_deg, frac * 360.0, Rmid - (R - Rin) * 0.16, Rmid + (R - Rin) * 0.16,
                           qcolor(self.tint, 0.55))
            ticks = 60 if full else 24
            for j in range(ticks):
                ang = j * 360.0 / ticks + rot_deg
                d = abs(_wrap180(ang))
                major = j % 5 == 0
                self._cut(p, ang, r1 - (14 if major else 8), r1, 0.20 + 0.5 * max(0.0, 1 - d / 60),
                          1.8 if major else 1.1)
            if full:
                self._cut(p, rot_deg, r0 - 3, r1 + 3, 0.95, 3.2)    # the start (and the end, a full turn on)

        lens, Lc = self._lens_body(p, Rmid, lens_r)
        if self.mode == "slides" and info.get("known") and n <= 60:
            nf, fm_n = self._font("display", (15 if n <= 30 else 11) * self.text_scale, QFont.Weight.DemiBold)
            p.setFont(nf)
            for i in range(n):
                rel = _wrap180(i * step + rot_deg)
                if abs(rel) > 100:
                    continue
                pos = angle_point(Rmid, rel)
                f = smoothstep(max(0.0, 1 - abs(rel) / max(step, 1e-3)))
                p.setPen(white((0.35 + 0.6 * f) * (1 - abs(rel) / 110)))
                p.drawText(QRectF(pos.x() - 22, pos.y() - 12, 44, 24), Qt.AlignmentFlag.AlignCenter, str(i + 1))
        self._lens_highlights(p, lens, Lc, lens_r)

        # the slide / time in the middle
        k = self.text_scale
        hc = self.high_contrast
        width = 2 * (rc - 22)
        title = info.get("title", "")
        if self.mode == "video" and info.get("duration", 0) > 0:
            from ..modes import fmt_time
            title = fmt_time(max(0.0, min(1.0, self.rot.value / max(1, n))) * info["duration"])
        elif self.mode == "volume":
            title = f"{round(max(0.0, min(1.0, self.rot.value / max(1, n))) * 100):.0f}%"
        tf, fm_t = self._font("display", 30 * k, QFont.Weight.DemiBold)
        sf, fm_s = self._font("text", 14 * k, QFont.Weight.Medium if hc else QFont.Weight.Normal)
        th, sh = fm_t.height(), fm_s.height()
        p.setFont(tf)
        p.setPen(black(0.28))
        p.drawText(QRectF(-width / 2, -th + 3.2, width, th), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(white(0.98))
        p.drawText(QRectF(-width / 2, -th + 2, width, th), Qt.AlignmentFlag.AlignCenter, title)
        p.setFont(sf)
        p.setPen(white(0.9 if hc else 0.6))
        p.drawText(QRectF(-width / 2, 4, width, sh + 2), Qt.AlignmentFlag.AlignCenter,
                   fm_s.elidedText(info.get("sub", ""), Qt.TextElideMode.ElideRight, width))
        top = info.get("top", "")
        if top:
            cf, fm_c = self._font("text", 11.5 * k, QFont.Weight.DemiBold)
            p.setFont(cf)
            p.setPen(white(0.85 if hc else 0.55))
            p.drawText(QRectF(-width / 2, -rc * 0.52 - 14 - 12 * k, width, 14 * k + 4), Qt.AlignmentFlag.AlignCenter,
                       fm_c.elidedText(top.upper(), Qt.TextElideMode.ElideRight, width * 0.8))
        hint = self.hint()
        hf, fm_h = self._font("text", 12.5 * k, QFont.Weight.Medium if hc else QFont.Weight.Normal)
        p.setFont(hf)
        p.setPen(white(0.88 if hc else 0.55))
        p.drawText(QRectF(-width / 2, rc * 0.47 + (k - 1.0) * 10 - fm_h.height() / 2, width, fm_h.height()),
                   Qt.AlignmentFlag.AlignCenter, fm_h.elidedText(hint, Qt.TextElideMode.ElideRight, width - 16))

    def volume_rect(self) -> QRectF:
        """The two-finger volume slider's track, relative to the centre of the ring."""
        R = self._geom()[0]
        h, w = R * 0.95, 14.0
        x = R + 38
        return QRectF(x - w / 2, -h / 2, w, h)

    def volume_button(self) -> tuple[QPointF, float]:
        """Centre and radius of the volume button beside the ring (the mouse and keyboard's way to the volume)."""
        R = self._geom()[0]
        return QPointF(R + 46, 0.0), 25.0

    # -- liquid glass pieces beside the ring: the same refracting glass as the ring, cached per backdrop
    def _glass_brush(self, name: str, shape) -> QBrush | None:
        bd = self._backdrop
        if bd is None or self.high_contrast:
            return None
        W, H = self._canvas_wh
        key = (repr(shape), self._backdrop_ver, W, H)
        got = self._pieces.get(name)
        if got is None or got[0] != key:
            img = liquid.render(bd, [shape], origin=(-W / 2, -H / 2), highlight=0.9)
            got = self._pieces[name] = (key, liquid.brush(img, bd.f, (-W / 2, -H / 2)))
        return got[1]

    def _glass_piece(self, p: QPainter, name: str, path: QPainterPath, shape, centre: QPointF, r: float,
                     glow: QColor | None = None) -> None:
        """A small liquid-glass body: soft shadow, the refracted backdrop, a tint as dark as the backdrop
        needs, a top sheen, an optional coloured glow, and the rim that catches the light."""
        hc = self.high_contrast
        soft_shadow(p, QPointF(centre.x(), centre.y()), r, 10, 0.18, dy=5)
        b = self._glass_brush(name, shape)
        if b is not None:
            p.fillPath(path, b)
        lum = self._backdrop.lum if self._backdrop is not None else 0.5
        p.fillPath(path, QColor(10, 10, 14, 215) if hc else qcolor("#0e0e14", liquid.tint_alpha(lum, 0.14, 0.40)))
        rect = path.boundingRect()
        lg = QLinearGradient(0, rect.top(), 0, rect.bottom())
        lg.setColorAt(0.0, white(0.16))
        lg.setColorAt(0.5, white(0.03))
        lg.setColorAt(1.0, white(0.08))
        p.fillPath(path, lg)
        if glow is not None:
            p.fillPath(path, glow)
        if hc:
            p.strokePath(path, QPen(white(0.9), 1.4))
        else:
            stroke_rim(p, path, centre, 1.3, 0.95)

    def _volume(self, p: QPainter, R: float) -> None:
        """The two-finger slider's level: a tall liquid-glass capsule to the right of the ring while it's in use."""
        v = self.volume_vis.value
        if v <= 0.01:
            return
        level = self.volume_level
        tr = self.volume_rect()
        h, w, x = tr.height(), tr.width(), tr.center().x()
        p.save()
        p.setOpacity(p.opacity() * min(1.0, v))
        track = QPainterPath()
        track.addRoundedRect(tr, w / 2, w / 2)
        shape = liquid.RoundRect(tr.left(), tr.top(), tr.right(), tr.bottom(), w / 2, bevel=w * 0.45, bend=0.8)
        self._glass_piece(p, "slider", track, shape, tr.center(), w / 2)
        if level >= 0:
            fill = QPainterPath()
            fh = max(w - 4, (h - 4) * level)
            fill.addRoundedRect(QRectF(x - w / 2 + 2, h / 2 - 2 - fh, w - 4, fh), (w - 4) / 2, (w - 4) / 2)
            p.fillPath(fill, white(0.92))
        p.setFont(font(ICONS, 16 * self.text_scale))
        p.setPen(white(0.95))
        glyph = "\uE74F" if level == 0 else "\uE767"           # muted · speaker
        p.drawText(QRectF(x - 16, -h / 2 - 30, 32, 24), Qt.AlignmentFlag.AlignCenter, glyph)
        if level >= 0:
            lf, fm = self._font("text", 12 * self.text_scale, QFont.Weight.DemiBold)
            p.setFont(lf)
            p.drawText(QRectF(x - 24, h / 2 + 6, 48, fm.height()), Qt.AlignmentFlag.AlignCenter,
                       f"{level * 100:.0f}%")
        p.restore()

    def _volume_btn(self, p: QPainter) -> None:
        """From the mouse or the keyboard: a liquid-glass button beside the ring. Clicking it turns the ring
        into a volume ring (turn it to change the volume); clicking it again goes back to the items."""
        v = self.button_vis.value
        if v <= 0.01:
            return
        c, r = self.volume_button()
        on = self.mode == "volume"
        hot = self.hot_vis.value
        r *= 1.0 + 0.06 * hot
        p.save()
        p.setOpacity(p.opacity() * min(1.0, v))
        path = circle(c.x(), c.y(), r)
        shape = liquid.Circle(c.x(), c.y(), round(r, 1), bevel=r * 0.55, bend=0.75, magnify=1.12)
        glow = qcolor(self.tint, 0.35 if on else 0.14 * hot) if (on or hot > 0.01) else None
        self._glass_piece(p, "volume_btn", path, shape, c, r, glow)
        level = self.volume_level
        if level >= 0:                         # the level, as an arc around the button
            rr = r + 5
            rect = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white(0.14), 2.6))
            p.drawEllipse(rect)
            p.setPen(QPen(qcolor(self.tint.lighter(135), 0.95) if on else white(0.85), 2.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-360 * 16 * level))
        p.setFont(font(ICONS, 19 * self.text_scale))
        p.setPen(black(0.25))
        glyph = "\uE74F" if level == 0 else "\uE767"
        p.drawText(QRectF(c.x() - r, c.y() - r + 1.2, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, glyph)
        p.setPen(white(1.0))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, glyph)
        lf, fm = self._font("text", 11.5 * self.text_scale, QFont.Weight.DemiBold)
        p.setFont(lf)
        p.setPen(white(0.85 if on or hot > 0.5 else 0.6))
        label = "Done" if on else ("Volume" if level < 0 else f"{level * 100:.0f}%")
        p.drawText(QRectF(c.x() - 36, c.y() + r + 9, 72, fm.height()), Qt.AlignmentFlag.AlignCenter, label)
        p.restore()

    def _clock(self, p: QPainter, R: float) -> None:
        """The time, the date and the weather in a small glass capsule under the ring."""
        if self.clock_mode not in ("12h", "24h"):
            return
        k = self.text_scale
        hc = self.high_contrast
        t = time.localtime()
        tf, fm_t = self._font("display", 16 * k, QFont.Weight.DemiBold)
        df, fm_d = self._font("text", 13 * k, QFont.Weight.Medium if hc else QFont.Weight.Normal)
        tm, dt = dial_time(t, self.clock_mode == "24h"), dial_date(t)
        gap, pad = 10.0 * k, 14.0 * k
        w = fm_t.horizontalAdvance(tm) + gap + fm_d.horizontalAdvance(dt)
        wx = self.weather
        icon = 18.0 * k
        temp = wx.temperature(self.fahrenheit) if wx is not None else ""
        if wx is not None:
            w += gap + 1 + gap + icon + 4 * k + fm_d.horizontalAdvance(temp)
        h = max(fm_t.height(), icon) + 12 * k
        cy = R + 18 + h / 2
        bw = math.ceil((w + 2 * pad) / 8) * 8              # in steps, so the glass is redone only now and then
        box = QRectF(-bw / 2, round(cy - h / 2), bw, round(h))
        pill = QPainterPath()
        pill.addRoundedRect(box, box.height() / 2, box.height() / 2)
        p.save()
        shape = liquid.RoundRect(box.left(), box.top(), box.right(), box.bottom(), box.height() / 2,
                                 bevel=box.height() * 0.42, bend=0.8, magnify=1.03)
        self._glass_piece(p, "clock", pill, shape, box.center(), box.height() / 2)
        x = -w / 2
        p.setFont(tf)
        p.setPen(white(0.98))
        p.drawText(QRectF(x, cy - fm_t.height() / 2, fm_t.horizontalAdvance(tm) + 2, fm_t.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, tm)
        x += fm_t.horizontalAdvance(tm) + gap
        p.setFont(df)
        p.setPen(white(0.9 if hc else 0.65))
        p.drawText(QRectF(x, cy - fm_d.height() / 2, fm_d.horizontalAdvance(dt) + 2, fm_d.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, dt)
        x += fm_d.horizontalAdvance(dt) + gap
        if wx is not None:
            p.setPen(QPen(white(0.2), 1.0))
            p.drawLine(QPointF(x, cy - h * 0.25), QPointF(x, cy + h * 0.25))
            x += 1 + gap
            draw_weather(p, wx.kind, QPointF(x + icon / 2, cy), icon, wx.night)
            x += icon + 4 * k
            p.setFont(df)
            p.setPen(white(0.9 if hc else 0.75))
            p.drawText(QRectF(x, cy - fm_d.height() / 2, fm_d.horizontalAdvance(temp) + 2, fm_d.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, temp)
        p.restore()

    # ------------------------------------------------------------- pieces
    def _light(self, p: QPainter, R: float, Rin: float, rc: float) -> None:
        """Reflections that live: while the ring turns, a glint runs along its rim in the direction of
        travel and settles back into the resting highlight; a sheen sweeps across the glass as it opens."""
        if self.reduce_motion or self.high_contrast:
            return
        v = self.rot.velocity
        lean = max(-1.0, min(1.0, v / 4.0))
        if abs(lean) > 0.02:
            # Qt angles: counter-clockwise from 3 o'clock; the resting highlight sits at the upper left.
            centre = 125.0 - 34.0 * lean
            for r, w in ((R - 0.6, 2.2), (Rin + 0.6, 1.6)):
                rect = QRectF(-r, -r, 2 * r, 2 * r)
                for span, a in ((56.0, 0.10), (30.0, 0.20), (12.0, 0.42)):
                    p.setPen(QPen(white(a * abs(lean)), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawArc(rect, int((centre - span / 2) * 16), int(span * 16))
        if self.open_t > 0.95:
            return
        u = ease_out_cubic(self.open_t / 0.95)
        x = -1.5 * R + 3.0 * R * u
        g = QLinearGradient(x - 0.35 * R, -0.6 * R, x + 0.35 * R, 0.6 * R)
        a = 0.20 * (1 - u)
        g.setColorAt(0.0, white(0.0))
        g.setColorAt(0.5, white(a))
        g.setColorAt(1.0, white(0.0))
        ring, disc = self._ring_paths()
        p.fillPath(ring.united(disc), g)

    def _gauge(self, p: QPainter, R: float) -> None:
        """360° gauge just outside the ring (primary ring only): how far it has turned since it opened.
        A thin track with a tinted arc from 12 o'clock, a full lap per 360°, and the angle above the top."""
        g = self.gauge_vis.value
        if g <= 0.01 or self.crumb:
            return
        deg = self.turned
        r = R + 11
        rect = QRectF(-r, -r, 2 * r, 2 * r)
        p.save()
        p.setOpacity(p.opacity() * min(1.0, g))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(white(0.10 if not self.high_contrast else 0.3), 3.0))
        p.drawEllipse(rect)
        laps, rest = divmod(abs(deg), 360.0)
        col = self.tint.lighter(135)
        if laps >= 1:            # a full lap underneath what's left of the next one
            p.setPen(QPen(qcolor(col, 0.38), 3.0))
            p.drawEllipse(rect)
        sign = 1.0 if deg >= 0 else -1.0
        p.setPen(QPen(qcolor(col, 0.95), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, int(-sign * rest * 16))
        # the needle: a bright dot at the current angle
        tip = angle_point(r, sign * rest)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white(0.95))
        p.drawEllipse(tip, 3.4, 3.4)
        # the 0° mark
        self._tick(p, 0.0, r + 5, 10, 0.55, 1.6)
        lf, fm = self._font("display", 14 * self.text_scale, QFont.Weight.DemiBold)
        p.setFont(lf)
        text = f"{deg:+.0f}°" if abs(deg) >= 0.5 else "0°"
        tw = fm.horizontalAdvance(text) + 8
        y = -(r + 12 + fm.height())
        p.setPen(black(0.30))
        p.drawText(QRectF(-tw / 2, y + 1.2, tw, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(white(0.92))
        p.drawText(QRectF(-tw / 2, y, tw, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def _level_gauge(self, p: QPainter, R: float, frac: float) -> None:
        """The volume ring's level, drawn like the 360° gauge: a hairline track just outside the ring, a tinted
        arc from 12 o'clock round to the level (all the way round is full) and a bright dot at its end."""
        r = R + 11
        rect = QRectF(-r, -r, 2 * r, 2 * r)
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(white(0.10 if not self.high_contrast else 0.3), 2.0))
        p.drawEllipse(rect)
        col = self.tint.lighter(135)
        p.setPen(QPen(qcolor(col, 0.95), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, int(-360 * 16 * frac))
        tip = angle_point(r, 360.0 * frac)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(col, 0.35))
        p.drawEllipse(tip, 6.0, 6.0)
        p.setBrush(white(0.97))
        p.drawEllipse(tip, 3.2, 3.2)
        self._tick(p, 0.0, r + 5, 10, 0.55, 1.6)          # 0° (and 100 %, a full turn on)
        p.restore()

    def _font(self, which: str, px: float, weight: QFont.Weight) -> tuple[QFont, QFontMetricsF]:
        key = (which, px, weight)
        got = self._fonts.get(key)
        if got is None:
            f = font(DISPLAY if which == "display" else TEXT, px, weight)
            got = self._fonts[key] = (f, QFontMetricsF(f))
        return got

    @staticmethod
    def _tick(p: QPainter, deg: float, r_out: float, length: float, alpha: float, width: float) -> None:
        a = math.radians(deg)
        s, c = math.sin(a), -math.cos(a)
        p.setPen(QPen(white(alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(s * (r_out - length), c * (r_out - length)), QPointF(s * r_out, c * r_out))

    def _decorate(self, p: QPainter, c: QPointF, r: float, it: Item) -> None:
        if self.high_contrast:
            p.strokePath(circle(c.x(), c.y(), r + 0.5), QPen(white(0.95), 2.0))
        if it.kind == "quicknav":          # a small chevron badge: this opens more
            bc = QPointF(c.x() + r * 0.72, c.y() + r * 0.72)
            br = r * 0.36
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(22, 22, 28, 240))
            p.drawEllipse(bc, br, br)
            p.strokePath(circle(bc.x(), bc.y(), br), QPen(white(0.75), 1.0))
            p.setFont(font(ICONS, br * 1.05))
            p.setPen(white(1.0))
            p.drawText(QRectF(bc.x() - br, bc.y() - br, 2 * br, 2 * br), Qt.AlignmentFlag.AlignCenter, "\uE76C")

    @staticmethod
    def _draw_platter(p: QPainter, c: QPointF, r: float, it: Item) -> None:
        tint = QColor(it.tint)
        soft_shadow(p, c, r, 6, 0.22, dy=3)
        path = circle(c.x(), c.y(), r)
        dev = p.device()
        icon = app_icon(it.icon, int(r * 2 * (dev.devicePixelRatioF() if dev else 1.0))) if it.icon else None
        if icon is not None:
            # Real app icon on a frosted white platter, tinted at the rim.
            base = QLinearGradient(c.x(), c.y() - r, c.x(), c.y() + r)
            base.setColorAt(0.0, white(0.92))
            base.setColorAt(1.0, QColor(226, 228, 236, 235))
            p.fillPath(path, base)
            p.fillPath(path, qcolor(tint, 0.10))
            s_ = r * 1.18
            p.drawPixmap(QRectF(c.x() - s_ / 2, c.y() - s_ / 2, s_, s_), icon, QRectF(icon.rect()))
            gloss = QRadialGradient(QPointF(c.x(), c.y() - r * 0.7), r * 0.9)
            gloss.setColorAt(0.0, white(0.30))
            gloss.setColorAt(1.0, white(0.0))
            p.fillPath(path, gloss)
            stroke_rim(p, path, c, 1.0, 0.85)
            return
        base = QLinearGradient(c.x(), c.y() - r, c.x(), c.y() + r)
        base.setColorAt(0.0, qcolor(tint.lighter(128), 0.95))
        base.setColorAt(1.0, qcolor(tint.darker(112), 0.95))
        p.fillPath(path, base)
        gloss = QRadialGradient(QPointF(c.x(), c.y() - r * 0.62), r * 1.05)
        gloss.setColorAt(0.0, white(0.42))
        gloss.setColorAt(0.55, white(0.10))
        gloss.setColorAt(1.0, white(0.0))
        p.fillPath(path, gloss)
        bounce = QRadialGradient(QPointF(c.x(), c.y() + r * 0.95), r * 0.85)
        bounce.setColorAt(0.0, white(0.22))
        bounce.setColorAt(1.0, white(0.0))
        p.fillPath(path, bounce)
        stroke_rim(p, path, c, 1.0, 0.75)
        p.setFont(font(ICONS, r * 0.92))
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(black(0.22))
        p.drawText(rect.translated(0, 1.2), Qt.AlignmentFlag.AlignCenter, it.glyph_char)
        p.setPen(white(1.0))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, it.glyph_char)

    def _texts(self, p: QPainter, rc: float) -> None:
        st = self.state
        n = len(self.items)
        k = self.text_scale
        hc = self.high_contrast
        width = 2 * (rc - 22)
        t = 1.0 if self.reduce_motion else max(0.0, min(1.0, self.title.value))
        tf, fm_t = self._font("display", 27 * k, QFont.Weight.DemiBold)
        sf, fm_s = self._font("text", 14 * k, QFont.Weight.Medium if hc else QFont.Weight.Normal)
        th, sh = fm_t.height(), fm_s.height()

        def draw_title(idx: int, alpha: float, dy: float) -> None:
            if alpha <= 0.01:
                return
            it = self.items[idx % n]
            p.setFont(tf)
            name = fm_t.elidedText(it.name, Qt.TextElideMode.ElideRight, width)
            p.setPen(black(0.28 * alpha))
            p.drawText(QRectF(-width / 2, -th + 3.2 + dy, width, th), Qt.AlignmentFlag.AlignCenter, name)
            p.setPen(white(0.98 * alpha))
            p.drawText(QRectF(-width / 2, -th + 2 + dy, width, th), Qt.AlignmentFlag.AlignCenter, name)
            p.setFont(sf)
            p.setPen(white((0.9 if hc else 0.6) * alpha))
            sub = "Opening…" if self.exec_t is not None else it.subtitle
            p.drawText(QRectF(-width / 2, 4 + dy, width, sh + 2), Qt.AlignmentFlag.AlignCenter,
                       fm_s.elidedText(sub, Qt.TextElideMode.ElideRight, width))

        d = 0.0 if self.reduce_motion else self._title_dir * 12
        if t < 1.0:
            draw_title(self._prev_sel, 1 - t, d * t)
        draw_title(self._cur_sel, t, -d * (1 - t))

        # breadcrumb inside a Quick nav ring, and position dots
        top_y = -rc * 0.52
        self._crumb_rect = None
        if self.crumb:
            cf, fm_c = self._font("text", 11.5 * k, QFont.Weight.DemiBold)
            p.setFont(cf)
            text = fm_c.elidedText(self.crumb.upper(), Qt.TextElideMode.ElideRight, width * 0.8)
            rect = QRectF(-width / 2, top_y - 14 - 12 * k, width, 14 * k + 4)
            if self.keyboard:
                # From the mouse it's a button: "‹ APP" takes you back out of Quick nav.
                text = "‹  " + text
                tw = fm_c.horizontalAdvance(text) + 18
                rect.translate(0, -5)
                chip = QRectF(-tw / 2, rect.top() - 2, tw, rect.height() + 4)
                path = QPainterPath()
                path.addRoundedRect(chip, chip.height() / 2, chip.height() / 2)
                p.fillPath(path, white(0.10))
                p.strokePath(path, QPen(white(0.22), 1.0))
                self._crumb_rect = chip
            p.setPen(white(0.85 if hc else 0.55) if not self.keyboard else white(0.9))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if n <= 16:
            gap = 9.0
            x0 = -gap * (n - 1) / 2
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(n):
                on = i == st.selected
                p.setBrush(white(0.95 if on else (0.5 if hc else 0.28)))
                r = 3.0 if on else 2.2
                p.drawEllipse(QPointF(x0 + i * gap, top_y), r, r)

        # hint + tracking dot
        hint = self.hint()
        hf, fm_h = self._font("text", 12.5 * k, QFont.Weight.Medium if hc else QFont.Weight.Normal)
        hint = fm_h.elidedText(hint, Qt.TextElideMode.ElideRight, width - 16)
        hw = fm_h.horizontalAdvance(hint)
        hy = rc * 0.47 + (k - 1.0) * 10
        p.setFont(hf)
        p.setPen(white(0.88 if hc else 0.55))
        p.drawText(QRectF(-width / 2 + 8, hy - fm_h.height() / 2, width, fm_h.height()),
                   Qt.AlignmentFlag.AlignCenter, hint)
        dot = QColor("#30D158") if (self.tracked_vis.value > 0.5 or self.keyboard) else QColor("#FF9F0A")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(dot, 0.95))
        p.drawEllipse(QPointF(-hw / 2 - 2, hy), 3.0 * k, 3.0 * k)

    def hint(self) -> str:
        st = self.state
        c = self.controls or {}
        style = c.get("turn_style", "tilt")
        if self.exec_t is not None:
            return "Launching"
        if st.sliding:
            return "Slide up or down to change the volume"
        if self.mode != "main":
            what = {"slides": "change slides", "volume": "change the volume"}.get(self.mode,
                                                                                 "move through the video")
            if self.keyboard and self.mode == "volume":
                return "Scroll or ← → to change it"
            if self.keyboard:
                return f"Scroll or ← → to {what} · Click the centre for apps · Esc to close"
            if not (st.hand_present and st.facing):
                return "Show your palm to continue"
            if st.grip_mode:
                return f"Tilt your fist to {what}" if st.gripping else "Close your hand to keep turning · point for apps"
            verb = {"tilt": "Tilt", "move": "Move", "direct": "Turn"}.get(style, "Tilt")
            return f"{verb} your hand to {what}"
        if self.keyboard:
            return "Scroll or ← → to turn · Click or Enter to open · Esc to close"
        if not (st.hand_present and st.facing):
            return "Raise your hand to continue" if c.get("either_side") else "Show your palm to continue"
        if st.scrolling:
            return "Scrolling — " + ("back to the middle to stop" if style == "move" else "straighten to stop")
        if st.pointing:
            return "Move aside to scroll · push to open" if c.get("finger_push", True) else "Move aside to scroll"
        verb = {"tilt": "Tilt", "move": "Move", "direct": "Turn"}.get(style, "Tilt")
        if st.grip_mode:
            if st.gripping:
                return "Point your finger to open" if st.interacted else "Tilt your fist to turn"
            return "Close your hand to keep turning · point to open"
        if st.dwell > 0.02:
            return "Hold still to open" + (" · or pinch" if c.get("pinch_click", True) else "")
        if not st.interacted:
            return {"tilt": "Tilt your hand to choose", "move": "Move your hand sideways to choose",
                    "direct": "Turn your hand to choose"}.get(style, "Tilt your hand to choose")
        ways = []
        if c.get("pinch_click", True):
            ways.append("pinch")
        if float(c.get("dwell_seconds", 1.6)) > 0:
            ways.append("hold still")
        if c.get("finger_push", True):
            ways.append("point and push")
        if not ways:
            return "Close your hand, tilt, then point"
        text = ways[0] if len(ways) == 1 else ", ".join(ways[:-1]) + " or " + ways[-1]
        return text[0].upper() + text[1:] + " to open"


CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def spot(area: QRect, w: int, h: int, corner: str = "", margin: int = 0) -> QPoint:
    """Top-left of a w × h window in the middle of `area`, or tucked into one of its corners (the window's own
    transparent margin, the glow around the ring, already keeps the ring off the edge)."""
    if corner not in CORNERS:
        return QPoint(area.center().x() - w // 2, area.center().y() - h // 2)
    inset = max(0, 24 - margin)
    x = area.left() + inset if corner.endswith("left") else area.right() + 1 - w - inset
    y = area.top() + inset if corner.startswith("top") else area.bottom() + 1 - h - inset
    return QPoint(x, y)


class DialWindow(QWidget):
    """The rotary on screen. Opened by a gesture it lets every click through; opened from the notch or the
    keyboard it also takes the mouse: scroll or drag the ring to turn it, click an item to choose it,
    click it again (or the centre) to open it, right-click to go back or close."""
    # ("turn", ±n) | ("select", index) | ("open", 0) | ("back", 0) | ("volume", 0): the volume button
    mouse = Signal(str, int)

    def __init__(self, items: list[Item], size: int = 600, live_glass: bool = False):
        super().__init__(None)
        overlay_flags(self)
        self.setWindowTitle("RotaryUI")
        self.setMouseTracking(True)
        self.painter_ = DialPainter(items, size)
        self.resize(self.painter_.size, self.painter_.size)
        self.live_glass = live_glass
        self.interactive = False
        self._excluded = False
        self._drag: dict | None = None
        self._wheel = 0
        self.corner = ""                  # "" = the middle of the screen, or the volume ring's corner
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._last = time.perf_counter()
        self._glass_timer = QTimer(self)
        self._glass_timer.setInterval(250)
        self._glass_timer.timeout.connect(self._refresh_glass)
        self._shown: tuple | None = None          # what the window shows now (DialPainter.visual_key)

    def set_items(self, items: list[Item]) -> None:
        self.painter_.set_items(items)

    def prewarm(self) -> None:
        if not self.isVisible():
            screen = QGuiApplication.primaryScreen()
            self.painter_.prewarm(screen.devicePixelRatio() if screen else 1.0)

    def set_level(self, items: list[Item], crumb: str, state: EngineSnapshot) -> None:
        self.painter_.set_level(items, crumb, state)
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def set_mode(self, mode: str, items: list[Item], info: dict, state: EngineSnapshot) -> None:
        self.painter_.set_mode(mode, items, info, state)
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def volume_zone(self) -> QRectF:
        """The volume button and its label, in window coordinates (for the mouse hook)."""
        c, r = self.painter_.volume_button()
        rect = QRectF(c.x() - r - 8, c.y() - r - 8, 2 * r + 16, 2 * r + 36)
        return rect.translated(self.width() / 2, self.height() / 2)

    def set_dial_size(self, size: int) -> None:
        if size != self.painter_.D and not self.isVisible():
            self.painter_.set_size(size)
            self.resize(self.painter_.size, self.painter_.size)

    def _set_interactive(self, on: bool) -> None:
        """Only while hidden: changing window flags recreates the native window."""
        if on == self.interactive:
            return
        self.interactive = on
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not on)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)
        self._excluded = False

    def open(self, state: EngineSnapshot, interactive: bool = False) -> None:
        dp = self.painter_
        if self.isVisible() and interactive != self.interactive:
            self._timer.stop()
            self._glass_timer.stop()
            self.hide()
        if not self.isVisible():
            self._set_interactive(interactive)
            self._drag = None
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.move(spot(screen.availableGeometry(), self.width(), self.height(), self.corner,
                           self.painter_.MARGIN))
            raw = grab_behind(self)
            dp.backdrop = liquid.Backdrop.from_pixmap(raw)
            self._timer.setInterval(frame_ms(screen))
            dp.prepare(self.width(), self.height(), screen.devicePixelRatio())
            self.show()
            no_activate(self)                   # clicking it never takes the focus from a full-screen app
            want = bool(self.live_glass)
            if want != self._excluded:          # also undo it when live glass gets switched off
                ok = exclude_from_capture(self, want)
                self._excluded = want and ok
            if self.live_glass and self._excluded:
                self._glass_timer.start()
        dp.begin(state)
        self._shown = None
        self._last = time.perf_counter()
        self._timer.start()

    def place(self, corner: str) -> None:
        """Move the open rotary to a corner of its screen ("top-left"…) or back to the middle ("")."""
        self.corner = corner
        if not self.isVisible():
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        pos = spot(screen.availableGeometry(), self.width(), self.height(), corner, self.painter_.MARGIN)
        if pos == self.pos():
            return
        self.hide()                       # out of the way while the glass picks up what's behind its new place
        self.move(pos)
        bd = liquid.Backdrop.from_pixmap(grab_behind(self))
        if bd is not None:
            self.painter_.backdrop = bd
        self.show()
        no_activate(self)

    def set_state(self, state: EngineSnapshot) -> None:
        self.painter_.set_state(state)

    def close_dial(self) -> None:
        self.painter_.close()

    def play_execute(self) -> None:
        self.painter_.execute()

    def _refresh_glass(self) -> None:
        bd = liquid.Backdrop.from_pixmap(grab_behind(self))
        if bd is not None and not bd.same_as(self.painter_.backdrop):
            self.painter_.backdrop = bd

    def _tick(self) -> None:
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        if not self.painter_.tick(dt):
            self._timer.stop()
            self._glass_timer.stop()
            self._shown = None
            self.hide()
            return
        key = self.painter_.visual_key()
        if key != self._shown:                # nothing moved since the last frame: leave the window as it is
            self._shown = key
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self.painter_.paint(p, self.width(), self.height())
        p.end()

    def nativeEvent(self, event_type, message):
        return refuse_activation(message)

    # ------------------------------------------------------------- mouse
    def _local(self, pt) -> tuple[float, float, float, float]:
        """(x, y, radius, clockwise-from-top angle) of a point relative to the dial centre."""
        x, y = pt.x() - self.width() / 2, pt.y() - self.height() / 2
        return x, y, math.hypot(x, y), math.degrees(math.atan2(x, -y))

    def hit(self, pt) -> tuple[str, int]:
        """What's under the mouse: ("item", index) | ("centre", 0) | ("ring", 0) | ("volume", 0) | ("back", 0)
        | ("", 0)."""
        dp = self.painter_
        n = len(dp.items)
        if not n or dp.exiting:
            return "", 0
        x, y, r, _a = self._local(pt)
        bc, br = dp.volume_button()
        if dp.keyboard and math.hypot(x - bc.x(), y - bc.y()) <= br + 6:
            return "volume", 0
        cr = dp._crumb_rect
        if cr is not None and cr.adjusted(-4, -4, 4, 4).contains(QPointF(x, y)):
            return "back", 0
        R, Rin, Rmid, rc, lens_r = dp._geom()
        if dp.mode != "main":                 # no items to pick on a slides / video ring: drag it or scroll
            return ("centre", 0) if r <= rc else (("ring", 0) if Rin - 4 <= r <= R + 6 else ("", 0))
        step = 360.0 / n
        best, best_d = -1, 1e9
        for i in range(n):
            rel = _wrap180((i - dp.rot.value) * step)
            c = angle_point(Rmid, rel)
            f = smoothstep(max(0.0, 1 - abs(rel) / step))
            d = math.hypot(x - c.x(), y - c.y())
            if d <= 27 * (1 + 0.30 * f) + 5 and d < best_d:
                best, best_d = i, d
        if best >= 0:
            return "item", best
        if r <= rc:
            return "centre", 0
        if Rin - 4 <= r <= R + 6:
            return "ring", 0
        return "", 0

    # The same press / drag / release comes from the window itself (opened for the mouse) or, wherever the
    # rotary is on screen however it was opened, from the mouse hook (mouse.py), in window coordinates.
    def press_at(self, pt, src: str = "window") -> None:
        kind, idx = self.hit(pt)
        _x, _y, _r, a = self._local(pt)
        self._drag = {"kind": kind, "idx": idx, "angle": a, "acc": 0.0, "moved": 0.0, "dragging": False, "src": src}
        if kind == "ring" and src == "window":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def drag_to(self, pt) -> None:
        d = self._drag
        if d is None or d["kind"] not in ("item", "ring", "centre"):
            return
        _x, _y, r, a = self._local(pt)
        if r < 12:
            return                            # right on the centre the angle is meaningless
        da = _wrap180(a - d["angle"])
        d["angle"] = a
        d["acc"] += da
        d["moved"] += abs(da)
        if d["moved"] > 6:
            d["dragging"] = True
        step = 360.0 / max(1, len(self.painter_.items))
        while d["dragging"] and abs(d["acc"]) >= step * 0.75:
            # turning the ring clockwise brings the previous item under the lens
            sgn = 1 if d["acc"] > 0 else -1
            d["acc"] -= sgn * step
            self.mouse.emit("turn", -sgn)

    def release_at(self, pt) -> None:
        d, self._drag = self._drag, None
        if d is None or d["dragging"]:
            return
        kind, idx = self.hit(pt)
        if kind == "back" and d["kind"] == "back":
            self.mouse.emit("back", 0)
        elif kind == "volume" and d["kind"] == "volume":
            self.mouse.emit("volume", 0)
        elif kind == "centre" and d["kind"] == "centre":
            self.mouse.emit("open", 0)
        elif kind == "item" and d["kind"] == "item" and idx == d["idx"]:
            n = max(1, len(self.painter_.items))
            if idx == self.painter_.state.selected % n:
                self.mouse.emit("open", 0)
            else:
                self.mouse.emit("select", idx)

    @property
    def dragging(self) -> bool:
        return bool((self._drag or {}).get("dragging"))

    def mousePressEvent(self, ev) -> None:
        if not self.interactive:
            return
        if ev.button() == Qt.MouseButton.RightButton:
            self.mouse.emit("back", 0)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self.press_at(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        if not self.interactive:
            return
        d = self._drag
        if d is not None and d["kind"] in ("item", "ring", "centre"):
            if d["src"] == "window":
                self.drag_to(ev.position())
            return                            # the hook's drag: it reports the moves itself
        kind, _idx = self.hit(ev.position())
        hot = kind == "volume"
        if hot != self.painter_.volume_hot:
            self.painter_.volume_hot = hot
        self.setCursor(Qt.CursorShape.PointingHandCursor if kind in ("item", "centre", "volume", "back")
                       else (Qt.CursorShape.OpenHandCursor if kind == "ring" else Qt.CursorShape.ArrowCursor))

    def leaveEvent(self, ev) -> None:
        self.painter_.volume_hot = False

    def mouseReleaseEvent(self, ev) -> None:
        if not self.interactive or ev.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag is not None and self._drag["src"] == "window":
            self.release_at(ev.position())

    def wheelEvent(self, ev) -> None:
        if not self.interactive:
            return
        dlt = ev.angleDelta()
        self._wheel += dlt.y() if abs(dlt.y()) >= abs(dlt.x()) else -dlt.x()
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel < 0 else -1          # wheel down = next item
            self._wheel += 120 if self._wheel < 0 else -120
            self.mouse.emit("turn", step)
        ev.accept()


class VolumeRing(DialWindow):
    """The volume as a ring of its own, full size, in a corner of the screen (Controls → Volume, slides and
    videos). It shows up while two fingers slide the volume, whether the rotary is open or not, turns with the
    level on the same springs as the rotary, and fades away a moment after the last change. Clicks go through."""

    def __init__(self, ticks: int, tint: str, size: int = 600, live_glass: bool = False):
        self._item = Item("Volume", "", "", tint, {"type": "mode"})
        super().__init__([self._item], size, live_glass)
        self.ticks = max(1, ticks)
        self.painter_.clock_mode = "off"
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(1600)
        self._idle.timeout.connect(self.close_dial)

    def _state(self, level: float) -> EngineSnapshot:
        pos = max(0.0, min(1.0, level)) * self.ticks
        # grip_mode: the ring follows the level exactly instead of snapping to its ticks
        return EngineSnapshot(phase=Phase.OPEN, hand_present=True, facing=True, sliding=True, grip_mode=True,
                              position=pos, selected=int(round(pos)) % self.ticks)

    def show_level(self, level: float, corner: str) -> None:
        st = self._state(level)
        dp = self.painter_
        if not self.isVisible() or dp.appear.target == 0.0:
            if not self.isVisible():
                self.corner = corner
            dp.set_mode("volume", [self._item] * self.ticks, {"top": "Volume"}, st)
            self.open(st)
            if not dp.reduce_motion:
                dp.rot.snap(0.0)              # it turns itself round to the level, like the volume ring
                dp.rot.target = st.position
        else:
            self.set_state(st)
        self._idle.start()

    def hide_now(self) -> None:
        self._idle.stop()
        if self.isVisible():
            self.close_dial()
