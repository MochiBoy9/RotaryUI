"""Liquid-glass drawing helpers shared by the overlay windows."""
from __future__ import annotations

import ctypes
import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QFontDatabase, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QWidget


# --------------------------------------------------------------------------- springs
class Spring:
    """Damped spring (semi-implicit Euler with sub-steps)."""

    def __init__(self, value: float = 0.0, stiffness: float = 220.0, damping: float = 24.0):
        self.value = self.target = value
        self.velocity = 0.0
        self.k, self.c = stiffness, damping

    def step(self, dt: float) -> None:
        n = max(1, int(dt / 0.004) + 1)
        h = dt / n
        for _ in range(n):
            a = self.k * (self.target - self.value) - self.c * self.velocity
            self.velocity += a * h
            self.value += self.velocity * h

    def snap(self, v: float) -> None:
        self.value = self.target = v
        self.velocity = 0.0

    @property
    def settled(self) -> bool:
        return abs(self.target - self.value) < 1e-3 and abs(self.velocity) < 1e-3


MAX_FPS = 60.0      # springs look as smooth at 60 frames a second; drawing faster on a 120/144 Hz screen only costs CPU


def frame_ms(screen) -> int:
    """Milliseconds between animation frames on this screen: one per refresh, at most MAX_FPS."""
    hz = (screen.refreshRate() if screen is not None else 60.0) or 60.0
    return max(4, int(1000.0 / max(30.0, min(hz, MAX_FPS))))


def smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


# --------------------------------------------------------------------------- fonts
_families: set[str] | None = None
BUNDLED_TEXT = "Inter Variable"     # assets/InterVariable.ttf (SIL OFL 1.1): closest open match to SF Pro
SYSTEM_UI = "<system>"              # stands for the desktop's own UI font (San Francisco on macOS)
SYSTEM_TEXT = {"darwin": ["SF Pro Text", SYSTEM_UI],
               "win32": ["Segoe UI Variable Text", "Segoe UI"]}.get(sys.platform, [SYSTEM_UI])
SYSTEM_DISPLAY = {"darwin": ["SF Pro Display", SYSTEM_UI],
                  "win32": ["Segoe UI Variable Display", "Segoe UI"]}.get(sys.platform, [SYSTEM_UI])
FALLBACK = ["Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "DejaVu Sans"]


def load_fonts() -> None:
    """Load the bundled fonts: Inter (the app's text face, unless you pick another in General ›
    Appearance) and, without Windows' Segoe Fluent Icons (macOS, Linux), Rotary Icons: the same glyphs at
    the same code points, drawn from Fluent UI System Icons."""
    global _families
    from .. import ROOT
    _families = set(QFontDatabase.families())
    wanted = [("InterVariable.ttf", BUNDLED_TEXT)]
    if not {"Segoe Fluent Icons", "Segoe MDL2 Assets", "Rotary Icons"} & _families:
        wanted.append(("rotary-icons.ttf", "Rotary Icons"))
    for file, family in wanted:
        path = ROOT / "assets" / file
        if family not in _families and path.exists():
            QFontDatabase.addApplicationFont(str(path))
    _families = set(QFontDatabase.families())


def font(families: list[str], px: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    fam = family(families)
    f = QFont(fam)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if fam == BUNDLED_TEXT and families is DISPLAY and px >= 20 and hasattr(f, "setVariableAxis"):
        f.setVariableAxis(QFont.Tag("opsz"), 32.0)     # Inter's display cut: tighter, like SF Pro Display
    return f


# The text faces everything draws with. Edited in place by set_text_font(), so modules that imported
# them (the rotary, the notch, the pill) follow a change of font without importing again.
DISPLAY: list[str] = []
TEXT: list[str] = []
ICONS = ["Segoe Fluent Icons", "Segoe MDL2 Assets", "Rotary Icons"]


def font_choices() -> list[tuple[str, str]]:
    """(key, label) for the font picker: the two built-in looks, then every installed family."""
    if _families is None:
        load_fonts()
    sys_name = {"darwin": "San Francisco", "win32": "Segoe UI"}.get(sys.platform, "System font")
    out = [("", "Inter (default)" if sys.platform != "darwin" else "San Francisco (default)"),
           ("system" if sys.platform != "darwin" else "inter", sys_name if sys.platform != "darwin" else "Inter")]
    skip = {BUNDLED_TEXT, "Rotary Icons", *ICONS}
    out += [(f, f) for f in sorted(_families or (), key=str.casefold)
            if f not in skip and not f.startswith(".") and not QFontDatabase.isPrivateFamily(f)
            and QFontDatabase.writingSystems(f).count(QFontDatabase.WritingSystem.Latin)]
    return out


def set_text_font(choice: str = "") -> None:
    """Point TEXT / DISPLAY at the chosen family. "" is the default look: SF Pro on macOS (the system
    font), Inter everywhere else. "inter" / "system" pick those; anything else is an installed family."""
    mac = sys.platform == "darwin"
    if choice == "system" or (choice == "" and mac):
        text, display = SYSTEM_TEXT + [BUNDLED_TEXT], SYSTEM_DISPLAY + [BUNDLED_TEXT]
    elif choice in ("", "inter"):
        text = display = [BUNDLED_TEXT, "Inter"] + SYSTEM_TEXT
    else:
        text = display = [choice, BUNDLED_TEXT] + SYSTEM_TEXT
    TEXT[:] = text + FALLBACK
    DISPLAY[:] = display + FALLBACK


def family(families: list[str] | None = None) -> str:
    """The first of `families` (default: TEXT) that's installed: what a style sheet should name."""
    if _families is None:
        load_fonts()
    for f in families or TEXT:
        if f == SYSTEM_UI:
            break
        if f in _families:
            return f
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


set_text_font("")


# --------------------------------------------------------------------------- backdrop
def blur_backdrop(pix: QPixmap):
    """Frosted copy of a screen grab, ready for liquid-glass rendering (see liquid.py)."""
    from .liquid import Backdrop
    return Backdrop.from_pixmap(pix)


def grab_behind(widget: QWidget) -> QPixmap | None:
    screen = widget.screen()
    if screen is None:
        return None
    g = widget.geometry()
    sg = screen.geometry()
    pix = screen.grabWindow(0, g.x() - sg.x(), g.y() - sg.y(), g.width(), g.height())
    return None if pix.isNull() else pix


def exclude_from_capture(widget: QWidget, on: bool = True) -> bool:
    """Hide a window from screen capture (Windows 10 2004+), so live glass never sees itself.
    (Not available on macOS / Linux: there, live glass may see a frame of its own window.)"""
    if sys.platform != "win32":
        return False
    try:
        WDA_EXCLUDEFROMCAPTURE = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()),
                                                                   WDA_EXCLUDEFROMCAPTURE if on else 0))
    except Exception:
        return False


def no_activate(widget: QWidget) -> None:
    """Clicking the window never makes it the active one (Windows: WS_EX_NOACTIVATE), so the app in front,
    a full-screen game or video say, keeps the focus and stays full screen. Call once the window is shown."""
    if sys.platform != "win32":
        return            # macOS: a Qt tool window that doesn't accept focus is already a non-activating panel
    try:
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(int(widget.winId()))
        GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_NOACTIVATE:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(style | WS_EX_NOACTIVATE))
    except Exception:
        pass


def refuse_activation(message) -> tuple[bool, int]:
    """For a QWidget.nativeEvent: answer Windows' WM_MOUSEACTIVATE with 'take the click, don't activate'."""
    if sys.platform != "win32":
        return False, 0
    try:
        from ctypes import wintypes
        if wintypes.MSG.from_address(int(message)).message == 0x0021:      # WM_MOUSEACTIVATE
            return True, 3                                                 # MA_NOACTIVATE
    except Exception:
        pass
    return False, 0


def overlay_flags(widget: QWidget) -> None:
    widget.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                          | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                          | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


# --------------------------------------------------------------------------- painting
def qcolor(c: str | QColor, alpha: float | None = None) -> QColor:
    q = QColor(c)
    if alpha is not None:
        q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def white(a: float) -> QColor:
    return qcolor("#ffffff", a)


def black(a: float) -> QColor:
    return qcolor("#000000", a)


def rim_gradient(center: QPointF, strength: float = 1.0, light_deg: float = 125.0) -> QConicalGradient:
    """Specular rim: bright where the light hits (top-left) and a softer bounce opposite."""
    g = QConicalGradient(center, light_deg)
    s = strength
    g.setColorAt(0.00, white(0.95 * s))
    g.setColorAt(0.12, white(0.40 * s))
    g.setColorAt(0.28, white(0.10 * s))
    g.setColorAt(0.50, white(0.55 * s))
    g.setColorAt(0.70, white(0.10 * s))
    g.setColorAt(0.88, white(0.40 * s))
    g.setColorAt(1.00, white(0.95 * s))
    return g


def stroke_rim(p: QPainter, path: QPainterPath, center: QPointF, width: float = 1.3,
               strength: float = 1.0, light_deg: float = 125.0) -> None:
    pen = QPen(rim_gradient(center, strength, light_deg), width)
    p.strokePath(path, pen)


def circle(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


def glass_disc(p: QPainter, c: QPointF, r: float, tint: QColor, sheen: float = 1.0) -> None:
    """Body shading for a convex glass disc (no backdrop): tint, top sheen, edge thickness."""
    path = circle(c.x(), c.y(), r)
    p.fillPath(path, tint)
    lg = QLinearGradient(c.x(), c.y() - r, c.x(), c.y() + r)
    lg.setColorAt(0.0, white(0.20 * sheen))
    lg.setColorAt(0.45, white(0.04 * sheen))
    lg.setColorAt(1.0, white(0.08 * sheen))
    p.fillPath(path, lg)
    rg = QRadialGradient(c, r)
    rg.setColorAt(0.80, white(0.0))
    rg.setColorAt(0.97, white(0.10 * sheen))
    rg.setColorAt(1.00, white(0.20 * sheen))
    p.fillPath(path, rg)


def soft_shadow(p: QPainter, c: QPointF, r: float, spread: float, alpha: float, dy: float = 10.0) -> None:
    rg = QRadialGradient(QPointF(c.x(), c.y() + dy), r + spread)
    k = r / (r + spread)
    rg.setColorAt(0.0, black(alpha))
    rg.setColorAt(k * 0.92, black(alpha))
    rg.setColorAt(1.0, black(0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(rg)
    p.drawEllipse(QPointF(c.x(), c.y() + dy), r + spread, r + spread)


def angle_point(r: float, deg_from_top: float) -> QPointF:
    a = math.radians(deg_from_top)
    return QPointF(r * math.sin(a), -r * math.cos(a))


# --------------------------------------------------------------------------- app icons
_icon_cache: dict = {}


def app_icon(path: str, px: int) -> QPixmap | None:
    """The real icon of an exe / shortcut / image file, or None."""
    import os

    from PySide6.QtCore import QFileInfo, QSize
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QFileIconProvider
    key = (path, px)
    if key in _icon_cache:
        return _icon_cache[key]
    pm = None
    try:
        p = os.path.expandvars(path)
        if os.path.exists(p):
            if p.lower().endswith((".png", ".ico", ".jpg", ".jpeg", ".svg", ".bmp")):
                pm = QIcon(p).pixmap(QSize(px, px))
            else:
                pm = QFileIconProvider().icon(QFileInfo(p)).pixmap(QSize(px, px))
            if pm is not None and pm.isNull():
                pm = None
    except Exception:
        pm = None
    _icon_cache[key] = pm
    return pm
