import sys
import wave

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication

from rotary import feedback, tracker
from rotary.actions import DEFAULT_CONFIG, build_items
from rotary.gestures import EngineSnapshot, Phase
from rotary.ui import liquid
from rotary.ui.dial import DialWindow, angle_point
from rotary.ui.notch import NotchWindow
from rotary.ui.settings import input_mode, set_input_mode

app = QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ liquid glass
def _stripes(w=200, h=200) -> QPixmap:
    """A left-to-right ramp: any sideways bend shows up as a change in brightness."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    for x in range(w):
        v = int(255 * x / (w - 1))
        for y in range(h):
            img.setPixelColor(x, y, QColor(v, v, v))
    return QPixmap.fromImage(img)


def test_backdrop_is_half_size_and_knows_its_brightness():
    bd = liquid.Backdrop.from_pixmap(_stripes())
    assert bd.img.shape == (100, 100, 3)
    assert bd.f == pytest.approx(0.5)
    assert 0.35 < bd.lum < 0.65
    assert liquid.Backdrop.from_pixmap(QPixmap()) is None


def test_glass_bends_the_edge_but_not_the_middle():
    bd = liquid.Backdrop.from_pixmap(_stripes())
    flat = liquid.render(bd, [liquid.Circle(100, 100, 80, bevel=20, bend=0.9)], highlight=0.0)
    arr = liquid.to_rgb(flat).astype(int)
    src = bd.img.astype(int)
    assert np.abs(arr[50, 50] - src[50, 50]).max() <= 2           # centre: clear, undistorted
    assert np.abs(arr[2, 2] - src[2, 2]).max() == 0                # outside the glass: untouched
    rim = (slice(46, 54), slice(10, 16))                           # just inside the left edge
    pulled = arr[rim] - src[rim]
    assert pulled.min() >= 0 and pulled.max() > 8                  # the bevel pulls the backdrop in (from the right)


def test_glass_geometry_is_reused():
    bd = liquid.Backdrop.from_pixmap(_stripes())
    shape = liquid.Annulus(100, 100, 40, 80)
    liquid.render(bd, [shape])
    n = len(liquid._maps)
    liquid.render(bd, [shape])
    assert len(liquid._maps) == n


def test_tint_darkens_over_bright_backdrops():
    assert liquid.tint_alpha(0.9, 0.2, 0.6) > liquid.tint_alpha(0.1, 0.2, 0.6)


# ------------------------------------------------------------------ notch
def _settle(n: NotchWindow):
    for _ in range(90):
        for sp in n._springs:
            sp.step(1 / 60)


def test_notch_grows_for_status_and_hover_then_rests():
    n = NotchWindow()
    _settle(n)
    rest = n.body_rect().width()
    n.set_status("Opening a fairly long application name", "done", "#30D158")
    _settle(n)
    assert n.body_rect().width() > rest + 100
    n.set_status("", hidden=True)
    _settle(n)
    rest = n.body_rect().width()
    n.enterEvent(None)
    _settle(n)
    assert n.body_rect().width() == pytest.approx(rest, abs=1)     # a passing hover changes nothing
    assert n._shown_text() == ""
    n._show_hint()                                                  # …a longer one says what a click does
    _settle(n)
    assert n.body_rect().width() > rest
    n.leaveEvent(None)
    assert not n.hint_ready
    n.hovered = False
    n.set_peek(True)
    _settle(n)
    assert n.body_rect().height() < 8


def test_notch_hover_never_says_rotary():
    from rotary.live import LiveState, NowPlaying
    from rotary.ui.notch import HOVER_HINT
    n = NotchWindow()
    n.enterEvent(None)
    assert n._hover_label() == ""
    n._show_hint()
    assert n._hover_label() == HOVER_HINT == "Click to open wheel"
    assert n._shown_text() == HOVER_HINT
    # music (or a call, a timer) on the notch: the hover shows that instead, never the hint
    n.set_live(LiveState(media=NowPlaying("a", "A", "Song", playing=True)))
    assert n._hover_label() == "" and n._card_kind() == "media"
    n.set_live(LiveState())
    n.start_timer(60)
    assert n._hover_label() == ""
    n.cancel_timer()
    n.set_status("Clip saved", "done", "#30D158")
    assert n._shown_text() == "Clip saved"
    n.leaveEvent(None)


def test_notch_click_and_wheel():
    n = NotchWindow()
    got = []
    n.clicked.connect(lambda: got.append("click"))
    n.scrolled.connect(lambda s: got.append(s))
    ev = type("E", (), {"button": lambda self: Qt.MouseButton.LeftButton, "position": lambda self: QPointF(5, 5),
                        "globalPosition": lambda self: QPointF(5, 5)})()
    n.mousePressEvent(ev)
    assert got == []                           # a click counts when it lets go (a press may start a drag)
    n.mouseReleaseEvent(ev)
    for dy in (-120, -60, -60, 120):
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        n.wheelEvent(ev)
    assert got == ["click", 1, 1, -1]          # wheel down = next; half notches add up


@pytest.mark.parametrize("mode,camera,notch", [("camera", True, "auto"), ("notch", False, "on"),
                                               ("both", True, "on")])
def test_input_modes_round_trip(mode, camera, notch):
    gen = {}
    set_input_mode(gen, mode)
    assert (gen["camera"], gen["notch"]) == (camera, notch)
    assert input_mode(gen) == mode


# ------------------------------------------------------------------ the rotary under the mouse
def test_dial_hit_test_finds_items_and_centre():
    d = DialWindow(build_items(DEFAULT_CONFIG), 600)
    dp = d.painter_
    dp.begin(EngineSnapshot(phase=Phase.OPEN, selected=0, position=0))
    R, Rin, Rmid, rc, lens_r = dp._geom()
    cx, cy = d.width() / 2, d.height() / 2
    assert d.hit(QPointF(cx, cy)) == ("centre", 0)
    top = angle_point(Rmid, 0)
    assert d.hit(QPointF(cx + top.x(), cy + top.y())) == ("item", 0)
    n = len(dp.items)
    p2 = angle_point(Rmid, 2 * 360 / n)
    assert d.hit(QPointF(cx + p2.x(), cy + p2.y())) == ("item", 2)
    gap = angle_point(Rmid, 0.5 * 360 / n)
    assert d.hit(QPointF(cx + gap.x(), cy + gap.y()))[0] == "ring"
    assert d.hit(QPointF(2, 2)) == ("", 0)


def test_dial_ignores_the_mouse_when_opened_by_a_gesture():
    d = DialWindow(build_items(DEFAULT_CONFIG), 600)
    got = []
    d.mouse.connect(lambda k, v: got.append(k))
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    d.wheelEvent(ev)
    assert got == []
    d._set_interactive(True)
    d.wheelEvent(ev)
    assert got == ["turn"]


def _press(d, kind_name, pt, button=Qt.MouseButton.LeftButton):
    ev = type("E", (), {"button": lambda self: button, "position": lambda self: pt})()
    getattr(d, kind_name)(ev)


def test_volume_button_beside_the_rotary():
    from PySide6.QtGui import QImage, QPainter
    d = DialWindow(build_items(DEFAULT_CONFIG), 600)
    dp = d.painter_
    dp.begin(EngineSnapshot(phase=Phase.OPEN, selected=0, position=0))
    got = []
    d.mouse.connect(lambda k, v: got.append((k, v)))
    c, r = dp.volume_button()
    pt = QPointF(d.width() / 2 + c.x(), d.height() / 2 + c.y())
    assert d.hit(pt) == ("", 0)                         # opened by a gesture: no button
    dp.keyboard = True
    for _ in range(40):
        dp.tick(1 / 60)
    assert d.hit(pt) == ("volume", 0)
    d._set_interactive(True)
    _press(d, "mousePressEvent", pt)
    _press(d, "mouseReleaseEvent", pt)
    assert got == [("volume", 0)]
    z = d.volume_zone()
    assert z.contains(pt) and z.right() <= d.width() and z.bottom() <= d.height()
    # as a volume ring: the level in the middle, the button lit
    dp.volume_level = 0.4
    dp.set_mode("volume", dp.items[:1] * 25, {"sub": "Volume", "top": "Volume"},
                EngineSnapshot(phase=Phase.OPEN, selected=10, position=10))
    for _ in range(40):
        dp.tick(1 / 60)
    img = QImage(d.width(), d.height(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    dp.paint(p, d.width(), d.height())
    p.end()
    assert img.pixelColor(int(pt.x()), int(pt.y())).alpha() > 150
    assert "change it" in dp.hint()


def test_breadcrumb_is_a_back_button_from_the_mouse():
    from PySide6.QtGui import QImage, QPainter
    d = DialWindow(build_items(DEFAULT_CONFIG), 600)
    dp = d.painter_
    dp.keyboard = True
    dp.set_level(dp.items[:4], "Steam", EngineSnapshot(phase=Phase.OPEN, selected=0, position=0))
    for _ in range(60):
        dp.tick(1 / 60)
    img = QImage(d.width(), d.height(), QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    dp.paint(p, d.width(), d.height())
    p.end()
    c = dp._crumb_rect.center()
    pt = QPointF(d.width() / 2 + c.x(), d.height() / 2 + c.y())
    assert d.hit(pt) == ("back", 0)
    got = []
    d.mouse.connect(lambda k, v: got.append(k))
    d._set_interactive(True)
    _press(d, "mousePressEvent", pt)
    _press(d, "mouseReleaseEvent", pt)
    assert got == ["back"]


def test_rotary_clock_text():
    import time
    from rotary.ui.dial import dial_date, dial_time
    t = time.struct_time((2026, 10, 23, 21, 5, 0, 4, 296, 0))
    assert dial_time(t, False) == "9:05 PM" and dial_time(t, True) == "21:05"
    t0 = time.struct_time((2026, 10, 23, 0, 7, 0, 4, 296, 0))
    assert dial_time(t0, False) == "12:07 AM" and dial_time(t0, True) == "00:07"
    assert dial_date(t) == "Fri, Oct 23"


def test_rotary_paints_its_clock_and_weather():
    import time
    from PySide6.QtGui import QImage, QPainter
    from rotary.ui.dial import draw_weather
    from rotary.weather import KINDS, Weather
    d = DialWindow(build_items(DEFAULT_CONFIG), 600)
    dp = d.painter_
    dp.weather = Weather("rain", 9.4, 48.9, False, "Light rain", time.time())
    dp.begin(EngineSnapshot(phase=Phase.OPEN, selected=0, position=0))
    for _ in range(60):
        dp.tick(1 / 60)
    for mode in ("12h", "24h", "off"):
        dp.clock_mode = mode
        img = QImage(d.width(), d.height(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        dp.paint(p, d.width(), d.height())
        p.end()
        R = dp._geom()[0]
        below = img.pixelColor(int(d.width() / 2), int(d.height() / 2 + R + 30)).alpha()
        assert (below > 140) == (mode != "off")       # the glass capsule, not just the ring's shadow
    assert Weather("clear", 21.6, 70.9).temperature(False) == "22°"
    img = QImage(60, 60, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    for k in KINDS:
        for night in (False, True):
            draw_weather(p, k, QPointF(30, 30), 40, night)
    p.end()


# ------------------------------------------------------------------ sounds & the slim MediaPipe import
def test_sound_volume_is_baked_into_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback.tempfile, "gettempdir", lambda: str(tmp_path))

    def peak(v):
        s = feedback.Sounds(True, v)
        with wave.open(s.files["tick"]) as w:
            return np.abs(np.frombuffer(w.readframes(w.getnframes()), "<i2")).max()
    assert peak(0.2) < peak(0.8)


def test_mediapipe_gets_stand_ins_for_what_we_leave_out():
    tracker._install_stubs()
    tracker._install_stubs()
    assert sum(isinstance(f, tracker._StubFinder) for f in sys.meta_path) == 1
    import importlib
    mod = importlib.import_module("mediapipe.tasks.python.vision.drawing_utils")
    assert not hasattr(mod, "plt")


# ------------------------------------------------------------------ moving the notch
class _Mouse:
    def __init__(self, pt, button=Qt.MouseButton.LeftButton):
        self.pt, self.b = QPointF(pt), button

    def button(self):
        return self.b

    def position(self):
        return self.pt

    def globalPosition(self):
        return self.pt


def _shown_notch():
    n = NotchWindow()
    n.show()
    n.place()
    _settle(n)
    return n


def _drag(n, points, release=True):
    n.mousePressEvent(_Mouse(points[0]))
    for pt in points[1:]:
        n.mouseMoveEvent(_Mouse(pt))
    if release:
        n.mouseReleaseEvent(_Mouse(points[-1]))


def test_notch_slides_along_the_top_and_remembers_where():
    n = _shown_notch()
    sr = n._screen_rect()
    moved, opened = [], []
    n.moved.connect(moved.append)
    n.clicked.connect(lambda: opened.append(1))
    start = n.pos()
    c = QPointF(start.x() + n.width() / 2, sr.top() + 10)
    _drag(n, [c, c + QPointF(10, 0), c + QPointF(-80, 0)])
    n._vel = None
    n._settle_move()
    assert n.docked and n.pos().y() == int(sr.top())
    assert n.pos().x() == start.x() - 80
    assert opened == []                                   # a drag isn't a click
    assert moved[-1]["docked"] is True and moved[-1]["x"] == pytest.approx(c.x() - 80, abs=1)
    n.hide()


def test_notch_stretches_then_tears_off_and_merges_back():
    n = _shown_notch()
    sr = n._screen_rect()
    c = QPointF(n.pos().x() + n.width() / 2, sr.top() + 10)
    _drag(n, [c, c + QPointF(0, 30)], release=False)
    assert n.docked and n.pull.value > 10 and n._stretch() > 0      # stretched like a drop, still attached
    n.mouseReleaseEvent(_Mouse(c + QPointF(0, 30)))
    _settle(n)
    assert n.docked and n.pull.value == pytest.approx(0, abs=0.5)   # let go: it snaps back up
    n._settle_move()
    _drag(n, [c, c + QPointF(0, 40), c + QPointF(0, 200)], release=False)
    assert not n.docked                                             # torn off
    assert n.body_rect().top() == pytest.approx(n.FLOAT_PAD * n.s, abs=0.1)
    path = n._body_path(n.body_rect(), 13)
    assert path.boundingRect().top() >= n.FLOAT_PAD * n.s - 1      # a free island: nothing reaches the edge
    n.mouseMoveEvent(_Mouse(c + QPointF(0, 2)))                     # back to the top edge: merges in
    assert n.docked and n.pos().y() == int(sr.top())
    n.mouseReleaseEvent(_Mouse(c + QPointF(0, 2)))
    n.hide()


def _free_notch(n, cx, top):
    """Float `n` with its body's top middle at (cx, top) on the screen."""
    n.edge = None
    n._up = False
    n._set_pos(cx - n.width() / 2, top - n.FLOAT_PAD * n.s)
    n._settle_move()


def _on_screen(n):
    return n.body_rect().translated(n.pos().x(), n.pos().y())


def test_thrown_notch_glides_then_merges_into_the_edge_it_hits():
    n = _shown_notch()
    sr = n._screen_rect()
    _free_notch(n, sr.center().x(), sr.top() + 120)
    n._vel = QPointF(3000, 900)
    n._moving = True
    moved = []
    n.moved.connect(moved.append)
    for _ in range(600):
        if n._vel is None:
            break
        n._fly(1 / 60)
    assert n._vel is None and not n._moving and moved
    assert n.edge == "right" and moved[-1]["edge"] == "right"
    body = _on_screen(n)
    assert body.right() == pytest.approx(sr.right(), abs=1)         # hanging from the right edge
    assert sr.top() <= body.center().y() <= sr.bottom()
    _free_notch(n, sr.center().x(), sr.center().y())
    n._vel = QPointF(0, 2500)                                      # straight down: joins the bottom edge
    n._moving = True
    for _ in range(600):
        if n._vel is None:
            break
        n._fly(1 / 60)
    assert n.edge == "bottom" and _on_screen(n).bottom() == pytest.approx(sr.bottom(), abs=1)
    n.hide()


def test_free_notch_goes_all_the_way_down_the_screen():
    """It used to stop well above the bottom of the screen: the whole (tall, mostly empty) window had to fit."""
    n = _shown_notch()
    sr = n._screen_rect()
    _free_notch(n, sr.center().x(), sr.top() + 100)
    c = _on_screen(n).center()
    target = sr.bottom() - n.DOCK_SNAP * n.s - 20                 # just clear of the bottom edge
    _drag(n, [c, c + QPointF(0, 10), QPointF(c.x(), target)], release=False)
    assert n.edge is None
    assert _on_screen(n).center().y() == pytest.approx(target, abs=2)
    n.mouseReleaseEvent(_Mouse(QPointF(c.x(), target)))
    _settle(n)
    assert n._up                                                   # in the bottom half: it grows upwards
    bottom = _on_screen(n).bottom()
    n.h_s.snap(n.MEDIA_CARD[1] * n.s)                              # a card opens: up, not off the screen
    assert _on_screen(n).bottom() == pytest.approx(bottom, abs=1) and _on_screen(n).top() >= sr.top()
    n._settle_move()
    saved = n.position
    assert saved["docked"] is False and saved["up"] is True and saved["y"] == pytest.approx(bottom, abs=1)
    n.hide()


@pytest.mark.parametrize("edge", ["bottom", "left", "right"])
def test_notch_docks_on_any_edge_slides_along_it_and_tears_off(edge):
    n = _shown_notch()
    sr = n._screen_rect()
    _free_notch(n, sr.center().x(), sr.center().y() - 15)
    c = _on_screen(n).center()
    to = {"bottom": QPointF(c.x(), sr.bottom() - 10), "left": QPointF(sr.left() + 60, c.y()),
          "right": QPointF(sr.right() - 60, c.y())}[edge]
    _drag(n, [c, c + (to - c) * 0.1, to], release=False)
    assert n.edge == edge                                          # brought near the edge: merged into it
    body = _on_screen(n)
    w, h = n.width(), n.height()
    assert n._hanging(n.body_rect())
    if edge == "bottom":
        assert body.bottom() == pytest.approx(sr.bottom(), abs=1) and n.pos().y() == int(sr.bottom() - h)
    elif edge == "left":
        assert body.left() == pytest.approx(sr.left(), abs=1) and n.pos().x() == int(sr.left())
    else:
        assert body.right() == pytest.approx(sr.right(), abs=1) and n.pos().x() == int(sr.right() - w)
    path = n._body_path(n.body_rect(), 13).boundingRect().translated(n.pos().x(), n.pos().y())
    assert sr.adjusted(-30, -30, 30, 30).contains(path)              # the part past the edge is off screen only
    n.mouseReleaseEvent(_Mouse(to))
    n._settle_move()
    saved = n.position
    assert saved["docked"] is True and saved["edge"] == edge

    # slide along the edge
    start = _on_screen(n).center()
    along = QPointF(-60, 0) if edge == "bottom" else QPointF(0, -60)
    _drag(n, [start, start + along * 0.2, start + along])
    n._vel = None
    n._settle_move()
    assert n.edge == edge
    moved = _on_screen(n).center() - start
    assert moved.x() == pytest.approx(along.x(), abs=2) and moved.y() == pytest.approx(along.y(), abs=2)

    # pull away a little: it stretches; further, it tears off
    start = _on_screen(n).center()
    away = {"bottom": QPointF(0, -1), "left": QPointF(1, 0), "right": QPointF(-1, 0)}[edge]
    _drag(n, [start, start + away * 30], release=False)
    assert n.edge == edge and n.pull.value > 10 and n._stretch() > 0
    assert not n._hanging(n.body_rect())
    n.mouseMoveEvent(_Mouse(start + away * 200))
    assert n.edge is None                                          # torn off
    n.mouseReleaseEvent(_Mouse(start + away * 200))
    n.hide()


def test_saved_edge_is_restored():
    n = NotchWindow()
    sr = n._screen_rect()
    n.set_position({"docked": True, "edge": "left", "x": sr.left(), "y": sr.top() + 300})
    n.show()
    n.place()
    _settle(n)
    assert n.edge == "left" and n.pos().x() == int(sr.left())
    assert n.pos().y() + n.height() / 2 == pytest.approx(sr.top() + 300, abs=1)
    n.set_position({"docked": True, "edge": "sideways", "x": sr.left() + 200, "y": sr.top()})
    n.place()
    assert n.edge == "top"                                         # junk edge: the top
    n.set_position({"docked": False, "x": sr.left() + 300, "y": sr.bottom() - 40, "up": True})
    n.place()
    assert n.edge is None and n._up
    assert _on_screen(n).bottom() == pytest.approx(sr.bottom() - 40, abs=1)
    n.hide()


def test_middle_click_sends_the_notch_home():
    n = _shown_notch()
    sr = n._screen_rect()
    n.docked = False
    n._set_pos(sr.left(), sr.top() + 100)
    n._settle_move()
    assert n.position is not None and n.position["docked"] is False
    moved = []
    n.moved.connect(moved.append)
    n.mousePressEvent(_Mouse(QPointF(10, 10), Qt.MouseButton.MiddleButton))
    assert n.docked and n._glide is not None
    for _ in range(300):
        if n._glide is None:
            break
        n._tick()
        n._last -= 1 / 60
    assert n._glide is None and n.is_home() and moved[-1] is None
    home = n._screen_rect()
    assert abs(n.pos().x() + n.width() / 2 - home.center().x()) <= 2 and n.pos().y() == int(home.top())
    n.hide()


def test_saved_position_is_restored():
    n = NotchWindow()
    sr = n._screen_rect()
    n.set_position({"docked": False, "x": sr.left() + 300, "y": sr.top() + 150})
    n.show()
    n.place()
    assert not n.docked
    assert n.pos().x() + n.width() / 2 == pytest.approx(sr.left() + 300, abs=1)
    assert n.body_rect().top() + n.pos().y() == pytest.approx(sr.top() + 150, abs=1)
    n.set_position({"x": "nonsense"})                     # junk in config.json: back home
    n.place()
    assert n.docked
    n.hide()


def test_rotary_held_still_is_not_painted_again():
    """While nothing on the rotary moves, a frame looks just like the last one: the window skips painting it."""
    from rotary.ui.dial import DialPainter
    dp = DialPainter(build_items(DEFAULT_CONFIG), 600)
    st = EngineSnapshot(phase=Phase.OPEN, hand_present=True, facing=True, position=0, selected=0)
    dp.begin(st)
    dp.set_state(st)
    for _ in range(240):
        dp.tick(1 / 60)
    key = dp.visual_key()
    dp.set_state(EngineSnapshot(phase=Phase.OPEN, hand_present=True, facing=True, position=0, selected=0,
                                facing_value=0.71, grip_value=0.13))          # hand jitter the ring doesn't show
    dp.tick(1 / 60)
    assert dp.visual_key() == key
    dp.set_state(EngineSnapshot(phase=Phase.OPEN, hand_present=True, facing=True, position=1, selected=1,
                                interacted=True))
    dp.tick(1 / 60)
    assert dp.visual_key() != key


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_get_window_rect_takes_the_standard_rect():
    """app.py hands GetWindowRect a wintypes.RECT; declaring another RECT class broke every gesture opening."""
    import ctypes
    from ctypes import wintypes

    from rotary import winapi
    r = wintypes.RECT()
    assert winapi.user32.GetWindowRect(wintypes.HWND(winapi.user32.GetDesktopWindow()), ctypes.byref(r))
    assert r.right > r.left
