"""The tour's card: a small glass window beside the rotary with a drawing of the hand shape, what to do,
and a tick once the user has done it. The steps themselves come from rotary/tutorial.py."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import tutorial
from . import widgets
from .glass import TEXT, font, qcolor, white
from .widgets import ACCENT, GREEN, paint_panel

SKIN = QColor(255, 255, 255, 235)
SKIN_BACK = QColor(200, 200, 210, 215)
INK = QColor(20, 20, 26, 150)


# --------------------------------------------------------------------------- drawing
def _capsule(p: QPainter, a: QPointF, b: QPointF, w: float) -> None:
    """A finger: a rounded bar from a to b."""
    pen = QPen(p.brush().color(), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(a, b)


def _at(o: QPointF, length: float, deg: float) -> QPointF:
    """`length` away from o, `deg` degrees clockwise from straight up."""
    r = math.radians(deg)
    return QPointF(o.x() + length * math.sin(r), o.y() - length * math.cos(r))


def draw_hand(p: QPainter, c: QPointF, k: float, pose: str) -> None:
    """A right hand seen from the camera's side, about 120 * k tall, its palm centred on c."""
    back = pose == "back"
    skin = SKIN_BACK if back else SKIN
    p.setBrush(skin)
    w = 17 * k
    top = c.y() - 24 * k
    xs = [c.x() + (dx * k) for dx in (-24, -8, 8, 23)]          # index, middle, ring, little finger
    lengths = [52, 60, 55, 43]
    spread = [-7, -2, 3, 9]
    pinch_at = None
    extended = {"palm": (1, 1, 1, 1), "back": (1, 1, 1, 1), "fist": (0, 0, 0, 0), "point": (1, 0, 0, 0),
                "two": (1, 1, 0, 0), "pinch": (0, 1, 1, 1)}.get(pose, (1, 1, 1, 1))
    for i, (x, n, sp, up) in enumerate(zip(xs, lengths, spread, extended)):
        base = QPointF(x, top + 4 * k)
        if up:
            _capsule(p, base, _at(base, n * k, sp), w)
        elif pose == "pinch" and i == 0:
            mid = _at(base, 30 * k, -28)
            _capsule(p, base, mid, w)
            pinch_at = _at(mid, 20 * k, -125)
            _capsule(p, mid, pinch_at, w * 0.95)
        else:
            _capsule(p, base, _at(base, 12 * k, 0), w)               # folded down: just the knuckle shows
    # the palm
    path = QPainterPath()
    path.addRoundedRect(QRectF(c.x() - 34 * k, top - 2 * k, 68 * k, 66 * k), 24 * k, 24 * k)
    p.setPen(Qt.PenStyle.NoPen)
    p.fillPath(path, skin)
    # the thumb (on the left as the camera sees a right palm; the back of the hand mirrors it)
    side = 1 if back else -1
    root = QPointF(c.x() + side * 26 * k, c.y() + 26 * k)
    p.setBrush(skin)
    if pose in ("fist", "point", "two"):
        _capsule(p, root, QPointF(c.x() + side * 2 * k, c.y() + 8 * k), w)       # across the curled fingers
    elif pose == "pinch":
        _capsule(p, root, pinch_at, w)                                             # meets the index finger
        p.setPen(QPen(qcolor(ACCENT), 2.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(pinch_at, 15 * k, 15 * k)
    else:
        _capsule(p, root, _at(root, 44 * k, side * 52), w)
    p.setPen(Qt.PenStyle.NoPen)
    # a little detail: palm lines in front, nails behind
    if back:
        p.setBrush(INK)
        for x, n, sp, up in zip(xs, lengths, spread, extended):
            if up:
                tip = _at(QPointF(x, top + 4 * k), (n - 6) * k, sp)
                p.drawEllipse(tip, 4.5 * k, 5.5 * k)
    elif pose in ("fist", "point", "two"):
        p.setPen(QPen(INK, 1.6 * k))
        for x in xs[1 if pose == "point" else 2 if pose == "two" else 0:]:
            p.drawLine(QPointF(x - 6 * k, top + 10 * k), QPointF(x + 6 * k, top + 10 * k))
    else:
        pen = QPen(INK, 1.5 * k)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        crease = QPainterPath(QPointF(c.x() - 24 * k, c.y() + 2 * k))
        crease.quadTo(QPointF(c.x(), c.y() - 6 * k), QPointF(c.x() + 26 * k, c.y() - 8 * k))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(crease)


def draw_ring(p: QPainter, c: QPointF, r: float, turn: float) -> None:
    """A tiny rotary: glass ring, a few item dots, the lens at the top."""
    p.setPen(QPen(white(0.30), 2.0))
    p.setBrush(QColor(255, 255, 255, 22))
    p.drawEllipse(c, r, r)
    colors = widgets.APPLE_COLORS
    for i in range(8):
        pt = _at(c, r * 0.72, i * 45 + turn)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(colors[i % len(colors)], 0.95))
        p.drawEllipse(pt, r * 0.13, r * 0.13)
    p.setPen(QPen(white(0.85), 2.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(_at(c, r * 0.72, 0), r * 0.21, r * 0.21)


def draw_notch(p: QPainter, box: QRectF, t: float) -> None:
    screen = QRectF(box.left() + 14, box.top() + 34, box.width() - 28, box.height() * 0.62)
    p.setPen(QPen(white(0.35), 2.0))
    p.setBrush(QColor(255, 255, 255, 16))
    p.drawRoundedRect(screen, 10, 10)
    notch = QRectF(screen.center().x() - 34, screen.top(), 68, 18)
    path = QPainterPath()
    path.addRoundedRect(notch, 9, 9)
    p.fillPath(path, QColor(0, 0, 0, 230))
    p.setPen(QPen(white(0.4), 1.0))
    p.drawPath(path)
    # the pointer clicking it
    tip = QPointF(notch.center().x() + 12, notch.bottom() + 14 + 4 * math.sin(t * 3))
    arrow = QPainterPath(tip)
    for dx, dy in ((0, 26), (7, 20), (12, 30), (16, 28), (11, 18), (20, 18)):
        arrow.lineTo(tip.x() + dx, tip.y() + dy)
    arrow.closeSubpath()
    p.setPen(QPen(QColor(0, 0, 0, 160), 1.4))
    p.setBrush(white(0.95))
    p.drawPath(arrow)


def draw_mouse(p: QPainter, c: QPointF, t: float) -> None:
    body = QRectF(c.x() - 34, c.y() - 52, 68, 104)
    p.setPen(QPen(white(0.55), 2.2))
    p.setBrush(QColor(255, 255, 255, 26))
    p.drawRoundedRect(body, 34, 34)
    p.drawLine(QPointF(c.x(), body.top()), QPointF(c.x(), c.y() - 12))
    wheel = QRectF(c.x() - 5, c.y() - 40, 10, 20)
    p.setBrush(qcolor(ACCENT, 0.9))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(wheel, 5, 5)
    for i in range(3):                                         # the wheel's ridges roll by
        y = wheel.top() + ((t * 18 + i * 7) % 20)
        p.setPen(QPen(white(0.6), 1.2))
        p.drawLine(QPointF(wheel.left() + 2, y), QPointF(wheel.right() - 2, y))


def draw_keys(p: QPainter, c: QPointF, labels: list[str]) -> None:
    f = font(TEXT, 13, QFont.Weight.DemiBold)
    p.setFont(f)
    widths = [max(40.0, 14 + 8.5 * len(s)) for s in labels]
    x = c.x() - (sum(widths) + 8 * (len(widths) - 1)) / 2
    for s, w in zip(labels, widths):
        r = QRectF(x, c.y() - 22, w, 44)
        p.setPen(QPen(white(0.40), 1.6))
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(white(0.92))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, s)
        x += w + 8


def _arrow(p: QPainter, path: QPainterPath, color: QColor, both: bool = True) -> None:
    pen = QPen(color, 3.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    ends = [(1.0, 0.985)] + ([(0.0, 0.015)] if both else [])
    for at, near in ends:
        tip, back = path.pointAtPercent(at), path.pointAtPercent(near)
        ang = math.atan2(tip.y() - back.y(), tip.x() - back.x())
        for d in (2.5, -2.5):
            p.drawLine(tip, QPointF(tip.x() - 10 * math.cos(ang + d / 6), tip.y() - 10 * math.sin(ang + d / 6)))


class StepArt(QWidget):
    """The picture for a card, gently animated (still when Reduce motion is on)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(190, 210)
        self.pose = "rotary"
        self.motion = ""
        self.keys: list[str] = []
        self.done = False
        self.still = False
        self._t0 = time.perf_counter()

    def set_step(self, step: tutorial.Step, keys: list[str]) -> None:
        self.pose, self.motion, self.keys = step.pose, step.motion, keys
        self.setAccessibleName(step.title)
        self._t0 = time.perf_counter()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        bg = QPainterPath()
        bg.addRoundedRect(box, 22, 22)
        p.fillPath(bg, QColor(255, 255, 255, 14))
        p.setPen(QPen(white(0.10), 1.0))
        p.drawPath(bg)
        t = 0.0 if self.still else time.perf_counter() - self._t0
        wave = math.sin(t * 2.2)
        c = QPointF(box.center().x(), box.center().y() + 14)
        accent = qcolor(GREEN if self.done else ACCENT)
        if self.pose == "rotary":
            draw_ring(p, QPointF(box.center()), 62, 0 if self.still else t * 18)
        elif self.pose == "notch":
            draw_notch(p, box, t)
        elif self.pose == "mouse":
            draw_mouse(p, box.center(), t)
        elif self.pose == "keys":
            draw_keys(p, box.center(), self.keys or ["Ctrl", "Alt", "Space"])
        else:
            p.save()
            m = self.motion
            if m == "tilt":
                p.translate(c.x(), c.y() + 40)
                p.rotate(18 * wave)
                p.translate(-c.x(), -(c.y() + 40))
            elif m == "slide":
                p.translate(22 * wave, 0)
            elif m == "updown":
                p.translate(0, 14 * wave)
            elif m == "push":
                s = 1.0 + 0.06 * (0.5 + 0.5 * wave)
                p.translate(c.x(), c.y())
                p.scale(s, s)
                p.translate(-c.x(), -c.y())
            elif m == "lower":
                p.translate(0, 26 * (0.5 + 0.5 * wave))
            pose = self.pose
            if m == "close" and not self.still:                  # open palm, then a fist, over and over
                pose = "palm" if (t % 2.4) < 1.2 else "fist"
            elif m == "flip" and not self.still:
                pose = "palm" if (t % 2.4) < 1.2 else "back"
            draw_hand(p, c, 0.95, pose)
            p.restore()
            if m == "hold":
                frac = 1.0 if self.still or self.done else (t % 3.0) / 3.0
                p.setPen(QPen(accent, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(c.x() - 78, c.y() - 92, 156, 156), 90 * 16, int(-360 * 16 * frac))
            elif m in ("tilt", "close"):
                arc = QPainterPath()
                r = QRectF(c.x() - 74, c.y() - 108, 148, 120)
                arc.arcMoveTo(r, 145)
                arc.arcTo(r, 145, -110)
                _arrow(p, arc, accent, both=m == "tilt")
            elif m == "slide":
                line = QPainterPath(QPointF(c.x() - 70, box.bottom() - 18))
                line.lineTo(c.x() + 70, box.bottom() - 18)
                _arrow(p, line, accent)
            elif m == "updown":
                line = QPainterPath(QPointF(box.right() - 22, c.y() - 70))
                line.lineTo(box.right() - 22, c.y() + 40)
                _arrow(p, line, accent)
            elif m == "push":
                p.setPen(QPen(accent, 2.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for i in range(2):
                    rr = 10 + ((t * 26 + i * 14) % 28)
                    tip = QPointF(c.x() - 23, c.y() - 72)
                    p.setOpacity(max(0.0, 1 - rr / 38))
                    p.drawEllipse(tip, rr, rr)
                p.setOpacity(1.0)
        p.end()


# --------------------------------------------------------------------------- the card
class Dots(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.n, self.i = 1, 0
        self.setFixedHeight(10)

    def set(self, n: int, i: int) -> None:
        self.n, self.i = n, i
        self.setMinimumWidth(n * 12)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        x = 0.0
        for k in range(self.n):
            w = 18.0 if k == self.i else 6.0
            p.setBrush(qcolor(ACCENT) if k == self.i else white(0.45 if k < self.i else 0.18))
            p.drawRoundedRect(QRectF(x, 2, w, 6), 3, 3)
            x += w + 6
        p.end()


class TutorialWindow(QWidget):
    """The tour, floating above everything so it stays visible while the user tries each gesture.
    Skip, Back and Next always work; reaching a card's goal ticks it and moves on after a moment."""
    finished = Signal(bool)              # True: went through to the end · False: skipped
    W, H = 640, 300

    def __init__(self, steps: list[tutorial.Step], keys: dict[str, list[str]] | None = None,
                 reduce_motion: bool = False):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("RotaryUI tour")
        self.setAccessibleName("RotaryUI tour")
        self.setStyleSheet(widgets.stylesheet())
        self.setFixedSize(self.W, self.H)
        self.tour = tutorial.Tour(steps)
        self.keys = keys or {}
        self._drag: QPointF | None = None
        self._closing = False

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 24, 20)
        root.setSpacing(20)
        self.art = StepArt()
        self.art.still = reduce_motion
        root.addWidget(self.art, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(6)
        top = QHBoxLayout()
        self.chapter = QLabel("")
        self.chapter.setObjectName("h2")
        self.chapter.setStyleSheet("padding-left: 0px;")
        top.addWidget(self.chapter, 1)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        top.addWidget(self.count)
        col.addLayout(top)
        self.title = QLabel("")
        self.title.setObjectName("hero")
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setStyleSheet("color: rgba(255,255,255,0.80); font-size: %.1fpx;" % (13.5 * widgets.UI_SCALE))
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self.body, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("value")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        self.dots = Dots()
        col.addWidget(self.dots)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.skip = QPushButton("Skip tour")
        self.skip.setStyleSheet("background: transparent; border: none; color: rgba(255,255,255,0.6);"
                                "padding: 6px 4px;")
        self.skip.clicked.connect(lambda: self.end(False))
        row.addWidget(self.skip)
        row.addStretch(1)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self.back)
        row.addWidget(self.back_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("accent")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self.next)
        row.addWidget(self.next_btn)
        col.addLayout(row)
        root.addLayout(col, 1)

        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self.art.update)
        self._advance = QTimer(self)           # a goal reached: a moment to see the tick, then the next card
        self._advance.setSingleShot(True)
        self._advance.setInterval(1300)
        self._advance.timeout.connect(self.next)
        self._show_step()

    # ----------------------------------------------------------------- api
    @property
    def practice(self) -> bool:
        """While the tour is up, launching an item is practice: the app doesn't run it."""
        return self.isVisible() and not self._closing

    def start(self) -> None:
        self.place()
        self.show()
        self.raise_()
        self.activateWindow()
        if not self.art.still:
            self._anim.start()

    def place(self) -> None:
        """Beside the rotary (which opens in the middle of the screen): on the right, half-way down."""
        screen = QGuiApplication.screenAt(self.pos()) if self.isVisible() else QGuiApplication.primaryScreen()
        if screen is None:
            return
        a = screen.availableGeometry()
        x = a.right() - self.W - 32
        x = max(a.left() + 16, x)
        y = a.top() + (a.height() - self.H) // 2
        self.move(x, y)

    def feed(self, event: str) -> None:
        if self.tour.feed(event):
            self._reached()

    def feed_snapshot(self, snap) -> None:
        if self.tour.feed_snapshot(snap):
            self._reached()

    def next(self) -> None:
        self._advance.stop()
        if self.tour.last:
            self.end(True)
            return
        self.tour.next()
        self._show_step()

    def back(self) -> None:
        self._advance.stop()
        if self.tour.back():
            self._show_step()

    def end(self, finished: bool) -> None:
        if self._closing:
            return
        self._closing = True
        self._anim.stop()
        self._advance.stop()
        self.hide()
        self.finished.emit(finished)

    # ----------------------------------------------------------------- drawing
    def _show_step(self) -> None:
        s = self.tour.step
        n = len(self.tour.steps)
        self.chapter.setText(s.chapter.upper())
        self.count.setText(f"{self.tour.index + 1} of {n}")
        self.title.setText(s.title)
        self.body.setText(s.body)
        self.art.done = False
        self.art.set_step(s, self.keys.get(s.key, []))
        self._hint()
        self.dots.set(n, self.tour.index)
        self.back_btn.setEnabled(self.tour.index > 0)
        self.next_btn.setText("Finish" if self.tour.last else "Next")
        self.skip.setVisible(not self.tour.last)

    def _hint(self) -> None:
        s = self.tour.step
        if s.goal and self.tour.done:
            self.hint.setText("✓  Nice, that's it")
            self.hint.setStyleSheet(f"color: {GREEN}; font-weight: 600;")
        else:
            self.hint.setText(s.hint)
            self.hint.setStyleSheet("")
        self.hint.setVisible(bool(self.hint.text()))

    def _reached(self) -> None:
        self.art.done = True
        self.art.update()
        self._hint()
        self._advance.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = QPainterPath()
        base.addRoundedRect(r, 24, 24)
        p.fillPath(base, QColor(22, 22, 28, 238))       # it floats over anything: keep the text readable
        paint_panel(p, r, 24)
        p.end()

    # ----------------------------------------------------------------- input
    def keyPressEvent(self, e: QKeyEvent):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.end(False)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.next()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.back()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition() - QPointF(self.pos())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move((e.globalPosition() - self._drag).toPoint())

    def mouseReleaseEvent(self, e):
        self._drag = None

    def closeEvent(self, e):
        self.end(False)
        e.accept()
