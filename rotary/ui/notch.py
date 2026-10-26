"""The notch: a small liquid-glass tab hanging from the top of the screen — a Dynamic Island for Windows,
and the way into the rotary without a camera.

Resting, it shows the time and one live activity around it — what's playing (album art + sound bars), a
timer counting down, or Medal recording a game — plus tiny dots when an app uses the microphone (orange)
or the camera (green). Short status messages (a clip was saved, an app launched…) slide in and out.

Hover it for the details (now playing: art, title, progress and ⏮ ⏯ ⏭); with nothing going on, a longer
hover says "Click to open wheel". Click it, or scroll on it, to open the rotary. Right-click for timers, screen
assist and to customise what it shows.

It can be moved: drag it along its edge, or pull it away until it tears off like a drop of liquid and put it
anywhere (throw it and it glides). Bring it near any edge of the screen — top, bottom, left or right — and it merges
into that edge and hangs from it. A middle click (the scroll wheel) sends it home to the middle of the top edge.

A call ringing in (Discord, Teams, …) opens a card on its own: who's calling, Accept and Decline. While on a
call, hover for Mute / Deafen and Leave.

Screen assist's answers stay on it for a while: the option to pick ("B") in a badge and a short answer;
hover for the longer, detailed answer, Copy (which copies the detailed one) and Dismiss.

AI agents (Claude Code, Codex, a local model… see agents.py) show while they work: a turning purple-blue ring
and how long it's been at it. Each new step it moves on to slides in for a moment ("Claude Code · Writing the
tests"); hover for every agent and what it's doing right now. When one has a question, the notch says so with an
amber "?" — hover it to answer: its options as buttons, or Reply… to type an answer.

Active Game Tracking (see rotary/gametrack) puts your match on it: a crosshair and the stats you picked
("K 5  D 2  A 1  +$2.5k"), kept small while you play. It opens by itself only when you're eliminated (who by,
every stat, buttons to fix one by hand or end the match) and when the match ends (its summary, which stays until
the next one starts); right-click for the rest.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import replace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QConicalGradient, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget

from ..live import LiveState, NowPlaying
from . import liquid
from .glass import (ICONS, TEXT, Spring, black, exclude_from_capture, font, frame_ms, grab_behind, no_activate,
                    qcolor, refuse_activation, stroke_rim, white)

GREEN, AMBER, RED, BLUE, GRAY, ORANGE = "#30D158", "#FF9F0A", "#FF453A", "#0A84FF", "#8E8E93", "#FF9F0A"
AI_COLORS = ("#BF5AF2", "#5E5CE6", "#0A84FF", "#64D2FF", "#BF5AF2")     # an agent at work: purple into blue
G_PREV, G_PLAY, G_PAUSE, G_NEXT, G_TIMER, G_MUSIC = "", "", "", "", "", ""
G_PHONE, G_MIC, G_DEAFEN = "\uE717", "\uE720", "\uE74F"
G_CHECK, G_CLOSE = "\uE73E", "\uE711"
# What the call card's buttons press in each app (its own shortcuts).
CALL_BUTTONS = {"Discord": [("mute", "Mute"), ("deafen", "Deafen"), ("leave", "Leave")],
                "Teams": [("mute", "Mute"), ("leave", "Leave")], "Zoom": [("mute", "Mute"), ("leave", "Leave")],
                "Slack": [("leave", "Leave")]}
PAUSED_SHOW_SECONDS = 120          # a paused song stays on the notch this long
HOVER_HINT = "Click to open wheel"
HOVER_HINT_SECONDS = 0.9           # a hover this long, with nothing else on the notch, shows the hint
AGENT_FLASH_SECONDS = 4.0          # an agent's new step stays on the notch this long
AGENT_ROWS = 3                     # agents listed on the hover card
GAME_COLOR, GAME_DEAD, GAME_GOLD = "#FF6B3D", "#FF453A", "#FFD60A"
EDGES = ("top", "bottom", "left", "right")
AWAY = {"top": (0, 1), "bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}   # from each edge into the screen

DEFAULT_OPTIONS = {"clock": "time", "clock_24h": None, "media": True, "media_art": True, "clips": True, "calls": True,
                   "privacy": True, "battery": True, "look": "dark", "alerts_over_games": True}


def system_24h() -> bool:
    """Does the system show times as 24-hour (its short time format uses H rather than h)?"""
    if sys.platform != "win32":
        from .. import posix
        forced = posix.force_24h()
        if forced is not None:
            return forced
        from PySide6.QtCore import QLocale
        return "H" in QLocale.system().timeFormat(QLocale.FormatType.ShortFormat)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as k:
            return "H" in winreg.QueryValueEx(k, "sShortTime")[0]
    except OSError:
        return False


def clock_text(t: time.struct_time, h24: bool, with_date: bool) -> str:
    hm = f"{t.tm_hour:02d}:{t.tm_min:02d}" if h24 else f"{(t.tm_hour % 12) or 12}:{t.tm_min:02d}"
    return f"{time.strftime('%a', t)} {t.tm_mday}  {hm}" if with_date else hm


def mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class NotchWindow(QWidget):
    clicked = Signal()               # open the rotary
    approached = Signal()            # the pointer came onto the notch: a click may follow
    scrolled = Signal(int)           # wheel steps over the notch (+1 = next item)
    media_command = Signal(str)      # "play_pause" | "next" | "previous"
    timer_done = Signal()
    call_command = Signal(str, str)  # (app, "mute" | "deafen" | "leave" | "accept" | "decline")
    moved = Signal(object)           # where it was put: {"docked", "edge", "x", "y", "up"} (None = back home)
    customize = Signal()             # "Customise the notch…" from its menu
    assist = Signal()                # "Solve what's on screen" from its menu
    assist_note = Signal()           # "Ask with a note…": the same, with a line of context first
    agent_answer = Signal(str, object, bool)     # (question id, the answer — None: put away, typed)
    game_command = Signal(str, object)           # Active Game Tracking: ("add", "kills"), ("end", "won"), …

    # Base geometry in logical px at the default text size: (width, height, bottom corner radius).
    COLLAPSED = (188.0, 30.0, 13.0)
    HOVER_H = 38.0
    PEEK = (116.0, 5.0, 2.5)
    STATUS_H = 38.0
    MEDIA_CARD = (440.0, 96.0, 30.0)
    TIMER_CARD = (300.0, 64.0, 26.0)
    CALL_CARD = (330.0, 64.0, 26.0)
    INCOMING_CARD = (390.0, 74.0, 30.0)
    ANSWER_CARD = (500.0, 300.0, 28.0)   # width, tallest, radius (the height follows the text)
    QUESTION_CARD = (500.0, 320.0, 28.0)
    AGENTS_CARD = (440.0, 0.0, 28.0)     # the height follows the number of agents
    GAME_CARD = (460.0, 0.0, 28.0)       # the height follows the stats shown
    MAX_W = 560.0
    MARGIN = 14.0
    SHOULDER = 8.0
    FLOAT_PAD = 12.0         # room above a free-floating notch for its shadow
    TEAR = 70.0              # pull it this far away from its edge and it comes away
    DOCK_SNAP = 16.0         # a free notch this close to an edge of the screen merges into it
    DRAG_START = 5.0         # a press that moves less than this is a click

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setWindowTitle("RotaryUI notch")
        self.setAccessibleName("RotaryUI notch")

        self.hovered = False
        self.peek = False
        self.active = False              # the rotary is open: the notch glows a little
        self.hotkey = "⌃⌥Space" if sys.platform == "darwin" else "Ctrl + Alt + Space"
        self.s = 1.0
        self.k = 1.0
        self.high_contrast = False
        self.reduce_motion = False
        self.opts = dict(DEFAULT_OPTIONS)
        self._h24 = system_24h()

        self.status_text = ""
        self.status_kind = "dot"
        self.status_accent = QColor(GRAY)
        self.status_progress: float | None = None
        self.status_dot = False          # a quiet status: just a coloured dot

        # live activities
        self.live = LiveState()
        self._art: QPixmap | None = None
        self._art_key = None
        self._art_tint = QColor("#ffffff")
        self._paused_at: float | None = None
        self.timer_end: float | None = None
        self.timer_total = 0.0
        self._buttons: dict[str, QRectF] = {}     # clickable parts of the hover card
        self._hover_button = ""
        self.answer: tuple[str, str, str] | None = None     # screen assist: (choice, answer, detail)
        self._copied_until = 0.0
        self._answer_timer = QTimer(self)
        self._answer_timer.setSingleShot(True)
        self._answer_timer.timeout.connect(self._answer_expired)
        # AI agents (agents.py): who's working on what, and the questions they're waiting on
        self.agents: list = []           # agents.Session, most recent first
        self.questions: list = []        # agents.Question, oldest first
        self.agent_flash = True          # show each new step for a moment
        self._agent_seen: dict[str, tuple] = {}
        self._flash_text = ""
        self._flash_until = 0.0
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._flash_over)
        self.reply_box: ReplyBox | None = None
        # Active Game Tracking: the match on now, or the last one's summary (GameWatcher.snapshot())
        self.game: dict | None = None
        self.game_enabled = False        # tracking is on (the menu offers to turn it on or off)
        self.game_modes: list[tuple[str, str]] = []     # (key, name) to start a match in, from the menu
        self.hint_ready = False          # hovered long enough for "Click to open wheel"
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._show_hint)

        self.w_s = Spring(self.COLLAPSED[0], 380.0, 30.0)
        self.h_s = Spring(self.COLLAPSED[1], 380.0, 30.0)
        self.r_s = Spring(self.COLLAPSED[2], 380.0, 30.0)
        self.fade = Spring(1.0, 260.0, 30.0)        # text cross-fade
        self.prog = Spring(0.0, 400.0, 40.0)
        self.press = Spring(0.0, 420.0, 30.0)       # squish on click
        self.lit = Spring(0.0, 200.0, 26.0)
        self.card = Spring(0.0, 320.0, 32.0)        # 0 = compact, 1 = hover card
        self.pull = Spring(0.0, 520.0, 22.0)        # stretched away from its edge (px), snaps back on release
        self.wob = Spring(0.0, 300.0, 9.0)          # liquid wobble after tearing off, merging or bouncing
        self._springs = (self.w_s, self.h_s, self.r_s, self.fade, self.prog, self.press, self.lit, self.card,
                         self.pull, self.wob)

        # where it is: hanging from an edge of the screen ("top", "bottom", "left", "right") or floating free
        # (None; then it grows downwards, or upwards in the lower half of the screen); _pos is the window's top-left
        self.edge: str | None = "top"
        self._up = False
        self.position: dict | None = None           # saved spot (None = home: the middle of the top edge)
        self._pos = QPointF(0.0, 0.0)
        self._drag: dict | None = None
        self._vel: QPointF | None = None            # thrown: px/s
        self._glide: QPointF | None = None          # on its way home
        self._moving = False                        # dragged or thrown: no cards, no glass (it'd be stale)
        self._declined = None                       # the ringing call the user put away
        self._prev_text = ""
        self._t0 = time.perf_counter()
        self._last = time.perf_counter()
        self._wheel = 0
        self._clock_key = ""

        self._bd: liquid.Backdrop | None = None
        self._bd_ver = 0
        self._excluded = False
        self._glass_key = None
        self._glass_brush = None
        self._fonts: dict = {}

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._bd_timer = QTimer(self)
        self._bd_timer.timeout.connect(self._refresh_backdrop)
        self._live_timer = QTimer(self)                 # clock, countdowns, progress, sound bars
        self._live_timer.timeout.connect(self._live_tick)
        self._resize_window()

    # ------------------------------------------------------------------ public api
    def set_status(self, text: str, kind: str = "dot", accent: str = GRAY, progress: float | None = None,
                   collapsed: bool = False, hidden: bool = False) -> None:
        """Same contract as the status pill."""
        text = "" if (hidden or collapsed) else text
        changed = False
        if text != self.status_text:
            self._prev_text, self.status_text = self._shown_text(), text
            self.fade.snap(0.0)
            self.fade.target = 1.0
            changed = True
        dot = collapsed and not hidden
        acc = QColor(accent)
        if kind != self.status_kind or acc != self.status_accent or dot != self.status_dot:
            self.status_kind, self.status_accent, self.status_dot = kind, acc, dot
            changed = True
        if (progress is None) != (self.status_progress is None):
            changed = True
        self.status_progress = None if hidden else progress
        if progress is not None:
            self.prog.target = progress
        if changed or kind == "pulse" or progress is not None:
            self._retarget()

    def set_options(self, opts: dict) -> None:
        self.opts = {**DEFAULT_OPTIONS, **{k: v for k, v in (opts or {}).items() if k in DEFAULT_OPTIONS}}
        self._h24 = system_24h() if self.opts["clock_24h"] is None else bool(self.opts["clock_24h"])
        self._glass_key = None
        self._retarget()

    def set_live(self, state: LiveState) -> None:
        m = state.media
        if m is not None and m.art is not None and (m.key, len(m.art)) != self._art_key:
            self._art_key = (m.key, len(m.art))
            self._art, self._art_tint = self._load_art(m.art)
        elif m is None or m.art is None:
            self._art, self._art_key = None, None
        if m is not None and not m.playing:
            if self._paused_at is None or (self.live.media is not None and self.live.media.key != m.key):
                self._paused_at = time.monotonic()
        else:
            self._paused_at = None
        if self._declined is not None and state.incoming != self._declined:
            self._declined = None            # that call stopped ringing: the next one shows again
        self.live = state
        self._retarget()

    def set_active(self, on: bool) -> None:
        self.active = bool(on)
        self.lit.target = 1.0 if on else 0.0
        self._wake()

    def set_hotkey(self, label: str) -> None:
        self.hotkey = label
        self._retarget()

    def set_accessibility(self, text_scale: float = 1.0, high_contrast: bool = False,
                          reduce_motion: bool = False) -> None:
        k = max(0.8, min(1.8, float(text_scale)))
        s = 1.0 + (k - 1.0) * 0.6
        if (s, high_contrast) != (self.s, self.high_contrast):
            self._fonts = {}
            self._glass_key = None
        self.k, self.s = k, s
        self.high_contrast = bool(high_contrast)
        self.reduce_motion = bool(reduce_motion)
        kc = (520.0, 46.0) if reduce_motion else (380.0, 30.0)
        for sp in (self.w_s, self.h_s, self.r_s):
            sp.k, sp.c = kc
        self._resize_window()
        self._retarget()

    def set_peek(self, on: bool) -> None:
        self.peek = bool(on)
        self._retarget()

    def set_position(self, pos: dict | None) -> None:
        """Where the user put it last time (from the settings)."""
        pos = pos if isinstance(pos, dict) and "x" in pos and "y" in pos else None
        if pos != self.position:
            self.position = pos
            if self.isVisible() and self._drag is None and self._vel is None:
                self.place()

    def place(self) -> None:
        pos = self.position
        screen = None
        if pos is not None:
            try:
                x, y = float(pos["x"]), float(pos["y"])
                edge = pos.get("edge", "top") if pos.get("docked", True) else None
                ax, ay = AWAY.get(edge, (0, -1 if pos.get("up") else 1))
                screen = QGuiApplication.screenAt(QPoint(int(x + 2 * ax), int(y + 2 * ay)))
            except (TypeError, ValueError, AttributeError):
                screen = None
        if screen is None:
            pos = None
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = QRectF(screen.availableGeometry())
        if pos is None:
            self.edge = "top"
            home = self._edge_pos("top", self._home_x(g), g)
            self._set_pos(home.x(), home.y())
        elif pos.get("docked", True):
            self.edge = pos.get("edge") if pos.get("edge") in EDGES else "top"
            along = float(pos["x"]) if self.edge in ("top", "bottom") else float(pos["y"])
            at = self._edge_pos(self.edge, along, g)
            self._set_pos(at.x(), at.y())
        else:
            self.edge = None
            self._up = bool(pos.get("up"))
            y = float(pos["y"]) - (self.height() - self.FLOAT_PAD * self.s if self._up else self.FLOAT_PAD * self.s)
            self._set_pos(*self._clamp_free(float(pos["x"]) - self.width() / 2, y, g))

    def is_home(self) -> bool:
        return self.position is None

    def go_home(self) -> None:
        """Back to the middle of the top edge (a middle click, or "Put the notch back" in its menu)."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        self._drag, self._vel = None, None
        g = QRectF(screen.availableGeometry())
        if self.edge != "top":
            self.wob.velocity += 6.0
        self.edge = "top"
        target = self._edge_pos("top", self._home_x(g), g)
        self.pull.snap(0.0)
        self.position = None
        if self.reduce_motion or not self.isVisible():
            self._set_pos(target.x(), target.y())
            self._settle_move()
        else:
            self._glide = target
            self._moving = True
            self._wake()

    # -- moving it
    @property
    def docked(self) -> bool:
        return self.edge is not None

    @docked.setter
    def docked(self, on: bool) -> None:
        if on != self.docked:
            self.edge = "top" if on else None

    def _set_pos(self, x: float, y: float) -> None:
        self._pos = QPointF(x, y)
        self.move(int(round(x)), int(round(y)))
        self.update()

    @staticmethod
    def _home_x(sr: QRectF) -> float:
        return sr.center().x() + (1 if int(sr.width()) % 2 else 0)

    def _anchor(self) -> QPointF:
        """Where the body is attached inside the window: the middle of its side against the edge (docked), or the
        middle of its top (floating; of its bottom when it grows upwards)."""
        w, h, e = self.width(), self.height(), self.edge
        if e == "bottom":
            return QPointF(w / 2, h)
        if e == "left":
            return QPointF(0.0, h / 2)
        if e == "right":
            return QPointF(w, h / 2)
        if e == "top":
            return QPointF(w / 2, 0.0)
        return QPointF(w / 2, h - self.FLOAT_PAD * self.s if self._up else self.FLOAT_PAD * self.s)

    def _edge_pos(self, edge: str, along: float, sr: QRectF) -> QPointF:
        """The window's top-left for a notch hanging from `edge` of `sr`, its middle at `along` (x for the top and
        bottom edges, y for the sides) — kept so its middle stays on the screen."""
        if edge in ("top", "bottom"):
            x = max(sr.left(), min(sr.right(), along)) - self.width() / 2
            return QPointF(x, sr.top() if edge == "top" else sr.bottom() - self.height())
        y = max(sr.top(), min(sr.bottom(), along)) - self.height() / 2
        return QPointF(sr.left() if edge == "left" else sr.right() - self.width(), y)

    def _along(self) -> float:
        """Where along its edge a docked notch is (its middle: x on the top and bottom, y on the sides)."""
        if self.edge in ("left", "right"):
            return self._pos.y() + self.height() / 2
        return self._pos.x() + self.width() / 2

    def _screen_rect(self, at: QPointF | None = None) -> QRectF:
        """The work area of the screen the notch is on (or the one under `at`)."""
        if at is None:
            ax, ay = AWAY.get(self.edge, (0, -1 if self._up else 1))
            at = self._pos + self._anchor() + QPointF(2 * ax, 2 * ay)
        screen = QGuiApplication.screenAt(at.toPoint()) or self.screen() or QGuiApplication.primaryScreen()
        return QRectF(screen.availableGeometry()) if screen is not None else QRectF(0, 0, 1920, 1080)

    def _free_top(self, y: float | None = None) -> float:
        """The top of a floating notch's body on the screen (with its window's top at `y`)."""
        y = self._pos.y() if y is None else y
        return y + self.height() - self.FLOAT_PAD * self.s - self.h_s.value if self._up else y + self.FLOAT_PAD * self.s

    def _free_window_y(self, top: float) -> float:
        return top - (self.height() - self.FLOAT_PAD * self.s - self.h_s.value if self._up else self.FLOAT_PAD * self.s)

    def _clamp_free(self, x: float, y: float, sr: QRectF) -> tuple[float, float]:
        """A free notch's window position, its body kept on the screen and just clear of the edges (closer, and
        it would merge into one)."""
        w, h, gap = self.w_s.value, self.h_s.value, (self.DOCK_SNAP + 1) * self.s
        cx = x + self.width() / 2
        cx = max(sr.left() + w / 2 + gap, min(sr.right() - w / 2 - gap, cx))
        top = max(sr.top() + gap, min(sr.bottom() - h - gap, self._free_top(y)))
        return cx - self.width() / 2, self._free_window_y(top)

    def _edge_near(self, x: float, y: float, sr: QRectF, reach: float) -> str | None:
        """The edge of the screen a free notch with its window at (x, y) is within `reach` of (the closest)."""
        w, h = self.w_s.value, self.h_s.value
        cx, top = x + self.width() / 2, self._free_top(y)
        gaps = {"top": top - sr.top(), "bottom": sr.bottom() - (top + h),
                "left": cx - w / 2 - sr.left(), "right": sr.right() - (cx + w / 2)}
        edge = min(gaps, key=gaps.get)
        return edge if gaps[edge] <= reach else None

    def _merge(self, edge: str, x: float, y: float, sr: QRectF) -> QPointF:
        """A free notch (window at x, y) joins `edge`: where its window goes."""
        along = (x + self.width() / 2) if edge in ("top", "bottom") else self._free_top(y) + self.h_s.value / 2
        self.edge = edge
        return self._edge_pos(edge, along, sr)

    def _tear_off(self, sr: QRectF) -> None:
        """Pulled far enough from its edge: the body comes away where the stretched drop was, now floating."""
        body = self.body_rect().translated(self._pos)
        self.edge = None
        self._up = body.center().y() > sr.center().y()
        self.pull.snap(0.0)
        self._kick(7.0)
        self._set_pos(*self._clamp_free(body.center().x() - self.width() / 2, self._free_window_y(body.top()), sr))

    def _settle_move(self) -> None:
        """It came to rest somewhere: remember it, and look at what's behind it again."""
        self._moving = False
        self._vel = self._glide = None
        sr = self._screen_rect()
        if self.edge is None:
            # grow towards the roomier half of the screen: downwards in the top half, upwards in the bottom one
            top = self._free_top()
            up = top + self.h_s.value / 2 > sr.center().y()
            if up != self._up:
                self._up = up
                self._set_pos(self._pos.x(), self._free_window_y(top))
            sr = self._screen_rect()
        along = self._along()
        if self.position is not None or self.edge != "top" or abs(along - self._home_x(sr)) > 2:
            e = self.edge
            if e is None:
                cx = self._pos.x() + self.width() / 2
                y = self._pos.y() + (self.height() - self.FLOAT_PAD * self.s if self._up else self.FLOAT_PAD * self.s)
                self.position = {"docked": False, "x": round(cx, 1), "y": round(y, 1), "up": self._up}
            elif e in ("top", "bottom"):
                self.position = {"docked": True, "edge": e, "x": round(along, 1),
                                 "y": round(sr.top() if e == "top" else sr.bottom(), 1)}
            else:
                self.position = {"docked": True, "edge": e, "x": round(sr.left() if e == "left" else sr.right(), 1),
                                 "y": round(along, 1)}
        self.moved.emit(self.position)
        self._refresh_backdrop(force=True)
        self._retarget()

    def _kick(self, amount: float) -> None:
        if not self.reduce_motion:
            self.wob.velocity += amount

    # -- timer
    def start_timer(self, seconds: float) -> None:
        self.timer_total = float(seconds)
        self.timer_end = time.monotonic() + seconds
        self._retarget()

    def add_time(self, seconds: float) -> None:
        if self.timer_end is not None:
            self.timer_end += seconds
            self.timer_total += seconds
            self._retarget()

    def cancel_timer(self) -> None:
        self.timer_end = None
        self._retarget()

    def timer_left(self) -> float:
        return max(0.0, self.timer_end - time.monotonic()) if self.timer_end is not None else 0.0

    # -- screen assist
    def show_answer(self, choice: str, text: str, detail: str = "", seconds: float = 20.0) -> None:
        """Keep an answer on the notch for `seconds` (hovering it keeps it there; clicking dismisses it)."""
        self.answer = (choice, text, detail)
        self._answer_timer.start(int(max(3.0, seconds) * 1000))
        self._retarget()

    def clear_answer(self) -> None:
        self._answer_timer.stop()
        if self.answer is not None:
            self.answer = None
            self._retarget()

    def _answer_expired(self) -> None:
        if self.hovered:
            self._answer_timer.start(4000)           # still being read: wait for the mouse to leave
        else:
            self.clear_answer()

    def answer_headline(self) -> str:
        if self.answer is None:
            return ""
        choice, text, _detail = self.answer
        return f"{choice} · {text}" if choice and text else choice or text

    def copy_answer(self) -> None:
        if self.answer is None:
            return
        choice, text, detail = self.answer
        QGuiApplication.clipboard().setText(detail or text or choice)     # the longer answer, when there is one
        self._copied_until = time.monotonic() + 1.5
        QTimer.singleShot(1600, self.update)
        self.update()

    # -- AI agents
    def set_agents(self, sessions: list, questions: list) -> None:
        """What the agent bridge knows now. A new step, or an agent finishing or waiting, slides in for a moment."""
        flash = ""
        now = time.monotonic()
        seen = {}
        for s in sessions:
            mark = (s.state, s.step)
            seen[s.key] = mark
            before = self._agent_seen.get(s.key)
            if not s.busy(now) or before == mark or s.state == "idle":
                continue
            if s.state == "working" and (s.step or before is None):
                text = s.step or "Working"
            elif s.state == "done" and (before is None or before[0] != "done"):
                text = s.text if s.text and s.text != "Finished" else "Finished"
            elif s.state in ("waiting", "error") and (before is None or before[0] != s.state):
                text = s.now()
            else:
                continue
            flash = flash or f"{s.agent} · {text}"
        self._agent_seen = seen
        had = self.question()
        self.agents, self.questions = list(sessions), list(questions)
        if self.reply_box is not None and self.reply_box.isVisible() and not any(
                q.id == self.reply_box.qid for q in self.questions):
            self.reply_box.hide()                    # answered somewhere else
        if flash and self.agent_flash and self.question() is None:
            self._flash(flash)
        elif (had is None) != (self.question() is None):
            self._prev_text = ""
            self.fade.snap(0.0)
            self.fade.target = 1.0
        self._retarget()

    def _flash(self, text: str) -> None:
        self._prev_text = self._shown_text()
        self._flash_text = text
        self._flash_until = time.monotonic() + AGENT_FLASH_SECONDS
        self._flash_timer.start(int(AGENT_FLASH_SECONDS * 1000) + 30)
        self.fade.snap(0.0)
        self.fade.target = 1.0

    def _flash_over(self) -> None:
        if self._flash_text and not self.status_text:
            self._prev_text = self._flash_text
            self.fade.snap(0.0)
            self.fade.target = 1.0
        self._flash_text = ""
        self._retarget()

    def flash_text(self) -> str:
        """An agent's new step, while it's on the notch (a hover shows the card instead)."""
        if not self._flash_text or time.monotonic() >= self._flash_until or self.hovered:
            return ""
        return self._flash_text

    def question(self):
        """The question to answer first (the oldest), or None."""
        return self.questions[0] if self.questions else None

    def question_headline(self) -> str:
        q = self.question()
        return f"{q.agent} · {q.text}" if q is not None else ""

    def answer_question(self, answer: str | None, typed: bool = False) -> None:
        q = self.question()
        if q is None:
            return
        self.questions = [x for x in self.questions if x.id != q.id]     # gone at once; the bridge confirms
        self._hover_button = ""
        self.agent_answer.emit(q.id, answer, typed)
        self._retarget()

    def open_reply(self) -> None:
        """Reply…: a small box under the notch to type an answer in."""
        q = self.question()
        if q is None:
            return
        if self.reply_box is None:
            self.reply_box = ReplyBox()
            self.reply_box.sent.connect(self._reply_sent)
        body = self.body_rect()
        g = QRectF(self.mapToGlobal(body.topLeft().toPoint()), body.size())
        self.reply_box.open_for(q, g, self.edge == "bottom" or (self.edge is None and self._up), self.s)

    def _reply_sent(self, qid: str, text: str) -> None:
        q = self.question()
        if q is not None and q.id == qid:
            self.answer_question(text, typed=True)
        elif any(x.id == qid for x in self.questions):
            self.questions = [x for x in self.questions if x.id != qid]
            self.agent_answer.emit(qid, text, True)
            self._retarget()

    def lead_agent(self):
        """The agent the resting notch shows: one waiting on you, else one at work, else one that just finished
        (the most recent of those)."""
        now = time.monotonic()
        busy = [s for s in self.agents if s.busy(now)]
        rank = {"waiting": 0, "working": 1}
        return min(busy, key=lambda s: rank.get(s.state, 2), default=None)

    # -- Active Game Tracking
    def set_game(self, snap: dict | None, enabled: bool = False) -> None:
        """The match on now (or the last one's summary), as GameWatcher.snapshot() describes it."""
        self.game_enabled = bool(enabled)
        if snap != self.game:
            self.game = snap
            self._retarget()

    def _game_cmd(self, cmd: str, arg=None) -> None:
        self._hover_button = ""
        self.game_command.emit(cmd, arg)

    # ------------------------------------------------------------------ what to show
    def incoming(self) -> tuple[str, str] | None:
        """A call ringing in that hasn't been put away: (app, who — "" if unknown)."""
        inc = self.live.incoming
        if inc is None or not self.opts["calls"] or inc == self._declined:
            return None
        return inc

    def decline_call(self) -> None:
        inc = self.incoming()
        if inc is not None:
            self._declined = inc
            self.call_command.emit(inc[0], "decline")
            self._retarget()

    def accept_call(self) -> None:
        inc = self.incoming()
        if inc is not None:
            self._declined = inc            # answered: the card goes (the call card follows when it connects)
            self.call_command.emit(inc[0], "accept")
            self._retarget()

    def media(self) -> NowPlaying | None:
        m = self.live.media
        if m is None or not self.opts["media"]:
            return None
        if not m.playing and self._paused_at is not None and time.monotonic() - self._paused_at > PAUSED_SHOW_SECONDS:
            return None
        return m

    def activity(self) -> str:
        """The one live activity the resting notch shows: ringing > call > timer > an AI agent > clip recording >
        now playing."""
        if self.incoming() is not None:
            return "ringing"
        if self.opts["calls"] and self.live.call:
            return "call"
        if self.timer_end is not None:
            return "timer"
        if self.game is not None:
            return "game"
        if self.lead_agent() is not None:
            return "agent"
        if self.opts["clips"] and self.live.clipping:
            return "clip"
        if self.media() is not None:
            return "media"
        return ""

    def _clock(self) -> str:
        mode = self.opts["clock"]
        if mode not in ("time", "date"):
            return ""
        return clock_text(time.localtime(), self._h24, mode == "date")

    def _privacy(self) -> list[QColor]:
        if not self.opts["privacy"]:
            return []
        return ([QColor(ORANGE)] if self.live.mic else []) + ([QColor(GREEN)] if self.live.camera else [])

    def _warning(self) -> bool:
        """A quiet problem (no camera): a small warning sign instead of words."""
        return self.status_dot and self.status_kind == "warning"

    def _card_kind(self) -> str:
        """Which detailed card a hover shows ('' = none: the resting notch, or a one-line label). A ringing call
        shows its card without a hover, and so does the game's when you're out or the match is over; while
        you're playing its stats stay on the notch, hover or not."""
        if self._moving or self._drag is not None and self._drag["moved"]:
            return ""
        if self.incoming() is not None:
            return "incoming"
        if self._game_open() and not self.status_text and self.status_progress is None and (
                self.question() is None or not self.hovered) and (self.answer is None or not self.hovered):
            return "game"
        if self.hovered and self.question() is not None:
            return "question"
        if self.hovered and self.answer is not None:
            return "answer"
        if not self.hovered or self.status_text or self.status_progress is not None:
            return ""
        act = self.activity()
        if act == "agent":
            return "agents"
        return act if act in ("media", "timer", "call") else ""

    def _game_open(self) -> bool:
        """The game's card opens by itself: you've been eliminated, or the match is over (its summary)."""
        g = self.game
        return g is not None and self.activity() == "game" and (g.get("state") == "ended" or bool(g.get("dead")))

    # ------------------------------------------------------------------ geometry
    def _resize_window(self) -> None:
        w = int(math.ceil((self.MAX_W + 2 * self.MARGIN) * self.s))
        tallest = max(self.MEDIA_CARD[1], self.ANSWER_CARD[1], self.QUESTION_CARD[1], self._agents_card_h(AGENT_ROWS),
                      self._game_card_h(6, True))
        h = int(math.ceil((tallest + self.MARGIN + self.FLOAT_PAD) * self.s))
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
            self._glass_key = None
            if self.isVisible():
                self.place()

    def _shown_text(self) -> str:
        if self.status_text:
            return self.status_text
        if self.flash_text():
            return self.flash_text()
        if self.hovered and not self._card_kind():
            return self._hover_label()
        return ""

    def _hover_label(self) -> str:
        """What a hover says when there's no card to show: nothing at first, then the hint (never over a call,
        music, a timer or a clip — those have their own)."""
        act = self.activity()
        if act == "clip":
            return f"Medal is clipping {self.live.clipping}"
        if act or not self.hint_ready or self._moving:
            return ""
        return HOVER_HINT

    def _show_hint(self) -> None:
        if not self.hovered or self.hint_ready:
            return
        self.hint_ready = True
        if (not self.status_text and not self._card_kind() and self.answer is None and self.question() is None
                and self._hover_label()):
            self._prev_text = ""
            self.fade.snap(0.0)
            self.fade.target = 1.0
        self._retarget()

    def _text_width(self, text: str, px: float = 13.5) -> float:
        return self._font("text", px * self.k, QFont.Weight.DemiBold)[1].horizontalAdvance(text)

    def _compact_width(self) -> float:
        s = self.s
        clock = self._clock()
        centre = self._text_width(clock, 13.0) + 12 * s if clock else 26 * s
        act = self.activity()
        side = 0.0
        if act == "timer":
            side = max(46 * s, self._text_width(mmss(self.timer_left()), 12.5) + 14 * s)
        elif act == "ringing":
            side = max(46 * s, self._text_width("Ringing", 12.5) + 14 * s)
        elif act == "agent":
            side = max(46 * s, self._text_width(self._agent_side(), 12.5) + 14 * s)
        elif act == "game":
            side = max(46 * s, self._text_width(self.game.get("line", ""), 12.5) + 14 * s)
        elif act == "call":
            side = max(46 * s, self._text_width(mmss(self.call_seconds()), 12.5) + 14 * s)
        elif act in ("media", "clip"):
            side = 42 * s
        side += 10 * s if self._privacy() else 0.0
        side += 18 * s if self._warning() else 0.0
        floor =self.COLLAPSED[0] * s + (52 * s if act else 0.0)      # an activity spreads the island out
        return max(floor, centre + 2 * side + 2 * 16 * s)

    def _answer_layout(self, w: float) -> tuple[float, float, float]:
        """Screen assist's hover card for a body `w` wide: (answer text height, detail height, card height)."""
        s = self.s
        _choice, text, detail = self.answer or ("", "", "")
        flags = int(Qt.TextFlag.TextWordWrap)
        lead = self._answer_lead()
        tw = w - lead - 16 * s - 112 * s
        th = self._font("text", 14 * self.k, QFont.Weight.DemiBold)[1].boundingRect(
            QRectF(0, 0, max(40.0, tw), 1000), flags, text).height() if text else 0.0
        th = min(th, 3 * self._font("text", 14 * self.k, QFont.Weight.DemiBold)[1].height())
        dfm = self._font("text", 12 * self.k, QFont.Weight.Normal)[1]
        dw = max(40.0, w - lead - 16 * s)
        dh = dfm.boundingRect(QRectF(0, 0, dw, 1000), flags, detail).height() if detail else 0.0
        room = self.ANSWER_CARD[1] * s - (14 * s + max(30 * s, th) + 8 * s + 16 * s)
        dh = min(dh, max(1, int(room / dfm.lineSpacing())) * dfm.lineSpacing())   # whole lines, up to the card's bottom
        h = 14 * s + max(30 * s, th) + (8 * s + dh if detail else 0.0) + 16 * s
        return th, dh, max(64 * s, min(self.ANSWER_CARD[1] * s, h))

    def _answer_lead(self) -> float:
        """Where the hover card's text starts: after the badge (a 15 px circle, wider for "A, C")."""
        return (16 + 30 + 12) * self.s + self._badge_extra() * 1.5

    def _target(self) -> tuple[float, float, float, float]:
        s = self.s
        card = self._card_kind()
        if card == "question":
            w, _h, r = self.QUESTION_CARD
            return w * s, self._question_layout(w * s)["h"], r * s, 1.0
        if card == "answer":
            w, _h, r = self.ANSWER_CARD
            return w * s, self._answer_layout(w * s)[2], r * s, 1.0
        if card == "agents":
            w, _h, r = self.AGENTS_CARD
            return w * s, self._agents_card_h(len(self._listed_agents())) * s, r * s, 1.0
        if card == "game":
            w, _h, r = self.GAME_CARD
            g = self.game or {}
            return w * s, self._game_card_h(len(g.get("tiles", [])), g.get("state") == "ended") * s, r * s, 1.0
        if self.question() is not None and card != "incoming":
            h = self.STATUS_H * s
            w = 44 * s + self._text_width(self.question_headline()) + 22
            return min(self.MAX_W * s, max(self.COLLAPSED[0] * s, w)), h, h / 2 - 2, 0.0
        if self.answer is not None:
            h = self.STATUS_H * s
            w = 44 * s + self._badge_extra() + self._text_width(self.answer_headline()) + 22
            return min(self.MAX_W * s, max(self.COLLAPSED[0] * s, w)), h, h / 2 - 2, 0.0
        if self.status_text or self.status_progress is not None or self.flash_text():
            h = self.STATUS_H * s
            text = self._shown_text()
            w = (40 * s + self._text_width(text) + 22) if text else 120 * s
            return min(self.MAX_W * s, max(self.COLLAPSED[0] * s, w)), h, h / 2 - 2, 0.0
        if card == "media":
            w, h, r = self.MEDIA_CARD
            return w * s, h * s, r * s, 1.0
        if card == "timer":
            w, h, r = self.TIMER_CARD
            return w * s, h * s, r * s, 1.0
        if card == "call":
            w, h, r = self.CALL_CARD
            return max(w * s, 60 * s + 120 * s + self._call_buttons_width()), h * s, r * s, 1.0
        if card == "incoming":
            w, h, r = self.INCOMING_CARD
            who = self.incoming()[1] if self.incoming() else ""
            text_w = max(self._text_width(who or "Incoming call", 18), self._text_width("Discord · Incoming call", 12))
            return min(self.MAX_W * s, max(w * s, 66 * s + text_w + 120 * s)), h * s, r * s, 1.0
        label = self._hover_label() if self.hovered else ""
        if label:
            h = self.HOVER_H * s
            kf, kfm = self._font("text", 11 * self.k, QFont.Weight.Normal)
            cap = kfm.horizontalAdvance(self.hotkey) + 30 * s if (self.hotkey and label == HOVER_HINT) else 0
            w = 40 * s + self._text_width(label) + 16 * s + cap
            return min(self.MAX_W * s, max(self.COLLAPSED[0] * s + 24 * s, w)), h, h / 2 - 2, 0.0
        busy = bool(self.activity() or self.status_dot or self._privacy())
        if self.peek and not self.active and not busy and not self.hovered:
            w, h, r = self.PEEK
            if self.edge in ("left", "right"):
                w, h = h, w                        # a thin line along the side
            return w * s, h * s, r * s, 0.0
        w, h, r = self.COLLAPSED
        return self._compact_width(), h * s, r * s, 0.0

    def _retarget(self) -> None:
        w, h, r, c = self._target()
        self.w_s.target, self.h_s.target, self.r_s.target = w, h, r
        self.card.target = c
        if self.reduce_motion:
            self.card.snap(c)
        self._wake()
        self._schedule_live()

    def body_rect(self) -> QRectF:
        w, h = max(8.0, self.w_s.value), max(2.0, self.h_s.value)
        sq = 1.0 - 0.04 * self.press.value
        w, h = w * sq, h * (1.0 - 0.06 * self.press.value)
        wob = max(-0.35, min(0.35, self.wob.value * 0.1))      # squash and stretch
        stretch = self._stretch()
        a, e = self._anchor(), self.edge
        pull = self.pull.value if e is not None else 0.0
        if e in ("left", "right"):         # a tab out of the side: thinner along the edge as it's pulled
            w, h = w * (1.0 - wob + 0.08 * stretch), h * (1.0 + wob - 0.12 * stretch)
            x = a.x() + pull if e == "left" else a.x() - pull - w
            return QRectF(x, a.y() + self._edge_shift(h) - h / 2, w, h)
        w, h = w * (1.0 + wob - 0.12 * stretch), h * (1.0 - wob + 0.08 * stretch)
        y = a.y() - pull - h if e == "bottom" or (e is None and self._up) else a.y() + pull
        return QRectF(a.x() + self._edge_shift(w) - w / 2, y, w, h)

    def _stretch(self) -> float:
        """0…1: how far it's been pulled away from its edge, towards tearing off."""
        return max(0.0, min(1.0, self.pull.value / (self.TEAR * self.s * 0.55))) if self.docked else 0.0

    def _edge_shift(self, length: float) -> float:
        """Near an end of its edge (or a side of the screen) the body slides over inside its window so it stays
        fully on screen."""
        if not self.isVisible():
            return 0.0
        sr = self._screen_rect()
        if self.edge in ("left", "right"):
            lo, hi, start = sr.top(), sr.bottom(), self._pos.y() + self.height() / 2 - length / 2
        else:
            lo, hi, start = sr.left(), sr.right(), self._pos.x() + self.width() / 2 - length / 2
        pad = 4.0 * self.s
        if start < lo + pad:
            return lo + pad - start
        if start + length > hi - pad:
            return hi - pad - (start + length)
        return 0.0

    def _canon(self, rect: QRectF) -> tuple[QRectF, QTransform]:
        """The body as if it hung from the top edge (the edge along y = 0, the body below it), and the transform
        that puts that back on its own edge: the shapes are drawn once for all four edges."""
        w, h, e = self.width(), self.height(), self.edge
        if e == "bottom":
            return QRectF(rect.left(), h - rect.bottom(), rect.width(), rect.height()), QTransform(1, 0, 0, -1, 0, h)
        if e == "left":
            return QRectF(rect.top(), rect.left(), rect.height(), rect.width()), QTransform(0, 1, 1, 0, 0, 0)
        if e == "right":
            return QRectF(rect.top(), w - rect.right(), rect.height(), rect.width()), QTransform(0, 1, -1, 0, w, 0)
        return QRectF(rect), QTransform()

    def _hanging(self, rect: QRectF) -> bool:
        """Docked and right against its edge (not pulled away from it)."""
        return self.docked and self._canon(rect)[0].top() <= 0.5

    def _body_path(self, rect: QRectF, r: float) -> QPainterPath:
        """Docked: shoulders curving into its edge of the screen, rounded at the far end (pulled away, a
        narrowing neck of liquid joins it to the edge). Floating: a rounded island."""
        c, t = self._canon(rect)
        if not self.docked or c.top() > 0.5:
            rr = max(1.0, min(r, rect.height() / 2, rect.width() / 2))
            path = QPainterPath()
            path.addRoundedRect(rect, rr, rr)
            if self.docked:
                path = path.united(t.map(self._neck(c)))
            return path
        r = max(1.0, min(r, c.height(), c.width() / 2))
        path = QPainterPath()
        path.addRoundedRect(QRectF(c.left(), -r - 2, c.width(), c.height() + r + 2), r, r)
        sh = min(self.SHOULDER * self.s, c.height() * 0.7)
        if sh > 1.0:
            for x, sgn in ((c.left(), -1.0), (c.right(), 1.0)):
                f = QPainterPath()
                f.moveTo(x + sgn * sh, 0)
                f.quadTo(x, 0, x, sh)
                f.lineTo(x, 0)
                f.closeSubpath()
                path = path.united(f)
        return t.map(path)

    def _neck(self, rect: QRectF) -> QPainterPath:
        """The liquid neck between the edge and a notch being pulled away from it: thinner the further it goes.
        `rect` is the body as _canon gives it (hanging from the top)."""
        t = self._stretch()
        cx, top, mid = rect.center().x(), rect.top(), rect.top() + rect.height() * 0.5
        a = rect.width() / 2 * (0.85 - 0.25 * t) + self.SHOULDER * self.s * (1 - t)   # where it meets the edge
        b = max(5.0 * self.s, rect.width() / 2 * (0.6 - 0.48 * t))                     # the waist
        c = rect.width() / 2 * 0.7                                                     # where it meets the body
        path = QPainterPath()
        path.moveTo(cx - a, -1.0)
        path.cubicTo(cx - a, top * 0.35, cx - b, top * 0.2, cx - b, top * 0.5)
        path.cubicTo(cx - b, top * 0.8, cx - c, top, cx - c, mid)
        path.lineTo(cx + c, mid)
        path.cubicTo(cx + c, top, cx + b, top * 0.8, cx + b, top * 0.5)
        path.cubicTo(cx + b, top * 0.2, cx + a, top * 0.35, cx + a, -1.0)
        path.closeSubpath()
        return path

    # ------------------------------------------------------------------ animation
    def _wake(self) -> None:
        if not self.isVisible():
            return
        if not self._timer.isActive():
            self._timer.setInterval(frame_ms(self.screen()))
            self._last = time.perf_counter()
            self._timer.start()
        self._schedule_backdrop()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt, self._last = min(now - self._last, 1 / 30), now
        for sp in self._springs:
            sp.step(dt)
        if self._vel is not None:
            self._fly(dt)
        elif self._glide is not None:
            d = self._glide - self._pos
            if abs(d.x()) + abs(d.y()) < 0.6:
                self._set_pos(self._glide.x(), self._glide.y())
                self._settle_move()
            else:
                f = 1.0 - math.exp(-12.0 * dt)
                self._set_pos(self._pos.x() + d.x() * f, self._pos.y() + d.y() * f)
        self.update()
        if (all(sp.settled for sp in self._springs) and self.status_kind not in ("pulse",)
                and self._vel is None and self._glide is None and self._drag is None
                and self.incoming() is None):
            self._timer.stop()

    def _fly(self, dt: float) -> None:
        """Thrown: glide with friction; docked, slide along the edge and bounce off its ends; free, merge into
        whichever edge of the screen it flies into."""
        sr = self._screen_rect()
        v = self._vel
        x, y = self._pos.x() + v.x() * dt, self._pos.y() + v.y() * dt
        v = v * math.exp(-3.0 * dt)
        e = self.edge
        if e is not None:
            side = e in ("left", "right")
            along = y + self.height() / 2 if side else x + self.width() / 2
            lo, hi = (sr.top(), sr.bottom()) if side else (sr.left(), sr.right())
            speed = v.y() if side else v.x()
            if along < lo or along > hi:
                self._kick(min(8.0, abs(speed) / 250))
                speed = -speed * 0.45
            v = QPointF(0.0, speed) if side else QPointF(speed, 0.0)
            pos = self._edge_pos(e, along, sr)
            x, y = pos.x(), pos.y()
        else:
            hit = self._edge_near(x, y, sr, (self.DOCK_SNAP + 1.5) * self.s)
            if hit is not None and v.x() * AWAY[hit][0] + v.y() * AWAY[hit][1] < 0:
                into = abs(v.x() * AWAY[hit][0] + v.y() * AWAY[hit][1])
                pos = self._merge(hit, x, y, sr)             # flew into an edge: merge with it
                self._kick(min(9.0, into / 220))
                x, y = pos.x(), pos.y()
                v = QPointF(v.x(), 0.0) if hit in ("top", "bottom") else QPointF(0.0, v.y())
            else:
                x, y = self._clamp_free(x, y, sr)
        self._vel = v
        self._set_pos(x, y)
        if math.hypot(v.x(), v.y()) < 35:
            self._settle_move()

    def _schedule_live(self) -> None:
        """Repaint as often as what's showing needs: sound bars ~12×/s, everything else once a second."""
        if not self.isVisible():
            self._live_timer.stop()
            return
        act = self.activity()
        m = self.media()
        if act == "media" and m is not None and m.playing and not self.reduce_motion and not self._card_kind():
            interval = 80
        elif (act == "agent" or self.flash_text() or self.question() is not None) and not self.reduce_motion:
            interval = 40                    # the agent's turning ring
        elif self._clock() or act or self._card_kind():
            interval = 1000 if not act == "clip" else 120
        else:
            self._live_timer.stop()
            return
        if not self._live_timer.isActive() or self._live_timer.interval() != interval:
            self._live_timer.start(interval)

    def _live_tick(self) -> None:
        if self.timer_end is not None and time.monotonic() >= self.timer_end:
            self.timer_end = None
            self.timer_done.emit()
        if self._paused_at is not None and time.monotonic() - self._paused_at > PAUSED_SHOW_SECONDS + 1:
            self._paused_at = None
            self.live = replace(self.live, media=None)
        if self._declined is not None and self.live.incoming != self._declined:
            self._declined = None            # that call stopped ringing: the next one shows again
        key = (f"{self._clock()}|{mmss(self.timer_left()) if self.timer_end else ''}|"
               f"{mmss(self.call_seconds()) if self.live.call else ''}|{self._agent_side()}|{self.activity()}|"
               f"{(self.game or {}).get('line', '')}")
        if key != self._clock_key:
            self._clock_key = key
            self._retarget()
        else:
            self._schedule_live()
        self.update()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        no_activate(self)            # clicking the notch never takes the focus from the app in front
        if not self._excluded:
            self._excluded = exclude_from_capture(self, True)
        self._refresh_backdrop(force=True)
        self._wake()
        self._schedule_live()

    def nativeEvent(self, event_type, message):
        return refuse_activation(message)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self.hovered = False             # no leave event comes for a window that's hidden under the mouse
        if self._drag is not None:
            self._drag = None
            self.pull.snap(0.0)
        self.hint_ready = False
        self._hint_timer.stop()
        self._timer.stop()
        self._bd_timer.stop()
        if self.timer_end is None:
            self._live_timer.stop()        # a running timer keeps counting (and rings) while hidden

    # ------------------------------------------------------------------ live glass
    def _schedule_backdrop(self) -> None:
        if not self._excluded:
            return               # it would see itself: keep the first grab
        interval = 700 if self.hovered or self.status_text else 3000
        if not self._bd_timer.isActive() or self._bd_timer.interval() != interval:
            self._bd_timer.start(interval)

    def _refresh_backdrop(self, force: bool = False) -> None:
        if not self.isVisible() or (self._bd is not None and not self._excluded and not force):
            return
        bd = liquid.Backdrop.from_pixmap(grab_behind(self))
        if bd is not None and not bd.same_as(self._bd):     # a still desktop: keep the glass already made
            self._bd = bd
            self._bd_ver += 1
            self.update()

    def _glass(self, rect: QRectF, r: float):
        """Refracted backdrop for the current body size (quantised so animation reuses the work)."""
        bd = self._bd
        if bd is None or self.high_contrast or self._moving or self._drag is not None and self._drag["moved"]:
            return None                  # what's behind it changes as it moves: tint only until it settles
        q = 4.0
        w, h = round(rect.width() / q) * q, round(rect.height() / q) * q
        cx, cy = round(rect.center().x()), round(rect.center().y())
        hanging = self._hanging(rect)
        key = (w, h, round(r), self._bd_ver, self.width(), cx, cy, self.edge, hanging)
        if key != self._glass_key:
            x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            if hanging:                  # runs on past its edge, so only the far end is rounded
                ext = r + 2
                if self.edge == "top":
                    y0 = -ext
                elif self.edge == "bottom":
                    y1 = self.height() + ext
                elif self.edge == "left":
                    x0 = -ext
                else:
                    x1 = self.width() + ext
                rr = max(2.0, r)
            else:
                rr = max(2.0, min(r, h / 2, w / 2))
            shape = liquid.RoundRect(x0, y0, x1, y1, rr, bevel=min(14 * self.s, min(w, h) * 0.5), bend=0.85,
                                     magnify=1.02)
            self._glass_brush = liquid.brush(liquid.render(bd, [shape]), bd.f)
            self._glass_key = key
        return self._glass_brush

    # ------------------------------------------------------------------ painting
    def _font(self, which: str, px: float, weight: QFont.Weight) -> tuple[QFont, QFontMetricsF]:
        key = (which, round(px, 2), weight)
        got = self._fonts.get(key)
        if got is None:
            f = font(ICONS if which == "icons" else TEXT, px, weight)
            got = self._fonts[key] = (f, QFontMetricsF(f))
        return got

    @staticmethod
    def _load_art(data: bytes) -> tuple[QPixmap | None, QColor]:
        img = QImage.fromData(data)
        if img.isNull():
            return None, QColor("#ffffff")
        side = min(img.width(), img.height())          # square crop (video thumbnails are 16:9)
        img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
        img = img.scaled(160, 160, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        avg = img.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        c = QColor(avg.pixel(0, 0))
        h, s_, v, _a = c.getHsvF()
        tint = QColor.fromHsvF(max(0.0, h), min(1.0, s_ * 1.3 + 0.15), max(0.75, v))     # vivid enough for bars
        return QPixmap.fromImage(img), tint

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        self.paint(p)
        p.end()

    def paint(self, p: QPainter) -> None:
        rect = self.body_rect()
        r = max(1.0, self.r_s.value)
        body = self._body_path(rect, r)
        hc = self.high_contrast
        glassy = self.opts["look"] == "glass" and not hc
        thick = min(rect.width(), rect.height())
        big = min(1.0, max(0.0, (thick - self.COLLAPSED[1] * self.s) / (6 * self.s)))   # grown
        cr, t = self._canon(rect)             # drawn as if it hung from the top, then put on its own edge

        # shadow (kept tight: every visible pixel catches the mouse)
        hanging = self._hanging(rect)
        for i, a in ((5, 0.05), (3, 0.07), (1.5, 0.09)):
            sp = QPainterPath()
            if hanging:
                sp.addRoundedRect(QRectF(cr.left() - i, -r, cr.width() + 2 * i, cr.height() + r + i + 1.5),
                                  r + i, r + i)
                sp = t.map(sp)
            else:
                rr = min(r, thick / 2) + i
                sp.addRoundedRect(rect.adjusted(-i, -i + 1.5, i, i + 1.5), rr, rr)
                if self.docked:
                    sp = sp.united(t.map(self._neck(cr)))
            p.fillPath(sp, black(a * (0.6 if glassy else 1.0)))

        # body: the refracted backdrop under a tint — "Dark" is nearly black at rest like a real notch,
        # "Glass" lets much more of the screen through
        lum = self._bd.lum if self._bd is not None else 0.5
        glass = self._glass(rect, r)
        if glass is not None:
            p.fillPath(body, glass)
        if hc:
            rest_a, grown_a = 0.9, 0.94
        elif glassy:
            rest_a, grown_a = liquid.tint_alpha(lum, 0.22, 0.48), liquid.tint_alpha(lum, 0.30, 0.56)
        else:
            rest_a, grown_a = 0.80, liquid.tint_alpha(lum, 0.52, 0.70)
        p.fillPath(body, qcolor("#09090d", rest_a + (grown_a - rest_a) * big))
        sheen = QLinearGradient(t.map(QPointF(0, 0)), t.map(QPointF(0, cr.bottom())))   # brighter away from the edge
        sheen.setColorAt(0.0, white(0.0))
        sheen.setColorAt(0.7, white(0.05 if glassy else 0.03))
        sheen.setColorAt(1.0, white(0.14 if glassy else 0.09))
        p.fillPath(body, sheen)
        lit = self.lit.value
        if lit > 0.01:
            p.fillPath(body, qcolor(BLUE, 0.18 * lit))
        p.save()
        if self.docked:
            p.setClipRect(t.mapRect(QRectF(-1e5, 1.0, 2e5, 2e5)))      # no rim along the edge itself
        if hc:
            p.strokePath(body, QPen(white(0.9), 1.6))
        else:
            stroke_rim(p, body, t.map(QPointF(cr.center().x(), cr.top())), 1.2, 1.0 if glassy else 0.8, light_deg=235)
        p.restore()

        p.save()
        p.setClipPath(body)
        c = max(0.0, min(1.0, self.card.value))
        self._buttons = {}
        if c < 0.999:
            p.setOpacity(1.0 - c)
            self._paint_content(p, rect)
        if c > 0.001:
            p.setOpacity(c)
            kind = self._card_kind() or self._last_card
            if kind == "media":
                self._paint_media_card(p, rect)
            elif kind == "timer":
                self._paint_timer_card(p, rect)
            elif kind == "call":
                self._paint_call_card(p, rect)
            elif kind == "incoming":
                self._paint_incoming_card(p, rect)
            elif kind == "answer":
                self._paint_answer_card(p, rect)
            elif kind == "question":
                self._paint_question_card(p, rect)
            elif kind == "agents":
                self._paint_agents_card(p, rect)
            elif kind == "game":
                self._paint_game_card(p, rect)
        p.restore()

    _last_card = ""

    # -- compact
    def _paint_content(self, p: QPainter, rect: QRectF) -> None:
        s, h = self.s, rect.height()
        if h < 14 * s:
            return
        cy = rect.top() + h / 2
        text = self._shown_text()
        tf, fm = self._font("text", 13.5 * self.k, QFont.Weight.DemiBold)
        f = max(0.0, min(1.0, self.fade.value))
        q = self.question()
        if q is not None:
            self._ask_badge(p, QPointF(rect.left() + 20 * s, cy), 10 * s)
            tx = rect.left() + 38 * s
            area = QRectF(tx, rect.top(), rect.right() - tx - 12, h)
            p.setFont(tf)
            p.setPen(white(0.97))
            p.drawText(area, Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.question_headline(), Qt.TextElideMode.ElideRight, area.width()))
            return
        if self.answer is not None:
            self._badge(p, QPointF(rect.left() + 20 * s + self._badge_extra() / 2, cy), 10 * s)
            tx = rect.left() + 38 * s + self._badge_extra()
            area = QRectF(tx, rect.top(), rect.right() - tx - 12, h)
            p.setFont(tf)
            p.setPen(white(0.97))
            p.drawText(area, Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.answer_headline(), Qt.TextElideMode.ElideRight, area.width()))
            return
        if text or (self._prev_text and f < 1):
            ic = QPointF(rect.left() + 20 * s, cy)
            if self.status_text or self.status_progress is not None:
                self._indicator(p, ic)
            elif self.flash_text() or (self._prev_text and not text and self.lead_agent() is not None):
                self._agent_icon(p, ic, self.lead_agent(), 9 * s)
            elif self.activity() == "clip":
                self._rec_dot(p, ic)
            else:
                self._dial_mark(p, ic, 6.0 * s, 1.0)
            tx = rect.left() + 38 * s
            area = QRectF(tx, rect.top(), rect.right() - tx - 12, h)
            if not self.status_text and self.hovered and self.hotkey and text == HOVER_HINT:
                kf, kfm = self._font("text", 11 * self.k, QFont.Weight.Normal)
                kw = kfm.horizontalAdvance(self.hotkey) + 14 * s
                cap = QRectF(rect.right() - kw - 12 * s, cy - 10 * s, kw, 20 * s)
                p.save()
                p.setOpacity(p.opacity() * f)
                p.setPen(QPen(white(0.22), 1.0))
                p.setBrush(white(0.07))
                p.drawRoundedRect(cap, 6 * s, 6 * s)
                p.setFont(kf)
                p.setPen(white(0.7))
                p.drawText(cap, Qt.AlignmentFlag.AlignCenter, self.hotkey)
                p.restore()
                area.setRight(cap.left() - 8)
            p.setFont(tf)
            if self._prev_text and f < 1:
                p.setPen(white(0.92 * (1 - f)))
                p.drawText(area.translated(0, -5 * f), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(self._prev_text, Qt.TextElideMode.ElideRight, area.width()))
            p.setPen(white(0.95 * f))
            p.drawText(area.translated(0, 5 * (1 - f)), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, area.width()))
            return
        self._paint_resting(p, rect, cy)

    def _paint_resting(self, p: QPainter, rect: QRectF, cy: float) -> None:
        """Clock in the middle, the live activity either side of it, privacy dots at the far right."""
        s = self.s
        clock = self._clock()
        if clock:
            cf, cfm = self._font("text", 13.0 * self.k, QFont.Weight.DemiBold)
            p.setFont(cf)
            p.setPen(white(0.92))
            p.drawText(QRectF(rect.left(), rect.top(), rect.width(), rect.height()), Qt.AlignmentFlag.AlignCenter,
                       clock)
        elif not self.activity():
            self._dial_mark(p, QPointF(rect.center().x(), cy + 1), 5.5 * s, 0.6 + 0.4 * self.lit.value)
        dots = self._privacy()
        right = rect.right() - 16 * s
        if self._warning():
            p.setFont(self._font("icons", 12.5 * s, QFont.Weight.Normal)[0])
            p.setPen(self.status_accent)
            p.drawText(QRectF(right - 8 * s, cy - 9 * s, 16 * s, 18 * s), Qt.AlignmentFlag.AlignCenter, "")
            right -= 18 * s
        if dots:
            for i, col in enumerate(dots):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(right - i * 8 * s, cy), 3.0 * s, 3.0 * s)
            right -= 10 * s + (len(dots) - 1) * 8 * s
        lead = QPointF(rect.left() + 16 * s + 10 * s, cy)
        act = self.activity()
        if act == "media":
            m = self.media()
            self._art_tile(p, QRectF(lead.x() - 10 * s, cy - 10 * s, 20 * s, 20 * s), 5 * s)
            self._bars(p, QRectF(right - 22 * s, cy - 8 * s, 22 * s, 16 * s), m is not None and m.playing)
        elif act == "timer":
            p.setFont(self._font("icons", 13 * s, QFont.Weight.Normal)[0])
            p.setPen(QColor(ORANGE))
            p.drawText(QRectF(lead.x() - 10 * s, cy - 10 * s, 20 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, G_TIMER)
            tf, tfm = self._font("text", 12.5 * self.k, QFont.Weight.DemiBold)
            p.setFont(tf)
            t = mmss(self.timer_left())
            p.drawText(QRectF(right - tfm.horizontalAdvance(t) - 2, rect.top(), tfm.horizontalAdvance(t) + 4,
                              rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        elif act == "ringing":
            self._phone(p, lead, 9 * s, self._ring_angle())
            tf, tfm = self._font("text", 12.5 * self.k, QFont.Weight.DemiBold)
            p.setFont(tf)
            p.setPen(QColor(GREEN))
            t = "Ringing"
            p.drawText(QRectF(right - tfm.horizontalAdvance(t) - 2, rect.top(), tfm.horizontalAdvance(t) + 4,
                              rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        elif act == "call":
            self._phone(p, lead, 9 * s)
            tf, tfm = self._font("text", 12.5 * self.k, QFont.Weight.DemiBold)
            p.setFont(tf)
            p.setPen(QColor(GREEN))
            t = mmss(self.call_seconds())
            p.drawText(QRectF(right - tfm.horizontalAdvance(t) - 2, rect.top(), tfm.horizontalAdvance(t) + 4,
                              rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        elif act == "agent":
            ag = self.lead_agent()
            self._agent_icon(p, lead, ag, 9 * s)
            tf, tfm = self._font("text", 12.5 * self.k, QFont.Weight.DemiBold)
            p.setFont(tf)
            p.setPen(qcolor(self._agent_color(ag), 1.0))
            t = self._agent_side()
            p.drawText(QRectF(right - tfm.horizontalAdvance(t) - 2, rect.top(), tfm.horizontalAdvance(t) + 4,
                              rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        elif act == "game":
            self._game_icon(p, lead, 9 * s)
            tf, tfm = self._font("text", 12.5 * self.k, QFont.Weight.DemiBold)
            p.setFont(tf)
            p.setPen(QColor(self._game_color()))
            t = self.game.get("line", "")
            p.drawText(QRectF(right - tfm.horizontalAdvance(t) - 2, rect.top(), tfm.horizontalAdvance(t) + 4,
                              rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, t)
        elif act == "clip":
            self._rec_dot(p, lead)
            tf, tfm = self._font("text", 10.5 * self.k, QFont.Weight.Bold)
            p.setFont(tf)
            p.setPen(QColor(RED))
            p.drawText(QRectF(right - 30 * s, rect.top(), 30 * s, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "REC")
        if self.status_dot and not act and not self._warning():
            dc = QPointF(rect.right() - 18 * s, cy)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(self.status_accent, 0.3))
            p.drawEllipse(dc, 6 * s, 6 * s)
            p.setBrush(self.status_accent)
            p.drawEllipse(dc, 3.4 * s, 3.4 * s)

    def _art_tile(self, p: QPainter, r: QRectF, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        if self._art is not None and self.opts["media_art"]:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r, self._art, QRectF(self._art.rect()))
            p.restore()
            p.strokePath(path, QPen(white(0.18), 0.8))
            return
        p.fillPath(path, qcolor(self._art_tint if self._art is not None else QColor("#FF375F"), 0.9))
        p.setFont(self._font("icons", r.height() * 0.55, QFont.Weight.Normal)[0])
        p.setPen(white(1.0))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, G_MUSIC)

    def _bars(self, p: QPainter, r: QRectF, playing: bool) -> None:
        """Four sound bars, tinted from the album art, dancing while it plays."""
        n, gap = 4, r.width() / 4
        w = gap * 0.55
        t = time.perf_counter() - self._t0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._art_tint)
        for i in range(n):
            if playing and not self.reduce_motion:
                v = 0.35 + 0.65 * abs(math.sin(t * (2.1 + 0.7 * i) + i * 1.3) * math.sin(t * (1.3 + 0.45 * i)))
            else:
                v = 0.22
            bh = max(w, r.height() * v)
            x = r.left() + i * gap + (gap - w) / 2
            p.drawRoundedRect(QRectF(x, r.center().y() - bh / 2, w, bh), w / 2, w / 2)

    def _rec_dot(self, p: QPainter, c: QPointF) -> None:
        a = 1.0 if self.reduce_motion else 0.55 + 0.45 * (0.5 + 0.5 * math.sin((time.perf_counter() - self._t0) * 3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(RED, 0.3 * a))
        p.drawEllipse(c, 7 * self.s, 7 * self.s)
        p.setBrush(qcolor(RED, a))
        p.drawEllipse(c, 4.2 * self.s, 4.2 * self.s)

    # -- hover cards
    def _button(self, p: QPainter, key: str, r: QRectF, glyph: str, big: bool = False, text: str = "",
                tint: str = "") -> None:
        self._buttons[key] = r
        hot = self._hover_button == key
        if hot or text:
            path = QPainterPath()
            path.addRoundedRect(r, r.height() / 2, r.height() / 2)
            p.fillPath(path, qcolor(tint, 0.95 if hot else 0.78) if tint else white(0.16 if hot else 0.08))
        if text:
            p.setFont(self._font("text", 12 * self.k, QFont.Weight.DemiBold)[0])
            p.setPen(white(0.95))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            return
        p.setFont(self._font("icons", (20 if big else 15) * self.s, QFont.Weight.Normal)[0])
        p.setPen(white(1.0 if hot else 0.92))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, glyph)

    def _paint_media_card(self, p: QPainter, rect: QRectF) -> None:
        m = self.media()
        if m is None:
            return
        self._last_card = "media"
        s = self.s
        art = QRectF(rect.left() + 16 * s, rect.top() + 18 * s, 60 * s, 60 * s)
        self._art_tile(p, art, 12 * s)
        x = art.right() + 14 * s
        right = rect.right() - 16 * s
        ctl_w = 116 * s
        text_w = right - ctl_w - x - 8 * s
        tf, tfm = self._font("text", 14 * self.k, QFont.Weight.DemiBold)
        sf, sfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
        p.setFont(tf)
        p.setPen(white(0.97))
        p.drawText(QRectF(x, rect.top() + 18 * s, text_w, tfm.height()), Qt.AlignmentFlag.AlignLeft,
                   tfm.elidedText(m.title, Qt.TextElideMode.ElideRight, text_w))
        p.setFont(sf)
        p.setPen(white(0.62))
        sub = " · ".join(x_ for x_ in (m.artist, m.app_name) if x_)
        p.drawText(QRectF(x, rect.top() + 20 * s + tfm.height(), text_w, sfm.height()), Qt.AlignmentFlag.AlignLeft,
                   sfm.elidedText(sub, Qt.TextElideMode.ElideRight, text_w))
        # controls
        cy = rect.top() + 34 * s
        bw = 36 * s
        cx0 = right - ctl_w
        self._button(p, "previous", QRectF(cx0, cy - bw / 2, bw, bw), G_PREV)
        self._button(p, "play_pause", QRectF(cx0 + 40 * s, cy - bw / 2, bw, bw), G_PAUSE if m.playing else G_PLAY,
                     big=True)
        self._button(p, "next", QRectF(cx0 + 80 * s, cy - bw / 2, bw, bw), G_NEXT)
        # progress
        py = rect.top() + 70 * s
        if m.duration > 0:
            pos = m.position_now()
            lf, lfm = self._font("text", 10.5 * self.k, QFont.Weight.Normal)
            p.setFont(lf)
            p.setPen(white(0.55))
            left_t, right_t = mmss(pos), "-" + mmss(m.duration - pos)
            lw = max(lfm.horizontalAdvance(left_t), lfm.horizontalAdvance("0:00")) + 6 * s
            rw = lfm.horizontalAdvance(right_t) + 6 * s
            p.drawText(QRectF(x, py - 8 * s, lw, 16 * s), Qt.AlignmentFlag.AlignVCenter, left_t)
            p.drawText(QRectF(right - rw, py - 8 * s, rw, 16 * s),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right_t)
            bar = QRectF(x + lw, py - 2 * s, right - rw - x - lw - 6 * s, 4 * s)
            path = QPainterPath()
            path.addRoundedRect(bar, 2 * s, 2 * s)
            p.fillPath(path, white(0.18))
            done = QRectF(bar.left(), bar.top(), bar.width() * min(1.0, pos / m.duration), bar.height())
            path = QPainterPath()
            path.addRoundedRect(done, 2 * s, 2 * s)
            p.fillPath(path, white(0.9))
        else:
            self._bars(p, QRectF(x, py - 7 * s, 22 * s, 14 * s), m.playing)

    def _paint_timer_card(self, p: QPainter, rect: QRectF) -> None:
        if self.timer_end is None:
            return
        self._last_card = "timer"
        s = self.s
        cy = rect.top() + rect.height() / 2 + 2 * s
        c = QPointF(rect.left() + 34 * s, cy)
        rr = 15 * s
        frac = self.timer_left() / max(1.0, self.timer_total)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(ORANGE, 0.25), 3 * s))
        p.drawEllipse(c, rr, rr)
        p.setPen(QPen(QColor(ORANGE), 3 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), 90 * 16, int(360 * 16 * frac))
        tf, tfm = self._font("text", 24 * self.k, QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(QColor(ORANGE))
        p.drawText(QRectF(c.x() + rr + 12 * s, rect.top(), 120 * s, rect.height() + 4 * s),
                   Qt.AlignmentFlag.AlignVCenter, mmss(self.timer_left()))
        bh = 28 * s
        self._button(p, "timer_cancel", QRectF(rect.right() - 16 * s - 64 * s, cy - bh / 2, 64 * s, bh), "",
                     text="Cancel")
        self._button(p, "timer_add", QRectF(rect.right() - 16 * s - 64 * s - 8 * s - 60 * s, cy - bh / 2, 60 * s, bh),
                     "", text="+1 min")

    def _badge_extra(self) -> float:
        """How much wider than a circle the answer's badge is (long labels like "A, C" make a pill)."""
        choice = self.answer[0] if self.answer else ""
        if len(choice) <= 2:
            return 0.0
        return max(0.0, self._font("text", 11 * self.k, QFont.Weight.Bold)[1].horizontalAdvance(choice) - 12 * self.s)

    def _badge(self, p: QPainter, c: QPointF, rr: float, extra: float = -1.0) -> None:
        """The answer's badge: the option to pick ("B") on blue, or a tick on green for a plain answer."""
        choice = self.answer[0] if self.answer else ""
        extra = self._badge_extra() * rr / (10 * self.s) if extra < 0 else extra
        path = QPainterPath()
        path.addRoundedRect(QRectF(c.x() - rr - extra / 2, c.y() - rr, 2 * rr + extra, 2 * rr), rr, rr)
        p.fillPath(path, QColor(BLUE if choice else GREEN))
        r = path.boundingRect()
        p.setPen(white(1.0))
        if choice:
            px = rr * (1.15 if len(choice) == 1 else 0.95) if len(choice) <= 2 else 1.1 * self.k * rr / self.s
            p.setFont(self._font("text", px, QFont.Weight.Bold)[0])
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, choice)
        else:
            p.setFont(self._font("icons", rr * 1.05, QFont.Weight.Bold)[0])
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, G_CHECK)

    def _paint_answer_card(self, p: QPainter, rect: QRectF) -> None:
        if self.answer is None:
            return
        self._last_card = "answer"
        s = self.s
        _choice, text, detail = self.answer
        th, dh, _h = self._answer_layout(rect.width())
        top = rect.top() + 14 * s
        rr = 15 * s
        extra = self._badge_extra() * 1.5
        self._badge(p, QPointF(rect.left() + 16 * s + rr + extra / 2, top + rr), rr, extra)
        x = rect.left() + self._answer_lead()
        right = rect.right() - 16 * s
        flags = int(Qt.TextFlag.TextWordWrap)
        tf, tfm = self._font("text", 14 * self.k, QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(white(0.97))
        title_h = max(2 * rr, th)
        p.drawText(QRectF(x, top + (title_h - th) / 2, right - 112 * s - x, th + 1),
                   flags | int(Qt.AlignmentFlag.AlignLeft), text)
        if detail:
            sf, _sfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
            p.setFont(sf)
            p.setPen(white(0.66))
            p.drawText(QRectF(x, top + title_h + 8 * s, right - x, dh + 1), flags | int(Qt.AlignmentFlag.AlignLeft),
                       detail)
        bh = 28 * s
        self._button(p, "answer_close", QRectF(right - bh, top + rr - bh / 2, bh, bh), G_CLOSE)
        copied = time.monotonic() < self._copied_until
        bw = self._text_width("Copied", 12) + 24 * s
        self._button(p, "answer_copy", QRectF(right - bh - 8 * s - bw, top + rr - bh / 2, bw, bh), "",
                     text="Copied" if copied else "Copy")

    # -- AI agents
    @staticmethod
    def _agent_color(ag) -> str:
        if ag is None or ag.state == "working":
            return "#A9A2FF"
        return {"waiting": AMBER, "done": GREEN, "error": RED}.get(ag.state, GRAY)

    def _agent_side(self) -> str:
        """The resting notch's right-hand side for the agent at work: how long it's been at it, or how it stands."""
        ag = self.lead_agent()
        if ag is None:
            return ""
        if ag.state == "working":
            return mmss(time.time() - ag.started)
        return {"waiting": "Waiting", "done": "Done", "error": "Error"}.get(ag.state, "")

    def _agent_icon(self, p: QPainter, c: QPointF, ag, rr: float) -> None:
        """At work: a ring turning through purple and blue. Waiting: an amber dot; finished: a green tick."""
        s = self.s
        state = ag.state if ag is not None else "working"
        if state == "working":
            t = 0.0 if self.reduce_motion else (time.perf_counter() - self._t0)
            g = QConicalGradient(c, -t * 240.0)
            for i, col in enumerate(AI_COLORS):
                g.setColorAt(i / (len(AI_COLORS) - 1), QColor(col))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(g, 2.6 * s))
            p.drawEllipse(c, rr - 1.5 * s, rr - 1.5 * s)
            p.setPen(Qt.PenStyle.NoPen)
            pulse = 0.5 if self.reduce_motion else 0.5 + 0.5 * math.sin(t * 4)
            p.setBrush(qcolor("#BF5AF2", 0.55 + 0.4 * pulse))
            p.drawEllipse(c, rr * 0.32, rr * 0.32)
            return
        col = self._agent_color(ag)
        if state == "waiting":
            a = 1.0 if self.reduce_motion else 0.55 + 0.45 * (0.5 + 0.5 * math.sin((time.perf_counter() - self._t0) * 4))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(col, 0.3 * a))
            p.drawEllipse(c, rr * 0.8, rr * 0.8)
            p.setBrush(qcolor(col, a))
            p.drawEllipse(c, rr * 0.45, rr * 0.45)
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawEllipse(c, rr, rr)
        p.setFont(self._font("icons", rr * 1.05, QFont.Weight.Bold)[0])
        p.setPen(white(1.0))
        p.drawText(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter,
                   G_CHECK if state == "done" else (G_CLOSE if state == "error" else ""))

    def _ask_badge(self, p: QPainter, c: QPointF, rr: float) -> None:
        """A question waiting: "?" on amber."""
        path = QPainterPath()
        path.addEllipse(c, rr, rr)
        p.fillPath(path, QColor(AMBER))
        p.setPen(white(1.0))
        p.setFont(self._font("text", rr * 1.3, QFont.Weight.Bold)[0])
        p.drawText(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter, "?")

    def _listed_agents(self) -> list:
        return self.agents[:AGENT_ROWS]

    @staticmethod
    def _agents_card_h(rows: int) -> float:
        return 14.0 + 48.0 * max(1, rows) + 10.0

    def _paint_agents_card(self, p: QPainter, rect: QRectF) -> None:
        listed = self._listed_agents()
        if not listed:
            return
        self._last_card = "agents"
        s = self.s
        tf, tfm = self._font("text", 13.5 * self.k, QFont.Weight.DemiBold)
        sf, sfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
        rf, rfm = self._font("text", 12 * self.k, QFont.Weight.DemiBold)
        y = rect.top() + 14 * s
        for ag in listed:
            cy = y + 24 * s
            self._agent_icon(p, QPointF(rect.left() + 32 * s, cy), ag, 13 * s)
            x = rect.left() + 58 * s
            side = mmss(time.time() - ag.started) if ag.state == "working" else {
                "waiting": "Waiting", "done": "Done", "error": "Error", "idle": "Ready"}.get(ag.state, "")
            sw = rfm.horizontalAdvance(side) + 4
            right = rect.right() - 18 * s
            tw = right - sw - 10 * s - x
            p.setFont(tf)
            p.setPen(white(0.97))
            p.drawText(QRectF(x, cy - tfm.height() + 1 * s, tw, tfm.height()), Qt.AlignmentFlag.AlignLeft,
                       tfm.elidedText(ag.label(), Qt.TextElideMode.ElideRight, tw))
            p.setFont(sf)
            p.setPen(white(0.62))
            line = ag.now()
            if ag.state == "working" and ag.step and ag.text and ag.step != ag.text:
                line = f"{ag.step} · {ag.text}"
            p.drawText(QRectF(x, cy + 2 * s, right - x, sfm.height()), Qt.AlignmentFlag.AlignLeft,
                       sfm.elidedText(line, Qt.TextElideMode.ElideRight, right - x))
            p.setFont(rf)
            p.setPen(qcolor(self._agent_color(ag), 0.95))
            p.drawText(QRectF(right - sw, cy - tfm.height() + 1 * s, sw, tfm.height()),
                       Qt.AlignmentFlag.AlignRight, side)
            y += 48 * s

    # -- Active Game Tracking
    def _game_color(self) -> str:
        g = self.game or {}
        if g.get("state") == "ended":
            return {"won": GREEN, "lost": RED}.get(g.get("result"), GAME_GOLD)
        return GAME_DEAD if g.get("dead") else GAME_COLOR

    def _game_icon(self, p: QPainter, c: QPointF, rr: float) -> None:
        """A crosshair: orange in a match, red while you're down, the result's colour after it."""
        s = self.s
        col = QColor(self._game_color())
        g = self.game or {}
        if g.get("state") == "ended":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(c, rr, rr)
            p.setFont(self._font("icons", rr * 1.05, QFont.Weight.Bold)[0])
            p.setPen(white(1.0))
            p.drawText(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter,
                       G_CHECK if g.get("result") == "won" else ("" if g.get("result") != "lost" else G_CLOSE))
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 1.8 * s))
        p.drawEllipse(c, rr - 2 * s, rr - 2 * s)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p.drawLine(QPointF(c.x() + dx * (rr - 5 * s), c.y() + dy * (rr - 5 * s)),
                       QPointF(c.x() + dx * (rr + 0.5 * s), c.y() + dy * (rr + 0.5 * s)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(c, 1.6 * s, 1.6 * s)

    @staticmethod
    def _game_card_h(tiles: int, ended: bool) -> float:
        rows = max(1, (tiles + 2) // 3)
        return 14.0 + 20.0 + (34.0 if ended else 0.0) + 46.0 * rows + 10.0 + 30.0 + 14.0

    def _game_buttons(self) -> list[tuple[str, str]]:
        g = self.game or {}
        if g.get("state") == "ended":
            fix = [("g_fix_won", "It was a win"), ("g_fix_lost", "It was a loss")] if g.get("estimated") else []
            return fix + [("g_next", "New match"), ("g_dismiss", "Dismiss")]
        return [("g_add_kills", "+ Elim"), ("g_add_deaths", "+ Death"), ("g_add_assists", "+ Assist"),
                ("g_add_revives", "+ Revive"), ("g_end", "End…")]

    def _paint_game_card(self, p: QPainter, rect: QRectF) -> None:
        g = self.game
        if g is None:
            return
        self._last_card = "game"
        s = self.s
        x, right = rect.left() + 18 * s, rect.right() - 18 * s
        y = rect.top() + 14 * s
        hf, hfm = self._font("text", 12 * self.k, QFont.Weight.DemiBold)
        self._game_icon(p, QPointF(x + 7 * s, y + 9 * s), 7.5 * s)
        head = " · ".join(t for t in (g.get("game", ""), g.get("mode", ""), mmss(g.get("seconds", 0))) if t)
        if g.get("state") == "playing":
            killer = g.get("killer", "")
            side = ((f"Eliminated by {killer}" if killer else "Eliminated") if g.get("dead") else
                    ("Final round" if g.get("final") else (f"Round {g['round']}" if g.get("round", 1) > 1 else "")))
        else:
            side = "Best guess: check it" if g.get("estimated") else "Waiting for the next game"
        sw = hfm.horizontalAdvance(side) + 4 if side else 0
        p.setFont(hf)
        p.setPen(white(0.62))
        hx = x + 22 * s
        p.drawText(QRectF(hx, y, right - sw - 8 * s - hx, hfm.height()), Qt.AlignmentFlag.AlignLeft,
                   hfm.elidedText(head, Qt.TextElideMode.ElideRight, right - sw - 8 * s - hx))
        if side:
            p.setPen(QColor(GAME_DEAD if g.get("dead") else (AMBER if g.get("estimated") else GRAY)))
            p.drawText(QRectF(right - sw, y, sw, hfm.height()), Qt.AlignmentFlag.AlignRight, side)
        y += 20 * s
        if g.get("state") == "ended":
            bf, bfm = self._font("text", 20 * self.k, QFont.Weight.Bold)
            p.setFont(bf)
            p.setPen(QColor(self._game_color()))
            p.drawText(QRectF(x, y, right - x, 30 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       bfm.elidedText(g.get("headline", "Match over"), Qt.TextElideMode.ElideRight, right - x))
            y += 34 * s
        tiles = g.get("tiles", [])
        cw = (right - x) / 3
        vf, vfm = self._font("text", 18 * self.k, QFont.Weight.Bold)
        lf, lfm = self._font("text", 11 * self.k, QFont.Weight.Normal)
        for i, (label, value) in enumerate(tiles):
            tx, ty = x + (i % 3) * cw, y + (i // 3) * 46 * s
            p.setFont(vf)
            p.setPen(white(0.97))
            p.drawText(QRectF(tx, ty, cw - 6 * s, vfm.height()), Qt.AlignmentFlag.AlignLeft,
                       vfm.elidedText(value, Qt.TextElideMode.ElideRight, cw - 6 * s))
            p.setFont(lf)
            p.setPen(white(0.55))
            p.drawText(QRectF(tx, ty + vfm.height(), cw - 6 * s, lfm.height()), Qt.AlignmentFlag.AlignLeft,
                       lfm.elidedText(label, Qt.TextElideMode.ElideRight, cw - 6 * s))
        y += 46 * s * max(1, (len(tiles) + 2) // 3) + 10 * s
        buttons = self._game_buttons()
        gap = 6 * s
        bw = (right - x - gap * (len(buttons) - 1)) / max(1, len(buttons))
        bf12 = self._font("text", 12 * self.k, QFont.Weight.DemiBold)[1]
        for i, (key, label) in enumerate(buttons):
            br = QRectF(x + i * (bw + gap), y, bw, 30 * s)
            if br.bottom() > rect.bottom() - 4 * s:
                break                                # the card is still growing
            tint = BLUE if key == "g_next" else ""
            self._button(p, key, br, "", text=bf12.elidedText(label, Qt.TextElideMode.ElideRight, bw - 10 * s),
                         tint=tint)

    def _game_end_menu(self, m: QMenu | None = None) -> QMenu:
        """How the match ended, for End…: a result, a placement, or just stop."""
        menu = m or QMenu()
        menu.addAction("We won", lambda: self._game_cmd("end", ("won", 0)))
        menu.addAction("We lost", lambda: self._game_cmd("end", ("lost", 0)))
        for n in (2, 3, 4):
            menu.addAction(f"We came {('', '', '2nd', '3rd', '4th')[n]}", lambda n=n: self._game_cmd("end", ("place", n)))
        menu.addAction("Draw", lambda: self._game_cmd("end", ("draw", 0)))
        menu.addAction("Just end it", lambda: self._game_cmd("end", ("ended", 0)))
        return menu

    def _game_menu(self, m: QMenu) -> None:
        """Active Game Tracking in the notch's menu: everything there is to do by hand."""
        t = m.addMenu("Game tracking")
        g = self.game or {}
        if g.get("state") == "playing":
            for stat, label in (("kills", "elimination"), ("deaths", "death"), ("assists", "assist"),
                                ("revives", "revive")):
                t.addAction(f"Add an {label}" if label[0] in "aeiou" else f"Add a {label}",
                            lambda st=stat: self._game_cmd("add", st))
            sub = t.addMenu("Take one away")
            for stat, label in (("kills", "Elimination"), ("deaths", "Death"), ("assists", "Assist"),
                                ("revives", "Revive")):
                sub.addAction(label, lambda st=stat: self._game_cmd("remove", st))
            self._game_end_menu(t.addMenu("End the match"))
        else:
            if self.game_modes:
                sm = t.addMenu("Start a match")
                for key, name in self.game_modes:
                    sm.addAction(name, lambda k=key: self._game_cmd("start", k))
                sm.addAction("Some other mode", lambda: self._game_cmd("start", ""))
            else:
                t.addAction("Start a match", lambda: self._game_cmd("start", ""))
            if g.get("state") == "ended":
                fix = t.addMenu("That match was…")
                fix.addAction("A win", lambda: self._game_cmd("fix", ("won", 0)))
                fix.addAction("A loss", lambda: self._game_cmd("fix", ("lost", 0)))
                for n in (2, 3, 4):
                    fix.addAction(f"{('', '', '2nd', '3rd', '4th')[n]} place",
                                  lambda n=n: self._game_cmd("fix", ("place", n)))
                t.addAction("Dismiss the summary", lambda: self._game_cmd("dismiss"))
        t.addSeparator()
        t.addAction("Stop tracking" if self.game_enabled else "Start tracking",
                    lambda: self._game_cmd("enable", not self.game_enabled))
        t.addAction("Game tracking settings…", lambda: self._game_cmd("settings"))

    def _question_layout(self, w: float) -> dict:
        """The question card for a body `w` wide: where everything goes (offsets from its top-left) and its height."""
        s = self.s
        q = self.question()
        flags = int(Qt.TextFlag.TextWordWrap)
        x0, right = 58 * s, w - 16 * s
        tfm = self._font("text", 14 * self.k, QFont.Weight.DemiBold)[1]
        dfm = self._font("text", 12 * self.k, QFont.Weight.Normal)[1]
        text = q.text if q is not None else ""
        detail = q.detail if q is not None else ""
        top = 14 * s + 18 * s                        # under the "Claude Code · project" line
        th = tfm.boundingRect(QRectF(0, 0, right - x0, 1000), flags, text).height() if text else 0.0
        th = min(th, 3 * tfm.lineSpacing())
        dh = dfm.boundingRect(QRectF(0, 0, right - x0, 1000), flags, detail).height() if detail else 0.0
        dh = min(dh, 4 * dfm.lineSpacing())
        y = top + th + (6 * s + dh if detail else 0.0) + 12 * s
        labels = list(q.options) if q is not None else []
        keys = [f"q_opt_{i}" for i in range(len(labels))]
        if q is not None and q.allow_text:
            labels.append("Reply…")
            keys.append("q_reply")
        bh, gap = 30 * s, 8 * s
        buttons, bx = [], x0
        for key, label in zip(keys, labels):
            bw = min(right - x0, self._text_width(label, 12) + 28 * s)
            if bx > x0 and bx + bw > right:
                bx, y = x0, y + bh + gap
            buttons.append((key, label, QRectF(bx, y, bw, bh)))
            bx += bw + gap
        h = (y + bh if buttons else y) + 16 * s
        return {"th": th, "dh": dh, "top": top, "buttons": buttons,
                "h": max(96 * s, min(self.QUESTION_CARD[1] * s, h))}

    def _paint_question_card(self, p: QPainter, rect: QRectF) -> None:
        q = self.question()
        if q is None:
            return
        self._last_card = "question"
        s = self.s
        lay = self._question_layout(rect.width())
        x, right = rect.left() + 58 * s, rect.right() - 16 * s
        self._ask_badge(p, QPointF(rect.left() + 16 * s + 15 * s, rect.top() + 14 * s + 15 * s), 15 * s)
        bh = 26 * s
        self._button(p, "q_close", QRectF(right - bh, rect.top() + 10 * s, bh, bh), G_CLOSE)
        hf, hfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
        p.setFont(hf)
        p.setPen(white(0.6))
        more = f"  ·  {len(self.questions) - 1} more" if len(self.questions) > 1 else ""
        head = (f"{q.agent} · {q.where}" if q.where else q.agent) + " asks"
        hw = right - bh - 8 * s - x
        p.drawText(QRectF(x, rect.top() + 14 * s, hw, hfm.height()), Qt.AlignmentFlag.AlignLeft,
                   hfm.elidedText(head + more, Qt.TextElideMode.ElideRight, hw))
        flags = int(Qt.TextFlag.TextWordWrap)
        top = rect.top() + lay["top"]
        p.setFont(self._font("text", 14 * self.k, QFont.Weight.DemiBold)[0])
        p.setPen(white(0.97))
        p.drawText(QRectF(x, top, right - x, lay["th"] + 1), flags | int(Qt.AlignmentFlag.AlignLeft), q.text)
        if q.detail:
            p.setFont(self._font("text", 12 * self.k, QFont.Weight.Normal)[0])
            p.setPen(white(0.62))
            p.drawText(QRectF(x, top + lay["th"] + 6 * s, right - x, lay["dh"] + 1),
                       flags | int(Qt.AlignmentFlag.AlignLeft), q.detail)
        for i, (key, label, r) in enumerate(lay["buttons"]):
            tint = BLUE if i == 0 and key.startswith("q_opt") else ""
            if label == "Deny":
                tint = ""
            br = r.translated(rect.left(), rect.top())
            if br.bottom() > rect.bottom() - 4 * s:
                break                                # the card is still growing
            p.setFont(self._font("text", 12 * self.k, QFont.Weight.DemiBold)[0])
            self._button(p, key, br, "", text=self._font("text", 12 * self.k, QFont.Weight.DemiBold)[1].elidedText(
                label, Qt.TextElideMode.ElideRight, br.width() - 16 * s), tint=tint)

    def call_seconds(self) -> float:
        return max(0.0, time.time() - self.live.call[1]) if self.live.call else 0.0

    def _phone(self, p: QPainter, c: QPointF, rr: float, angle: float = 0.0, color: str = GREEN,
               hot: bool = False) -> None:
        """A phone on a coloured circle; turned 135° (and red) it's the hang-up sign."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color).lighter(118) if hot else QColor(color))
        p.drawEllipse(c, rr, rr)
        p.save()
        p.translate(c)
        p.rotate(angle)
        p.setFont(self._font("icons", rr * 1.05, QFont.Weight.Normal)[0])
        p.setPen(white(1.0))
        p.drawText(QRectF(-rr, -rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter, G_PHONE)
        p.restore()

    def _ring_angle(self) -> float:
        """The ringing phone's buzz: short shakes, then a pause."""
        if self.reduce_motion:
            return 0.0
        t = time.perf_counter() - self._t0
        return 14.0 * math.sin(t * 38.0) if (t % 1.2) < 0.55 else 0.0

    def _call_buttons_width(self) -> float:
        call = self.live.call
        labels = [label for _k, label in CALL_BUTTONS.get(call[0], [])] if call else []
        return sum(self._text_width(label, 12) + 26 * self.s + 8 * self.s for label in labels)

    def _paint_incoming_card(self, p: QPainter, rect: QRectF) -> None:
        inc = self.incoming()
        if inc is None:
            return
        self._last_card = "incoming"
        s = self.s
        app, who = inc
        cy = rect.top() + rect.height() / 2 + 2 * s
        self._phone(p, QPointF(rect.left() + 36 * s, cy), 17 * s, self._ring_angle())
        x = rect.left() + 66 * s
        rr = 18 * s
        accept = QPointF(rect.right() - 16 * s - rr, cy)
        decline = QPointF(accept.x() - 2 * rr - 12 * s, cy)
        text_w = decline.x() - rr - 10 * s - x
        sf, sfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
        tf, tfm = self._font("text", 18 * self.k, QFont.Weight.DemiBold)
        p.setFont(sf)
        p.setPen(white(0.62))
        p.drawText(QRectF(x, cy - tfm.height() / 2 - sfm.height() + 2 * s, text_w, sfm.height()),
                   Qt.AlignmentFlag.AlignLeft, sfm.elidedText(f"{app} · Incoming call" if who else "Incoming call",
                                                              Qt.TextElideMode.ElideRight, text_w))
        p.setFont(tf)
        p.setPen(white(0.97))
        p.drawText(QRectF(x, cy - tfm.height() / 2 + 4 * s, text_w, tfm.height()), Qt.AlignmentFlag.AlignLeft,
                   tfm.elidedText(who or app, Qt.TextElideMode.ElideRight, text_w))
        for key, c, angle, color in (("ring_decline", decline, 135.0, RED), ("ring_accept", accept, 0.0, GREEN)):
            self._buttons[key] = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
            self._phone(p, c, rr, angle, color, hot=self._hover_button == key)

    def _paint_call_card(self, p: QPainter, rect: QRectF) -> None:
        call = self.live.call
        if not call:
            return
        self._last_card = "call"
        s = self.s
        cy = rect.top() + rect.height() / 2 + 2 * s
        self._phone(p, QPointF(rect.left() + 34 * s, cy), 15 * s)
        x = rect.left() + 60 * s
        sf, sfm = self._font("text", 12 * self.k, QFont.Weight.Normal)
        tf, tfm = self._font("text", 20 * self.k, QFont.Weight.DemiBold)
        p.setFont(sf)
        p.setPen(white(0.62))
        p.drawText(QRectF(x, cy - tfm.height() / 2 - sfm.height() + 4 * s, 150 * s, sfm.height()),
                   Qt.AlignmentFlag.AlignLeft, f"{call[0]} call")
        p.setFont(tf)
        p.setPen(QColor(GREEN))
        p.drawText(QRectF(x, cy - tfm.height() / 2 + 6 * s, 150 * s, tfm.height()), Qt.AlignmentFlag.AlignLeft,
                   mmss(self.call_seconds()))
        bh, right = 28 * s, rect.right() - 16 * s
        for key, label in reversed(CALL_BUTTONS.get(call[0], [])):
            bw = self._text_width(label, 12) + 26 * s
            self._button(p, f"call_{key}", QRectF(right - bw, cy - bh / 2, bw, bh), "", text=label,
                         tint=RED if key == "leave" else "")
            right -= bw + 8 * s

    def _dial_mark(self, p: QPainter, c: QPointF, rr: float, alpha: float) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(white(0.6 * alpha), 1.6 * self.s))
        p.drawEllipse(c, rr, rr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(BLUE, alpha))
        p.drawEllipse(QPointF(c.x(), c.y() - rr), 2.3 * self.s, 2.3 * self.s)

    def _indicator(self, p: QPainter, c: QPointF) -> None:
        s = self.s
        acc = self.status_accent
        if self.status_progress is not None:
            r = 9.5 * s
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white(0.16), 2.4 * s))
            p.drawEllipse(c, r, r)
            p.setPen(QPen(acc, 2.4 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16,
                      int(-360 * 16 * max(0.0, min(1.0, self.prog.value))))
            return
        if self.status_kind in ("done", "error"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc)
            p.drawEllipse(c, 10 * s, 10 * s)
            p.setFont(self._font("icons", 11 * s, QFont.Weight.Bold)[0])
            p.setPen(white(1.0))
            p.drawText(QRectF(c.x() - 10 * s, c.y() - 10 * s, 20 * s, 20 * s), Qt.AlignmentFlag.AlignCenter,
                       "" if self.status_kind == "done" else "")
            return
        if self.status_kind in ("clip", "timer", "power", "music"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc)
            p.drawEllipse(c, 10 * s, 10 * s)
            glyph = {"clip": "", "timer": G_TIMER, "power": "", "music": G_MUSIC}[self.status_kind]
            p.setFont(self._font("icons", 11 * s, QFont.Weight.Normal)[0])
            p.setPen(white(1.0))
            p.drawText(QRectF(c.x() - 10 * s, c.y() - 10 * s, 20 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, glyph)
            return
        a = 1.0
        if self.status_kind == "pulse":
            a = 0.45 + 0.55 * (0.5 + 0.5 * math.sin((time.perf_counter() - self._t0) * 5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(acc, 0.28 * a))
        p.drawEllipse(c, 7.5 * s, 7.5 * s)
        p.setBrush(qcolor(acc, a))
        p.drawEllipse(c, 4.2 * s, 4.2 * s)

    # ------------------------------------------------------------------ mouse
    def enterEvent(self, ev) -> None:
        if not self.hovered:
            self.hovered = True
            self.approached.emit()
            self.hint_ready = False
            self._hint_timer.start(int(HOVER_HINT_SECONDS * 1000))
            if not self.status_text and not self._card_kind():
                self._prev_text = ""
                self.fade.snap(0.0)
                self.fade.target = 1.0
            self._refresh_backdrop()
            self._retarget()

    def leaveEvent(self, ev) -> None:
        self.hovered = False
        self.hint_ready = False
        self._hint_timer.stop()
        self._hover_button = ""
        self._retarget()

    def _button_at(self, pt: QPointF) -> str:
        if self.card.value < 0.6:
            return ""
        return next((k for k, r in self._buttons.items() if r.adjusted(-3, -3, 3, 3).contains(pt)), "")

    def mouseMoveEvent(self, ev) -> None:
        drag = self._drag
        if drag is None:
            b = self._button_at(ev.position())
            if b != self._hover_button:
                self._hover_button = b
                self.update()
            return
        g = ev.globalPosition()
        d = g - drag["start"]
        if not drag["moved"]:
            if math.hypot(d.x(), d.y()) < self.DRAG_START * self.s:
                return
            drag["moved"] = True
            self._hint_timer.stop()
            self.hint_ready = False
            self._retarget()
        now = time.monotonic()
        drag["samples"].append((now, g))
        while len(drag["samples"]) > 2 and now - drag["samples"][0][0] > 0.1:
            drag["samples"].pop(0)
        self._drag_to(g)

    def _drag_to(self, g: QPointF) -> None:
        """Follow the mouse: along its edge while docked (pulling away stretches it, then it tears off); anywhere
        once free (brought near any edge of the screen, it merges into that one)."""
        drag = self._drag
        s = self.s
        d = g - drag["start"]
        sr = self._screen_rect(g)
        e = self.edge
        if e is not None:
            side = e in ("left", "right")
            along = drag["pos"].y() + d.y() + self.height() / 2 if side else drag["pos"].x() + d.x() + self.width() / 2
            ax, ay = AWAY[e]
            away = (g.x() - drag["anchor"].x()) * ax + (g.y() - drag["anchor"].y()) * ay
            pos = self._edge_pos(e, along, sr)
            if away < self.TEAR * s:
                self.pull.snap(max(0.0, away) * 0.55)
                self._set_pos(pos.x(), pos.y())
                self._wake()
                return
            self._set_pos(pos.x(), pos.y())
            self._tear_off(sr)
            drag["pos"] = self._pos - d
            self._retarget()
            return
        x, y = drag["pos"].x() + d.x(), drag["pos"].y() + d.y()
        edge = self._edge_near(x, y, sr, self.DOCK_SNAP * s)
        if edge is not None:
            pos = self._merge(edge, x, y, sr)        # touched an edge: merge into it
            self._kick(-6.0)
            drag["anchor"] = QPointF(g)
            drag["pos"] = pos - d
            self._set_pos(pos.x(), pos.y())
            self._retarget()
            return
        x, y = self._clamp_free(x, y, sr)
        self._set_pos(x, y)
        self._wake()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.RightButton:
            self._menu()
            return
        if ev.button() == Qt.MouseButton.MiddleButton:
            self.go_home()                           # the scroll wheel's click: back to the middle
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        b = self._button_at(ev.position())
        if b in ("previous", "play_pause", "next"):
            self.media_command.emit(b)
            return
        if b == "ring_accept":
            self.accept_call()
            return
        if b == "ring_decline":
            self.decline_call()
            return
        if b.startswith("call_") and self.live.call:
            self.call_command.emit(self.live.call[0], b[5:])
            return
        if b == "timer_cancel":
            self.cancel_timer()
            return
        if b == "timer_add":
            self.add_time(60)
            return
        if b == "answer_copy":
            self.copy_answer()
            return
        if b == "answer_close":
            self.clear_answer()
            return
        if b.startswith("q_opt_"):
            q = self.question()
            i = int(b[6:])
            if q is not None and i < len(q.options):
                self.answer_question(q.options[i])
            return
        if b == "q_reply":
            self.open_reply()
            return
        if b == "q_close":
            self.answer_question(None)               # the agent asks the usual way instead
            return
        if b.startswith("g_add_"):
            self._game_cmd("add", b[6:])
            return
        if b == "g_end":
            self._game_end_menu().exec(QCursor.pos())
            return
        if b in ("g_fix_won", "g_fix_lost"):
            self._game_cmd("fix", (b[6:], 0))
            return
        if b == "g_next":
            self._game_cmd("start", "")
            return
        if b == "g_dismiss":
            self._game_cmd("dismiss")
            return
        # a click or the start of a drag: which one is decided when it moves or lets go
        g = ev.globalPosition()
        self._vel = self._glide = None
        self._drag = {"start": g, "pos": QPointF(self._pos), "anchor": QPointF(g), "moved": False,
                      "samples": [(time.monotonic(), g)]}
        self.press.snap(1.0)
        self.press.target = 0.0
        self._wake()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or self._drag is None:
            return
        drag, self._drag = self._drag, None
        if not drag["moved"]:
            if self._card_kind() == "question":
                return                               # answered with its buttons, not a click anywhere on it
            if self.answer is not None:
                self.clear_answer()                  # a click puts the answer away
            else:
                self.clicked.emit()
            return
        # let go: how fast was it going?
        (t0, p0), (t1, p1) = drag["samples"][0], drag["samples"][-1]
        dt = t1 - t0
        fresh = dt > 0.012 and time.monotonic() - t1 < 0.08      # still moving when it let go
        v = QPointF((p1.x() - p0.x()) / dt, (p1.y() - p0.y()) / dt) if fresh else QPointF()
        e = self.edge
        if e is not None:
            ax, ay = AWAY[e]
            if v.x() * ax + v.y() * ay > 900 and self.pull.value > 8 * self.s:
                self._tear_off(self._screen_rect())     # flicked off the edge: tear off and fly
            else:
                self.pull.target = 0.0               # snaps back into the edge
                v = QPointF(v.x(), 0.0) if e in ("top", "bottom") else QPointF(0.0, v.y())
        if math.hypot(v.x(), v.y()) > 250 and not self.reduce_motion:
            limit = 4500.0
            self._vel = QPointF(max(-limit, min(limit, v.x())), max(-limit, min(limit, v.y())))
            self._moving = True
            self._wake()
        else:
            self._settle_move()

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta()
        self._wheel += d.y() if abs(d.y()) >= abs(d.x()) else -d.x()
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel < 0 else -1          # wheel down = next item, like scrolling a list
            self._wheel += 120 if self._wheel < 0 else -120
            self.scrolled.emit(step)
        ev.accept()

    def _menu(self) -> None:
        m = QMenu()
        m.addAction("Open the rotary", self.clicked.emit)
        m.addAction("Solve what's on screen", self.assist.emit)
        m.addAction("Ask with a note…", self.assist_note.emit)
        q = self.question()
        if q is not None:
            m.addSeparator()
            for opt in q.options:
                m.addAction(f"Answer {q.agent}: {opt}", lambda o=opt: self.answer_question(o))
            if q.allow_text:
                m.addAction(f"Reply to {q.agent}…", self.open_reply)
            m.addAction("Answer it in the app instead", lambda: self.answer_question(None))
        if self.answer is not None:
            m.addAction("Copy the answer", self.copy_answer)
            m.addAction("Dismiss the answer", self.clear_answer)
        m.addSeparator()
        self._game_menu(m)
        t = m.addMenu("Timer")
        if self.timer_end is not None:
            t.addAction(f"{mmss(self.timer_left())} left — add 1 minute", lambda: self.add_time(60))
            t.addAction("Cancel timer", self.cancel_timer)
            t.addSeparator()
        for minutes in (1, 3, 5, 10, 15, 25, 30, 45, 60):
            t.addAction(f"{minutes} minute{'s' if minutes > 1 else ''}",
                        lambda mins=minutes: self.start_timer(mins * 60))
        if self.media() is not None:
            m.addSeparator()
            md = self.media()
            m.addAction("Pause" if md.playing else "Play", lambda: self.media_command.emit("play_pause"))
            m.addAction("Next", lambda: self.media_command.emit("next"))
            m.addAction("Previous", lambda: self.media_command.emit("previous"))
        inc = self.incoming()
        if inc is not None:
            m.addSeparator()
            m.addAction(f"Accept the {inc[0]} call", self.accept_call)
            m.addAction("Decline", self.decline_call)
        elif self.live.call and any(k == "leave" for k, _l in CALL_BUTTONS.get(self.live.call[0], [])):
            app = self.live.call[0]
            m.addSeparator()
            m.addAction(f"Leave the {app} call", lambda: self.call_command.emit(app, "leave"))
        m.addSeparator()
        if not self.is_home() or not self.docked:
            m.addAction("Put the notch back in the middle", self.go_home)
        m.addAction("Customize the notch…", self.customize.emit)
        m.exec(QCursor.pos())


class ReplyBox(QWidget):
    """Reply…: a small dark box beside the notch to type an answer to an agent's question. Enter sends it, Esc or
    clicking elsewhere puts it away. Unlike the notch it takes the keyboard, so it can be typed in."""
    sent = Signal(str, str)          # (question id, the reply)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Reply from the notch")
        self.qid = ""
        frame = QFrame(self)
        frame.setObjectName("reply")
        frame.setStyleSheet(
            "#reply { background: rgba(14, 14, 18, 240); border: 1px solid rgba(255, 255, 255, 40);"
            " border-radius: 16px; }"
            "QLabel { color: rgba(255, 255, 255, 160); background: transparent; }"
            "QLineEdit { color: white; background: rgba(255, 255, 255, 18); border: 1px solid rgba(255, 255, 255, 50);"
            " border-radius: 10px; padding: 7px 10px; selection-background-color: #0A84FF; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type your answer, then press Enter")
        self.edit.setAccessibleName("Your answer")
        self.edit.returnPressed.connect(self._send)
        lay.addWidget(self.title)
        lay.addWidget(self.edit)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def open_for(self, q, notch: QRectF, above: bool, s: float = 1.0) -> None:
        """Show it for question `q` just past the notch's body (`notch`, in screen coordinates)."""
        self.qid = q.id
        self.title.setText(f"Reply to {q.agent}: {q.text}")
        self.title.setFont(font(TEXT, 12 * s, QFont.Weight.Normal))
        self.edit.setFont(font(TEXT, 13.5 * s, QFont.Weight.Normal))
        self.edit.clear()
        w = int(max(320 * s, notch.width()))
        self.setFixedWidth(w)
        self.adjustSize()
        x = int(notch.center().x() - w / 2)
        y = int(notch.top() - self.height() - 8 * s) if above else int(notch.bottom() + 8 * s)
        scr = QGuiApplication.screenAt(notch.center().toPoint()) or QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            x = max(g.left() + 8, min(x, g.right() - w - 8))
            y = max(g.top() + 8, min(y, g.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            from .. import winapi
            winapi.focus_window(int(self.winId()))     # the notch never takes the focus, so this box has to
        self.edit.setFocus()

    def _send(self) -> None:
        text = self.edit.text().strip()
        if text:
            self.hide()
            self.sent.emit(self.qid, text)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(ev)

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, lambda: self.isActiveWindow() or self.hide())   # clicked somewhere else
