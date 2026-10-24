"""Application wiring: tracker → gesture engine → overlay UI → actions."""
from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QFileSystemWatcher, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QConicalGradient, QFont, QFontMetricsF,
                           QGuiApplication, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import (DATA_DIR, LINUX, MAC, T0, WIN, __version__, assist, integrations, modes, system, tutorial, update,
               winapi)
from .actions import (CONFIG_PATH, Item, back_item, build_items, cleanup_scripts, execute, item_from, load_config,
                      save_config)
from .feedback import Sounds, Speaker
from .live import LiveWatcher
from .mouse import MouseTrigger
from .resize import TwoHandResize, WindowSizer
from .volume import Volume
from .weather import WeatherWatcher
from .gestures import GestureEngine, GestureSettings, HandednessTracker, HandFeatures, Phase, extract_features
from .tracker import CameraStatus, TrackerThread, TrackResult
from .ui.dial import CORNERS, DialWindow, VolumeRing
from .ui.glass import TEXT, family, font, load_fonts, set_text_font
from .ui.notch import NotchWindow
from .ui.pill import AMBER, BLUE, GRAY, GREEN, RED, PillWindow

log = logging.getLogger(__name__)
# These, the data folder and the installed files keep the names from before RotaryUI: they're what existing
# installs, their taskbar pins and their updates go by.
INSTANCE_KEY = "GestureRotary.single-instance"
APP_ID = "GestureRotary.App"


def fullscreen_state() -> int:
    """Windows' own 'busy' signal, as used to hold back notifications: 2 = something is full screen
    (borderless games, videos), 3 = a Direct3D game owns the screen exclusively, 4 = presentation mode;
    anything else = normal desktop."""
    if sys.platform != "win32":
        return 0
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return 0
    except Exception:
        return 0
    return state.value


def fullscreen_app_running() -> bool:
    return fullscreen_state() in (2, 3, 4)


# --------------------------------------------------------------------------- global hotkeys
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEYS = {"ctrl+alt+space": (MOD_CONTROL | MOD_ALT, 0x20), "ctrl+shift+space": (MOD_CONTROL | MOD_SHIFT, 0x20),
           "ctrl+alt+r": (MOD_CONTROL | MOD_ALT, 0x52), "win+alt+space": (MOD_WIN | MOD_ALT, 0x20)}
VIDEO_TICKS = 120                  # a video ring is this many steps long: one full turn, start to end
SLIDES_TINT, VIDEO_TINT, VOLUME_TINT = "#FF9F0A", "#FF375F", "#0A84FF"
VOLUME_TICKS = 25                  # a volume ring: a full turn from silent to full, 4 % a click
HOTKEY_LABELS = {"ctrl+alt+space": "Ctrl + Alt + Space", "ctrl+shift+space": "Ctrl + Shift + Space",
                 "ctrl+alt+r": "Ctrl + Alt + R", "win+alt+space": "Win + Alt + Space"}
if MAC:
    HOTKEY_LABELS = {"ctrl+alt+space": "⌃⌥Space", "ctrl+shift+space": "⌃⇧Space", "ctrl+alt+r": "⌃⌥R",
                     "win+alt+space": "⌥⌘Space"}
elif not WIN:
    HOTKEY_LABELS["win+alt+space"] = "Super + Alt + Space"
HK_TOGGLE = 1
HK_ASSIST = 2
ASSIST_HOTKEYS = {"win+alt+a": (MOD_WIN | MOD_ALT, 0x41), "ctrl+alt+a": (MOD_CONTROL | MOD_ALT, 0x41),
                  "ctrl+shift+a": (MOD_CONTROL | MOD_SHIFT, 0x41)}
ASSIST_LABELS = {"win+alt+a": "Win + Alt + A" if WIN else "Super + Alt + A", "ctrl+alt+a": "Ctrl + Alt + A",
                 "ctrl+shift+a": "Ctrl + Shift + A"}
if MAC:
    ASSIST_LABELS = {"ctrl+alt+a": "⌃⌥A", "win+alt+a": "⌥⌘A", "ctrl+shift+a": "⌃⇧A"}
NAV = {11: (0x25, -1), 12: (0x27, 1), 13: (0x26, -1), 14: (0x28, 1), 15: (0x0D, 0), 16: (0x1B, 0)}  # ← → ↑ ↓ ⏎ Esc
# While the rotary is open from the mouse or the keyboard: + and − (and the keypad's) turn it into the volume ring
# and then turn that ring.
VOLUME_KEYS = {17: (0xBB, 1), 18: (0xBD, -1), 19: (0x6B, 1), 20: (0x6D, -1)}


class HotkeyWindow(QWidget):
    """An invisible native window that receives RegisterHotKey messages. On macOS (Carbon hotkeys) and
    Linux (an X11 key grab) the same calls go to posix.py, and `triggered` fires all the same."""
    triggered = Signal(int)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.active: set[int] = set()
        self.backend = None
        if WIN:
            self.hwnd = int(self.winId())
        else:
            from . import posix
            self.backend = posix.hotkeys(self.triggered.emit)   # a queued signal when it fires off-thread

    def register(self, hid: int, mods: int, vk: int) -> bool:
        if not WIN:
            ok = self.backend is not None and self.backend.register(hid, mods & ~MOD_NOREPEAT, vk)
        else:
            ok = bool(winapi.user32.RegisterHotKey(self.hwnd, hid, mods, vk))
        if ok:
            self.active.add(hid)
        return ok

    def unregister(self, hid: int) -> None:
        if hid in self.active:
            if WIN:
                winapi.user32.UnregisterHotKey(self.hwnd, hid)
            elif self.backend is not None:
                self.backend.unregister(hid)
        self.active.discard(hid)

    def nativeEvent(self, event_type, message):
        if not WIN:
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312:                          # WM_HOTKEY
                self.triggered.emit(int(msg.wParam))
                return True, 0
        except Exception:
            pass
        return False, 0


# --------------------------------------------------------------------------- icons
# The ring of items on the app icon, clockwise from 12 o'clock (that one is the selected item).
ICON_DOTS = ["#0A84FF", "#30D158", "#FFD60A", "#FF9F0A", "#FF375F", "#BF5AF2", "#5E5CE6", "#64D2FF"]


def _icon_pixmap(px: int, active: bool = True, tile: bool = True) -> QPixmap:
    """App icon: the rotary itself: a glass ring of coloured items turning to the one at the top, under
    the selection lens, around the glass hub that names it. The tray version is a plain ring with the
    lens, which still reads at 16 px."""
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = px / 64.0
    if tile:
        body = QRectF(2 * s, 2 * s, 60 * s, 60 * s)
        g = QLinearGradient(0, 0, 0, px)
        g.setColorAt(0, QColor("#24306b"))
        g.setColorAt(1, QColor("#0c0e1c"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(body, 14 * s, 14 * s)
        glow = QRadialGradient(QPointF(32 * s, 30 * s), 30 * s)
        glow.setColorAt(0, QColor(94, 92, 230, 120))
        glow.setColorAt(1, QColor(94, 92, 230, 0))
        p.setBrush(glow)
        p.drawRoundedRect(body, 14 * s, 14 * s)
        c, r = QPointF(32 * s, 33 * s), 20.5 * s
        # The glass track the items sit on.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 34), 9.5 * s))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(255, 255, 255, 70), 0.8 * s))
        p.drawEllipse(c, r + 4.75 * s, r + 4.75 * s)
        p.drawEllipse(c, r - 4.75 * s, r - 4.75 * s)
        # A trail behind the selection: the ring has just turned to it.
        trail = QConicalGradient(c, 90)
        trail.setColorAt(0.0, QColor(10, 132, 255, 230))
        trail.setColorAt(0.2, QColor(10, 132, 255, 0))
        trail.setColorAt(1.0, QColor(10, 132, 255, 0))
        pen = QPen(QBrush(trail), 3.2 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16, 70 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        n = len(ICON_DOTS)
        for i, col in enumerate(ICON_DOTS[1:], 1):
            a = math.radians(-90 + i * 360 / n)
            p.setBrush(QColor(col) if active else QColor("#8E8E93"))
            p.drawEllipse(QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a)), 3.3 * s, 3.3 * s)
        # The selected item at 12 o'clock, under a lens.
        top = QPointF(c.x(), c.y() - r)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(top + QPointF(0, 1.2 * s), 6.8 * s, 6.8 * s)
        p.setBrush(QColor("white"))
        p.drawEllipse(top, 6.8 * s, 6.8 * s)
        p.setBrush(QColor(ICON_DOTS[0]) if active else QColor("#8E8E93"))
        p.drawEllipse(top, 5.2 * s, 5.2 * s)
        # The centre, as on the real rotary: a glass hub holding the chosen item's name.
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(c + QPointF(0, 1.0 * s), 11.5 * s, 11.5 * s)
        hub = QLinearGradient(0, c.y() - 11.5 * s, 0, c.y() + 11.5 * s)
        hub.setColorAt(0, QColor(255, 255, 255, 70))
        hub.setColorAt(1, QColor(255, 255, 255, 22))
        p.setBrush(hub)
        p.setPen(QPen(QColor(255, 255, 255, 110), 0.8 * s))
        p.drawEllipse(c, 11.5 * s, 11.5 * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(c.x() - 6 * s, c.y() - 3.4 * s, 12 * s, 3.2 * s), 1.6 * s, 1.6 * s)
        p.setBrush(QColor(255, 255, 255, 140))
        p.drawRoundedRect(QRectF(c.x() - 4 * s, c.y() + 1.4 * s, 8 * s, 2.2 * s), 1.1 * s, 1.1 * s)
        p.end()
        return pm
    c = QPointF(32 * s, 33 * s)
    alpha = 235 if active else 120
    p.setPen(QPen(QColor(255, 255, 255, alpha), 6.0 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = 24 * s
    p.drawEllipse(c, r, r)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0A84FF") if active else QColor("#8E8E93"))
    p.drawEllipse(QPointF(c.x(), c.y() - r), 7.5 * s, 7.5 * s)
    p.setBrush(QColor(255, 255, 255, alpha))
    p.drawEllipse(c, 5.5 * s, 5.5 * s)
    p.end()
    return pm


def brand_icon() -> QIcon:
    icon = QIcon()
    for px in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_icon_pixmap(px))
    return icon


def tray_icon(active: bool = True) -> QIcon:
    return QIcon(_icon_pixmap(64, active, tile=False))


# --------------------------------------------------------------------------- face test & hand choice
# The camera only sees in 2D, so a hand held up in front of the face overlaps it on screen just like a hand
# resting on the chin. What tells them apart is depth: a hand touching the face is about as far from the camera
# as the face, a hand held up in front of it is clearly closer. Both sizes are known in real units (MediaPipe's
# metric hand skeleton; the face detector's box is about FACE_SIZE_M across), so comparing how many image
# heights a metre takes up on each gives the ratio of their distances.
FACE_SIZE_M = 0.16        # real size of the face detector's box (cheek to cheek / brow to chin), metres
TOUCH_DEPTH = 1.18        # a hand less than this much closer to the camera than the face is touching it
TOUCH_DEPTH_EXIT = 1.32   # once touching, it has to come this far forward to count as in front again


def face_depth(f: HandFeatures, face: tuple[float, float, float, float], aspect: float) -> float:
    """How much closer to the camera the hand is than the face: 1 = same distance, 2 = half as far away.
    0 when the hand's real size is unknown."""
    fx0, fy0, fx1, fy1 = face
    face_px_per_m = max((fx1 - fx0) * aspect, fy1 - fy0) / FACE_SIZE_M
    return f.px_per_m / max(face_px_per_m, 1e-6)


def _overlaps_face(f: HandFeatures, face: tuple[float, float, float, float]) -> bool:
    """On screen, is the hand over the face, under the chin, or reaching into the face with a fingertip?
    A hand raised beside the head does not overlap."""
    hx0, hy0, hx1, hy1 = f.bbox
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    cx, cy = (hx0 + hx1) / 2, (hy0 + hy1) / 2
    if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
        return True
    if any(fx0 <= x <= fx1 and fy0 <= y <= fy1 for x, y in f.tips):    # scratching a cheek, rubbing an eye
        return True
    ha = max((hx1 - hx0) * (hy1 - hy0), 1e-6)
    zx0, zx1 = fx0 - 0.15 * fw, fx1 + 0.15 * fw
    zy0, zy1 = fy0 + 0.45 * fh, fy1 + 0.8 * fh
    ix = max(0.0, min(hx1, zx1) - max(hx0, zx0))
    iy = max(0.0, min(hy1, zy1) - max(hy0, zy0))
    return ix * iy / ha > 0.35


def face_contact(f: HandFeatures, faces: list, aspect: float, was: bool = False) -> tuple[bool, float]:
    """(touching the face, depth ratio to the face it overlaps or 0). Overlapping a face on screen is not enough:
    the hand also has to be at about the face's distance, so gestures made in front of the face still work."""
    depth = 0.0
    for face in faces:
        if not _overlaps_face(f, face):
            continue
        d = face_depth(f, face, aspect)
        if d < (TOUCH_DEPTH_EXIT if was else TOUCH_DEPTH):
            return True, d
        depth = max(depth, d)
    return False, depth


FACE_SETTLE = 0.3         # seconds a hand has to stay against the face before it counts as touching it


class FaceTouch:
    """Touching the face, for the hand we follow, only once it has lasted a moment. A hand moving out from in
    front of the face (or past the chin) can look like it's touching it for a frame or two; that mustn't spoil
    a palm being held up or a fist closing."""

    def __init__(self):
        self.on = False
        self._since: float | None = None

    def update(self, f: HandFeatures, faces: list, aspect: float, same: bool, t: float) -> tuple[bool, float]:
        if not same:
            self.on, self._since = False, None
        touching, depth = face_contact(f, faces, aspect, self.on)
        if not touching:
            self.on, self._since = False, None
        elif not self.on:
            self._since = t if self._since is None else self._since
            self.on = t - self._since >= FACE_SETTLE
        return self.on, depth


def hand_at_face(f: HandFeatures, faces: list, aspect: float, was: bool = False) -> bool:
    """Is the hand touching the face: propping up the chin, resting on a cheek, scratching?
    A hand raised beside the head, or held up in front of the face, is *not* at the face."""
    return face_contact(f, faces, aspect, was)[0]


def pick_hand(feats: list[HandFeatures], faces: list, prev: tuple[float, float] | None,
              aspect: float = 4 / 3) -> tuple[int, bool]:
    """Which hand to follow: the one being held up on purpose, preferring the one we already follow.
    Returns (index, is_same_hand_as_before)."""
    if not feats:
        return -1, False
    best, best_score, same = -1, -1e9, False
    for i, f in enumerate(feats):
        near_prev = prev is not None and math.hypot(f.center[0] - prev[0], f.center[1] - prev[1]) < 0.25
        score = (f.scale * 4 + (1.5 if f.facing > 0.2 else 0.0) + (1.0 if f.upright_dev < 60 else 0.0)
                 - (2.5 if hand_at_face(f, faces, aspect) else 0.0) + (1.2 if near_prev else 0.0))
        if score > best_score:
            best, best_score, same = i, score, near_prev
    return best, same


# --------------------------------------------------------------------------- app
class RotaryApp(QObject):
    action_done = Signal(str, bool, str)   # item name, ok, error
    dynamic_ready = Signal(str)            # an app's games / scenes / projects were refreshed
    assist_done = Signal(object, str)      # screen assist: the Answer (or None), error
    update_done = Signal(object, object, str)  # update check: newer Release (or None), Staged (or None), error
    update_state = Signal(str)             # what General → Updates says
    mode_data = Signal(int, str, object)   # slides / video ring: (token, what, data) from a background thread
    volume_level = Signal(float)           # the volume slider set this level (-1: unknown)
    volume_read = Signal(float)            # the level when the rotary opened from the mouse / keyboard
    resize_window = Signal(object)         # two-hand resize began on this ActiveWindow (None: nothing to resize)

    def __init__(self, app: QApplication, show_window: bool | None = None):
        super().__init__()
        self.app = app
        self.quitting = False
        load_fonts()                             # the bundled icon font on macOS / Linux
        self.app_icon = brand_icon()
        app.setWindowIcon(self.app_icon)
        self.cfg = load_config()
        self._font_choice = None
        self.apply_font()
        install_changed = system.refresh_install(self.cfg)
        self.items = build_items(self.cfg)       # the top-level ring
        self.level_items = self.items            # what the ring shows right now
        self.levels: list[tuple[list, int, str]] = []
        self.crumb = ""
        self._qn_app = ""
        self.settings = GestureSettings.from_dict(self.cfg.get("gestures"))
        self.engine = GestureEngine(len(self.items), self.settings)
        self.hands = HandednessTracker()
        ap = self.cfg.get("appearance", {})
        self.sounds = Sounds(bool(ap.get("sounds", True)), float(ap.get("volume", 0.6)))
        self.speaker = Speaker()
        self.dial = DialWindow(self.items, int(ap.get("dial_size", 540)), bool(ap.get("live_glass", False)))
        self.dial.mouse.connect(self._on_dial_mouse)
        self.volume_ring = VolumeRing(VOLUME_TICKS, VOLUME_TINT, int(ap.get("dial_size", 540)),
                                      bool(ap.get("live_glass", False)))
        self.pill_enabled = bool(ap.get("status_pill", True))
        self.pill = PillWindow()
        self.notch = NotchWindow()
        self.notch.clicked.connect(self._on_notch_click)
        self.notch.scrolled.connect(self._on_notch_scroll)
        self.notch.customize.connect(lambda: self.show_window("notch"))
        self.notch.timer_done.connect(self._on_timer_done)
        self.notch.assist.connect(self.start_assist)
        self._assist_busy = False
        self._assist_until = 0.0           # keeps the notch up for a screen-assist message
        self.live = LiveWatcher(self)
        self.live.state.connect(self.notch.set_live)
        self.live.state.connect(self._on_live_state)
        self._ringing: tuple[str, str] | None = None     # a call ringing in: the notch shows it, camera or not
        self.notch.moved.connect(self._on_notch_moved)
        self.live.event.connect(self._on_live_event)
        self.notch.media_command.connect(self.live.control)
        self.notch.call_command.connect(self._on_call_command)
        self._fs_state = 0
        self._alert_until = 0.0            # a clip / timer alert may show the notch over a full-screen game
        self._fullscreen = False
        self._fs_checked = 0.0
        self.hotkeys = HotkeyWindow()
        self.hotkeys.triggered.connect(self._on_hotkey)
        if not WIN:
            from . import posix
            posix.prepare_keyboard()           # macOS: has to happen on this (the main) thread
        self._hotkey_now = None
        self._assist_hotkey_now = None
        self.keyboard_session = False      # opened from the keyboard or the notch: mouse + arrow keys work
        self._kb_fg = 0                    # the window that was in front when it opened
        # slides / video rings: what's in front decides how the rotary opens
        self.mode = "main"
        self.mode_ctx = modes.Context()
        self._mode_token = 0
        self._mode_sel = 0                 # engine position the slides / video already follow
        self._main_sel = 0                 # where the item ring was, to go back to it
        self._mode_moved = 0.0             # last time the user turned a mode ring
        self._mode_n = 1
        self._slides: tuple[int, int] | None = None   # (current, count) once the app has said
        self._np = None                    # the video: NowPlaying from the media controls
        self._np_read = 0.0
        self._seek_to: float | None = None
        self._mode_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mode")
        self.media = modes.MediaLink()
        self.mode_data.connect(self._on_mode_data)
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(160)
        self._seek_timer.timeout.connect(self._flush_seek)
        self.volume = Volume(self.volume_level.emit)
        self.volume_level.connect(self._on_volume_level)
        self.volume_read.connect(self._on_volume_read)
        self.weather = WeatherWatcher(self)
        self.weather.changed.connect(lambda w: setattr(self.dial.painter_, "weather", self.weather.fresh()))
        self._was_sliding = False
        self._face_touch = FaceTouch()
        self.tour = None                   # the first-run tour's card while it's up (ui/tutorial.py)
        self.mouse_trigger = MouseTrigger(self)
        self.mouse_trigger.event.connect(self._on_mouse_trigger)
        self.mouse_trigger.pointer.connect(self._on_mouse_pointer)
        self._hook_map = (0.0, 0.0, 1.0)      # the dial window's origin and scale in the mouse hook's coordinates
        self.resizer = TwoHandResize()
        self.sizer = WindowSizer(winapi.active_window, winapi.set_window_rect, self.resize_window.emit)
        self.resize_window.connect(self._on_resize_window)

        gen = self.cfg.get("general", {})
        self.camera_on = bool(gen.get("camera", True))
        self.cam_status = CameraStatus("searching" if self.camera_on else "off",
                                       message="Starting…" if self.camera_on else "Camera gestures are off")
        self._no_cam_since: float | None = None if self.camera_on else time.monotonic() - 60
        self._prev_center: tuple[float, float] | None = None
        self._last_result_t = time.monotonic()
        self._last_hand_t = 0.0
        self._last_dbg = 0.0
        self._cam_lost_t = time.monotonic()
        self._flash: tuple[str, str, str, float, float | None] | None = None  # text, kind, accent, until, progress
        self._calib: list[float] | None = None
        self._calib_until = 0.0

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_cfg)
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.setInterval(260)
        self._speak_timer.timeout.connect(self._speak_current)

        prefer = (self.cfg.get("camera") or {}).get("preferred") or None
        self.tracker = TrackerThread(prefer_camera=prefer)
        self.tracker.result.connect(self.on_result)
        self.tracker.status.connect(self.on_status)
        self.action_done.connect(self._on_action_done)
        self.dynamic_ready.connect(self._on_dynamic)
        self.assist_done.connect(self._on_assist_done)
        self.update_done.connect(self._on_update_done)
        self.update_note = ""
        self._update_busy = False
        self._update_manual = False
        self._staged: update.Staged | None = None
        self._update_timer = QTimer(self)                 # check again every few hours
        self._update_timer.setInterval(update.CHECK_HOURS * 3600 * 1000)
        self._update_timer.timeout.connect(self.check_for_updates)
        self._apply_timer = QTimer(self)                  # an update waits for the rotary to be put away
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(60_000)
        self._apply_timer.timeout.connect(self._apply_update)
        gen = self.cfg.setdefault("general", {})
        last = gen.get("last_version")
        if last != __version__:
            gen["last_version"] = __version__
            install_changed = True
            if last and update.is_newer(__version__, last):
                log.info("updated from %s to %s", last, __version__)
                QTimer.singleShot(1500, lambda: self._flash_msg(f"Updated to RotaryUI {__version__}",
                                                                "done", GREEN, 4.0))

        from .ui.settings import MainWindow
        self.window = MainWindow(self)

        self._build_tray()
        self._watch_config()
        self.apply_config(save=install_changed)

        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(100)
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._heartbeat.start()

        app.commitDataRequest.connect(self._session_ending)
        app.aboutToQuit.connect(self._shutdown)
        app.primaryScreenChanged.connect(lambda _s: self.notch.place())
        if self.camera_on:
            self.tracker.start()
        else:
            self.engine.paused = True
        threading.Thread(target=self.refresh_apps, daemon=True, name="apps").start()
        threading.Thread(target=cleanup_scripts, daemon=True).start()
        threading.Thread(target=update.cleanup, daemon=True, name="update-cleanup").start()
        self._update_timer.start()
        QTimer.singleShot(60_000, self.check_for_updates)
        self._update_notch()
        self._update_pill()
        onboarded = self.cfg.get("general", {}).get("onboarded", False)
        if show_window is None:
            show_window = (not onboarded) or not self.cfg.get("general", {}).get("start_minimized", True)
        if show_window:
            self.show_window()
        else:
            self.window.showMinimized()   # lives on the taskbar
        if onboarded and not self.cfg.get("general", {}).get("tutorial_done", False):
            QTimer.singleShot(1500, self.start_tutorial)     # quit half-way through last time: pick it up again
        QTimer.singleShot(2500, self.dial.prewarm)
        QTimer.singleShot(3000, self.media.warm)            # the video ring asks the media controls
        log.info("ready in %.0f ms (camera %s)", (time.perf_counter() - T0) * 1000, "on" if self.camera_on else "off")

    # ------------------------------------------------------------ window / tray
    def show_window(self, page: str | None = None) -> None:
        if page:
            self.window.show_page(page)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    # ------------------------------------------------------------ first-run tour
    def start_tutorial(self) -> None:
        """Show the tour (from the start if it's already up). Launching items is only practice meanwhile."""
        from .ui.tutorial import TutorialWindow
        if self.tour is not None:
            self.tour.finished.disconnect()
            self.tour.end(False)
            self.tour.deleteLater()
        gen = self.cfg.get("general", {})
        hotkey = HOTKEY_LABELS.get(gen.get("hotkey", "ctrl+alt+space"), "")
        assist_key = ASSIST_LABELS.get((self.cfg.get("assist") or {}).get("hotkey", ""), "")
        ctx = tutorial.Context.from_config(self.cfg, hotkey, assist_key)
        ap = self.cfg.get("appearance", {})
        self.tour = TutorialWindow(tutorial.steps_for(ctx), {"assist": _keycaps(assist_key)},
                                   reduce_motion=bool(ap.get("reduce_motion", False)))
        self.tour.finished.connect(self._tutorial_finished)
        self.tour.start()
        log.info("tour started (%d cards)", len(self.tour.tour.steps))

    def _tutorial_finished(self, completed: bool) -> None:
        log.info("tour %s at card %d", "finished" if completed else "skipped", self.tour.tour.index + 1)
        self.tour.deleteLater()
        self.tour = None
        gen = self.cfg.setdefault("general", {})
        if not gen.get("tutorial_done", False):
            gen["tutorial_done"] = True
            self._save_timer.start()
        if not completed:
            self._flash_msg("Tour skipped · replay it from Home any time", "dot", BLUE, 3.0)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(tray_icon(True), self)
        self.tray.setToolTip("RotaryUI")
        m = QMenu()
        m.addAction("Open RotaryUI", self.show_window)
        m.addAction("Open rotary now", self.manual_open)
        m.addAction("Show the tour", self.start_tutorial)
        m.addSeparator()
        self.cam_menu = m.addMenu("Camera")
        self._fill_camera_menu()
        m.addAction("Calibrate palm direction", self.start_calibration)
        self.pause_action = QAction("Pause (camera off)", m, checkable=True)
        self.pause_action.toggled.connect(self.set_paused)
        self.pause_action.setEnabled(self.camera_on)
        m.addAction(self.pause_action)
        m.addSeparator()
        m.addAction("Quit", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_window()
                                    if r in (QSystemTrayIcon.ActivationReason.Trigger,
                                             QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self._menu = m
        self.tray.show()

    def _fill_camera_menu(self) -> None:
        self.cam_menu.clear()
        group = QActionGroup(self.cam_menu)
        cur = self.cam_status.camera.key if self.cam_status.camera else None
        for c in self.cam_status.cameras:
            a = QAction(c.label(), self.cam_menu, checkable=True)
            a.setChecked(c.key == cur)
            a.triggered.connect(lambda _=False, k=c.key: self.tracker.select_camera(k))
            group.addAction(a)
            self.cam_menu.addAction(a)
        if not self.cam_status.cameras:
            a = self.cam_menu.addAction("No cameras found")
            a.setEnabled(False)
        self.cam_menu.addSeparator()
        self.cam_menu.addAction("Rescan cameras", self.tracker.rescan)

    def set_paused(self, paused: bool) -> None:
        self.engine.paused = paused or not self.camera_on
        if self.tracker.isRunning():
            self.tracker.set_paused(paused or not self.camera_on)
        cam = self.window.page("camera")
        for sw in (self.pause_action,) + ((cam.pause,) if cam is not None else ()):
            if sw.isChecked() != paused:
                if hasattr(sw, "set_silent"):
                    sw.set_silent(paused)
                else:
                    sw.blockSignals(True)
                    sw.setChecked(paused)
                    sw.blockSignals(False)
        self.tray.setIcon(tray_icon(not paused))
        if paused and self.engine.phase == Phase.OPEN:
            self._handle(self.engine.force_close(time.monotonic(), "paused"))
        self._update_pill()

    def _set_camera(self, on: bool) -> None:
        """Camera gestures on or off. Off, the camera and the hand-tracking model are never loaded."""
        if on == self.camera_on:
            return
        self.camera_on = on
        paused = self.pause_action.isChecked()
        self.pause_action.setEnabled(on)
        if on:
            self.engine.paused = paused
            self._no_cam_since = None
            self.cam_status = CameraStatus("searching", message="Starting…")
            if self.tracker.isRunning():
                self.tracker.set_paused(paused)
            else:
                self.tracker.start()
        else:
            self.engine.paused = True
            if self.tracker.isRunning():
                self.tracker.set_paused(True)        # lets go of the camera; the light goes off
            self.cam_status = CameraStatus("off", message="Camera gestures are off")
            self._no_cam_since = time.monotonic() - 60
        log.info("camera gestures %s", "on" if on else "off")

    def apply_font(self) -> None:
        """Use the font picked in General → Appearance (default: Inter, or San Francisco on macOS) for the
        window, the rotary, the notch and the status pill."""
        choice = str(self.cfg.get("appearance", {}).get("font", "") or "")
        if choice == self._font_choice:
            return
        first = self._font_choice is None
        self._font_choice = choice
        set_text_font(choice)
        f = QApplication.font()
        f.setFamily(family())
        QApplication.setFont(f)
        if first:
            return                                       # nothing has drawn any text yet
        for cache in (getattr(self.dial.painter_, "_fonts", None), getattr(self.notch, "_fonts", None)):
            if isinstance(cache, dict):
                cache.clear()
        if hasattr(self.pill, "_tf"):
            self.pill._tf = font(TEXT, 13.5, QFont.Weight.DemiBold)
            self.pill._fm = QFontMetricsF(self.pill._tf)
        self.dial.update()
        self.notch.update()
        if getattr(self, "window", None) is not None:
            self.window.restyle()

    def manual_open(self) -> None:
        self.open_manual()

    def test_item(self, item) -> None:
        if item.kind == "quicknav":
            if self.engine.phase != Phase.OPEN:
                self.manual_open()
            self._enter_quicknav(item)
            return
        if item.kind == "back":
            return
        if item.kind == "assist":
            self.start_assist()
            return
        self._flash_msg(f"Testing {item.name}…", "dot", BLUE, 1.0)
        self._run_action(item)

    # ------------------------------------------------------------ config
    def _watch_config(self) -> None:
        self.watcher = QFileSystemWatcher([str(CONFIG_PATH)], self)
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self.reload_config))

    def apply_config(self, save: bool = True) -> None:
        """Apply self.cfg (edited in the window) right away; save shortly after."""
        self.items = build_items(self.cfg)
        in_mode = self.mode != "main"            # a slides / video ring keeps its own count and settings
        if not self.levels and not in_mode:
            self.level_items = self.items
            if self.engine.n != len(self.items):
                self.engine.set_item_count(len(self.items))
            self.dial.set_items(self.items)
        self.settings = GestureSettings.from_dict(self.cfg.get("gestures"))
        if not in_mode:
            self.engine.s = self.settings
        ap = self.cfg.get("appearance", {})
        self.apply_font()
        self.dial.set_dial_size(int(ap.get("dial_size", 540)))
        self.dial.live_glass = bool(ap.get("live_glass", False))
        self.dial.painter_.set_accessibility(ap.get("text_scale", 1.0), ap.get("high_contrast", False),
                                             ap.get("reduce_motion", False))
        self.dial.painter_.controls = self.settings.to_dict()
        self.volume_ring.set_dial_size(int(ap.get("dial_size", 540)))
        self.volume_ring.live_glass = self.dial.live_glass
        self.volume_ring.painter_.set_accessibility(ap.get("text_scale", 1.0), ap.get("high_contrast", False),
                                                    ap.get("reduce_motion", False))
        clock = ap.get("dial_clock", "12h")
        self.dial.painter_.clock_mode = clock if clock in ("12h", "24h", "off") else "12h"
        self.weather.configure(clock != "off" and bool(ap.get("dial_weather", True)), str(ap.get("weather_place", "")))
        self.dial.painter_.weather = self.weather.fresh()
        self.sounds.enabled = bool(ap.get("sounds", True))
        self.sounds.set_volume(float(ap.get("volume", 0.6)))
        self.speaker.enabled = bool(ap.get("speak", False))
        self.pill_enabled = bool(ap.get("status_pill", True))
        if not self.pill_enabled:
            self.pill.set_status("", hidden=True)
            self.notch.set_status("", hidden=True)
        gen = self.cfg.get("general", {})
        self._set_camera(bool(gen.get("camera", True)))
        self.notch.set_accessibility(ap.get("text_scale", 1.0), ap.get("high_contrast", False),
                                     ap.get("reduce_motion", False))
        self.notch.set_peek(bool(gen.get("notch_peek", False)))
        island = self.cfg.get("island", {})
        self.notch.set_options(island)
        self.notch.set_position(island.get("position"))
        self.live.configure(island)
        self.notch.set_hotkey(HOTKEY_LABELS.get(gen.get("hotkey", "ctrl+alt+space"), ""))
        self.mouse_trigger.configure(gen.get("mouse_button", "middle"))
        self._register_hotkey()
        self._update_notch()
        self._update_pill()
        if save:
            self._save_timer.start()

    def reload_config(self) -> None:
        """config.json was edited outside the app."""
        if str(CONFIG_PATH) not in self.watcher.files() and CONFIG_PATH.exists():
            self.watcher.addPath(str(CONFIG_PATH))
        # Half-way through an edit (or a slip in the JSON), keep running on the settings there are: reading it
        # now would set the broken file aside and start again from the defaults, saved keys and all.
        try:
            if not isinstance(json.loads(CONFIG_PATH.read_text(encoding="utf-8")), dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            pass                                 # deleted: start again from the defaults, as at first start
        except (OSError, ValueError) as e:
            log.warning("config.json can't be read (%s): keeping the current settings", e)
            self._flash_msg("config.json has a mistake in it; nothing changed", "error", AMBER, 4.0)
            return
        self.cfg = load_config()
        self.apply_config(save=False)
        if self.window.built:
            self.window.rotary.reload(self.window.rotary.list.currentRow())
            self.window.gestures.refresh()
            self.window.apps.sync_switches()
            self.window.notch.refresh()
            self.window.assist.refresh()
        self._flash_msg(f"Loaded {len(self.items)} items", "done", GREEN)
        log.info("config reloaded: %d items", len(self.items))

    def _save_cfg(self) -> None:
        self.watcher.removePath(str(CONFIG_PATH))
        try:
            save_config(self.cfg)
        except OSError:
            log.exception("couldn't save settings")
        QTimer.singleShot(600, lambda: self.watcher.addPath(str(CONFIG_PATH)))

    def refresh_apps(self, force: bool = False) -> None:
        """Detect installed apps and warm their games / projects / scenes (background-safe)."""
        try:
            integrations.detect(force=force)
            for a in integrations.installed():
                if a.spec.dynamic is not None:
                    integrations.refresh_dynamic(a.id, self.cfg, self.dynamic_ready.emit, force=force)
        except Exception:
            log.exception("app detection failed")

    # ------------------------------------------------------------ hotkeys
    def _register_hotkey(self) -> None:
        want = self.cfg.get("general", {}).get("hotkey", "ctrl+alt+space")
        if want != self._hotkey_now:
            self.hotkeys.unregister(HK_TOGGLE)
            self._hotkey_now = want
            if want in HOTKEYS:
                mods, vk = HOTKEYS[want]
                if not self.hotkeys.register(HK_TOGGLE, mods | MOD_NOREPEAT, vk):
                    self._flash_msg(f"{want.replace('+', ' + ').title()} is used by another app", "error", AMBER, 4.0)
        want = assist.settings(self.cfg)["hotkey"]
        if want != self._assist_hotkey_now:
            self.hotkeys.unregister(HK_ASSIST)
            self._assist_hotkey_now = want
            if want in ASSIST_HOTKEYS:
                mods, vk = ASSIST_HOTKEYS[want]
                if not self.hotkeys.register(HK_ASSIST, mods | MOD_NOREPEAT, vk):
                    self._flash_msg(f"{want.replace('+', ' + ').title()} is used by another app", "error", AMBER, 4.0)

    def _nav_keys(self, on: bool) -> None:
        for hid, (vk, _d) in {**NAV, **VOLUME_KEYS}.items():
            if on:
                self.hotkeys.register(hid, 0, vk)
            else:
                self.hotkeys.unregister(hid)

    def _end_keyboard(self) -> None:
        if self.keyboard_session:
            self.keyboard_session = False
            self.dial.painter_.keyboard = False
            self._nav_keys(False)
        self.mouse_trigger.set_active(False)
        self.notch.set_active(False)

    def _dial_area(self) -> tuple[tuple[float, float, float], tuple[tuple[float, float, float, float], ...]]:
        """Where the rotary is, in the mouse hook's coordinates (Windows: physical pixels): the ring as a circle,
        and the volume slider beside it."""
        d = self.dial
        radius = d.painter_.D / 2 + 6
        ox = oy = k = None
        if WIN:
            r = wintypes.RECT()
            try:
                if winapi.user32.GetWindowRect(wintypes.HWND(int(d.winId())), ctypes.byref(r)):
                    ox, oy, k = r.left, r.top, (r.right - r.left) / max(1, d.width())
            except Exception:          # never let this stop the rotary opening: Qt's geometry below will do
                log.warning("couldn't read the rotary's window position", exc_info=True)
        if k is None:
            g = d.geometry()
            k = d.devicePixelRatioF() if LINUX else 1.0
            ox, oy = g.x() * k, g.y() * k
        self._hook_map = (ox, oy, k)
        circle = (ox + d.width() / 2 * k, oy + d.height() / 2 * k, radius * k)
        if not self.keyboard_session:
            return circle, ()                  # the volume button is only there for the mouse and keyboard
        z = d.volume_zone()
        zones = [(ox + z.left() * k, oy + z.top() * k, ox + z.right() * k, oy + z.bottom() * k)]
        if self.tour is not None and self.tour.isVisible():
            zones.append(_window_zone(self.tour))     # the tour's buttons still take clicks
        return circle, tuple(zones)

    def _arm_mouse(self) -> None:
        """The mouse works on the rotary wherever it is, however it was opened: the hook catches the wheel and
        clicks over it before the app underneath does. Opened for the mouse / keyboard, the mouse outside it is
        caught too (a click there closes it)."""
        if self.engine.phase != Phase.OPEN or not self.dial.isVisible():
            return
        window = self.dial.interactive and not self.mouse_trigger.holds_back
        self.mouse_trigger.set_active(True, *self._dial_area(), manual=self.keyboard_session, window=window)

    def _on_mouse_pointer(self, kind: str, x: float, y: float) -> None:
        """A press / drag / release on the rotary, caught by the mouse hook (in its own coordinates)."""
        if self.engine.phase != Phase.OPEN:
            return
        ox, oy, k = self._hook_map
        pt = QPointF((x - ox) / max(k, 1e-6), (y - oy) / max(k, 1e-6))
        if kind == "press":
            self.dial.press_at(pt, src="hook")
        elif kind == "drag":
            self.dial.drag_to(pt)
        elif kind == "release":
            self.dial.release_at(pt)

    def open_manual(self) -> None:
        """Open the rotary for the mouse and keyboard (shortcut, notch, tray, window button)."""
        now = time.monotonic()
        if self.engine.phase == Phase.OPEN:
            return
        self.keyboard_session = True
        self._kb_fg = winapi.user32.GetForegroundWindow() if sys.platform == "win32" else 0
        ev = self.engine.force_open(now)
        if ev:
            self._nav_keys(True)
            self.notch.set_active(True)
        else:
            self.keyboard_session = False
        self._handle(ev)

    def _close_manual(self, reason: str) -> None:
        self._handle(self.engine.force_close(time.monotonic(), reason))

    def _on_hotkey(self, hid: int) -> None:
        now = time.monotonic()
        if hid == HK_ASSIST:
            self.start_assist()
            return
        if hid == HK_TOGGLE:
            if self.engine.phase == Phase.OPEN:
                self._close_manual("keyboard")
            else:
                self.open_manual()
            return
        if hid in VOLUME_KEYS and self.engine.phase == Phase.OPEN:
            if self.mode != "volume":
                self._enter_volume()
            else:
                self._handle(self.engine.key_step(VOLUME_KEYS[hid][1], now))
                self.dial.set_state(self.engine.snapshot())
            return
        if hid in NAV and self.engine.phase == Phase.OPEN:
            vk, delta = NAV[hid]
            if vk == 0x0D:
                self._handle(self.engine.key_execute(now))
            elif vk == 0x1B:
                if self.levels:
                    self._go_back()
                else:
                    self._close_manual("keyboard")
            else:
                if self.mode == "volume" and vk in (0x26, 0x28):
                    delta = -delta                       # ↑ louder, ↓ quieter
                self._handle(self.engine.key_step(delta, now))
            self.dial.set_state(self.engine.snapshot())

    # ------------------------------------------------------------ notch & mouse
    def _on_notch_click(self) -> None:
        if self.engine.phase == Phase.OPEN:
            self._close_manual("notch")
        else:
            self.open_manual()

    def _on_notch_scroll(self, steps: int) -> None:
        if self.engine.phase != Phase.OPEN:
            self.open_manual()
        elif self.keyboard_session:
            self._handle(self.engine.key_step(-steps if self.mode == "volume" else steps, time.monotonic()))
            self.dial.set_state(self.engine.snapshot())

    def _on_mouse_trigger(self, kind: str, n: int) -> None:
        """The mouse button that opens the rotary, and the wheel / clicks while it's open (from mouse.py)."""
        if kind in ("toggle", "open"):
            if self.engine.phase == Phase.OPEN:
                if kind == "toggle":
                    self._close_manual("mouse")
            elif self.engine.phase == Phase.IDLE:
                self.open_manual()
        elif kind == "close":
            if self.engine.phase == Phase.OPEN:
                self._close_manual("mouse")
        elif kind in ("turn", "back"):
            self._on_dial_mouse(kind, n)

    def _on_dial_mouse(self, kind: str, n: int) -> None:
        if self.engine.phase != Phase.OPEN:
            return
        now = time.monotonic()
        if kind == "turn":
            # wheel up = louder on the volume ring (elsewhere wheel down = the next item)
            self._handle(self.engine.key_step(-n if self.mode == "volume" else n, now))
        elif kind == "select":
            count = len(self.level_items)
            d = (n - self.engine.selected) % count
            self._handle(self.engine.key_step(d - count if d > count / 2 else d, now))
        elif kind == "open":
            self._handle(self.engine.key_execute(now))
        elif kind == "volume":
            if self.mode == "volume":
                self._leave_mode()
            else:
                self._enter_volume()
        elif kind == "back":
            if self.levels:
                self._go_back()
            else:
                self._close_manual("mouse")
        if self.engine.phase == Phase.OPEN:
            self.dial.set_state(self.engine.snapshot())

    def _alert(self, text: str, kind: str, accent: str, seconds: float = 3.5) -> None:
        """A moment worth a glance. Over a borderless full-screen game the notch pops up briefly to show it
        (never over an exclusive full-screen one — that could knock the game out of full screen)."""
        island = self.cfg.get("island", {})
        if (self._fullscreen and self.notch.isHidden() and self._notch_wanted()
                and island.get("alerts_over_games", True) and self._fs_state == 2):
            self._alert_until = time.monotonic() + seconds
            self._update_notch()
            QTimer.singleShot(int(seconds * 1000) + 300, self._update_notch)
        self._flash_msg(text, kind, accent, seconds)

    def _on_live_event(self, name: str, data: dict) -> None:
        island = self.cfg.get("island", {})
        if name == "clip" and island.get("clips", True):
            what = "Screenshot saved" if data.get("kind") == "screenshot" else "Clip saved"
            self.sounds.play("confirm")
            self._alert(f"{what} · {data.get('who', '')}", "clip", GREEN)
        elif name == "power" and island.get("battery", True):
            pct = data.get("percent", 0)
            self._alert(f"Charging · {pct}%" if data.get("plugged") else f"On battery · {pct}%", "power",
                        GREEN if data.get("plugged") else GRAY, 2.5)
        elif name == "battery_low" and island.get("battery", True):
            pct = data.get("percent", 0)
            self._alert(f"Battery low · {pct}%", "power", RED if pct <= 10 else AMBER, 5.0)

    # Each app's own shortcuts. Discord has no built-in one for leaving a call: RotaryUI presses its Disconnect
    # button (UI Automation, see _leave_discord), and only if it can't find one presses Ctrl+Shift+H — give
    # Discord's "Disconnect from voice channel" keybind that key (Settings → Keybinds), or set another under
    # "island" → "discord_leave_keys" in config.json.
    CALL_KEYS = {("Discord", "mute"): ("discord", "ctrl+shift+m", ""),
                 ("Discord", "deafen"): ("discord", "ctrl+shift+d", ""),
                 ("Discord", "accept"): ("discord", "ctrl+enter", ""),
                 ("Discord", "decline"): ("discord", "esc", ""),
                 ("Discord", "leave"): ("discord", "ctrl+shift+h", ""),
                 ("Teams", "mute"): ("teams", "ctrl+shift+m", ""),
                 ("Teams", "accept"): ("teams", "ctrl+shift+s", ""),
                 ("Teams", "decline"): ("teams", "ctrl+shift+d", ""),
                 ("Teams", "leave"): ("teams", "ctrl+shift+h", ""),
                 ("Zoom", "mute"): ("zoom", "alt+a", "Meeting"),
                 ("Zoom", "leave"): ("zoom", "alt+q", "Meeting"),
                 ("Slack", "leave"): ("slack", "ctrl+shift+h", "")}
    CALL_APP_IDS = {"Discord": "discord", "Teams": "teams", "Zoom": "zoom", "Slack": "slack",
                    "WhatsApp": "whatsapp", "Telegram": "telegram", "Signal": "signal"}

    def _on_call_command(self, app_name: str, what: str) -> None:
        """The notch's call buttons — mute, deafen, leave, and accept / decline a call ringing in: the app's own
        shortcut, sent to its window. An app without one for answering is brought to the front instead (decline
        there just puts the notch's card away)."""
        app_id, keys, title = self.CALL_KEYS.get((app_name, what), ("", "", ""))
        if app_id and (app_name, what) == ("Discord", "leave"):
            keys = str(self.cfg.get("island", {}).get("discord_leave_keys") or keys)
        if what in ("accept", "decline", "leave"):
            log.info("call: %s in %s", what, app_name)
        if not app_id:
            a = integrations.get(self.CALL_APP_IDS.get(app_name, "")) if what == "accept" else None
            if a is not None:
                action = a.launch()          # no shortcut to answer: bring the app up to answer it there
                threading.Thread(target=lambda: self._run_quiet(action), daemon=True).start()
            return
        a = integrations.get(app_id)
        if a is None:
            return
        action = a.keys(keys, prefer_title=title)
        label = {"mute": "Mute", "deafen": "Deafen", "accept": "Answered", "decline": "Declined",
                 "leave": "Left the call"}.get(what, what)

        def work():
            try:
                if (app_name, what) == ("Discord", "leave") and self._leave_discord():
                    self.action_done.emit(f"{app_name} · {label}", True, "")
                    return
                execute(action, self.cfg)
                self.action_done.emit(f"{app_name} · {label}", True, "")
            except Exception as e:
                self.action_done.emit(f"{app_name} · {label}", False, str(e))
        threading.Thread(target=work, daemon=True).start()

    DISCORD_LEAVE_BUTTONS = ("Disconnect", "Leave Call", "Leave call", "Hang Up", "Hang up")

    def _leave_discord(self) -> bool:
        """Press the Disconnect button in Discord's window, the way a screen reader would: no keybind needed,
        and Discord stays where it is. False if it isn't there (Discord in another language, say)."""
        if not WIN:
            return False
        from . import uia
        from .live import DISCORD_EXES
        names = tuple(self.cfg.get("island", {}).get("discord_leave_buttons") or self.DISCORD_LEAVE_BUTTONS)
        for exe in DISCORD_EXES:
            for hwnd in winapi.windows_of(exe, include_hidden=True)[:3]:
                pressed = uia.press_button(hwnd, names)
                if pressed:
                    log.info("call: pressed Discord's %r button", pressed)
                    return True
        log.info("call: no Disconnect button in Discord's window; pressing its leave keybind instead")
        return False

    def _run_quiet(self, action: dict) -> None:
        try:
            execute(action, self.cfg)
        except Exception:
            log.debug("couldn't run %s", action, exc_info=True)

    def _on_live_state(self, state) -> None:
        """A call ringing in shows the notch (even with the camera in use) until it's answered or stops."""
        ring = state.incoming if self.cfg.get("island", {}).get("calls", True) else None
        if ring != self._ringing:
            self._ringing = ring
            self._update_notch()

    def _on_notch_moved(self, pos) -> None:
        island = self.cfg.setdefault("island", {})
        if pos is None:
            if island.pop("position", None) is None:
                return
        elif island.get("position") == pos:
            return
        else:
            island["position"] = dict(pos)
        self._save_timer.start()

    def _on_timer_done(self) -> None:
        self.sounds.play("confirm")
        QTimer.singleShot(450, lambda: self.sounds.play("confirm"))
        self._say("Timer done")
        self._alert("Timer done", "timer", AMBER, 6.0)

    def _notch_wanted(self) -> bool:
        mode = self.cfg.get("general", {}).get("notch", "auto")
        if mode == "off":
            return False
        if mode == "on" or self._assist_showing() or self._ringing is not None:
            return True
        # auto: when there's no camera to use (switched off, or none found for a few seconds)
        return self._no_cam_since is not None and time.monotonic() - self._no_cam_since > 4.0

    def _update_notch(self) -> None:
        gen = self.cfg.get("general", {})
        wanted = self._notch_wanted()
        # Alerts and screen-assist answers may show over a borderless full-screen game, never an exclusive one.
        alert = time.monotonic() < self._alert_until or (self._assist_showing() and self._fs_state != 3)
        if self._ringing is not None and self._fs_state != 3 and self.cfg.get("island", {}).get("alerts_over_games",
                                                                                                 True):
            alert = True
        show = wanted and (alert or not (self._fullscreen and gen.get("notch_hide_fullscreen", True)))
        # The live activities keep watching while a game hides the notch, so a saved clip can still pop up. With
        # the notch away (the camera's in use), they still listen for a call ringing in.
        listen = wanted or (gen.get("notch", "auto") != "off" and self.cfg.get("island", {}).get("calls", True))
        self.live.calls_only = not wanted
        if listen and not self.live.isRunning():
            self.live.start()
        elif not listen and self.live.isRunning():
            self.live.stop()
        if show != self.notch.isVisible():
            why = ("on" if show else "off") + f" (setting {gen.get('notch', 'auto')!r}, camera "
            why += f"{'off' if not self.camera_on else self.cam_status.state}, full screen {self._fullscreen})"
            log.info("notch %s", why)
        if show and not self.notch.isVisible():
            self.notch.place()
            self.notch.show()
            self.pill.set_status("", hidden=True)
            self._update_pill()
        elif not show and self.notch.isVisible():
            self.notch.hide()
            self._update_pill()

    # ------------------------------------------------------------ screen assist
    def _assist_showing(self) -> bool:
        return self._assist_busy or self.notch.answer is not None or time.monotonic() < self._assist_until

    def _assist_message(self, text: str, kind: str, accent: str, seconds: float) -> None:
        self._assist_until = time.monotonic() + seconds
        self._update_notch()
        self._flash_msg(text, kind, accent, seconds)

    def start_assist(self) -> None:
        """Screen assist (hotkey, rotary item, notch menu): grab the screen, ask the AI, answer on the notch."""
        if self._assist_busy:
            return
        missing = assist.ready(self.cfg)
        if missing:
            self._assist_message(missing, "error", AMBER, 6.0)
            self.show_window("assist")
            return
        self._assist_busy = True
        self.notch.clear_answer()
        if self.engine.phase == Phase.OPEN:
            self._close_manual("assist")
        QTimer.singleShot(150, self._assist_capture)

    def _assist_capture(self, tries: int = 0) -> None:
        if self.dial.isVisible() and tries < 20:          # wait for the rotary to get out of the picture
            QTimer.singleShot(120, lambda: self._assist_capture(tries + 1))
            return
        try:
            shot = assist.capture()
        except assist.AssistError as e:
            self._assist_busy = False
            self._assist_message(str(e), "error", RED, 5.0)
            return
        self._update_notch()
        self._flash_msg("Looking at your screen…", "pulse", BLUE, assist.TIMEOUT + 5)
        cfg = self.cfg

        def work():
            try:
                app_name, title = modes.foreground()       # context clues for the AI: "steam", "Elden Ring on Steam"
                if title.lower().startswith(("rotaryui", "gesture rotary")):
                    app_name = title = ""                   # our own settings window says nothing about the screen
                self.assist_done.emit(assist.solve(shot, cfg, app=app_name, title=title), "")
            except assist.AssistError as e:
                self.assist_done.emit(None, str(e))
            except Exception:
                log.exception("screen assist failed")
                self.assist_done.emit(None, "Screen assist failed (details in the log)")
        threading.Thread(target=work, daemon=True, name="assist").start()

    def _on_assist_done(self, ans, err: str) -> None:
        self._assist_busy = False
        self._flash = None
        if err:
            self._assist_message(err[:110], "error", RED, 6.0)
            return
        if not (ans.choice or ans.answer):              # the AI always tries, so this is a blank reply
            self._assist_message("The AI couldn't make out anything on screen", "dot", GRAY, 3.5)
            return
        seconds = float(assist.settings(self.cfg).get("seconds", 20))
        self._assist_until = time.monotonic() + 1.0
        self._update_notch()
        self.sounds.play("confirm")
        self._say(ans.spoken())
        if self.notch.isVisible():
            self.notch.show_answer(ans.choice, ans.answer, ans.detail, seconds)
            self._update_pill()
        else:                                            # the notch is switched off: the status pill says it
            self._flash_msg(ans.headline(), "done", GREEN, seconds)

    # ------------------------------------------------------------ updates
    def _set_update_note(self, text: str) -> None:
        self.update_note = text
        self.update_state.emit(text)

    def check_for_updates(self, manual: bool = False) -> None:
        """Every few hours (General → Updates → Update automatically), or Check now: if GitHub has a newer
        release, download it in the background and install it."""
        if self._update_busy or self.quitting:
            return
        if not manual and not self.cfg.get("general", {}).get("auto_update", True):
            return
        if self._staged is not None:                     # downloaded already, waiting for a quiet moment
            if manual:
                self._update_manual = True
                self._apply_update()
            return
        self._update_busy = True
        self._update_manual = manual
        self._set_update_note("Checking for updates…")
        shown = [-1]

        def progress(frac: float) -> None:
            pct = int(frac * 100)
            if pct != shown[0]:
                shown[0] = pct
                self.update_state.emit(f"Downloading the update… {pct}%")

        def work():
            try:
                self.update_done.emit(*update.check_and_prepare(progress), "")
            except update.UpdateError as e:
                self.update_done.emit(None, None, str(e))
            except Exception:
                log.exception("update failed")
                self.update_done.emit(None, None, "The update failed (details in the log)")
        threading.Thread(target=work, daemon=True, name="update").start()

    def _on_update_done(self, release, staged, err: str) -> None:
        self._update_busy = False
        if err:
            log.info("update: %s", err)
            self._set_update_note(err)
            if self._update_manual:
                self._flash_msg(err[:110], "error", AMBER, 5.0)
            return
        if release is None:
            self._set_update_note(f"RotaryUI {__version__} is the latest version")
            return
        if staged is None:                               # running from source, or a folder it can't change
            self._set_update_note(f"Version {release.version} is out. {update.unsupported()}.")
            return
        self._staged = staged
        self._set_update_note(f"Version {staged.version} is ready; it installs when the rotary isn't in use")
        self._apply_update()

    def _apply_update(self) -> None:
        """Install the downloaded update and restart, once nobody's using the rotary (or right away for
        Check now). The app quits; a small script swaps the new version in and starts it again."""
        staged = self._staged
        if staged is None or self.quitting:
            return
        manual = self._update_manual
        if not manual and not self.cfg.get("general", {}).get("auto_update", True):
            return
        busy = (self.engine.phase == Phase.OPEN or self.keyboard_session or self._assist_busy
                or self._calib is not None or (not manual and self.window.isActiveWindow()))
        if busy:
            self._apply_timer.start()
            return
        showing = self.window.isVisible() and not self.window.isMinimized()
        try:
            update.apply(staged, ["--show"] if showing else ["--minimized"])
        except Exception as e:
            log.warning("couldn't start the update", exc_info=True)
            self._staged = None
            self._set_update_note(f"Couldn't install version {staged.version} ({e})")
            return
        self._set_update_note(f"Updating to {staged.version}…")
        self._flash_msg(f"Updating to RotaryUI {staged.version}…", "pulse", BLUE, 5.0)
        QTimer.singleShot(900, self.quit)

    # ------------------------------------------------------------ quick nav levels
    def _enter_quicknav(self, item) -> None:
        app_id = str(item.action.get("app", ""))
        a = integrations.get(app_id)
        children = [i for i in (item_from(d) for d in integrations.quicknav(app_id)) if i is not None]
        integrations.refresh_dynamic(app_id, self.cfg, self.dynamic_ready.emit)
        if a is None or not a.installed or not children:
            self._flash_msg(f"{item.name} isn't installed", "error", RED, 3.0)
            self._handle(self.engine.force_close(time.monotonic(), "missing"))
            return
        self.levels.append((self.level_items, self.engine.selected, self.crumb))
        self._qn_app = app_id
        self.crumb = item.name
        self.level_items = [back_item("")] + children
        self.engine.enter_level(len(self.level_items), time.monotonic(), selected=1)
        self.dial.set_level(self.level_items, item.name, self.engine.snapshot())
        self.sounds.play("open")
        self._say(f"{item.name}. {self.level_items[1].name}")

    def _go_back(self) -> None:
        if not self.levels:
            return
        items, sel, crumb = self.levels.pop()
        self.level_items, self.crumb = items, crumb
        self._qn_app = ""
        self.engine.enter_level(len(items), time.monotonic(), selected=sel)
        self.dial.set_level(items, crumb, self.engine.snapshot())
        self.sounds.play("tick")
        self._say(f"Back. {items[sel % len(items)].name}")

    def _reset_levels(self) -> None:
        if self.levels:
            root_sel = self.levels[0][1]
            self.levels.clear()
            self.level_items = self.items
            self.engine.set_item_count(len(self.items))
            self.engine.sel_raw = root_sel % max(1, len(self.items))
            self.engine.position = float(self.engine.sel_raw)
        self.crumb = ""
        self._qn_app = ""

    def _on_dynamic(self, app_id: str) -> None:
        """Games / scenes arrived while their Quick nav ring is open and untouched: show them."""
        if not (self.levels and app_id == self._qn_app and self.engine.phase == Phase.OPEN):
            return
        if self.engine.snapshot().interacted:
            return
        children = [i for i in (item_from(d) for d in integrations.quicknav(app_id)) if i is not None]
        new = [self.level_items[0]] + children
        if [i.name for i in new] == [i.name for i in self.level_items]:
            return
        self.level_items = new
        self.engine.enter_level(len(new), time.monotonic(), selected=min(1, len(new) - 1))
        self.dial.set_level(new, self.crumb, self.engine.snapshot())

    # ------------------------------------------------------------ slides / video rings
    def _modes_cfg(self) -> dict:
        return {"slides": True, "video": True, **(self.cfg.get("modes") or {})}

    def _detect_context(self) -> modes.Context:
        mc = self._modes_cfg()
        if not (mc["slides"] or mc["video"]):
            return modes.Context()
        exe, title = modes.foreground()
        ctx = modes.classify(exe, title, slides=bool(mc["slides"]), video=bool(mc["video"]))
        if ctx.kind:
            log.info("%s in front (%s): %s ring", ctx.app, ctx.exe, ctx.kind)
        return ctx

    def _watch_context(self) -> bool:
        """Open as a slides / video ring if that's what's in front. True if it stays the item ring (for now).
        On Windows this is instant; elsewhere asking can take a moment, so the ring switches when it knows."""
        if WIN:
            ctx = self._detect_context()
            if ctx.kind:
                self._enter_mode(ctx)
                return False
            return True
        token = self._mode_token
        self._mode_exec.submit(lambda: self.mode_data.emit(token, "context", self._detect_context()))
        return True

    def _mode_item(self, name: str, tint: str) -> Item:
        return Item(name, "", "", tint, {"type": "mode"})

    def _enter_mode(self, ctx: modes.Context) -> None:
        self._main_sel = self.engine.selected
        self.mode_ctx = ctx
        self.mode = ctx.kind
        self._mode_token += 1
        token = self._mode_token
        if ctx.kind == "slides":
            self._slides = None
            self._setup_slides()
            self._say(f"{ctx.app} slides")
            self._mode_exec.submit(lambda: self.mode_data.emit(token, "slides", modes.slide_info(ctx)))
        else:
            self._np = None
            self._setup_video()
            self._say(f"{ctx.app} video")
            self._read_media()

    def _enter_volume(self) -> None:
        """The volume button (or + / −): the ring becomes the volume, a full turn from silent to full."""
        if self.mode == "volume":
            return
        if self.mode != "main":
            self._leave_mode(quiet=True)
        self._main_sel = self.engine.selected
        self.mode = "volume"
        self._mode_token += 1
        token = self._mode_token
        level = self.dial.painter_.volume_level
        self._setup_volume(level if level >= 0 else 0.5)
        self.sounds.play("open")
        self._say("Volume")
        self.volume.read(lambda lv: self.mode_data.emit(token, "volume", lv))     # where it really is

    def _setup_volume(self, level: float) -> None:
        s = self.settings
        self._apply_mode_settings(detent_force=s.detent_force + 0.1, scroll_speed=s.scroll_speed * 1.2)
        n = VOLUME_TICKS
        now = time.monotonic()
        self.engine.enter_level(n, now, selected=int(round(level * n)), limit=(0, n), fixed_step=14.0)
        self._mode_n = n
        self._mode_sel = self.engine.sel_raw
        self.dial.set_mode("volume", [self._mode_item("Volume", VOLUME_TINT)] * n, self._volume_info(),
                           self.engine.snapshot())
        self.volume_ring.hide_now()
        self.dial.place(self._volume_corner())       # the volume ring lives in its corner
        self._arm_mouse()

    def _volume_corner(self) -> str:
        corner = (self.cfg.get("modes") or {}).get("volume_corner", "bottom-right")
        return corner if corner in CORNERS else "bottom-right"

    def _volume_info(self) -> dict:
        return {"top": "Volume"}

    def _apply_mode_settings(self, **changes) -> None:
        self.engine.s = replace(self.settings, dwell_seconds=0.0, **changes)

    def _setup_slides(self) -> None:
        """A cut between every two slides (or plain steps while the count is unknown), with a firm click per
        slide so a small wobble never changes slide."""
        s = self.settings
        self._apply_mode_settings(detent_force=s.detent_force + 0.32, detent_hysteresis=s.detent_hysteresis + 0.22,
                                  scroll_speed=s.scroll_speed * 0.55)
        known = self._slides is not None
        n = self._slides[1] if known else 12
        sel = self._slides[0] - 1 if known else 0
        self._mode_n = n
        self.engine.enter_level(n, time.monotonic(), selected=sel, limit=(0, n - 1) if known else None,
                                fixed_step=26.0)
        self._mode_sel = self.engine.sel_raw
        self.dial.set_mode("slides", [self._mode_item(self.mode_ctx.app, SLIDES_TINT)] * n, self._slides_info(),
                           self.engine.snapshot())

    def _slides_info(self) -> dict:
        app = self.mode_ctx.app
        if self._slides is not None:
            return {"known": True, "title": f"Slide {self.engine.sel_raw + 1}", "sub": f"of {self._slides[1]} · {app}",
                    "top": "Slides"}
        moved = self.engine.sel_raw
        return {"known": False, "title": f"{moved:+d}" if moved else "Slides",
                "sub": f"{app} · next / previous", "top": "Slides"}

    def _setup_video(self) -> None:
        """A full turn is the whole video. Without its length (no media controls for it), each click jumps
        the player's own seek step."""
        s = self.settings
        np_ = self._np
        dur = np_.duration if np_ is not None else 0.0
        now = time.monotonic()
        if dur > 0:
            self._apply_mode_settings(detent_force=0.0, detent_hysteresis=0.02, scroll_speed=s.scroll_speed * 2.5)
            n = VIDEO_TICKS
            pos = np_.position_now() / dur * n
            self.engine.enter_level(n, now, selected=int(round(pos)), limit=(0, n))
            self.engine.set_position(pos, now)
        else:
            self._apply_mode_settings(detent_force=0.1, scroll_speed=s.scroll_speed * 0.8)
            n = 24
            self.engine.enter_level(n, now, selected=0, fixed_step=20.0)
        self._mode_n = n
        self._mode_sel = self.engine.sel_raw
        self.dial.set_mode("video", [self._mode_item(self.mode_ctx.app, VIDEO_TINT)] * n, self._video_info(),
                           self.engine.snapshot())

    def _video_info(self) -> dict:
        app = self.mode_ctx.app
        np_ = self._np
        if np_ is not None and np_.duration > 0:
            return {"duration": np_.duration, "sub": f"of {modes.fmt_time(np_.duration)}",
                    "top": np_.title or app}
        _f, _b, secs = modes.seek_keys(app)
        moved = self.engine.sel_raw * secs
        title = ("+" if moved > 0 else "−") + modes.fmt_time(abs(moved)) if moved else "Video"
        return {"duration": 0, "title": title, "sub": f"{app} · {secs:g} s per click", "top": app}

    def _read_media(self) -> None:
        token = self._mode_token
        self._np_read = time.monotonic()
        fut = self.media.read()
        fut.add_done_callback(lambda f: self.mode_data.emit(token, "video", f.result()))

    def _on_mode_data(self, token: int, what: str, data) -> None:
        if token != self._mode_token or self.engine.phase != Phase.OPEN:
            return
        if what == "volume":
            if self.mode == "volume" and data >= 0:
                self.dial.painter_.volume_level = data
                if self.engine.sel_raw == self._mode_sel:    # not turned yet: start from the real level
                    self.engine.set_position(round(data * self._mode_n), time.monotonic())
                    self._mode_sel = self.engine.sel_raw
                    self.dial.set_state(self.engine.snapshot())
        elif what == "context":
            if data.kind and self.mode == "main" and not self.levels and not self.engine.snapshot().interacted:
                self._enter_mode(data)
        elif what == "slides" and self.mode == "slides" and data:
            self._slides = data
            self._setup_slides()
        elif what == "video" and self.mode == "video":
            ok = data is not None and data.duration > 0 and modes.media_matches(self.mode_ctx, data.app,
                                                                                 data.app_name)
            had = self._np is not None
            if ok:
                self._np = data
                if not had:
                    self._setup_video()              # now we know its length: the ring becomes the timeline
                self._follow_video()
            elif not had and self.mode_ctx.browser and self.media.available:
                log.info("no video playing in %s: the item ring", self.mode_ctx.app)
                self._leave_mode(quiet=True)          # a site's home page, not a video
        elif what == "seeked" and self.mode == "video":
            sec, ok = data
            if not ok:                                # the player won't take a position: its seek keys then
                np_ = self._np
                cur = np_.position_now() if np_ is not None else 0.0
                _f, _b, step = modes.seek_keys(self.mode_ctx.app)
                self._press_mode_keys(lambda: modes.press_seek(self.mode_ctx.app, int(round((sec - cur) / step))))
            if self._np is not None:
                self._np.position, self._np.at = sec, time.monotonic()

    def _follow_video(self) -> None:
        """The dial turns with the video as it plays, and holds still while it's being turned."""
        if self.mode != "video" or self._np is None or self._np.duration <= 0:
            return
        snap = self.engine.snapshot()
        busy = (time.monotonic() - self._mode_moved < 1.5 or self._seek_to is not None or snap.gripping
                or snap.scrolling or (self.dial._drag or {}).get("dragging"))
        if busy:
            return
        self.engine.set_position(self._np.position_now() / self._np.duration * self._mode_n, time.monotonic())
        self._mode_sel = self.engine.sel_raw
        self.dial.set_state(self.engine.snapshot())

    def _mode_select(self) -> None:
        delta = self.engine.sel_raw - self._mode_sel
        self._mode_sel = self.engine.sel_raw
        self._mode_moved = time.monotonic()
        if not delta:
            return
        if self.mode == "volume":
            self.volume.set(self.engine.sel_raw / self._mode_n)
        elif self.mode == "slides":
            self._mode_exec.submit(modes.step_slides, delta)
            self.dial.painter_.info = self._slides_info()
        elif self._np is not None and self._np.duration > 0:
            self._seek_to = self.engine.sel_raw / self._mode_n * self._np.duration
            self._seek_timer.start()
        else:
            app = self.mode_ctx.app
            self._press_mode_keys(lambda: modes.press_seek(app, delta))
            self.dial.painter_.info = self._video_info()

    def _press_mode_keys(self, fn) -> None:
        """Arrow keys for a player: our own ← → shortcuts step aside meanwhile, or they'd catch them."""
        nav = self.keyboard_session
        if nav:
            self._nav_keys(False)
        try:
            fn()
        except Exception as e:
            log.warning("couldn't press the player's keys: %s", e)
        finally:
            if nav and self.keyboard_session:
                self._nav_keys(True)

    def _flush_seek(self) -> None:
        sec, self._seek_to = self._seek_to, None
        self._seek_timer.stop()
        if sec is None or self.mode != "video":
            return
        token = self._mode_token
        self._mode_moved = time.monotonic()
        fut = self.media.seek(sec)
        fut.add_done_callback(lambda f: self.mode_data.emit(token, "seeked", (sec, bool(f.result()))))

    def _leave_mode(self, quiet: bool = False) -> None:
        """From a slides / video ring to the usual ring of items."""
        if self.mode == "main":
            return
        self._flush_seek()
        was = self.mode
        self.mode = "main"
        self._mode_token += 1
        self.engine.s = self.settings
        self.engine.enter_level(len(self.level_items), time.monotonic(), selected=self._main_sel)
        self.dial.set_mode("main", self.level_items, {}, self.engine.snapshot())
        self.dial.painter_.crumb = self.crumb
        if was == "volume":
            self.dial.place("")                      # back to the middle with the items
            self._arm_mouse()
        if not quiet:
            self.sounds.play("open")
            self._say("Rotary. " + self.level_items[self.engine.selected % len(self.level_items)].name)

    def _reset_mode(self) -> None:
        if self.mode == "main" and self.engine.limit is None and self.engine.fixed_step is None:
            return
        self._seek_timer.stop()
        self._seek_to = None
        self.mode = "main"
        self._mode_token += 1
        self.engine.s = self.settings
        self.engine.limit = None
        self.engine.fixed_step = None
        self.engine.set_item_count(len(self.level_items))
        self.engine.sel_raw = self._main_sel % max(1, len(self.level_items))
        self.engine.position = float(self.engine.sel_raw)

    def _on_volume_level(self, level: float) -> None:
        if self.mode == "volume" and self.dial.isVisible():
            # the volume ring set it (its click already ticked), or the hand's slider: the ring follows
            self.dial.painter_.volume_level = level
            turning = time.monotonic() - self._mode_moved < 1.0      # a late answer to our own turn
            if level >= 0 and not turning and abs(level * self._mode_n - self.engine.position) > 0.5:
                self.engine.set_position(round(level * self._mode_n), time.monotonic())
                self._mode_sel = self.engine.sel_raw
                self.dial.set_state(self.engine.snapshot())
            return
        self.sounds.play("tick")
        if level >= 0:
            self.volume_ring.show_level(level, self._volume_corner())    # its own ring, in its corner
        else:
            self._flash_msg("Volume", "dot", BLUE, 1.6)

    def _on_volume_read(self, level: float) -> None:
        if self.dial.isVisible() and level >= 0:
            self.dial.painter_.volume_level = level

    # ------------------------------------------------------------ tracking
    def on_result(self, res: TrackResult) -> None:
        self._last_result_t = time.monotonic()
        flip = self.settings.flip_handedness
        feats = [extract_features(h, flip) for h in res.hands]
        aspect = res.width / max(res.height, 1)
        idx, same = pick_hand(feats, res.faces, self._prev_center, aspect)
        f = None
        if idx >= 0:
            if not same:
                self.hands.reset()          # a different hand: forget its left/right vote
            h = res.hands[idx]
            label, conf = self.hands.update(h.handedness, h.score, feats[idx].grip, res.t)
            f = extract_features(h, flip, handedness=label, handed_conf=conf)
            f.at_face, f.face_depth = self._face_touch.update(f, res.faces, aspect, same, res.t)
            feats[idx] = f
            self._prev_center = f.center
            self._last_hand_t = time.monotonic()
            if self._calib is not None:
                self._calib.append(f.raw_facing)
        else:
            self._prev_center = None
        for i, other in enumerate(feats):
            if i != idx:
                other.at_face = hand_at_face(other, res.faces, aspect)
        self._update_resize(feats, res.t)
        # Both hands pinching to resize a window: that's not a gesture for the rotary.
        self._handle(self.engine.update(None if self.resizer.busy else f, res.t))
        snap = self.engine.snapshot()
        if self.tour is not None:
            self.tour.feed_snapshot(snap)
        if snap.sliding and not self._was_sliding:
            self.volume.forget()                 # read the level afresh: something else may have changed it
        self._was_sliding = snap.sliding
        if f is not None and log.isEnabledFor(logging.DEBUG) and res.t - self._last_dbg > 0.5:
            self._last_dbg = res.t
            log.debug("hand %s(%.2f) facing=%+.2f grip=%.2f curls=%s pinch=%.2f roll=%+.0f fs=%.2f face=%s(%.2f) "
                      "frame=%s presented=%s point=%s phase=%s arming=%.2f pos=%.2f",
                      f.handedness, f.handed_conf, f.facing, f.grip, "/".join(f"{c:.1f}" for c in f.curls), f.pinch,
                      math.degrees(f.angle), f.index_fs, f.at_face, f.face_depth, f.in_frame, snap.presented, snap.pointing,
                      snap.phase.value, snap.arming, snap.position)
        if self.dial.isVisible():
            self.dial.set_state(snap)
        want = self.window.wants_frames()
        self.tracker.want_preview = want
        if want:
            self.window.camera.set_frame(res.preview, res.hands, feats, idx, snap, res.faces)
        self._update_pill(snap)

    def _update_resize(self, feats: list[HandFeatures], now: float) -> None:
        """Two-hand pinch-and-stretch resizes the window in front, while the rotary is put away."""
        if (self.settings.window_resize and self.engine.phase == Phase.IDLE and not self.keyboard_session
                and not self.engine.paused):
            events = self.resizer.update(feats, now)
        else:
            events = self.resizer.cancel()
        for name, payload in events:
            log.debug("event %s %s", name, payload)
            if name == "resize_start":
                self.sizer.begin()
            elif name == "resize":
                self.sizer.scale(*payload)
            elif name == "resize_end":
                self.sizer.end()

    def _on_resize_window(self, win) -> None:
        if win is None:
            self._flash_msg("Bring a windowed app to the front to resize it", "error", AMBER, 2.5)
        else:
            self.sounds.play("tick")
            self._flash_msg("Resizing " + (win.title[:40] or "window"), "dot", BLUE, 1.5)

    def _on_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_result_t > 0.25:
            self._update_resize([], now)
            self._handle(self.engine.update(None, now))
            snap = self.engine.snapshot()
            if self.dial.isVisible():
                self.dial.set_state(snap)
            self._update_pill(snap)
        if self._calib is not None and now >= self._calib_until:
            self._finish_calibration()
        if self.mode == "video" and self.engine.phase == Phase.OPEN and self._np is not None:
            if now - self._np_read > 1.0:
                self._read_media()               # where the video really is (it may have been paused)
            self._follow_video()
        self.tracker.want_preview = self.window.wants_frames()
        # A mouse / keyboard session ends when you click into another window.
        if self.keyboard_session and self.engine.phase == Phase.OPEN and sys.platform == "win32":
            fg = winapi.user32.GetForegroundWindow()
            if fg and fg != self._kb_fg and fg != int(self.window.winId()):
                self._close_manual("focus")
        if now - self._fs_checked > 1.0:
            self._fs_checked = now
            self._fs_state = fullscreen_state()
            fs = self._fs_state in (2, 3, 4)
            if fs != self._fullscreen:
                self._fullscreen = fs
                log.info("full-screen app %s", "started" if fs else "ended")
            self._update_notch()

    def on_status(self, st: CameraStatus) -> None:
        changed = (st.state != self.cam_status.state
                   or [c.key for c in st.cameras] != [c.key for c in self.cam_status.cameras]
                   or (st.camera and self.cam_status.camera and st.camera.key != self.cam_status.camera.key))
        if not self.camera_on:
            return                       # a last message from a camera that's being switched off
        self.cam_status = st
        if st.state == "running":
            self._no_cam_since = None
        elif st.state in ("none", "error") and self._no_cam_since is None:
            self._no_cam_since = time.monotonic()
        if changed:
            self._fill_camera_menu()
            if st.state == "running" and st.camera:
                cam_cfg = self.cfg.setdefault("camera", {})
                if cam_cfg.get("preferred") != st.camera.name:
                    cam_cfg["preferred"] = st.camera.name
                    self._save_timer.start()
        self._sync_camera_ui(changed)
        self._update_notch()
        self._update_pill()

    def _sync_camera_ui(self, cameras_changed: bool = True) -> None:
        st = self.cam_status
        cam = self.window.page("camera")
        if cam is not None and cameras_changed:
            cam.set_cameras(st.cameras, st.camera.key if st.camera else None)
        if st.state == "running" and st.camera:
            if cam is not None:
                cam.stats.setText(f"{st.camera.width}×{st.camera.height} · {st.camera.backend_name}\n"
                                  f"capture {st.capture_fps:.0f} fps · tracking {st.infer_fps:.0f} fps")
            self.window.set_status(f"● {st.camera.name}")
        else:
            if cam is not None:
                cam.stats.setText(st.message or st.state)
                cam.view.message = st.message or "Waiting for camera…"
                cam.view.update()
                if not self.camera_on:
                    cam.refresh()
            self.window.set_status(st.message or "")

    def window_built(self) -> None:
        """The settings window just made its pages: bring them up to date."""
        self._sync_camera_ui(True)

    # ------------------------------------------------------------ events
    def _handle(self, events: list) -> None:
        for name, payload in events:
            log.debug("event %s %s", name, payload)
            if self.tour is not None:
                self.tour.feed(name)
            if name == "open":
                self._reset_levels()
                self._reset_mode()
                self.dial.set_items(self.level_items)
                self.dial.painter_.crumb = ""
                self.dial.painter_.mode = "main"
                self.dial.painter_.keyboard = self.keyboard_session
                self.dial.painter_.weather = self.weather.fresh()
                self.dial.place("")
                self.dial.open(self.engine.snapshot(), interactive=self.keyboard_session)
                self._arm_mouse()
                if self.keyboard_session:
                    self.volume.read(self.volume_read.emit)      # the slider beside it starts at today's level
                self.sounds.play("open")
                if self._watch_context():
                    self._say("Rotary. " + self.level_items[self.engine.selected % len(self.level_items)].name)
            elif name == "select":
                self.sounds.play("tick")
                if self.mode != "main":
                    self._mode_select()
                else:
                    self._speak_timer.start()
            elif name == "volume":
                self.volume.change(int(payload))
            elif name == "execute" and self.mode != "main":
                self._leave_mode()           # a click / pinch / point on a slides or video ring: the items
            elif name == "execute":
                item = self.level_items[payload % len(self.level_items)]
                if item.kind == "quicknav":
                    self._enter_quicknav(item)
                    continue
                if item.kind == "back":
                    self._go_back()
                    continue
                self.dial.set_state(self.engine.snapshot())
                self.dial.play_execute()
                self.sounds.play("confirm")
                self._say(item.name)
                if self.tour is not None and self.tour.practice:
                    self._flash_msg(f"Practice · {item.name} would open now", "done", GREEN, 2.5)
                elif item.kind == "assist":
                    self.start_assist()
                else:
                    self._run_action(item)
                self._end_keyboard()
            elif name in ("close", "closed"):
                if name == "close":
                    self.dial.close_dial()
                self._flush_seek()
                self._reset_mode()
                self._reset_levels()
                self._end_keyboard()

    def _run_action(self, item) -> None:
        cfg = self.cfg

        def work():
            try:
                execute(item.action, cfg)
                self.action_done.emit(item.name, True, "")
            except Exception as e:
                log.exception("action %s failed", item.name)
                self.action_done.emit(item.name, False, str(e))
        QTimer.singleShot(140, lambda: threading.Thread(target=work, daemon=True).start())

    def _on_action_done(self, name: str, ok: bool, err: str) -> None:
        if ok:
            self._flash_msg(name, "done", GREEN)
        else:
            self._flash_msg(f"Couldn't run {name}: {err}"[:96], "error", RED, 4.5)
            self._say(f"Couldn't run {name}")

    def _say(self, text: str) -> None:
        self.speaker.say(text)

    def _speak_current(self) -> None:
        if self.engine.phase == Phase.OPEN and self.level_items:
            self._say(self.level_items[self.engine.selected % len(self.level_items)].name)

    # ------------------------------------------------------------ calibration
    def start_calibration(self) -> None:
        self._calib = []
        self._calib_until = time.monotonic() + 2.5
        self._update_pill()

    def _finish_calibration(self) -> None:
        samples, self._calib = self._calib or [], None
        good = [s for s in samples if abs(s) > 0.12]
        if len(good) < 10:
            self._flash_msg("Calibration failed — no palm seen", "error", RED, 3.0)
            return
        flip = float(np.median(good)) < 0
        self.cfg.setdefault("gestures", {})["flip_handedness"] = flip
        self.apply_config()
        if self.window.built:
            self.window.gestures.refresh()
        self._flash_msg("Palm calibrated", "done", GREEN)
        log.info("calibrated flip_handedness=%s from %d samples", flip, len(good))

    # ------------------------------------------------------------ pill
    def _flash_msg(self, text: str, kind: str, accent: str, seconds: float = 1.8,
                   progress: float | None = None) -> None:
        self._flash = (text, kind, accent, time.monotonic() + seconds, progress)
        self._update_pill()

    def _update_pill(self, snap=None) -> None:
        if not self.pill_enabled:
            return
        if self.notch.isVisible() and self.pill.isVisible():
            self.pill.set_status("", hidden=True)
        snap = snap or self.engine.snapshot()
        now = time.monotonic()
        st = self.cam_status
        if st.state not in ("none", "error"):
            self._cam_lost_t = now
        if self._flash and now < self._flash[3]:
            text, kind, accent, _, progress = self._flash
            args = dict(text=text, kind=kind, accent=accent)
            if progress is not None:
                args["progress"] = progress
        elif self._calib is not None:
            prog = 1 - max(0.0, self._calib_until - now) / 2.5
            args = dict(text="Hold your palm toward the camera…", accent=BLUE, progress=prog)
        elif not self.camera_on:
            args = dict(text="", hidden=True)
        elif self.engine.paused:
            args = dict(text="Paused — camera off", accent=GRAY, collapsed=now - self._last_hand_t > 6)
        elif st.state == "searching":
            args = dict(text="", hidden=True)        # usually over in a second; nothing to say yet
        elif st.state in ("none", "error"):
            # No words, just a small warning sign (hover the notch, or open Camera, for the details).
            args = dict(text="", kind="warning", accent=AMBER, collapsed=True)
        elif snap.phase != Phase.IDLE:
            args = dict(text="", hidden=True)
        elif not snap.presented:
            # Quiet unless a hand is held up on purpose; gone entirely when nobody's around.
            if not snap.hand_present and now - self._last_hand_t > 4.0:
                args = dict(text="", hidden=True)
            else:
                args = dict(text="Ready", accent=GREEN if snap.hand_present and not snap.at_face else GRAY,
                            collapsed=True)
        elif snap.cooling:
            args = dict(text="", accent=GRAY, collapsed=True)
        elif snap.rearm_needed:
            args = dict(text="Move your hand to re-arm" + (" · or grab" if self.settings.grip_summon else ""),
                        accent=GRAY)
        elif snap.arming > 0.08:
            args = dict(text="Hold still to open", accent=BLUE, progress=snap.arming)
        else:
            ways = (["hold still"] if self.settings.still_summon else []) + \
                   (["close your hand"] if self.settings.grip_summon else [])
            text = " or ".join(ways) or "use the keyboard shortcut"
            args = dict(text=text[0].upper() + text[1:], accent=GREEN)
        if self.notch.isVisible():
            self.notch.set_status(**args)
            tip = (st.message or "No camera found") if args.get("kind") == "warning" else ""
            if self.notch.toolTip() != tip:
                self.notch.setToolTip(tip)
        else:
            self.pill.set_status(**args)

    # ------------------------------------------------------------ lifecycle
    def uninstall(self, delete_settings: bool) -> None:
        self.quitting = True
        system.uninstall(delete_settings)
        self.quit(save=not delete_settings)

    def _session_ending(self, *_):
        """Windows is signing out / shutting down: don't block it."""
        self.quitting = True
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_cfg()

    def _shutdown(self) -> None:
        if self.tour is not None:            # quit mid-tour: it picks up again next time (tutorial_done stays false)
            import shiboken6
            self.tour.finished.disconnect()
            shiboken6.delete(self.tour)       # while Qt is still up; Python would free it after the QApplication
            self.tour = None
        if self.live.isRunning():
            self.live.stop()
            self.live.wait(2000)
        if self.tracker.isRunning():
            self.tracker.stop()
            self.tracker.wait(3000)

    def quit(self, save: bool = True) -> None:
        self.quitting = True
        if save and self._save_timer.isActive():
            self._save_timer.stop()
            self._save_cfg()
        self.hotkeys.unregister(HK_TOGGLE)
        self.hotkeys.unregister(HK_ASSIST)
        self._nav_keys(False)
        self.mouse_trigger.stop()
        self._shutdown()
        self.tray.hide()
        self.volume_ring.hide_now()
        self.volume_ring.close()
        self.window.close()
        self.app.quit()


# --------------------------------------------------------------------------- entry
def _keycaps(label: str) -> list[str]:
    """'Win + Alt + A' → ['Win', 'Alt', 'A']; '⌃⌥A' → ['⌃', '⌥', 'A']."""
    if not label:
        return []
    if "+" in label:
        return [k.strip() for k in label.split("+")]
    caps = []
    for ch in label:
        if ch in "⌃⌥⇧⌘" or not caps or caps[-1] in "⌃⌥⇧⌘":
            caps.append(ch)
        else:
            caps[-1] += ch
    return caps


def _window_zone(w: QWidget) -> tuple[float, float, float, float]:
    """A window's rectangle in the mouse hook's coordinates (Windows: physical pixels)."""
    if WIN:
        r = wintypes.RECT()
        if winapi.user32.GetWindowRect(wintypes.HWND(int(w.winId())), ctypes.byref(r)):
            return r.left, r.top, r.right, r.bottom
    g = w.frameGeometry()
    k = w.devicePixelRatioF() if LINUX else 1.0
    return g.left() * k, g.top() * k, (g.right() + 1) * k, (g.bottom() + 1) * k


def _instance_key() -> str:
    """One running copy per person: on macOS and Linux the socket sits in the shared /tmp, so another
    account's copy mustn't be mistaken for (or get in the way of) this one."""
    import getpass
    import hashlib
    try:
        who = getpass.getuser()
    except Exception:
        who = str(Path.home())
    return f"{INSTANCE_KEY}-{hashlib.sha1(who.encode('utf-8'), usedforsecurity=False).hexdigest()[:10]}"


def _single_instance(app: QApplication) -> QLocalServer | None:
    key = _instance_key()
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return None
    QLocalServer.removeServer(key)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(key):
        log.warning("couldn't listen for a second copy starting: %s", server.errorString())
    return server


def _run_python_file(path: str, err_file: str | None) -> int:
    """`--run-python file.py`: the packaged app doubling as a Python interpreter for code actions."""
    import runpy
    import traceback
    sys.argv = [path]
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        if err_file:
            Path(err_file).write_text(traceback.format_exc(), encoding="utf-8")
        return 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    args_in = sys.argv[1:] if argv is None else argv
    # A windowed .exe has no console: give print() and friends somewhere harmless to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    ap = argparse.ArgumentParser(prog="gesture-rotary", add_help=False)
    ap.add_argument("--show", "--preview", dest="show", action="store_true")
    ap.add_argument("--minimized", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--run-python", dest="run_python")
    ap.add_argument("--error-file", dest="error_file")
    args, _unknown = ap.parse_known_args(args_in)
    if args.run_python:
        return _run_python_file(args.run_python, args.error_file)

    logdir = DATA_DIR / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    handlers: list[logging.Handler] = [RotatingFileHandler(logdir / "rotary.log", maxBytes=1_000_000, backupCount=2,
                                                           encoding="utf-8")]
    if getattr(sys.stderr, "name", "") != os.devnull:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("matplotlib", "PIL", "absl", "websocket"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = lambda t, v, tb: log.error("unhandled error", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.error("unhandled error in %s", a.thread.name if a.thread else "thread",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    if sys.platform == "win32":
        try:  # own taskbar identity (icon + grouping) instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(["gesture-rotary"])
    app.setApplicationName("RotaryUI")
    app.setApplicationDisplayName("RotaryUI")
    app.setQuitOnLastWindowClosed(False)
    server = _single_instance(app)
    if server is None:
        log.info("already running — brought the existing window forward")
        return 0
    show = True if args.show else (False if args.minimized else None)
    rotary = RotaryApp(app, show_window=show)

    def on_conn():
        c = server.nextPendingConnection()
        if c:
            c.readyRead.connect(lambda: (c.readAll(), rotary.show_window()))
            c.disconnected.connect(c.deleteLater)
    server.newConnection.connect(on_conn)
    return app.exec()
