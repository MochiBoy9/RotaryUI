"""The RotaryUI window.

Pages: Welcome (first run), Rotary (items + live preview), Apps (integrations & Quick nav),
Controls (gestures), Accessibility, Camera, General. Every change applies immediately and is
saved to config.json. The window lives on the taskbar: closing it minimises instead of quitting."""
from __future__ import annotations

import copy
import ctypes
import itertools
import logging
import os
import sys
import threading

from PySide6.QtCore import QEvent, QFileInfo, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QIcon, QImage, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPixmap, QRadialGradient, QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFileIconProvider,
                               QGridLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import DATA_DIR, DEVICE, MAC, OS_NAME, ROOT, WIN, __version__, apps, integrations, obs, system, update, winapi
from .. import assist as ai
from ..actions import open_target
from ..actions import ACTION_TYPES, ASSIST_ITEM, CONFIG_PATH, MEDIA_PRESETS, Item, build_items
from ..gestures import EngineSnapshot, GestureSettings, Phase
from . import widgets
from .camera_view import VideoView
from ..live import clip_folders
from .dial import DialPainter
from .notch import DEFAULT_OPTIONS as DEFAULT_ISLAND
from .notch import system_24h
from .glass import BUNDLED_TEXT, ICONS, SYSTEM_TEXT, TEXT, app_icon, blur_backdrop, font, font_choices, white
from .widgets import (Card, ColorPicker, GlyphPicker, PresetTile, Row, Segmented, SliderControl, Switch,
                      icon_button, scroll_page, stylesheet)

log = logging.getLogger(__name__)
_uid = itertools.count(1)
INTENTS = ("relaxed", "balanced", "strict")

# ---------------------------------------------------------------------------- presets
PRESETS = [
    ("standard", "E815", "Standard", "Grab, or hold your palm still, to open. Tilt to choose. "
     "Pinch, point or hold still to launch.", "#0A84FF",
     {"grip_summon": True, "still_summon": True, "still_seconds": 3.0, "turn_style": "tilt", "scroll_speed": 1.0,
      "dwell_seconds": 1.6, "pinch_click": True, "finger_swipe": True, "finger_push": True, "large_move": 0.45,
      "idle_timeout": 15.0}),
    ("palm", "EB68", "Palm only", "No fist or pinch. Raise your palm for a second to open, slide it sideways "
     "to choose, hold still to launch.", "#30D158",
     {"grip_summon": False, "still_summon": True, "still_seconds": 1.0, "turn_style": "move", "move_distance": 0.8,
      "scroll_speed": 0.8, "dwell_seconds": 1.2, "pinch_click": False, "finger_swipe": False,
      "finger_push": False, "large_move": 0.45, "idle_timeout": 20.0}),
    ("move", "E8AB", "No wrist turning", "Like Standard, but slide your hand left or right instead of "
     "tilting it.", "#FF9F0A",
     {"grip_summon": True, "still_summon": True, "still_seconds": 2.0, "turn_style": "move", "move_distance": 0.8,
      "scroll_speed": 1.0, "dwell_seconds": 1.6, "pinch_click": True, "finger_swipe": True, "finger_push": True,
      "large_move": 0.45, "idle_timeout": 15.0}),
    ("gentle", "E81C", "Gentle", "Slower, roomier and forgiving of shaky or slow movement. Holding still "
     "launches.", "#BF5AF2",
     {"grip_summon": False, "still_summon": True, "still_seconds": 2.0, "turn_style": "move", "move_distance": 1.1,
      "scroll_speed": 0.6, "dwell_seconds": 2.2, "pinch_click": False, "finger_swipe": False,
      "finger_push": False, "large_move": 0.75, "idle_timeout": 30.0}),
]
HOTKEYS = [("ctrl+alt+space", "Ctrl + Alt + Space"), ("ctrl+shift+space", "Ctrl + Shift + Space"),
           ("ctrl+alt+r", "Ctrl + Alt + R"), ("win+alt+space", "Win + Alt + Space"), ("off", "Off")]
if MAC:
    HOTKEYS = [("ctrl+alt+space", "⌃⌥Space  (Control + Option + Space)"),
               ("ctrl+shift+space", "⌃⇧Space  (Control + Shift + Space)"), ("ctrl+alt+r", "⌃⌥R  (Control + Option + R)"),
               ("win+alt+space", "⌥⌘Space  (Option + Command + Space)"), ("off", "Off")]
elif not WIN:
    HOTKEYS[3] = ("win+alt+space", "Super + Alt + Space")
MOUSE_BUTTONS = [("middle", "Hold the middle button (wheel)"), ("back", "Back side button"),
                 ("forward", "Forward side button"), ("off", "Off")]
# Screen assist. Ctrl + Alt + a letter is AltGr on many Windows keyboards (Polish ą…), so it isn't the default there.
ASSIST_HOTKEYS = [("win+alt+a", "Win + Alt + A" if WIN else "Super + Alt + A"), ("ctrl+alt+a", "Ctrl + Alt + A"),
                  ("ctrl+shift+a", "Ctrl + Shift + A"), ("off", "Off")]
if MAC:
    ASSIST_HOTKEYS = [("ctrl+alt+a", "⌃⌥A  (Control + Option + A)"), ("win+alt+a", "⌥⌘A  (Option + Command + A)"),
                      ("ctrl+shift+a", "⌃⇧A  (Control + Shift + A)"), ("off", "Off")]
if WIN:
    KEY_PRESETS = [("win+d", "Show desktop"), ("win+tab", "Task view"), ("win+shift+s", "Snip screen"),
                   ("ctrl+shift+esc", "Task Manager"), ("alt+tab", "Switch window"), ("win+e", "File Explorer"),
                   ("win+a", "Quick settings"), ("win+v", "Clipboard history"), ("win+.", "Emoji panel"),
                   ("win+g", "Xbox Game Bar")]
elif MAC:
    KEY_PRESETS = [("ctrl+up", "Mission Control"), ("cmd+space", "Spotlight"), ("cmd+shift+5", "Screenshot"),
                   ("cmd+tab", "Switch app"), ("ctrl+cmd+q", "Lock screen"), ("ctrl+cmd+space", "Emoji & symbols"),
                   ("cmd+alt+esc", "Force quit"), ("cmd+shift+a", "Applications folder")]
else:
    KEY_PRESETS = [("win", "Overview"), ("printscreen", "Screenshot"), ("alt+tab", "Switch window"),
                   ("win+l", "Lock screen"), ("ctrl+alt+t", "Terminal")]


def active_preset(g: dict) -> str | None:
    d = GestureSettings.from_dict(g).to_dict()
    for key, *_rest, values in PRESETS:
        if all(abs(float(d.get(k)) - v) < 1e-6 if isinstance(v, float) else d.get(k) == v
               for k, v in values.items()):
            return key
    return None


def glyph_tile(code: str, color: str, px: int = 28) -> QIcon:
    """macOS-Settings style tile: white symbol on a coloured rounded square."""
    pm = QPixmap(px * 2, px * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0, 0, px, px)
    g = QLinearGradient(0, 0, 0, px)
    c = QColor(color)
    g.setColorAt(0, c.lighter(118))
    g.setColorAt(1, c.darker(108))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, px * 0.24, px * 0.24)
    p.setPen(QColor("white"))
    p.setFont(font(ICONS, px * 0.55))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, chr(int(code, 16)))
    p.end()
    icon = QIcon(pm)
    icon.addPixmap(pm, QIcon.Mode.Selected)          # lists would otherwise tint the selected row's tile
    return icon


def app_tile(a, px: int = 28) -> QIcon:
    pm = app_icon(a.icon, px * 2) if a.icon else None
    return QIcon(pm) if pm is not None else glyph_tile(a.spec.glyph, a.spec.tint, px)


def action_summary(a: dict) -> str:
    t = a.get("type", "")
    if t == "app":
        tgt = str(a.get("target", ""))
        if tgt.lower().startswith("shell:appsfolder") or "://" in tgt:
            return "App"
        return "App · " + (os.path.splitext(os.path.basename(tgt))[0] or "not chosen")
    if t == "quicknav":
        spec = integrations.BY_ID.get(a.get("app", ""))
        return "Quick nav · " + (spec.name if spec else "app")
    if t in ("browser", "url"):
        return "Website · " + (a.get("url") or "browser home")
    if t == "open":
        return "Open · " + (a.get("target") or "—")
    if t == "keys":
        k = a.get("keys", [])
        k = k if isinstance(k, str) else "+".join(k)
        return "Shortcut · " + (MEDIA_PRESETS.get(k, k.replace("+", " + ").title()) or "—")
    if t == "window_keys":
        return f"Shortcut in {a.get('label') or a.get('process', 'an app')}"
    if t == "obs":
        return "OBS · " + obs.REQUESTS.get(a.get("request", ""), "command")
    if t == "shell":
        return "Command · " + (a.get("command") or "—")
    if t == "powershell":
        return "PowerShell script"
    if t == "python":
        return "Python code"
    if t == "assist":
        return "Screen assist · AI answers what's on screen"
    return t


def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 6)
    v.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("h1")
    t.setAccessibleName(title)
    s = QLabel(subtitle)
    s.setObjectName("muted")
    s.setWordWrap(True)
    v.addWidget(t)
    v.addWidget(s)
    return w


# =========================================================================== live dial preview
class DialPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(340)
        self.dp = DialPainter([], 600)
        self._wall: QImage | None = None
        self._sel = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._started = False
        self.setAccessibleName("Rotary preview")

    def _wallpaper(self) -> QImage:
        S = self.dp.size
        img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, S, S)
        g.setColorAt(0, QColor("#16224f"))
        g.setColorAt(0.55, QColor("#5b2563"))
        g.setColorAt(1, QColor("#c46a3a"))
        p.fillRect(0, 0, S, S, g)
        for x, y, r, c in ((0.22, 0.25, 0.32, "#2f80ed"), (0.8, 0.7, 0.35, "#ff5f8f"), (0.65, 0.15, 0.2, "#ffd36b")):
            rg = QRadialGradient(QPointF(x * S, y * S), r * S)
            rg.setColorAt(0, QColor(c))
            rg.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(rg)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x * S, y * S), r * S, r * S)
        p.end()
        return img

    def configure(self, cfg: dict) -> None:
        ap = cfg.get("appearance", {})
        self.dp.set_accessibility(ap.get("text_scale", 1.0), ap.get("high_contrast", False),
                                  ap.get("reduce_motion", False))
        self.dp.controls = GestureSettings.from_dict(cfg.get("gestures")).to_dict()
        self.update()

    def set_items(self, items: list[Item], selected: int) -> None:
        self.dp.set_items(items)
        if self._wall is None:
            self._wall = self._wallpaper()
            self.dp.backdrop = blur_backdrop(QPixmap.fromImage(self._wall))
        self._sel = max(0, min(selected, len(items) - 1))
        st = self._state()
        if not self._started:
            self.dp.begin(st)
            self._started = True
        self.dp.set_state(st)
        self.dp._cur_sel = self.dp._prev_sel = self._sel
        self._wake()

    def select(self, i: int) -> None:
        n = max(1, len(self.dp.items))
        base = round(self.dp.rot.target)
        delta = (i - base % n + n // 2) % n - n // 2 if n > 1 else 0
        self._sel = i
        self.dp.set_state(self._state(position=base + delta))
        self._wake()

    def _state(self, position: float | None = None) -> EngineSnapshot:
        return EngineSnapshot(phase=Phase.OPEN, hand_present=True, facing=True, interacted=True,
                              position=float(self._sel if position is None else position), selected=self._sel)

    def _wake(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        self.dp.tick(1 / 60)
        self.update()
        if all(s.settled for s in (self.dp.appear, self.dp.rot, self.dp.title)) and self.dp.open_t > 1.0:
            self._timer.stop()

    def paintEvent(self, _):
        if self._wall is None or not self.dp.items:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        S = self.dp.size
        k = min(r.width(), r.height()) / (S * 0.82)
        side = S * k
        tgt = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
        p.drawImage(r, self._wall)
        p.fillRect(r, QColor(0, 0, 0, 30))
        p.save()
        p.translate(tgt.topLeft())
        p.scale(k, k)
        self.dp.paint(p, S, S)
        p.restore()


# =========================================================================== items list
class ItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(240, int(54 * widgets.UI_SCALE))

    def paint(self, p: QPainter, option, index) -> None:
        d = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        k = widgets.UI_SCALE
        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            path = QPainterPath()
            path.addRoundedRect(r, 9, 9)
            p.fillPath(path, QColor(10, 132, 255, 70))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            path = QPainterPath()
            path.addRoundedRect(r, 9, 9)
            p.fillPath(path, white(0.05))
        c = QPointF(r.left() + 26, r.center().y())
        rad = 16.0
        tint = QColor(d.get("tint", "#0A84FF"))
        icon = app_icon(d.get("icon", ""), 64) if d.get("icon") else None
        if icon is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(236, 238, 244))
            p.drawEllipse(c, rad, rad)
            s = rad * 1.25
            p.drawPixmap(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), icon, QRectF(icon.rect()))
        else:
            g = QLinearGradient(c.x(), c.y() - rad, c.x(), c.y() + rad)
            g.setColorAt(0, tint.lighter(125))
            g.setColorAt(1, tint.darker(110))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, rad, rad)
            p.setPen(QColor("white"))
            p.setFont(font(ICONS, 15))
            glyph = d.get("glyph", "E8A7")
            try:
                ch = chr(int(glyph, 16))
            except ValueError:
                ch = glyph[:1]
            p.drawText(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), Qt.AlignmentFlag.AlignCenter, ch)
        quick = (d.get("action") or {}).get("type") == "quicknav"
        tx = r.left() + 52
        tw = r.width() - 60 - (22 if quick else 0)
        tf = font(TEXT, 13.5 * k, QFont.Weight.DemiBold)
        sf = font(TEXT, 11.5 * k)
        th, sh = QFontMetricsF(tf).height(), QFontMetricsF(sf).height()
        top = r.center().y() - (th + sh) / 2
        p.setPen(white(0.95))
        p.setFont(tf)
        p.drawText(QRectF(tx, top, tw, th), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(tf).elidedText(d.get("name", ""), Qt.TextElideMode.ElideRight, tw))
        p.setFont(sf)
        p.setPen(white(0.5))
        p.drawText(QRectF(tx, top + th, tw, sh), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(sf).elidedText(action_summary(d.get("action", {})), Qt.TextElideMode.ElideRight, tw))
        if quick:
            p.setFont(font(ICONS, 12))
            p.setPen(white(0.45))
            p.drawText(QRectF(r.right() - 28, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, "\uE76C")
        p.restore()


# =========================================================================== pickers
class AppPicker(QDialog):
    """Searchable list of everything in Start → All apps (desktop and Store apps)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose an app")
        self.setStyleSheet(stylesheet() + "QDialog { background: #1d1d21; }")
        self.resize(440, 580)
        self.result_app = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps")
        self.search.setAccessibleName("Search apps")
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        self.list.setAccessibleName("Apps")
        lay.addWidget(self.list, 1)
        self._entries = [a for a in apps.start_apps() if not a.url.lower().startswith(("http:", "https:"))]
        self._entries.sort(key=lambda a: a.name.lower())
        self._lnks = apps.start_menu_shortcuts()
        self._prov = QFileIconProvider()
        for i, a in enumerate(self._entries):
            it = QListWidgetItem(a.name)
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setSizeHint(QSize(0, 34))
            self.list.addItem(it)
        self._next_icon = 0
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(0)
        self._icon_timer.timeout.connect(self._load_icons)
        self._icon_timer.start()
        self.search.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _: self._accept())
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Choose")
        ok.setObjectName("accent")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.search.setFocus()

    def _load_icons(self) -> None:
        """A few icons per event-loop turn so the list is usable immediately."""
        end = min(self._next_icon + 12, len(self._entries))
        for i in range(self._next_icon, end):
            a = self._entries[i]
            src = a.exe or self._lnks.get(a.name.lower(), "")
            if src:
                self.list.item(i).setIcon(self._prov.icon(QFileInfo(src)))
            else:
                self.list.item(i).setIcon(glyph_tile("E8A7", "#5E5CE6", 24))
        self._next_icon = end
        if end >= len(self._entries):
            self._icon_timer.stop()

    def _filter(self, t: str) -> None:
        t = t.lower().strip()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = bool(t) and t not in it.text().lower()
            it.setHidden(hide)
            if not hide and first is None:
                first = it
        if first is not None:
            self.list.setCurrentItem(first)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept()
            return
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.search.hasFocus():
            self.list.setFocus()
        super().keyPressEvent(e)

    def _accept(self) -> None:
        it = self.list.currentItem()
        if it is not None and not it.isHidden():
            a = self._entries[it.data(Qt.ItemDataRole.UserRole)]
            exe = a.exe
            if not exe and a.name.lower() in self._lnks:
                t = apps.shortcut_target(self._lnks[a.name.lower()])
                exe = t if t.lower().endswith(".exe") else ""
            self.result_app = (a.name, a.launch_target, exe)
            self.accept()


class GalleryDialog(QDialog):
    """Ready-made actions for the apps on this PC, and their Quick nav rings."""
    picked = Signal(list)

    def __init__(self, app, parent=None, start: str | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Add from your apps")
        self.setStyleSheet(stylesheet() + "QDialog { background: #18181c; }")
        self.resize(900, 620)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)
        self.list = QListWidget()
        self.list.setIconSize(QSize(26, 26))
        self.list.setFixedWidth(250)
        self.list.setAccessibleName("Apps")
        root.addWidget(self.list)
        right = QVBoxLayout()
        right.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("h1")
        self.blurb = QLabel()
        self.blurb.setObjectName("muted")
        self.blurb.setWordWrap(True)
        right.addWidget(self.title)
        right.addWidget(self.blurb)
        self.quick_btn = QPushButton()
        self.quick_btn.setObjectName("accent")
        self.quick_btn.clicked.connect(self._add_quicknav)
        right.addWidget(self.quick_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.actions_w = QWidget()
        self.actions_l = QVBoxLayout(self.actions_w)
        self.actions_l.setContentsMargins(0, 6, 8, 0)
        self.actions_l.setSpacing(6)
        right.addWidget(scroll_page(self.actions_w), 1)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        right.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(right, 1)

        self._ids: list[str | None] = []
        installed = {a.id: a for a in integrations.installed()}
        for cat in integrations.CATEGORIES:
            members = [s for s in integrations.CATALOG if s.category == cat and s.id in installed]
            if not members:
                continue
            h = QListWidgetItem(cat.upper())
            h.setFlags(Qt.ItemFlag.NoItemFlags)
            h.setForeground(QColor(255, 255, 255, 110))
            h.setFont(font(TEXT, 11, QFont.Weight.DemiBold))
            self.list.addItem(h)
            self._ids.append(None)
            for s in members:
                it = QListWidgetItem(app_tile(installed[s.id]), s.name)
                it.setSizeHint(QSize(0, 38))
                self.list.addItem(it)
                self._ids.append(s.id)
        self.list.currentRowChanged.connect(self._show)
        app.dynamic_ready.connect(self._dynamic_ready)
        rows = [i for i, x in enumerate(self._ids) if x is not None]
        first = next((i for i in rows if self._ids[i] == start), rows[0] if rows else -1)
        if first >= 0:
            self.list.setCurrentRow(first)

    def _current(self) -> str | None:
        r = self.list.currentRow()
        return self._ids[r] if 0 <= r < len(self._ids) else None

    def _show(self, _row: int) -> None:
        app_id = self._current()
        if not app_id:
            return
        a = integrations.get(app_id)
        self.title.setText(a.name)
        self.blurb.setText(a.spec.blurb or "Open it from the rotary, or add a Quick nav ring with its actions.")
        has = any((d.get("action") or {}).get("type") == "quicknav" and d["action"].get("app") == app_id
                  for d in self.app.cfg.get("items", []))
        self.quick_btn.setText(f"{'✓ ' if has else ''}Quick nav for {a.name}")
        self.quick_btn.setEnabled(not has)
        self.quick_btn.setToolTip("Adds one item that opens a ring of this app's actions")
        integrations.refresh_dynamic(app_id, self.app.cfg, self.app.dynamic_ready.emit)
        self._fill(app_id)

    def _fill(self, app_id: str) -> None:
        while self.actions_l.count():
            w = self.actions_l.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        acts = integrations.all_actions(app_id)
        for d in acts:
            frame = QWidget()
            frame.setObjectName("cardrow")
            frame.setStyleSheet("QWidget#cardrow { background: #232328; border-radius: 10px; }")
            h = QHBoxLayout(frame)
            h.setContentsMargins(12, 6, 8, 6)
            icon = QLabel()
            pm = app_icon(d.get("icon", ""), 56) if d.get("icon") else None
            if pm is not None:
                icon.setPixmap(pm.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            else:
                icon.setPixmap(glyph_tile(d.get("glyph", "E8A7"), d.get("tint", "#0A84FF"), 28).pixmap(28, 28))
            h.addWidget(icon)
            txt = QVBoxLayout()
            txt.setSpacing(0)
            n = QLabel(d["name"])
            n.setObjectName("rowtitle")
            txt.addWidget(n)
            if d.get("subtitle"):
                sub = QLabel(d["subtitle"])
                sub.setObjectName("muted")
                txt.addWidget(sub)
            h.addLayout(txt, 1)
            add = QPushButton("Add")
            add.setAccessibleName(f"Add {d['name']}")
            add.clicked.connect(lambda _=False, d=d, b=add: self._add(d, b))
            h.addWidget(add)
            self.actions_l.addWidget(frame)
        if not acts:
            self.actions_l.addWidget(QLabel("Nothing to add for this app yet."))
        self.actions_l.addStretch(1)

    def _dynamic_ready(self, app_id: str) -> None:
        if app_id == self._current():
            self._fill(app_id)

    def _add(self, d: dict, btn: QPushButton) -> None:
        self.picked.emit([copy.deepcopy(d)])
        btn.setText("Added")
        btn.setEnabled(False)

    def _add_quicknav(self) -> None:
        app_id = self._current()
        if app_id:
            self.picked.emit([integrations.quicknav_item(app_id)])
            self.quick_btn.setText(f"✓ Quick nav for {integrations.get(app_id).name}")
            self.quick_btn.setEnabled(False)


# =========================================================================== action editor
def _code_editor(placeholder: str) -> QPlainTextEdit:
    e = QPlainTextEdit()
    e.setPlaceholderText(placeholder)
    e.setMinimumHeight(150)
    e.setTabStopDistance(28)
    return e


class ActionEditor(QWidget):
    changed = Signal()
    app_picked = Signal(str, str, str)   # name, target, exe

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.card = Card("Action")
        lay.addWidget(self.card)
        self.type = QComboBox()
        for k, label in ACTION_TYPES.items():
            self.type.addItem(label, k)
        self.type.setMinimumWidth(250)
        self.card.add(Row("When chosen", None, self.type))
        self.stack = QStackedWidget()
        self.card.add(self.stack)
        self.pages: dict[str, QWidget] = {}

        def page() -> tuple[QWidget, QVBoxLayout]:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(14, 8, 14, 10)
            v.setSpacing(8)
            return w, v

        # app
        w, v = page()
        h = QHBoxLayout()
        self.app_target = QLineEdit()
        self.app_target.setPlaceholderText("Choose an app, or paste a path to a program")
        self.app_target.setAccessibleName("App")
        pick = QPushButton("Choose app…")
        pick.setObjectName("accent")
        pick.clicked.connect(self._pick_installed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_app)
        h.addWidget(self.app_target, 1)
        h.addWidget(pick)
        h.addWidget(browse)
        v.addLayout(h)
        self.app_args = QLineEdit()
        self.app_args.setPlaceholderText("Arguments (optional)")
        self.app_args.setAccessibleName("Arguments")
        v.addWidget(self.app_args)
        self._add_page("app", w)

        # quick nav
        w, v = page()
        self.qn_app = QComboBox()
        self.qn_app.setAccessibleName("App for Quick nav")
        v.addWidget(self.qn_app)
        note = QLabel("Choosing this item opens a second ring with the app's own actions — games, scenes, "
                      "recent projects and more. A Back item takes you out again.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self._add_page("quicknav", w)

        # browser
        w, v = page()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…   (leave empty to just open your browser)")
        self.url.setAccessibleName("Website address")
        v.addWidget(self.url)
        self._add_page("browser", w)

        # open
        w, v = page()
        h = QHBoxLayout()
        self.open_target = QLineEdit()
        self.open_target.setPlaceholderText("A file, folder or link — e.g. ms-settings:, mailto:, D:\\Music")
        self.open_target.setAccessibleName("File, folder or link")
        f_btn = QPushButton("File…")
        f_btn.clicked.connect(lambda: self._browse_open(False))
        d_btn = QPushButton("Folder…")
        d_btn.clicked.connect(lambda: self._browse_open(True))
        h.addWidget(self.open_target, 1)
        h.addWidget(f_btn)
        h.addWidget(d_btn)
        v.addLayout(h)
        self._add_page("open", w)

        # keys
        w, v = page()
        h = QHBoxLayout()
        self.keys = QLineEdit()
        self.keys.setPlaceholderText("e.g. win+d, ctrl+shift+esc, media_play_pause")
        self.keys.setAccessibleName("Keys")
        self.rec = QKeySequenceEdit()
        self.rec.setMaximumSequenceLength(1)
        self.rec.setToolTip("Click, then press a shortcut to record it")
        self.rec.setAccessibleName("Record a shortcut")
        self.rec.keySequenceChanged.connect(lambda seq: self._recorded(seq, self.keys, self.rec))
        h.addWidget(self.keys, 1)
        h.addWidget(QLabel("Record"))
        h.addWidget(self.rec)
        v.addLayout(h)
        self.preset = QComboBox()
        self.preset.addItem("Presets…", "")
        for k, label in [*MEDIA_PRESETS.items(), *KEY_PRESETS]:
            self.preset.addItem(label, k)
        self.preset.activated.connect(self._preset)
        self.preset.setAccessibleName("Shortcut presets")
        v.addWidget(self.preset, 0, Qt.AlignmentFlag.AlignLeft)
        self._add_page("keys", w)

        # window_keys
        w, v = page()
        h = QHBoxLayout()
        self.wk_app = QComboBox()
        self.wk_app.setAccessibleName("App")
        self.wk_app.setMinimumWidth(210)
        self.wk_proc = QLineEdit()
        self.wk_proc.setPlaceholderText("or its program name, e.g. Discord.exe" if WIN else "or its name, e.g. Discord")
        self.wk_proc.setAccessibleName("Program name")
        h.addWidget(self.wk_app)
        h.addWidget(self.wk_proc, 1)
        v.addLayout(h)
        h = QHBoxLayout()
        self.wk_keys = QLineEdit()
        self.wk_keys.setPlaceholderText("Shortcut, e.g. ctrl+shift+m")
        self.wk_keys.setAccessibleName("Shortcut")
        self.wk_rec = QKeySequenceEdit()
        self.wk_rec.setMaximumSequenceLength(1)
        self.wk_rec.setAccessibleName("Record a shortcut")
        self.wk_rec.keySequenceChanged.connect(lambda seq: self._recorded(seq, self.wk_keys, self.wk_rec))
        h.addWidget(self.wk_keys, 1)
        h.addWidget(QLabel("Record"))
        h.addWidget(self.wk_rec)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("Switch back to what I was doing afterwards"))
        h.addStretch(1)
        self.wk_restore = Switch(True)
        self.wk_restore.setAccessibleName("Switch back afterwards")
        self.wk_restore.toggled.connect(self._emit)
        h.addWidget(self.wk_restore)
        v.addLayout(h)
        self._add_page("window_keys", w)

        # obs
        w, v = page()
        h = QHBoxLayout()
        self.obs_req = QComboBox()
        for k, label in obs.REQUESTS.items():
            self.obs_req.addItem(label, k)
        self.obs_req.setAccessibleName("OBS command")
        self.obs_scene = QComboBox()
        self.obs_scene.setEditable(True)
        self.obs_scene.setAccessibleName("Scene name")
        self.obs_scene.lineEdit().setPlaceholderText("Scene name")
        h.addWidget(self.obs_req)
        h.addWidget(self.obs_scene, 1)
        v.addLayout(h)
        tip = QLabel("Works while OBS is closed (it starts OBS doing it). To control OBS while it's open, turn on "
                     "Tools → WebSocket Server Settings and add the password in Apps → OBS Studio.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self._add_page("obs", w)

        # shell
        w, v = page()
        self.command = QLineEdit()
        self.command.setPlaceholderText("cmd.exe command, e.g. shutdown /h" if WIN else
                                        "shell command, e.g. pmset sleepnow" if MAC else
                                        "shell command, e.g. systemctl suspend")
        self.command.setAccessibleName("Command")
        v.addWidget(self.command)
        h = QHBoxLayout()
        h.addWidget(QLabel("Show a console window"))
        h.addStretch(1)
        self.console = Switch(False)
        self.console.setAccessibleName("Show a console window")
        self.console.toggled.connect(self._emit)
        h.addWidget(self.console)
        v.addLayout(h)
        self._add_page("shell", w)

        # powershell / python
        w, v = page()
        self.ps = _code_editor("# PowerShell\nStart-Process notepad" if WIN else
                               "# PowerShell (pwsh must be installed)\nGet-Date")
        self.ps.setAccessibleName("PowerShell script")
        v.addWidget(self.ps)
        self._add_page("powershell", w)
        w, v = page()
        self.py = _code_editor("# Python — runs in its own process\n"
                               "import webbrowser\nwebbrowser.open('https://news.ycombinator.com')")
        self.py.setAccessibleName("Python code")
        v.addWidget(self.py)
        note = QLabel("Uses your own Python when it's installed (with your packages); otherwise the built-in one. "
                      "Errors show in the status pill.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self._add_page("python", w)

        # assist
        w, v = page()
        note = QLabel("An AI looks at your screen, finds the question or problem on it and answers on the notch "
                      "(for multiple choice: which option). Choose the AI service on the Screen assist page.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self._add_page("assist", w)

        for e in (self.app_target, self.app_args, self.url, self.open_target, self.keys, self.command,
                  self.wk_proc, self.wk_keys):
            e.textChanged.connect(self._emit)
        for e in (self.ps, self.py):
            e.textChanged.connect(self._emit)
        self.qn_app.currentIndexChanged.connect(self._emit)
        self.wk_app.currentIndexChanged.connect(self._wk_app_changed)
        self.obs_req.currentIndexChanged.connect(self._obs_req_changed)
        self.obs_scene.currentTextChanged.connect(self._emit)
        self.type.currentIndexChanged.connect(self._type_changed)
        self.refresh_apps()

    def refresh_apps(self) -> None:
        self._loading = True
        for combo in (self.qn_app, self.wk_app):
            cur = combo.currentData()
            combo.clear()
            if combo is self.wk_app:
                combo.addItem("Choose an app…", "")
            for a in sorted(integrations.installed(), key=lambda a: a.name.lower()):
                if combo is self.wk_app and not a.spec.process:
                    continue
                combo.addItem(app_tile(a, 18), a.name, a.id)
            i = combo.findData(cur)
            if i >= 0:
                combo.setCurrentIndex(i)
        self._loading = False

    def _add_page(self, key: str, w: QWidget) -> None:
        self.pages[key] = w
        self.stack.addWidget(w)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def _type_changed(self, _i):
        self.stack.setCurrentWidget(self.pages[self.type.currentData()])
        self.stack.setFixedHeight(self.stack.currentWidget().sizeHint().height())
        self._emit()

    def _wk_app_changed(self, _i):
        a = integrations.get(self.wk_app.currentData() or "")
        if a and not self._loading:
            self.wk_proc.setText(a.spec.process)
        self._emit()

    def _obs_req_changed(self, _i):
        is_scene = self.obs_req.currentData() == "SetCurrentProgramScene"
        self.obs_scene.setVisible(is_scene)
        if is_scene and self.obs_scene.count() == 0 and not self._loading:
            for name in obs.scenes(self.app.cfg, 0.5):
                self.obs_scene.addItem(name)
        self._emit()

    def set_action(self, a: dict) -> None:
        self._loading = True
        t = a.get("type", "app")
        t = "browser" if t == "url" else t
        if t not in self.pages:
            t = "app"
        self.type.setCurrentIndex(max(0, self.type.findData(t)))
        self.stack.setCurrentWidget(self.pages[t])
        self.stack.setFixedHeight(self.stack.currentWidget().sizeHint().height())
        self.app_target.setText(a.get("target", "") if t == "app" else "")
        self.app_args.setText(a.get("args", ""))
        self.qn_app.setCurrentIndex(max(0, self.qn_app.findData(a.get("app", ""))))
        self.url.setText(a.get("url", ""))
        self.open_target.setText(a.get("target", "") if t == "open" else "")
        k = a.get("keys", [])
        ks = k if isinstance(k, str) else "+".join(k)
        self.keys.setText(ks if t == "keys" else "")
        self.wk_keys.setText(ks if t == "window_keys" else "")
        self.wk_proc.setText(a.get("process", "") if t == "window_keys" else "")
        match = next((x.id for x in integrations.installed()
                      if x.spec.process and x.spec.process.lower() == str(a.get("process", "")).lower()), "")
        self.wk_app.setCurrentIndex(max(0, self.wk_app.findData(match)))
        self.wk_restore.set_silent(bool(a.get("restore", True)))
        self.obs_req.setCurrentIndex(max(0, self.obs_req.findData(a.get("request", "ToggleRecord"))))
        self.obs_scene.setEditText((a.get("data") or {}).get("sceneName", ""))
        self.obs_scene.setVisible(self.obs_req.currentData() == "SetCurrentProgramScene")
        self.command.setText(a.get("command", ""))
        self.console.set_silent(not a.get("hidden", True))
        self.ps.setPlainText(a.get("script", ""))
        self.py.setPlainText(a.get("code", ""))
        self._loading = False

    def action(self, old: dict) -> dict:
        t = self.type.currentData()
        a: dict = {"type": t}
        if t == "app":
            a["target"] = self.app_target.text().strip()
            if self.app_args.text().strip():
                a["args"] = self.app_args.text().strip()
            if old.get("type") == "app" and old.get("target") == a["target"]:
                for keep in ("fallback", "exe", "process"):
                    if old.get(keep):
                        a[keep] = old[keep]
        elif t == "quicknav":
            a["app"] = self.qn_app.currentData() or ""
        elif t == "browser":
            if self.url.text().strip():
                a["url"] = self.url.text().strip()
        elif t == "open":
            a["target"] = self.open_target.text().strip()
        elif t == "keys":
            a["keys"] = self.keys.text().replace(" ", "").lower()
        elif t == "window_keys":
            a["process"] = self.wk_proc.text().strip()
            a["keys"] = self.wk_keys.text().replace(" ", "").lower()
            a["restore"] = self.wk_restore.isChecked()
            spec = integrations.BY_ID.get(self.wk_app.currentData() or "")
            if spec:
                a["label"] = spec.name
        elif t == "obs":
            a["request"] = self.obs_req.currentData()
            if a["request"] == "SetCurrentProgramScene":
                a["data"] = {"sceneName": self.obs_scene.currentText().strip()}
        elif t == "shell":
            a["command"] = self.command.text()
            a["hidden"] = not self.console.isChecked()
        elif t == "powershell":
            a["script"] = self.ps.toPlainText()
        elif t == "python":
            a["code"] = self.py.toPlainText()
        return a

    @staticmethod
    def _recorded(seq: QKeySequence, target: QLineEdit, recorder: QKeySequenceEdit) -> None:
        txt = seq.toString(QKeySequence.SequenceFormat.PortableText).lower()
        if txt:
            target.setText(txt.replace("meta", "win"))
            recorder.clear()

    def _preset(self, i: int) -> None:
        k = self.preset.itemData(i)
        if k:
            self.keys.setText(k)
        self.preset.setCurrentIndex(0)

    def _browse_app(self) -> None:
        if WIN:
            start, kinds = os.environ.get("ProgramFiles", "C:\\"), "Programs (*.exe *.lnk *.bat *.cmd *.appref-ms)"
        elif MAC:
            start, kinds = "/Applications", "Apps (*.app)"
        else:
            start, kinds = "/usr/share/applications", "Apps (*.desktop);;Programs (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app", start, f"{kinds};;All files (*)")
        if path:
            path = os.path.normpath(path)
            self.app_target.setText(path)
            target = apps.shortcut_target(path)
            self.app_picked.emit(os.path.splitext(os.path.basename(path))[0], path,
                                 target if target.lower().endswith(".exe") else "")

    def _pick_installed(self) -> None:
        dlg = AppPicker(self)
        if dlg.exec() and dlg.result_app:
            name, target, exe = dlg.result_app
            self.app_target.setText(target)
            self.app_picked.emit(name, target, exe)

    def _browse_open(self, folder: bool) -> None:
        if folder:
            path = QFileDialog.getExistingDirectory(self, "Choose a folder")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a file")
        if path:
            self.open_target.setText(os.path.normpath(path))


# =========================================================================== pages
class RotaryPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.app = win.app
        self.models: dict[int, dict] = {}
        self._loading = False

        root = QHBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(22)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(page_header("Rotary", "What the ring shows, in order. Drag to reorder."))
        self.list = QListWidget()
        self.list.setItemDelegate(ItemDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setAccessibleName("Rotary items")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.model().rowsMoved.connect(self._reordered)
        self.list.currentRowChanged.connect(self._select)
        pane = widgets.Panel(radius=16)
        pl = QVBoxLayout(pane)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.addWidget(self.list)
        left.addWidget(pane, 1)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.b_add = icon_button("\uE710", "Add an item")
        self.b_add.setAccessibleName("Add an item")
        self.b_dup = icon_button("\uE8C8", "Duplicate")
        self.b_dup.setAccessibleName("Duplicate item")
        self.b_del = icon_button("\uE74D", "Remove")
        self.b_del.setAccessibleName("Remove item")
        self.b_up = icon_button("\uE70E", "Move up")
        self.b_up.setAccessibleName("Move item up")
        self.b_down = icon_button("\uE70D", "Move down")
        self.b_down.setAccessibleName("Move item down")
        self.b_add.clicked.connect(self._add_menu)
        self.b_dup.clicked.connect(self._duplicate)
        self.b_del.clicked.connect(self._delete)
        self.b_up.clicked.connect(lambda: self._move(-1))
        self.b_down.clicked.connect(lambda: self._move(1))
        for b in (self.b_add, self.b_dup, self.b_del):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.b_up)
        bar.addWidget(self.b_down)
        left.addLayout(bar)
        gallery = QPushButton("Add from your apps…")
        gallery.setObjectName("accent")
        gallery.clicked.connect(lambda: self.open_gallery())
        left.addWidget(gallery)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(310)
        root.addWidget(lw)

        inner = QWidget()
        right = QVBoxLayout(inner)
        right.setContentsMargins(0, 0, 8, 0)
        right.setSpacing(18)
        self.preview = DialPreview()
        right.addWidget(self.preview)

        look = Card("Item")
        self.name = QLineEdit()
        self.name.setMinimumWidth(260)
        self.subtitle = QLineEdit()
        self.subtitle.setMinimumWidth(260)
        look.add(Row("Name", None, self.name))
        look.add(Row("Subtitle", "Shown under the name in the centre", self.subtitle))
        icon_row = QWidget()
        h = QHBoxLayout(icon_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.icon_mode = Segmented([("glyph", "Symbol"), ("app", "App icon")], "glyph")
        self.icon_mode.setFixedWidth(190)
        self.icon_mode.setAccessibleName("Icon style")
        self.glyph_btn = QPushButton()
        self.glyph_btn.setFixedSize(46, 34)
        self.glyph_btn.setStyleSheet("font-family: 'Segoe Fluent Icons', 'Rotary Icons'; font-size: 16px;")
        self.glyph_btn.setAccessibleName("Choose symbol")
        self.glyph_btn.clicked.connect(self._pick_glyph)
        self.icon_btn = QPushButton("Choose…")
        self.icon_btn.setAccessibleName("Choose icon file")
        self.icon_btn.clicked.connect(self._pick_icon)
        h.addWidget(self.icon_mode)
        h.addWidget(self.glyph_btn)
        h.addWidget(self.icon_btn)
        look.add(Row("Icon", None, icon_row))
        self.color = ColorPicker("#0A84FF")
        look.add(Row("Colour", None, self.color))
        right.addWidget(look)

        self.editor = ActionEditor(self.app)
        right.addWidget(self.editor)
        row = QHBoxLayout()
        self.test_btn = QPushButton("Test action")
        self.test_btn.setObjectName("accent")
        self.test_btn.clicked.connect(self._test)
        self.del_btn = QPushButton("Remove item")
        self.del_btn.setObjectName("danger")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.test_btn)
        row.addStretch(1)
        row.addWidget(self.del_btn)
        right.addLayout(row)
        right.addStretch(1)
        root.addWidget(scroll_page(inner), 1)

        self.name.textChanged.connect(self._fields_changed)
        self.subtitle.textChanged.connect(self._fields_changed)
        self.color.changed.connect(self._fields_changed)
        self.icon_mode.changed.connect(self._icon_mode_changed)
        self.editor.changed.connect(self._fields_changed)
        self.editor.app_picked.connect(self._app_picked)
        self.reload()

    # ---- model <-> list
    def reload(self, select: int = 0) -> None:
        self._loading = True
        self.list.clear()
        self.models.clear()
        for d in self.app.cfg.get("items", []):
            self._append(copy.deepcopy(d))
        self._loading = False
        self.list.setCurrentRow(max(0, min(select, self.list.count() - 1)))
        self.preview.configure(self.app.cfg)
        self._refresh_preview()

    def _append(self, d: dict, row: int | None = None) -> QListWidgetItem:
        uid = next(_uid)
        self.models[uid] = d
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, uid)
        it.setData(Qt.ItemDataRole.UserRole + 1, d)
        it.setData(Qt.ItemDataRole.AccessibleTextRole, d.get("name", ""))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        if row is None:
            self.list.addItem(it)
        else:
            self.list.insertItem(row, it)
        return it

    def ordered(self) -> list[dict]:
        return [self.models[self.list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.list.count())]

    def _current(self) -> dict | None:
        it = self.list.currentItem()
        return self.models.get(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _push(self) -> None:
        self.app.cfg["items"] = copy.deepcopy(self.ordered())
        self.app.apply_config()
        self._refresh_preview()
        n = self.list.count()
        self.b_del.setEnabled(n > 1)
        self.del_btn.setEnabled(n > 1)
        self.b_up.setEnabled(self.list.currentRow() > 0)
        self.b_down.setEnabled(0 <= self.list.currentRow() < n - 1)
        self.win.apps.sync_switches()

    def _refresh_preview(self) -> None:
        self.preview.set_items(build_items({"items": self.ordered()}), max(0, self.list.currentRow()))

    # ---- selection
    def _select(self, row: int) -> None:
        d = self._current()
        if d is None:
            return
        self._loading = True
        self.name.setText(d.get("name", ""))
        self.subtitle.setText(d.get("subtitle", ""))
        self.color.set_color(d.get("tint", "#0A84FF"))
        self.icon_mode.set_value("app" if d.get("icon") else "glyph")
        self._sync_icon_controls(d)
        self.editor.set_action(d.get("action", {}))
        self._loading = False
        self.preview.select(row)
        n = self.list.count()
        self.b_up.setEnabled(row > 0)
        self.b_down.setEnabled(row < n - 1)
        self.b_del.setEnabled(n > 1)
        self.del_btn.setEnabled(n > 1)

    def _sync_icon_controls(self, d: dict) -> None:
        g = d.get("glyph", "E8A7")
        try:
            self.glyph_btn.setText(chr(int(g, 16)))
        except ValueError:
            self.glyph_btn.setText(g[:1])
        app_mode = self.icon_mode.value == "app"
        self.glyph_btn.setVisible(not app_mode)
        self.icon_btn.setVisible(app_mode)
        self.icon_btn.setText(os.path.basename(d.get("icon", "")) or "Choose…")

    # ---- edits
    def _fields_changed(self, *_):
        if self._loading:
            return
        d = self._current()
        if d is None:
            return
        d["name"] = self.name.text() or "Untitled"
        d["subtitle"] = self.subtitle.text()
        d["tint"] = self.color.color
        old = d.get("action", {})
        d["action"] = self.editor.action(old)
        a = d["action"]
        if a.get("type") == "quicknav" and a.get("app") and a.get("app") != old.get("app"):
            q = integrations.quicknav_item(a["app"])          # name/icon follow the chosen app
            self._loading = True
            for k in ("name", "subtitle", "glyph", "tint"):
                d[k] = q[k]
            if q.get("icon"):
                d["icon"] = q["icon"]
                self.icon_mode.set_value("app")
            self.name.setText(d["name"])
            self.subtitle.setText(d["subtitle"])
            self.color.set_color(d["tint"])
            self._sync_icon_controls(d)
            self._loading = False
        self.list.currentItem().setData(Qt.ItemDataRole.UserRole + 1, d)
        self.list.viewport().update()
        self._push()

    def _icon_mode_changed(self, mode: str) -> None:
        d = self._current()
        if d is None:
            return
        if mode == "glyph":
            d.pop("icon", None)
        elif not d.get("icon"):
            a = d.get("action", {})
            guess = a.get("exe") or (a.get("target", "") if a.get("type") == "app" else "")
            if a.get("type") == "quicknav" and a.get("app"):
                guess = integrations.quicknav_item(a["app"]).get("icon", "")
            if guess and str(guess).lower().endswith((".exe", ".lnk")):
                d["icon"] = apps.shortcut_target(guess)
            else:
                self._sync_icon_controls(d)
                self._pick_icon()
                return
        self._sync_icon_controls(d)
        self._fields_changed()

    def _pick_glyph(self) -> None:
        d = self._current()
        if d is None:
            return
        dlg = GlyphPicker(d.get("glyph", ""), self)
        dlg.move(self.glyph_btn.mapToGlobal(self.glyph_btn.rect().bottomLeft()))
        dlg.picked.connect(lambda code: (d.__setitem__("glyph", code), self._sync_icon_controls(d),
                                         self._fields_changed()))
        dlg.exec()

    def _pick_icon(self) -> None:
        d = self._current()
        if d is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose an icon (app, shortcut or image)", "",
                                              ("Apps and images (*.exe *.lnk *.ico *.png *.jpg *.svg)" if WIN else
                                               "Apps and images (*.app *.desktop *.icns *.png *.jpg *.svg)")
                                              + ";;All files (*)")
        if path:
            d["icon"] = apps.shortcut_target(os.path.normpath(path))
        elif not d.get("icon"):
            self.icon_mode.set_value("glyph")
        self._sync_icon_controls(d)
        self._fields_changed()

    def _app_picked(self, name: str, target: str, exe: str) -> None:
        d = self._current()
        if d is None:
            return
        self._loading = True
        if d.get("name", "") in ("", "New item", "Untitled") or d.get("name") == d.get("_auto_name"):
            d["name"] = d["_auto_name"] = name
            self.name.setText(name)
        if not d.get("subtitle") or d.get("subtitle") == d.get("_auto_sub"):
            d["subtitle"] = d["_auto_sub"] = "Open " + name
            self.subtitle.setText(d["subtitle"])
        self._loading = False
        a = self.editor.action(d.get("action", {}))
        if exe:
            a["exe"] = exe
            a["process"] = os.path.basename(exe)
            d["icon"] = exe
            self.icon_mode.set_value("app")
        d["action"] = a
        self._sync_icon_controls(d)
        self.list.currentItem().setData(Qt.ItemDataRole.UserRole + 1, d)
        self.list.viewport().update()
        self._push()

    # ---- list operations
    def _add_menu(self) -> None:
        m = QMenu(self)
        m.setStyleSheet(stylesheet())
        m.addAction("From your apps…", lambda: self.open_gallery())
        m.addAction("Any installed app…", self._add_app)
        m.addAction("Screen assist (AI answers what's on screen)", lambda: self.add_items([ASSIST_ITEM]))
        m.addAction("Custom item", self._add_blank)
        m.exec(self.b_add.mapToGlobal(self.b_add.rect().topLeft()))

    def add_items(self, ds: list[dict]) -> None:
        row = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else self.list.count()
        for d in ds:
            self._append(copy.deepcopy(d), row)
            row += 1
        self.list.setCurrentRow(row - 1)
        self._push()

    def open_gallery(self, start: str | None = None) -> None:
        dlg = GalleryDialog(self.app, self, start)
        dlg.picked.connect(self.add_items)
        dlg.exec()

    def _add_app(self) -> None:
        dlg = AppPicker(self)
        if dlg.exec() and dlg.result_app:
            name, target, exe = dlg.result_app
            d = {"name": name, "subtitle": "Open " + name, "glyph": "E8A7", "tint": "#0A84FF",
                 "action": {"type": "app", "target": target}}
            if exe:
                d["action"]["exe"] = exe
                d["action"]["process"] = os.path.basename(exe)
                d["icon"] = exe
            self.add_items([d])

    def _add_blank(self) -> None:
        self.add_items([{"name": "New item", "subtitle": "", "glyph": "E8A7", "tint": "#0A84FF",
                         "action": {"type": "app", "target": ""}}])
        self.name.setFocus()
        self.name.selectAll()

    def _duplicate(self) -> None:
        d = self._current()
        if d is None:
            return
        nd = copy.deepcopy(d)
        nd["name"] = d.get("name", "") + " copy"
        self.add_items([nd])

    def _delete(self) -> None:
        if self.list.count() <= 1:
            return
        row = self.list.currentRow()
        it = self.list.takeItem(row)
        self.models.pop(it.data(Qt.ItemDataRole.UserRole), None)
        self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._push()

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < self.list.count():
            return
        it = self.list.takeItem(row)
        self.list.insertItem(new, it)
        self.list.setCurrentRow(new)
        self._push()

    def _reordered(self, *_):
        QTimer.singleShot(0, self._push)

    def _test(self) -> None:
        d = self._current()
        if d is not None:
            self.app.test_item(build_items({"items": [d]})[0])


class AppsPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.app = win.app
        self.inner = QWidget()
        self.v = QVBoxLayout(self.inner)
        self.v.setContentsMargins(28, 24, 36, 32)
        self.v.setSpacing(22)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(self.inner))
        self.app.dynamic_ready.connect(lambda _id: self._update_status())
        self._status_labels: dict[str, QLabel] = {}
        self._switches: dict[str, Switch] = {}
        self.build()

    def build(self) -> None:
        while self.v.count():
            item = self.v.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._status_labels = {}
        self._switches = {}
        head = QHBoxLayout()
        head.addWidget(page_header("Apps", f"Apps on this {DEVICE} that RotaryUI works with. Turn on Quick nav to put "
                                           "an app on the rotary — choosing it opens a ring of that app's own "
                                           "actions."), 1)
        refresh = QPushButton("Rescan")
        refresh.setToolTip("Look for newly installed apps")
        refresh.clicked.connect(self._rescan)
        head.addWidget(refresh, 0, Qt.AlignmentFlag.AlignTop)
        hw = QWidget()
        hw.setLayout(head)
        self.v.addWidget(hw)
        installed = {a.id: a for a in integrations.installed()}
        for cat in integrations.CATEGORIES:
            members = [s for s in integrations.CATALOG if s.category == cat and s.id in installed]
            if not members:
                continue
            card = Card(cat)
            for s in members:
                card.add(self._row(installed[s.id]))
                integrations.refresh_dynamic(s.id, self.app.cfg, self.app.dynamic_ready.emit)
            self.v.addWidget(card)
        if "obs" in installed:
            self.v.addWidget(self._obs_card())
        foot = QLabel("Don't see an app? Rotary → + → Any installed app works with everything in your "
                      + ("Start menu." if WIN else "Applications folder." if MAC else "apps menu."))
        foot.setObjectName("muted")
        foot.setWordWrap(True)
        self.v.addWidget(foot)
        self.v.addStretch(1)
        self._update_status()
        MainWindow.tab_focus_only(self.inner)

    def _row(self, a) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(app_tile(a, 30).pixmap(30, 30))
        h.addWidget(icon)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        n = QLabel(a.name)
        n.setObjectName("rowtitle")
        st = QLabel("")
        st.setObjectName("muted")
        self._status_labels[a.id] = st
        txt.addWidget(n)
        txt.addWidget(st)
        h.addLayout(txt, 1)
        acts = QPushButton("Actions…")
        acts.setAccessibleName(f"{a.name} actions")
        acts.clicked.connect(lambda _=False, i=a.id: self.win.rotary.open_gallery(i))
        h.addWidget(acts)
        sw = Switch(self._has_quicknav(a.id))
        sw.setAccessibleName(f"{a.name} Quick nav on the rotary")
        sw.setToolTip("Quick nav on the rotary")
        sw.toggled.connect(lambda on, i=a.id: self._toggle(i, on))
        self._switches[a.id] = sw
        h.addWidget(sw)
        return w

    def _obs_card(self) -> QWidget:
        o = (self.app.cfg.get("integrations") or {}).get("obs") or {}
        card = Card("OBS Studio connection",
                    "Optional. Without it, OBS items start OBS already recording or streaming. To control OBS "
                    "while it's open: in OBS choose Tools → WebSocket Server Settings, tick Enable, and copy the "
                    "password here. " + ("The password is encrypted for your Windows account." if WIN else
                                         "The password is kept in your macOS Keychain." if MAC else
                                         "The password is kept in your keyring when one is available."))
        self.obs_host = QLineEdit(str(o.get("host", "localhost")))
        self.obs_host.setFixedWidth(200)
        card.add(Row("Server", None, self.obs_host))
        self.obs_port = QLineEdit(str(o.get("port", 4455)))
        self.obs_port.setFixedWidth(90)
        card.add(Row("Port", None, self.obs_port))
        self.obs_pw = QLineEdit()
        self.obs_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_pw.setPlaceholderText("saved" if o.get("password_dpapi") else "WebSocket password")
        self.obs_pw.setFixedWidth(240)
        card.add(Row("Password", None, self.obs_pw))
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.obs_status = QLabel("")
        self.obs_status.setObjectName("value")
        self.obs_status.setWordWrap(True)
        test = QPushButton("Save && test")
        test.setObjectName("accent")
        test.clicked.connect(self._obs_test)
        h.addWidget(self.obs_status, 1)
        h.addWidget(test)
        card.add(Row("Status", None, w))
        return card

    def _obs_test(self) -> None:
        o = self.app.cfg.setdefault("integrations", {}).setdefault("obs", {})
        o["host"] = self.obs_host.text().strip() or "localhost"
        try:
            o["port"] = int(self.obs_port.text().strip() or 4455)
        except ValueError:
            o["port"] = 4455
        if self.obs_pw.text():
            o["password_dpapi"] = winapi.protect(self.obs_pw.text(), "obs-websocket")
            self.obs_pw.clear()
            self.obs_pw.setPlaceholderText("saved")
        self.app.apply_config()
        host, port, pw = obs.settings_from(self.app.cfg)
        self.obs_status.setText("Checking…")
        self.obs_status.repaint()
        self.obs_status.setText(obs.test_connection(host, port, pw))
        integrations.refresh_dynamic("obs", self.app.cfg, self.app.dynamic_ready.emit, force=True)

    def _has_quicknav(self, app_id: str) -> bool:
        return any((d.get("action") or {}).get("type") == "quicknav" and d["action"].get("app") == app_id
                   for d in self.app.cfg.get("items", []))

    def _toggle(self, app_id: str, on: bool) -> None:
        items = self.app.cfg.setdefault("items", [])
        if on and not self._has_quicknav(app_id):
            items.append(integrations.quicknav_item(app_id))
        elif not on:
            kept = [d for d in items if not ((d.get("action") or {}).get("type") == "quicknav"
                                             and d["action"].get("app") == app_id)]
            self.app.cfg["items"] = kept or items
        self.app.apply_config()
        self.win.rotary.reload(self.win.rotary.list.currentRow())

    def sync_switches(self) -> None:
        for app_id, sw in self._switches.items():
            sw.set_silent(self._has_quicknav(app_id))

    def _update_status(self) -> None:
        for app_id, lab in self._status_labels.items():
            lab.setText(integrations.status_line(app_id))

    def _rescan(self) -> None:
        apps.clear_caches()
        self.app.refresh_apps(force=True)
        self.build()
        self.win.rotary.editor.refresh_apps()


class GesturesPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)
        v.addWidget(page_header("Controls", "How the rotary opens, how you choose, and how you launch. Quick "
                                            "setups for different needs are in Accessibility."))
        g = self.g()

        c = Card("Opening the rotary")
        self.fist = Switch(g.grip_summon)
        c.add(Row("Grab", "Show your open palm, then close it into a fist", self.fist))
        self.still = Switch(g.still_summon)
        c.add(Row("Hold your palm still", None, self.still))
        self.still_s = SliderControl(0.5, 6.0, 0.25, g.still_seconds, lambda x: f"{x:g} s")
        c.add(Row("How long to hold", "Shorter opens faster", self.still_s))
        self.intent = Segmented([("relaxed", "Low"), ("balanced", "Medium"), ("strict", "High")], g.intent)
        self.intent.setFixedWidth(270)
        self.intent_row = c.add(Row("Accidental-open protection", "", self.intent))
        self.face = Switch(g.ignore_near_face)
        c.add(Row("Ignore hands touching my face",
                  "Chin on your hand or scratching never opens it. Gestures in front of your face still work",
                  self.face))
        v.addWidget(c)

        c = Card("Choosing an item")
        self.style = Segmented([("tilt", "Tilt"), ("move", "Move"), ("direct", "Follow")], g.turn_style)
        self.style.setFixedWidth(270)
        self.style_row = c.add(Row("Choose by", "", self.style))
        self.deg = SliderControl(10, 40, 1, g.degrees_per_item, lambda x: f"{x:.0f}°", invert=True)
        self.deg_row = c.add(Row("Tilt sensitivity", "How far you tilt before it moves", self.deg))
        self.dist = SliderControl(0.4, 1.6, 0.1, g.move_distance, lambda x: f"{x:.1f} palms")
        self.dist_row = c.add(Row("Move distance", "How far you slide your hand before it moves", self.dist))
        self.speed = SliderControl(0.4, 2.5, 0.1, g.scroll_speed, lambda x: f"{x:.1f}×")
        self.speed_row = c.add(Row("Scroll speed", "How quickly it keeps going while you hold", self.speed))
        self.grip_auto = SliderControl(0.0, 2.0, 0.1, g.grip_auto_seconds, lambda x: "Off" if x <= 0 else f"{x:.1f} s")
        c.add(Row("Fist keeps turning", "Hold your fist turned this long and the ring keeps going by itself, "
                                        "until you turn back", self.grip_auto))
        v.addWidget(c)

        c = Card("Launching", "Point with your index finger while the rotary is open to use finger controls.")
        self.dwell = SliderControl(0.0, 4.0, 0.2, g.dwell_seconds, lambda x: "Off" if x <= 0 else f"{x:.1f} s")
        c.add(Row("Hold still on an item", "After choosing, keep still this long to launch it", self.dwell))
        self.pinch = Switch(g.pinch_click)
        c.add(Row("Pinch", "Touch your thumb and index finger", self.pinch))
        self.swipe = Switch(g.finger_swipe)
        c.add(Row("Finger scrolling", "Move your pointing finger left or right", self.swipe))
        self.push = Switch(g.finger_push)
        c.add(Row("Push to click", "Push your pointing finger toward the camera", self.push))
        self.push_s = SliderControl(0.0, 1.0, 0.05, g.push_sensitivity,
                                    lambda x: "Firm" if x < 0.34 else ("Light" if x > 0.66 else "Medium"))
        c.add(Row("Push sensitivity", None, self.push_s))
        v.addWidget(c)

        c = Card("Volume, windows, slides and videos", "Two fingers change the volume whether the rotary is open or not. "
                                             "Over a slideshow or a video the rotary opens as a slides or video "
                                             "ring; click its centre, pinch or point for your items instead.")
        self.vol = Switch(g.volume_slide)
        c.add(Row("Volume slider", "Hold up your index and middle finger and slide up or down", self.vol))
        mc = {"slides": True, "video": True, **(self.app.cfg.get("modes") or {})}
        self.slides = Switch(bool(mc["slides"]))
        c.add(Row("Slides ring", "Over PowerPoint, Google Slides, Keynote and other slideshows: turn to change "
                                 "slides, with a firm click per slide", self.slides))
        self.video = Switch(bool(mc["video"]))
        c.add(Row("Video ring", "Over YouTube, Netflix, Hulu, VLC and other players: a full turn is the whole "
                                "video", self.video))
        self.winsize = Switch(g.window_resize)
        c.add(Row("Resize windows", "Pinch with both hands, then pull apart or push together. Side by side "
                                    "changes the width, one above the other the height, opposite corners both",
                  self.winsize))
        self.vol_corner = Segmented([("top-left", "Top left"), ("top-right", "Top right"),
                                     ("bottom-left", "Bottom left"), ("bottom-right", "Bottom right")], self._corner())
        self.vol_corner.setFixedWidth(400)
        c.add(Row("Volume ring corner", "The volume gets a full-size ring of its own, in this corner of the "
                                        "screen", self.vol_corner))
        v.addWidget(c)

        c = Card("Recognition")
        self.cal_label = QLabel()
        self.cal_label.setObjectName("value")
        cal = QPushButton("Calibrate…")
        cal.clicked.connect(self._calibrate)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.cal_label)
        h.addWidget(cal)
        c.add(Row("Palm direction", "If palm and back of hand look swapped: hold your palm up, then calibrate", w))
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        c.add(Row("Restore default controls", None, reset))
        v.addWidget(c)
        v.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

        self.fist.toggled.connect(lambda on: self.set("grip_summon", on))
        self.still.toggled.connect(lambda on: self.set("still_summon", on))
        self.still_s.changed.connect(lambda x: self.set("still_seconds", x))
        self.intent.changed.connect(lambda k: self.set("intent", k))
        self.face.toggled.connect(lambda on: self.set("ignore_near_face", on))
        self.style.changed.connect(lambda k: self.set("turn_style", k))
        self.deg.changed.connect(lambda x: self.set("degrees_per_item", x))
        self.dist.changed.connect(lambda x: self.set("move_distance", x))
        self.speed.changed.connect(lambda x: self.set("scroll_speed", x))
        self.dwell.changed.connect(lambda x: self.set("dwell_seconds", x))
        self.pinch.toggled.connect(lambda on: self.set("pinch_click", on))
        self.swipe.toggled.connect(lambda on: self.set("finger_swipe", on))
        self.push.toggled.connect(lambda on: self.set("finger_push", on))
        self.push_s.changed.connect(lambda x: self.set("push_sensitivity", x))
        self.vol.toggled.connect(lambda on: self.set("volume_slide", on))
        self.winsize.toggled.connect(lambda on: self.set("window_resize", on))
        self.slides.toggled.connect(lambda on: self._mode("slides", on))
        self.video.toggled.connect(lambda on: self._mode("video", on))
        self.vol_corner.changed.connect(lambda k: self._mode("volume_corner", k))
        self.grip_auto.changed.connect(lambda x: self.set("grip_auto_seconds", x))
        self.refresh()

    def _mode(self, key: str, on) -> None:
        self.app.cfg.setdefault("modes", {})[key] = on
        self.app.apply_config()

    def _corner(self) -> str:
        return (self.app.cfg.get("modes") or {}).get("volume_corner", "bottom-right")

    def g(self) -> GestureSettings:
        return GestureSettings.from_dict(self.app.cfg.get("gestures"))

    def set(self, key: str, value) -> None:
        self.app.cfg.setdefault("gestures", {})[key] = value
        self.app.apply_config()
        self._texts()

    def _texts(self) -> None:
        g = self.g()
        self.still_s.setEnabled(g.still_summon)
        self.push_s.setEnabled(g.finger_push)
        self.intent_row.subtitle.setText({
            "relaxed": "Opens easily — good for a quiet desk",
            "balanced": "Needs a clear, upright palm and a deliberate grab",
            "strict": "Only very deliberate, steady gestures"}[g.intent if g.intent in INTENTS else "balanced"])
        self.style_row.subtitle.setText({
            "tilt": "Lean your hand left or right and hold — it keeps going until you straighten up",
            "move": "Slide your hand left or right and hold — no wrist turning needed",
            "direct": "The ring follows your hand's turn exactly"}.get(g.turn_style, ""))
        self.deg_row.setVisible(g.turn_style in ("tilt", "direct"))
        self.deg_row.title.setText("Tilt sensitivity" if g.turn_style == "tilt" else "Turn per item")
        self.dist_row.setVisible(g.turn_style == "move")
        self.speed_row.setVisible(g.turn_style in ("tilt", "move"))

    def refresh(self) -> None:
        g = self.g()
        self.fist.set_silent(g.grip_summon)
        self.still.set_silent(g.still_summon)
        self.still_s.set_value(g.still_seconds)
        self.intent.set_value(g.intent)
        self.face.set_silent(g.ignore_near_face)
        self.style.set_value(g.turn_style)
        self.deg.set_value(g.degrees_per_item)
        self.dist.set_value(g.move_distance)
        self.speed.set_value(g.scroll_speed)
        self.dwell.set_value(g.dwell_seconds)
        self.pinch.set_silent(g.pinch_click)
        self.swipe.set_silent(g.finger_swipe)
        self.push.set_silent(g.finger_push)
        self.push_s.set_value(g.push_sensitivity)
        self.vol.set_silent(g.volume_slide)
        self.winsize.set_silent(g.window_resize)
        mc = {"slides": True, "video": True, **(self.app.cfg.get("modes") or {})}
        self.slides.set_silent(bool(mc["slides"]))
        self.video.set_silent(bool(mc["video"]))
        self.vol_corner.set_value(self._corner())
        self.grip_auto.set_value(g.grip_auto_seconds)
        self.cal_label.setText("Flipped" if g.flip_handedness else "Standard")
        self._texts()

    def _calibrate(self) -> None:
        self.app.start_calibration()
        QTimer.singleShot(2800, self.refresh)

    def _reset(self) -> None:
        keep = {"flip_handedness": self.g().flip_handedness}
        self.app.cfg["gestures"] = {**GestureSettings().to_dict(), **keep}
        self.app.apply_config()
        self.refresh()


class PresetGrid(QWidget):
    applied = Signal(str)

    def __init__(self, app, columns: int = 2):
        super().__init__()
        self.app = app
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.tiles: dict[str, PresetTile] = {}
        for i, (key, glyph, title, text, tint, _values) in enumerate(PRESETS):
            t = PresetTile(glyph, title, text, tint)
            t.clicked.connect(lambda _=False, k=key: self.apply(k))
            grid.addWidget(t, i // columns, i % columns)
            self.tiles[key] = t
        self.sync()

    def apply(self, key: str) -> None:
        values = next(v for k, *_rest, v in PRESETS if k == key)
        self.app.cfg.setdefault("gestures", {}).update(values)
        self.app.apply_config()
        self.sync()
        self.applied.emit(key)

    def sync(self) -> None:
        cur = active_preset(self.app.cfg.get("gestures", {}))
        for k, t in self.tiles.items():
            t.setChecked(k == cur)


class AccessibilityPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)
        v.addWidget(page_header("Accessibility", "Make RotaryUI comfortable for your hands, eyes and ears. "
                                                 "Pick a quick setup, then fine-tune anything below."))
        g = GestureSettings.from_dict(self.app.cfg.get("gestures"))
        ap = self.app.cfg.get("appearance", {})
        gen = self.app.cfg.get("general", {})

        t = QLabel("QUICK SETUPS")
        t.setObjectName("h2")
        v.addWidget(t)
        self.presets = PresetGrid(self.app)
        self.presets.applied.connect(lambda _k: self.win.gestures.refresh())
        v.addWidget(self.presets)

        c = Card("Your hands")
        self.either = Switch(g.either_side)
        c.add(Row("Either side of the hand", "For when turning your palm toward the camera is hard — the back of "
                                             "your hand works too", self.either))
        self.distance = Segmented([("0.1", "Near"), ("0.07", "Normal"), ("0.04", "Far")],
                                  self._dist_key(g.min_hand_size))
        self.distance.setFixedWidth(260)
        c.add(Row("How far you sit from the camera", "Far notices smaller hands, e.g. from a wheelchair or "
                                                     "across the room", self.distance))
        self.steady = SliderControl(0.3, 1.0, 0.05, g.large_move, lambda x: "Strict" if x < 0.4 else
                                    ("Relaxed" if x > 0.7 else "Normal"))
        c.add(Row("Steadiness needed when holding still", "Relaxed allows more drift and tremor", self.steady))
        self.idle = SliderControl(0, 60, 5, g.idle_timeout, lambda x: "Never" if x <= 0 else f"{x:.0f} s")
        c.add(Row("Close the rotary after inactivity", None, self.idle))
        v.addWidget(c)

        c = Card("Seeing & hearing")
        self.text = Segmented([("1.0", "Default"), ("1.25", "Large"), ("1.5", "Larger")],
                              self._scale_key(ap.get("text_scale", 1.0)))
        self.text.setFixedWidth(270)
        c.add(Row("Text size", "On the rotary and in this window", self.text))
        self.contrast = Switch(bool(ap.get("high_contrast", False)))
        c.add(Row("High contrast", "Solid dark glass, bright outlines and text", self.contrast))
        self.motion = Switch(bool(ap.get("reduce_motion", False)))
        c.add(Row("Reduce motion", "No bounce, zoom or stretch", self.motion))
        self.speak = Switch(bool(ap.get("speak", False)))
        c.add(Row("Speak item names", "Reads out what you're on, and what launches", self.speak))
        self.sounds = Switch(bool(ap.get("sounds", True)))
        c.add(Row("Sounds", "Soft ticks while choosing", self.sounds))
        self.volume = SliderControl(0.1, 1.0, 0.05, float(ap.get("volume", 0.6)), lambda x: f"{x * 100:.0f}%")
        c.add(Row("Sound volume", None, self.volume))
        v.addWidget(c)

        c = Card("Keyboard & mouse", "Open the rotary without gestures. While it's open from the keyboard, the "
                                     "mouse or the notch: ← and → move, Enter launches, Esc goes back or closes — and "
                                     "the mouse works too: scroll anywhere or drag the ring, click to choose and open. "
                                     "Clicks and scrolls outside the rotary stop there, so a full-screen game or "
                                     "video underneath is never clicked into or knocked out of full screen.")
        self.hotkey = QComboBox()
        for k, label in HOTKEYS:
            self.hotkey.addItem(label, k)
        self.hotkey.setCurrentIndex(max(0, self.hotkey.findData(gen.get("hotkey", "ctrl+alt+space"))))
        c.add(Row("Shortcut to open the rotary", None, self.hotkey))
        self.mouse_btn = QComboBox()
        for k, label in MOUSE_BUTTONS:
            self.mouse_btn.addItem(label, k)
        self.mouse_btn.setCurrentIndex(max(0, self.mouse_btn.findData(gen.get("mouse_button", "middle"))))
        c.add(Row("Mouse button to open the rotary", "Works over full-screen apps too. A quick middle click "
                                                     "still works as usual", self.mouse_btn))
        v.addWidget(c)
        v.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

        self.either.toggled.connect(lambda on: self._g("either_side", on))
        self.distance.changed.connect(lambda k: self._g("min_hand_size", float(k)))
        self.steady.changed.connect(lambda x: self._g("large_move", x))
        self.idle.changed.connect(lambda x: self._g("idle_timeout", x))
        self.text.changed.connect(lambda k: self._ap("text_scale", float(k)))
        self.contrast.toggled.connect(lambda on: self._ap("high_contrast", on))
        self.motion.toggled.connect(lambda on: self._ap("reduce_motion", on))
        self.speak.toggled.connect(lambda on: self._ap("speak", on))
        self.sounds.toggled.connect(lambda on: self._ap("sounds", on))
        self.volume.changed.connect(lambda x: self._ap("volume", x))
        self.hotkey.currentIndexChanged.connect(self._hotkey)
        self.mouse_btn.currentIndexChanged.connect(self._mouse_btn)

    def _mouse_btn(self, _i: int) -> None:
        self.app.cfg.setdefault("general", {})["mouse_button"] = self.mouse_btn.currentData()
        self.app.apply_config()

    @staticmethod
    def _dist_key(v: float) -> str:
        return min(("0.1", "0.07", "0.04"), key=lambda k: abs(float(k) - v))

    @staticmethod
    def _scale_key(v: float) -> str:
        return min(("1.0", "1.25", "1.5"), key=lambda k: abs(float(k) - v))

    def _g(self, key: str, value) -> None:
        self.app.cfg.setdefault("gestures", {})[key] = value
        self.app.apply_config()
        self.presets.sync()

    def _ap(self, key: str, value) -> None:
        self.app.cfg.setdefault("appearance", {})[key] = value
        self.app.apply_config()
        if key == "text_scale":
            self.win.apply_text_scale(float(value))
        self.win.rotary.preview.configure(self.app.cfg)

    def _hotkey(self, _i: int) -> None:
        self.app.cfg.setdefault("general", {})["hotkey"] = self.hotkey.currentData()
        self.app.apply_config()

    def refresh(self) -> None:
        g = GestureSettings.from_dict(self.app.cfg.get("gestures"))
        self.either.set_silent(g.either_side)
        self.distance.set_value(self._dist_key(g.min_hand_size))
        self.steady.set_value(g.large_move)
        self.idle.set_value(g.idle_timeout)
        self.presets.sync()


INPUT_MODES = [("camera", "Hand gestures"), ("notch", "Mouse & keyboard"), ("both", "Both")]


def input_mode(gen: dict) -> str:
    cam, notch = bool(gen.get("camera", True)), gen.get("notch", "auto")
    if not cam:
        return "notch"
    return "both" if notch == "on" else "camera"


def set_input_mode(gen: dict, mode: str) -> None:
    gen["camera"] = mode != "notch"
    gen["notch"] = "auto" if mode == "camera" else "on"


class NotchPage(QWidget):
    """Using RotaryUI without a camera: the notch, the mouse and the keyboard."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)
        v.addWidget(page_header("Notch", f"A small notch at the top of your screen: a Dynamic Island for {OS_NAME}, and the way "
                                         "into the rotary with your mouse and keyboard — no camera needed."))
        gen = self.app.cfg.get("general", {})

        c = Card("How you use it")
        self.mode = Segmented(INPUT_MODES, input_mode(gen))
        self.mode.setFixedWidth(380)
        self.mode_row = c.add(Row("Open the rotary with", "", self.mode))
        v.addWidget(c)

        c = Card("The notch")
        self.notch = Segmented([("auto", "Auto"), ("on", "Always"), ("off", "Never")], gen.get("notch", "auto"))
        self.notch.setFixedWidth(270)
        c.add(Row("Show the notch", "Auto: only when there's no camera to use. It never shows up in screenshots "
                                    "or recordings.", self.notch))
        self.peek = Switch(bool(gen.get("notch_peek", False)))
        c.add(Row("Tuck it away", "Just a thin line until your mouse reaches it", self.peek))
        self.fs = Switch(bool(gen.get("notch_hide_fullscreen", True)))
        c.add(Row("Hide during full-screen games and videos", None, self.fs))
        home = QPushButton("Put it back")
        home.clicked.connect(lambda: self.app.notch.go_home())
        c.add(Row("Where it sits", "Drag the notch along its edge, or pull it away until it tears off and put it "
                                   "anywhere — throw it and it glides. Bring it to the top, bottom, left or right "
                                   "edge and it hangs from that one. A middle click on it sends it home to the "
                                   "middle of the top edge.", home))
        v.addWidget(c)

        isl = {**DEFAULT_ISLAND, **(self.app.cfg.get("island") or {})}
        c = Card("What it shows", "Like a Dynamic Island: the time, plus whatever's happening right now. Hover the "
                                  "notch for details and controls; right-click it for a timer. Set Show the notch "
                                  "to Always to keep it on screen when you also use the camera.")
        self.clock = Segmented([("off", "Off"), ("time", "Time"), ("date", "Day & time")], isl["clock"])
        self.clock.setFixedWidth(270)
        c.add(Row("Clock", None, self.clock))
        self.h24 = Switch(system_24h() if isl["clock_24h"] is None else bool(isl["clock_24h"]))
        c.add(Row("24-hour time", None, self.h24))
        self.media = Switch(bool(isl["media"]))
        c.add(Row("Now playing", "YouTube and other sites, Spotify, Apple Music — anything Windows' media "
                                 "controls know. Hover for ⏮ ⏯ ⏭." if WIN else
                                 "Spotify, browsers, VLC — any MPRIS player (needs playerctl). Hover for ⏮ ⏯ ⏭."
                                 if not MAC else "Not available on macOS yet.", self.media))
        self.art = Switch(bool(isl["media_art"]))
        c.add(Row("Album art", None, self.art))
        found = [who for who, _p, _d in clip_folders()]
        self.clips = Switch(bool(isl["clips"]))
        c.add(Row("Clips & recording", "A red dot while Medal records a game, and a note when a clip or screenshot "
                                       "is saved" + (f" — watching {', '.join(dict.fromkeys(found))}" if found else ""),
                  self.clips))
        self.calls = Switch(bool(isl["calls"]))
        c.add(Row("Calls", "A call ringing in pops up with Accept and Decline (even while you use the camera); "
                           "time on a Discord, Teams, Zoom or other call, with Mute, Deafen and Leave on hover",
                  self.calls))
        self.privacy = Switch(bool(isl["privacy"]))
        c.add(Row("Microphone & camera in use", "An orange dot while an app listens, green while one watches",
                  self.privacy))
        self.battery = Switch(bool(isl["battery"]))
        c.add(Row("Battery", "Plugged in, unplugged and running low (laptops)", self.battery))
        self.alerts = Switch(bool(isl["alerts_over_games"]))
        c.add(Row("Show alerts over full-screen games", "A saved clip or a finished timer pops up briefly "
                                                         "(not over exclusive full-screen games)", self.alerts))
        self.look = Segmented([("dark", "Dark"), ("glass", "Glass")], isl["look"])
        self.look.setFixedWidth(200)
        c.add(Row("Look", "Dark is nearly black like a real notch; Glass lets more of the screen through",
                  self.look))
        v.addWidget(c)
        self.clock.changed.connect(lambda k: self._isl("clock", k))
        self.h24.toggled.connect(lambda on: self._isl("clock_24h", on))
        self.media.toggled.connect(lambda on: self._isl("media", on))
        self.art.toggled.connect(lambda on: self._isl("media_art", on))
        self.clips.toggled.connect(lambda on: self._isl("clips", on))
        self.privacy.toggled.connect(lambda on: self._isl("privacy", on))
        self.calls.toggled.connect(lambda on: self._isl("calls", on))
        self.battery.toggled.connect(lambda on: self._isl("battery", on))
        self.alerts.toggled.connect(lambda on: self._isl("alerts_over_games", on))
        self.look.changed.connect(lambda k: self._isl("look", k))

        c = Card("Using the rotary with a mouse",
                 "Click the notch — or press your keyboard shortcut (Accessibility → Keyboard & mouse) — and the rotary "
                 "opens. Then:\n"
                 "•  Scroll, or drag around the ring, to turn it\n"
                 "•  Click an item to bring it to the top; click it again, or click the centre, to open it\n"
                 "•  Right-click (or click ‹ at the top) to go back, or close\n"
                 "•  Click the volume button beside it (or press + or −), then turn the ring to change the volume\n"
                 "•  ← → move, Enter opens, Esc goes back — clicking another window closes it")
        tw = QWidget()
        th = QHBoxLayout(tw)
        th.setContentsMargins(14, 10, 14, 10)
        th.addStretch(1)
        trybtn = QPushButton("Open the rotary now")
        trybtn.clicked.connect(self.app.manual_open)
        th.addWidget(trybtn)
        c.add(tw)
        v.addWidget(c)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

        self.mode.changed.connect(self._mode)
        self.notch.changed.connect(lambda k: self._set("notch", k))
        self.peek.toggled.connect(lambda on: self._set("notch_peek", on))
        self.fs.toggled.connect(lambda on: self._set("notch_hide_fullscreen", on))
        self._texts()

    def _set(self, key: str, value) -> None:
        self.app.cfg.setdefault("general", {})[key] = value
        self.app.apply_config()
        self.refresh()

    def _mode(self, mode: str) -> None:
        set_input_mode(self.app.cfg.setdefault("general", {}), mode)
        self.app.apply_config()
        self.refresh()

    def _isl(self, key: str, value) -> None:
        self.app.cfg.setdefault("island", {})[key] = value
        self.app.apply_config()

    def _texts(self) -> None:
        self.mode_row.subtitle.setText({
            "camera": "Your hand in front of the camera. The notch appears by itself if there's no camera.",
            "notch": "The camera stays off and hand tracking never loads — lighter and faster.",
            "both": "Gestures, plus the notch at the top of the screen."}[input_mode(self.app.cfg.get("general", {}))])

    def refresh(self) -> None:
        gen = self.app.cfg.get("general", {})
        self.mode.set_value(input_mode(gen))
        self.notch.set_value(gen.get("notch", "auto"))
        self.peek.set_silent(bool(gen.get("notch_peek", False)))
        self.fs.set_silent(bool(gen.get("notch_hide_fullscreen", True)))
        self._texts()


class AssistPage(QWidget):
    """Screen assist: an AI looks at the screen and answers the question on it, on the notch."""

    models_loaded = Signal(str, list, str)      # provider id, [(model id, name)], error ('' = fine)

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)
        header = page_header("Screen assist", "")
        self.intro = header.findChildren(QLabel)[-1]
        v.addWidget(header)
        a = ai.settings(self.app.cfg)

        c = Card("AI service", "The screenshot goes only to the service you choose, and only when you ask. Ollama "
                               "keeps everything on this " + DEVICE + ".")
        self.provider = QComboBox()
        for prov in ai.PROVIDERS:
            self.provider.addItem(prov.name, prov.id)
        self.provider.setMinimumWidth(260)
        self.provider_row = c.add(Row("Service", "", self.provider))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setFixedWidth(260)
        self.get_key = QPushButton("Get a free key…")
        self.get_key.clicked.connect(self._open_key_page)
        kw = QWidget()
        kh = QHBoxLayout(kw)
        kh.setContentsMargins(0, 0, 0, 0)
        kh.addWidget(self.key)
        kh.addWidget(self.get_key)
        self.key_row = c.add(Row("API key", "Encrypted for your Windows account" if WIN else
                                 "Kept in your macOS Keychain" if MAC else
                                 "Kept in your keyring when one is available", kw))
        # Editable: the list holds the models the service offers this key, and any other name can be typed in.
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setFixedWidth(260)
        self.list_btn = QPushButton("List my models")
        self.list_btn.setToolTip("Asks the service which models your key can use")
        self.list_btn.clicked.connect(lambda: self._load_models(force=True))
        mw = QWidget()
        mh = QHBoxLayout(mw)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.model)
        mh.addWidget(self.list_btn)
        self.model_row = c.add(Row("Model", "", mw))
        self._models: dict[str, list] = {}          # provider → [(id, name)] its key can use, once asked
        self._loading: set[str] = set()
        self.models_loaded.connect(self._models_loaded)
        self.url = QLineEdit()
        self.url.setFixedWidth(300)
        self.url_row = c.add(Row("Address", "Only change this for another server", self.url))
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setObjectName("value")
        self.status.setWordWrap(True)
        test = QPushButton("Save && try it")
        test.setObjectName("accent")
        test.setToolTip("Hides this window, then answers whatever is on your screen")
        test.clicked.connect(self._try)
        h.addWidget(self.status, 1)
        h.addWidget(test)
        c.add(Row("Status", None, w))
        v.addWidget(c)

        c = Card("Using it")
        self.hotkey = QComboBox()
        for k, label in ASSIST_HOTKEYS:
            self.hotkey.addItem(label, k)
        self.hotkey.setCurrentIndex(max(0, self.hotkey.findData(a["hotkey"])))
        self.hotkey.currentIndexChanged.connect(lambda _i: self._set("hotkey", self.hotkey.currentData()))
        c.add(Row("Keyboard shortcut", "Works from any app", self.hotkey))
        self.seconds = Segmented([("10", "10 s"), ("20", "20 s"), ("45", "45 s"), ("90", "90 s")],
                                 str(int(a.get("seconds", 20))))
        self.seconds.setFixedWidth(300)
        self.seconds.changed.connect(lambda k: self._set("seconds", int(k)))
        c.add(Row("Keep the answer on the notch", "Hover it for the explanation and Copy; click it to put it away",
                  self.seconds))
        self.add_btn = QPushButton("Add to the rotary")
        self.add_btn.clicked.connect(self._add_item)
        c.add(Row("Screen assist on the rotary", "An item that answers what's on screen when you choose it",
                  self.add_btn))
        v.addWidget(c)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.key.editingFinished.connect(self._save_key)
        self.model.lineEdit().editingFinished.connect(lambda: self._per_provider("models", self.model.currentText()))
        self.model.activated.connect(lambda _i: self._per_provider("models", self.model.currentText()))
        self.url.editingFinished.connect(lambda: self._per_provider("urls", self.url.text()))
        self.refresh()

    def _prov(self):
        return ai.BY_ID.get(self.provider.currentData() or "", ai.PROVIDERS[0])

    def _set(self, key: str, value) -> None:
        self.app.cfg.setdefault("assist", {})[key] = value
        self.app.apply_config()
        self.refresh()

    def _per_provider(self, section: str, value: str) -> None:
        prov = self._prov()
        d = self.app.cfg.setdefault("assist", {}).setdefault(section, {})
        value = value.strip()
        default = prov.model if section == "models" else prov.url
        if value and value.rstrip("/") != default:
            d[prov.id] = value
        else:
            d.pop(prov.id, None)
        self.app.apply_config()
        self.refresh()

    def _save_key(self) -> None:
        if not self.key.text().strip():
            return
        try:
            ai.save_key(self.app.cfg, self._prov().id, self.key.text())
        except OSError as e:
            self.status.setText(f"Couldn't save the key: {e}")
            return
        self.key.clear()
        self.app.apply_config()
        self.refresh()

    def _load_models(self, force: bool = False) -> None:
        """Ask the service, in the background, which models the saved key can use."""
        prov = self._prov()
        if prov.id in self._loading or (prov.id in self._models and not force) or ai.ready(self.app.cfg):
            if force and ai.ready(self.app.cfg):
                self.status.setText(ai.ready(self.app.cfg))
            return
        self._loading.add(prov.id)
        self.list_btn.setEnabled(False)
        cfg = copy.deepcopy(self.app.cfg)

        def work():
            try:
                self.models_loaded.emit(prov.id, ai.list_models(cfg, prov), "")
            except ai.AssistError as e:
                self.models_loaded.emit(prov.id, [], str(e))
            except Exception:
                log.exception("listing %s models failed", prov.name)
                self.models_loaded.emit(prov.id, [], "Couldn't list the models (details in the log)")
        threading.Thread(target=work, daemon=True, name="assist-models").start()

    def _models_loaded(self, provider_id: str, models: list, error: str) -> None:
        self._loading.discard(provider_id)
        self.list_btn.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self._models[provider_id] = models
        self.refresh()
        if provider_id == self._prov().id:
            self.status.setText(f"{len(models)} models can see images with your key; pick one above"
                                if models else "The service didn't list any models that can see images")

    def _provider_changed(self, _i) -> None:
        self._set("provider", self._prov().id)

    def _open_key_page(self) -> None:
        prov = self._prov()
        open_target(prov.key_url or "https://ollama.com/download")

    def _has_item(self) -> bool:
        return any((d.get("action") or {}).get("type") == "assist" for d in self.app.cfg.get("items", []))

    def _add_item(self) -> None:
        if not self._has_item():
            self.app.cfg.setdefault("items", []).append(copy.deepcopy(ASSIST_ITEM))
            self.app.apply_config()
            self.win.rotary.reload(self.win.rotary.list.currentRow())
        self.refresh()

    def _try(self) -> None:
        self._save_key()
        self._per_provider("models", self.model.currentText())
        self._per_provider("urls", self.url.text())
        missing = ai.ready(self.app.cfg)
        if missing:
            self.status.setText(missing)
            return
        self.status.setText("Asking… the answer appears on the notch")
        self.win.showMinimized()
        QTimer.singleShot(700, self.app.start_assist)

    def refresh(self) -> None:
        cfg = self.app.cfg
        a = ai.settings(cfg)
        prov = ai.provider(cfg)
        label = dict((k, t.split("  ")[0]) for k, t in ASSIST_HOTKEYS).get(a["hotkey"], "")
        how = f"Press {label}, choose" if label and a["hotkey"] != "off" else "Choose"
        self.intro.setText(
            f"{how} Screen assist on the rotary, or right-click the notch, and an AI looks at your screen, finds the "
            "question or problem on it and answers on the notch; with no question it goes by context clues or sums "
            "up the screen. For multiple choice it tells you which option (A, B, C…). Hover the answer for a longer, "
            "detailed one. It works with free AI services, or with your own paid Claude, ChatGPT or Gemini account.")
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(prov.id)))
        self.provider.blockSignals(False)
        self.provider_row.subtitle.setText(prov.note)
        has_key = bool(a["keys"].get(prov.id))
        self.key_row.setVisible(bool(prov.key_url))
        self.key.setPlaceholderText("saved — paste a new one to replace it" if has_key else "Paste your API key")
        self.get_key.setText("Get a key…" if prov.paid else "Get a free key…")
        chosen = str(a["models"].get(prov.id, ""))
        listed = self._models.get(prov.id, [])
        self.model.blockSignals(True)
        self.model.clear()
        for mid, name in listed:
            self.model.addItem(mid)
            if name != mid:
                self.model.setItemData(self.model.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
        self.model.lineEdit().setPlaceholderText(prov.model)
        self.model.setEditText(chosen)
        self.model.blockSignals(False)
        default = "the default" if prov.paid else "the free default"
        self.model_row.subtitle.setText(
            f"Pick one your key can use, or leave it empty for {default}. It has to be able to see images."
            if listed else f"Leave it empty for {default}, or List my models to pick one. It has to be able to see "
                           "images.")
        self.url.setPlaceholderText(prov.url)
        self.url.setText(str(a["urls"].get(prov.id, "")))
        self.url_row.setVisible(prov.style != "gemini")
        missing = ai.ready(cfg)
        self.status.setText(("Paste an API key above" + (", or choose Ollama to run it on this " + DEVICE
                                                          if not prov.paid else "") if missing else
                             f"Ready · {prov.name} · {ai.model_for(cfg, prov)}"))
        if not missing:
            self._load_models()
        self.add_btn.setEnabled(not self._has_item())
        self.add_btn.setText("On the rotary" if self._has_item() else "Add to the rotary")
        self.hotkey.blockSignals(True)
        self.hotkey.setCurrentIndex(max(0, self.hotkey.findData(a["hotkey"])))
        self.hotkey.blockSignals(False)
        self.seconds.set_value(str(int(a.get("seconds", 20))))


class CameraPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(page_header("Camera", "Green: your palm · Red: back of the hand · Yellow box: your face "
                                             "(hands resting near it are ignored)."))
        self.view = VideoView()
        self.view.setAccessibleName("Camera view")
        left.addWidget(self.view, 1)
        root.addLayout(left, 1)

        side = QVBoxLayout()
        side.setSpacing(18)
        side.addSpacing(62)
        c = Card("Source")
        self.combo = QComboBox()
        self.combo.setAccessibleName("Camera")
        self.combo.activated.connect(lambda i: self.app.tracker.select_camera(self.combo.itemData(i) or ""))
        w = QWidget()
        h = QVBoxLayout(w)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(8)
        h.addWidget(self.combo)
        rescan = QPushButton("Rescan cameras")
        rescan.clicked.connect(self.app.tracker.rescan)
        h.addWidget(rescan)
        self.stats = QLabel("—")
        self.stats.setObjectName("muted")
        h.addWidget(self.stats)
        c.add(w)
        side.addWidget(c)
        c = Card("Tracking")
        self.metrics = QLabel("No hand in view")
        self.metrics.setObjectName("value")
        self.metrics.setWordWrap(True)
        mw = QWidget()
        mh = QVBoxLayout(mw)
        mh.setContentsMargins(14, 10, 14, 10)
        mh.addWidget(self.metrics)
        c.add(mw)
        self.use = Switch(bool(self.app.cfg.get("general", {}).get("camera", True)))
        self.use.toggled.connect(self._use)
        c.add(Row("Hand gestures", "Off: the camera and hand tracking never start", self.use))
        self.pause = Switch(self.app.pause_action.isChecked())
        self.pause.toggled.connect(self.app.set_paused)
        c.add(Row("Pause for now", "Camera off until you turn this back off", self.pause))
        side.addWidget(c)
        side.addStretch(1)
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(300)
        root.addWidget(sw)

    def _use(self, on: bool) -> None:
        self.app.cfg.setdefault("general", {})["camera"] = on
        self.app.apply_config()
        self.refresh()

    def refresh(self) -> None:
        on = bool(self.app.cfg.get("general", {}).get("camera", True))
        self.use.set_silent(on)
        self.pause.setEnabled(on)
        self.combo.setEnabled(on)
        if not on:
            self.view.image = None
            self.view.message = "Hand gestures are off — use the notch, your mouse or the keyboard shortcut"
            self.stats.setText("Camera off")
            self.metrics.setText("—")
            self.view.update()

    def set_cameras(self, cameras, current_key) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        for cam in cameras:
            self.combo.addItem(cam.label(), cam.key)
            if cam.key == current_key:
                self.combo.setCurrentIndex(self.combo.count() - 1)
        if not cameras:
            self.combo.addItem("No camera", "")
        self.combo.blockSignals(False)

    def set_frame(self, image, hands, feats, chosen, snap, faces) -> None:
        if image is not None:
            self.view.image = image
        g = GestureSettings.from_dict(self.app.cfg.get("gestures"))
        self.view.still_seconds = g.still_seconds
        self.view.hands, self.view.feats, self.view.chosen, self.view.snap = hands, feats, chosen, snap
        self.view.faces = faces
        if 0 <= chosen < len(feats):
            f = feats[chosen]
            min_facing, max_tilt, min_handed = g.profile[0], g.profile[1], g.profile[2]
            if snap.presented:
                why = ["ready to open"]
            else:
                why = []
                if f.at_face and g.ignore_near_face:
                    why.append("touching your face")
                if not f.in_frame:
                    why.append("partly out of view")
                if f.upright_dev > max_tilt:
                    why.append("tilted — hold it upright")
                if f.scale < g.min_hand_size:
                    why.append("too far away")
                if f.handed_conf < min_handed:
                    why.append("show your open hand")
                if snap.facing_value < min_facing:
                    why.append("turn your palm toward the camera")
            self.metrics.setText(f"{f.handedness} hand — {', '.join(why) or 'tracking'}")
        else:
            self.metrics.setText("No hand in view")
        self.view.update()


# The license texts shipped next to the app (and at the top of the repo): (title, file).
LICENSES = [("RotaryUI — MIT License", "LICENSE"), ("Third-party software", "THIRD_PARTY_NOTICES.txt"),
            ("Apache License 2.0", "licenses/Apache-2.0.txt"), ("GNU LGPL 3.0", "licenses/LGPL-3.0.txt"),
            ("GNU GPL 3.0", "licenses/GPL-3.0.txt"), ("GNU LGPL 2.1", "licenses/LGPL-2.1.txt"),
            ("Python Software Foundation License", "licenses/Python.txt"),
            ("SIL Open Font License 1.1 (Inter)", "licenses/OFL-1.1.txt")]
COPYRIGHT = "Copyright © 2026 MochiBoy9"


class LicensesDialog(QDialog):
    """RotaryUI's license, the third-party notices and the full license texts they refer to."""

    def __init__(self, parent=None, start: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Licenses")
        self.setStyleSheet(stylesheet() + "QDialog { background: #18181c; }")
        self.resize(980, 640)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)
        self.list = QListWidget()
        self.list.setFixedWidth(250)
        self.list.setAccessibleName("Licenses")
        for title, _ in LICENSES:
            self.list.addItem(title)
        root.addWidget(self.list)
        right = QVBoxLayout()
        right.setSpacing(10)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setMinimumWidth(self.text.fontMetrics().horizontalAdvance("M" * 80) + 40)   # the texts' width
        self.text.setAccessibleName("License text")
        right.addWidget(self.text, 1)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        right.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(right, 1)
        self.list.currentRowChanged.connect(self._show)
        self.list.setCurrentRow(start)

    def _show(self, row: int) -> None:
        if row < 0:
            return
        try:
            self.text.setPlainText((ROOT / LICENSES[row][1]).read_text(encoding="utf-8"))
        except OSError:
            self.text.setPlainText(f"{LICENSES[row][1]} is missing from this copy of RotaryUI.\n"
                                   "See https://github.com/MochiBoy9/Gesture_Rotary")


class GeneralPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)
        v.addWidget(page_header("General", f"RotaryUI {__version__}"))
        gen = self.app.cfg.get("general", {})
        ap = self.app.cfg.get("appearance", {})

        c = Card("Startup")
        self.login = Switch(system.run_at_login())
        self.login.toggled.connect(self._login)
        c.add(Row(f"Start with {OS_NAME}" if not MAC else "Open at login", None, self.login))
        self.minimized = Switch(gen.get("start_minimized", True))
        self.minimized.toggled.connect(lambda on: self.set("general", "start_minimized", on))
        c.add(Row("Start minimised", "Open quietly on the taskbar" if WIN else "Open quietly in the background",
                  self.minimized))
        self.taskbar = Switch(gen.get("keep_in_taskbar", True))
        self.taskbar.toggled.connect(lambda on: self.set("general", "keep_in_taskbar", on))
        c.add(Row("Keep on the taskbar when closed" if WIN else "Keep in the Dock when closed" if MAC else
                  "Keep in the taskbar when closed",
                  "Otherwise closing hides it to the system tray" if not MAC else "Otherwise closing hides it to "
                  "the menu bar", self.taskbar))
        v.addWidget(c)

        c = Card("Appearance")
        self.font = QComboBox()
        self.font.setMaxVisibleItems(18)
        self.font.setMinimumWidth(240)
        for k, label in font_choices():
            self.font.addItem(label, k)
            if k:
                self.font.setItemData(self.font.count() - 1, font([k] if k not in ("inter", "system") else
                                      ([BUNDLED_TEXT] if k == "inter" else SYSTEM_TEXT), 13),
                                      Qt.ItemDataRole.FontRole)
        i = self.font.findData(ap.get("font", ""))
        self.font.setCurrentIndex(i if i >= 0 else 0)
        self.font.currentIndexChanged.connect(lambda _i: self.set("appearance", "font", self.font.currentData() or ""))
        c.add(Row("Font", "Used in this window, on the rotary, in the notch and in status messages", self.font))
        self.size = SliderControl(460, 760, 20, ap.get("dial_size", 540), lambda x: f"{x:.0f} px")
        self.size.changed.connect(lambda x: self.set("appearance", "dial_size", int(x)))
        c.add(Row("Rotary size", None, self.size))
        self.dial_clock = Segmented([("12h", "12-hour"), ("24h", "24-hour"), ("off", "Off")],
                                    ap.get("dial_clock", "12h"))
        self.dial_clock.setFixedWidth(300)
        self.dial_clock.changed.connect(lambda k: self.set("appearance", "dial_clock", k))
        c.add(Row("Clock on the rotary", "The time and date under the ring: 9:41 AM, or 21:41 as 24-hour time",
                  self.dial_clock))
        self.dial_weather = Switch(ap.get("dial_weather", True))
        self.dial_weather.toggled.connect(lambda on: self.set("appearance", "dial_weather", on))
        c.add(Row("Weather", "An icon for sun, cloud, rain or snow and the temperature beside the clock. It comes "
                             "from wttr.in, which works out roughly where you are from your internet address.",
                  self.dial_weather))
        self.pill = Switch(ap.get("status_pill", True))
        self.pill.toggled.connect(lambda on: self.set("appearance", "status_pill", on))
        c.add(Row("Status messages", "Short notes at the top of the screen — inside the notch when it's showing",
                  self.pill))
        self.live = Switch(ap.get("live_glass", False))
        self.live.toggled.connect(lambda on: self.set("appearance", "live_glass", on))
        c.add(Row("Live glass", "Keep the blur behind the rotary live. Hides the rotary from screen recordings.",
                  self.live))
        v.addWidget(c)

        c = Card("Code actions")
        self.python = Segmented([("auto", "Mine if installed"), ("bundled", "Built-in")], gen.get("python", "auto"))
        self.python.setFixedWidth(300)
        self.python.changed.connect(lambda k: self.set("general", "python", k))
        c.add(Row("Python for code actions", "Your own Python has your packages", self.python))
        v.addWidget(c)

        c = Card("Updates")
        self.auto_update = Switch(gen.get("auto_update", True))
        self.auto_update.toggled.connect(lambda on: self.set("general", "auto_update", on))
        c.add(Row("Update automatically", "When a new version is out, download it from GitHub and restart into it "
                  "while the rotary isn't in use", self.auto_update))
        b = QPushButton("Check now")
        b.clicked.connect(lambda: self.app.check_for_updates(manual=True))
        why = update.unsupported()
        row = Row("Check for updates", self.app.update_note or why or f"You have version {__version__}", b)
        self.app.update_state.connect(row.subtitle.setText)
        c.add(row)
        v.addWidget(c)

        c = Card("Help")
        b = QPushButton("Take the tour")
        b.clicked.connect(self.app.start_tutorial)
        c.add(Row("Tutorial", "Replay the first-run tour: which hand shapes to use, and how to use the rotary "
                              "without a camera", b))
        v.addWidget(c)

        c = Card("About")
        b = QPushButton("View")
        b.clicked.connect(lambda: LicensesDialog(self, 0).exec())
        c.add(Row("License", f"MIT License · {COPYRIGHT}", b))
        b = QPushButton("View")
        b.clicked.connect(lambda: LicensesDialog(self, 1).exec())
        c.add(Row("Third-party software", "Qt, MediaPipe, OpenCV, NumPy, the Inter font and more, each under its own license", b))
        v.addWidget(c)

        c = Card("Advanced")
        b1 = QPushButton("Open config.json")
        b1.clicked.connect(lambda: open_target(str(CONFIG_PATH)) if CONFIG_PATH.exists() else None)
        c.add(Row("Edit the raw settings file", None, b1))
        b2 = QPushButton("Open logs")
        b2.clicked.connect(lambda: open_target(str(DATA_DIR / "logs")))
        c.add(Row("Diagnostics", None, b2))
        q = QPushButton("Quit RotaryUI")
        q.setObjectName("danger")
        q.clicked.connect(self.app.quit)
        c.add(Row("Stop the app and turn the camera off", None, q))
        if system.install_folder() is not None:
            u = QPushButton("Uninstall…")
            u.setObjectName("danger")
            u.clicked.connect(self._uninstall)
            c.add(Row(f"Remove RotaryUI from this {DEVICE}", None, u))
        v.addWidget(c)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

    def set(self, section: str, key: str, value) -> None:
        self.app.cfg.setdefault(section, {})[key] = value
        self.app.apply_config()

    def _login(self, on: bool) -> None:
        try:
            system.set_run_at_login(on)
        except OSError as e:
            QMessageBox.warning(self, "Start at login", f"Couldn't change the startup setting:\n{e}")
            self.login.set_silent(system.run_at_login())

    def _uninstall(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Uninstall RotaryUI")
        box.setText(f"Remove RotaryUI from this {DEVICE}?")
        box.setInformativeText("This removes the app, its shortcuts and its start-at-login entry.")
        keep = QCheckBox("Also delete my settings")
        box.setCheckBox(keep)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.button(QMessageBox.StandardButton.Yes).setText("Uninstall")
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.app.uninstall(keep.isChecked())


class WelcomePage(QWidget):
    """First run: camera check, a quick setup, and which apps to put on the rotary."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(36, 30, 40, 32)
        v.setSpacing(20)
        v.addWidget(page_header("Welcome to RotaryUI",
                                "Open apps and run actions by moving your hand in front of your camera. "
                                "A few quick steps and you're set."))

        c = Card("1 · Camera")
        self.cam = QLabel("Looking for a camera…")
        self.cam.setObjectName("value")
        self.cam.setWordWrap(True)
        cw = QWidget()
        ch = QHBoxLayout(cw)
        ch.setContentsMargins(14, 10, 14, 10)
        ch.addWidget(self.cam, 1)
        show = QPushButton("See camera")
        show.clicked.connect(lambda: self.win.show_page("camera"))
        ch.addWidget(show)
        c.add(cw)
        self.mode = Segmented(INPUT_MODES, input_mode(self.app.cfg.get("general", {})))
        self.mode.setFixedWidth(380)
        self.mode.changed.connect(self._mode)
        c.add(Row("Open the rotary with", "No camera? Mouse & keyboard puts a small notch at the top of your "
                                         "screen — click it to open the rotary.", self.mode))
        v.addWidget(c)

        t = QLabel("2 · HOW WOULD YOU LIKE TO CONTROL IT?")
        t.setObjectName("h2")
        v.addWidget(t)
        self.presets = PresetGrid(self.app)
        v.addWidget(self.presets)

        t = QLabel("3 · YOUR APPS")
        t.setObjectName("h2")
        v.addWidget(t)
        sub = QLabel("Put these on the rotary as Quick nav — each opens a ring of that app's own actions.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        v.addWidget(sub)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.checks: dict[str, QCheckBox] = {}
        suggested = set(integrations.suggested_quicknav(4))
        pool = [a for a in integrations.installed()
                if a.spec.dynamic or a.id in suggested or a.spec.actions is not integrations._simple]
        have = {(d.get("action") or {}).get("app") for d in self.app.cfg.get("items", [])}
        for i, a in enumerate(sorted(pool, key=lambda a: (a.id not in suggested, a.name.lower()))[:12]):
            cb = QCheckBox(a.name)
            cb.setIcon(app_tile(a, 20))
            cb.setChecked(a.id in suggested or a.id in have)
            cb.setEnabled(a.id not in have)
            grid.addWidget(cb, i // 3, i % 3)
            self.checks[a.id] = cb
        if not self.checks:
            grid.addWidget(QLabel("No supported apps found — you can add any app later."), 0, 0)
        v.addWidget(grid_w)

        c = Card("4 · Try it", "Raise your hand toward the camera. Close it into a fist, or hold your palm still, "
                               "and the rotary appears. No camera? Click the notch at the top of the screen, or "
                               f"press {'⌃⌥Space' if MAC else 'Ctrl + Alt + Space'} — then scroll to turn it and "
                               "click to open.")
        tw = QWidget()
        th = QHBoxLayout(tw)
        th.setContentsMargins(14, 10, 14, 10)
        th.addStretch(1)
        trybtn = QPushButton("Open the rotary now")
        trybtn.clicked.connect(self.app.manual_open)
        th.addWidget(trybtn)
        c.add(tw)
        v.addWidget(c)

        done = QPushButton("Done — start using RotaryUI")
        done.setObjectName("accent")
        done.setMinimumHeight(40)
        done.clicked.connect(self._done)
        v.addWidget(done, 0, Qt.AlignmentFlag.AlignRight)
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))

    def set_camera(self, text: str) -> None:
        self.cam.setText(text)

    def _mode(self, mode: str) -> None:
        set_input_mode(self.app.cfg.setdefault("general", {}), mode)
        self.app.apply_config()
        if mode == "notch":
            self.cam.setText("Camera off — you'll use the notch at the top of the screen")

    def _done(self) -> None:
        have = {(d.get("action") or {}).get("app") for d in self.app.cfg.get("items", [])}
        items = self.app.cfg.setdefault("items", [])
        for app_id, cb in self.checks.items():
            if cb.isChecked() and cb.isEnabled() and app_id not in have:
                items.append(integrations.quicknav_item(app_id))
        self.app.cfg.setdefault("general", {})["onboarded"] = True
        self.app.apply_config()
        self.win.finish_welcome()
        if not self.app.cfg["general"].get("tutorial_done", False):
            self.app.start_tutorial()           # then a one-minute tour of how to use it


# =========================================================================== home
SECTION_BLURBS = {
    "welcome": "Set up your camera, controls and apps",
    "rotary": "What's on the ring, and in what order",
    "apps": "Quick nav rings for your apps, and OBS",
    "gestures": "Hand gestures and how they feel",
    "notch": "Mouse & keyboard, and the Dynamic Island",
    "assist": "Ask AI about what's on your screen",
    "accessibility": "Text size, contrast, motion and sound",
    "camera": "Which camera, and what it sees",
    "general": "Startup, appearance, fonts and updates",
}


def glyph_icon(code: str, color: QColor, px: int = 16) -> QIcon:
    """A bare symbol (no tile) as an icon, e.g. the magnifier in the search field."""
    pm = QPixmap(px * 2, px * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font(ICONS, px * 0.8))
    p.setPen(color)
    p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, chr(int(code, 16)))
    p.end()
    return QIcon(pm)


class SectionTile(QAbstractButton):
    """A way into one section from the home page: its symbol, name and what's in it."""

    def __init__(self, glyph: str, color: str, title: str, text: str):
        super().__init__()
        self.glyph, self.color, self.title, self.text = glyph, color, title, text
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.setMinimumHeight(int(70 * widgets.UI_SCALE))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:
        return QSize(240, int(72 * widgets.UI_SCALE))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        widgets.paint_panel(p, r, 14, hover=self.underMouse() or self.isDown())
        if self.hasFocus():
            widgets.focus_ring(p, r, 14)
        t = QRectF(r.left() + 14, r.center().y() - 17, 34, 34)
        tile = QPainterPath()
        tile.addRoundedRect(t, 9, 9)
        g = QLinearGradient(t.topLeft(), t.bottomLeft())
        g.setColorAt(0, QColor(self.color).lighter(118))
        g.setColorAt(1, QColor(self.color))
        p.fillPath(tile, g)
        p.setPen(QColor("white"))
        p.setFont(font(ICONS, 16))
        p.drawText(t, Qt.AlignmentFlag.AlignCenter, chr(int(self.glyph, 16)))
        k = widgets.UI_SCALE
        tf = font(TEXT, 13.5 * k, QFont.Weight.DemiBold)
        sf = font(TEXT, 11.5 * k)
        th, sh = QFontMetricsF(tf).height(), QFontMetricsF(sf).height()
        x = t.right() + 12
        w = r.right() - x - 28
        top = r.center().y() - (th + sh + 2) / 2
        p.setFont(tf)
        p.setPen(white(0.95))
        p.drawText(QRectF(x, top, w, th), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setFont(sf)
        p.setPen(white(0.58))
        p.drawText(QRectF(x, top + th + 2, w, sh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(sf).elidedText(self.text, Qt.TextElideMode.ElideRight, w))
        p.setFont(font(ICONS, 11))
        p.setPen(white(0.35))
        p.drawText(QRectF(r.right() - 28, r.top(), 18, r.height()), Qt.AlignmentFlag.AlignCenter, "")


class HomePage(QWidget):
    """The front page: whether the rotary is ready, the one button most people want, the switches people
    reach for most, and a way into every section."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.app = win.app
        self.win = win
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(28, 24, 36, 32)
        v.setSpacing(22)

        hero = widgets.Panel(radius=20)
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(22, 20, 22, 20)
        hl.setSpacing(18)
        icon = QLabel()
        icon.setPixmap(self.app.app_icon.pixmap(QSize(72, 72)))
        icon.setFixedSize(72, 72)
        hl.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel("RotaryUI")
        title.setObjectName("hero")
        col.addWidget(title)
        self.state = QLabel("")
        self.state.setObjectName("value")
        self.state.setWordWrap(True)
        col.addWidget(self.state)
        self.how = QLabel("")
        self.how.setObjectName("muted")
        self.how.setWordWrap(True)
        col.addWidget(self.how)
        col.addStretch(1)
        hl.addLayout(col, 1)
        self.open_btn = QPushButton("Open the rotary")
        self.open_btn.setObjectName("accent")
        self.open_btn.setStyleSheet("padding: 9px 22px; border-radius: 10px; font-size: "
                                    f"{14 * widgets.UI_SCALE:.1f}px;")
        self.open_btn.clicked.connect(self.app.manual_open)
        hl.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(hero)

        gen = self.app.cfg.get("general", {})
        ap = self.app.cfg.get("appearance", {})
        c = Card("Quick settings")
        self.mode = Segmented(INPUT_MODES, input_mode(gen))
        self.mode.setFixedWidth(380)
        self.mode.changed.connect(self._mode)
        c.add(Row("Open the rotary with", None, self.mode))
        self.sounds = Switch(bool(ap.get("sounds", True)))
        self.sounds.toggled.connect(lambda on: self._ap("sounds", on))
        c.add(Row("Sounds", "Soft ticks while choosing", self.sounds))
        self.pill = Switch(bool(ap.get("status_pill", True)))
        self.pill.toggled.connect(lambda on: self._ap("status_pill", on))
        c.add(Row("Status messages", "Short notes at the top of the screen", self.pill))
        tour = QPushButton("Take the tour")
        tour.clicked.connect(self.app.start_tutorial)
        c.add(Row("Tutorial", "A one-minute tour of the hand shapes, the notch and the mouse", tour))
        v.addWidget(c)

        t = QLabel("ALL SETTINGS")
        t.setObjectName("h2")
        v.addWidget(t)
        grid_w = QWidget()
        self.grid = QGridLayout(grid_w)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        v.addWidget(grid_w)
        self.tiles: dict[str, SectionTile] = {}
        self.fill_tiles()
        v.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll_page(inner))
        self.refresh()

    def fill_tiles(self) -> None:
        for tile in self.tiles.values():
            self.grid.removeWidget(tile)
            tile.deleteLater()
        self.tiles = {}
        keys = [k for k in self.win._keys if k not in ("home", "welcome")]
        for i, key in enumerate(keys):
            _k, label, glyph, color = self.win.info(key)
            tile = SectionTile(glyph, color, label, SECTION_BLURBS.get(key, ""))
            tile.clicked.connect(lambda _=False, k=key: self.win.show_page(k))
            self.grid.addWidget(tile, i // 2, i % 2)
            self.tiles[key] = tile

    def _mode(self, mode: str) -> None:
        set_input_mode(self.app.cfg.setdefault("general", {}), mode)
        self.app.apply_config()
        self.refresh()

    def _ap(self, key: str, value) -> None:
        self.app.cfg.setdefault("appearance", {})[key] = value
        self.app.apply_config()

    def set_status(self, text: str) -> None:
        gen = self.app.cfg.get("general", {})
        if not gen.get("camera", True):
            self.state.setText("Ready · hand gestures are off, so the notch, your mouse and keyboard open it")
        elif text.startswith("●"):
            self.state.setText(f"Ready · watching for your hand with {text.lstrip('● ')}")
        else:
            self.state.setText(text or "Looking for a camera…")

    def refresh(self) -> None:
        gen = self.app.cfg.get("general", {})
        ap = self.app.cfg.get("appearance", {})
        self.mode.set_value(input_mode(gen))
        self.sounds.set_silent(bool(ap.get("sounds", True)))
        self.pill.set_silent(bool(ap.get("status_pill", True)))
        ways = []
        if gen.get("camera", True):
            ways.append("close your hand or hold your palm still")
        hk = dict(HOTKEYS).get(gen.get("hotkey", "ctrl+alt+space"), "Off")
        if hk != "Off":
            ways.append("press " + hk.split("  (")[0])
        mb = gen.get("mouse_button", "middle")
        if mb != "off":
            ways.append({"middle": "hold the middle mouse button", "back": "press the mouse's back button",
                         "forward": "press the mouse's forward button"}.get(mb, "use your mouse button"))
        if not gen.get("camera", True) or gen.get("notch", "auto") == "on":
            ways.append("click the notch at the top of the screen")
        self.how.setText(("To open it: " + ", ".join(ways[:-1]) + (" or " if len(ways) > 1 else "") + ways[-1]
                          + ".") if ways else "")
        self.set_status(self.win.status.text())


# =========================================================================== window
class MainWindow(QWidget):
    """The RotaryUI window: a floating glass sidebar (search, sections, status) beside the current
    page, all over a soft, blurred copy of your wallpaper."""
    # (key, label, glyph, tile colour, sidebar group)
    PAGES = [("welcome", "Welcome", "E734", "#FF9F0A", ""), ("home", "Home", "E80F", "#0A84FF", ""),
             ("rotary", "Rotary", "E8F1", "#0A84FF", "Rotary"), ("apps", "Apps", "F0E2", "#FF375F", "Rotary"),
             ("gestures", "Controls", "E815", "#FF9F0A", "Rotary"),
             ("notch", "Notch", "E962", "#5E5CE6", "Features"), ("assist", "Screen assist", "E897", "#BF5AF2", "Features"),
             ("accessibility", "Accessibility", "E776", "#30D158", "System"),
             ("camera", "Camera", "E722", "#64D2FF", "System"), ("general", "General", "E713", "#8E8E93", "System")]

    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.setObjectName("window")
        self.setWindowTitle("RotaryUI")
        widgets.UI_SCALE = float(app.cfg.get("appearance", {}).get("text_scale", 1.0))
        self.setStyleSheet(stylesheet())
        self.resize(1180, 800)
        self.setMinimumSize(980, 660)
        self._dark_done = False

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 0, 10)
        root.setSpacing(0)
        side = widgets.Panel(radius=18, tint=1.0)
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(8)
        bh = QHBoxLayout()
        bh.setContentsMargins(6, 0, 0, 4)
        bh.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app.app_icon.pixmap(QSize(28, 28)))
        logo.setFixedSize(28, 28)
        bh.addWidget(logo)
        brand = QLabel("RotaryUI")
        brand.setObjectName("brand")
        bh.addWidget(brand, 1)
        sv.addLayout(bh)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText(f"Search  ({'⌘' if MAC else 'Ctrl+'}F)")
        self.search.setAccessibleName("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(glyph_icon("E721", white(0.55)), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._search)
        self.search.installEventFilter(self)
        sv.addWidget(self.search)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(24, 24))
        self.nav.setAccessibleName("Sections")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sv.addWidget(self.nav, 1)
        self.open_btn = QPushButton("Open rotary now")
        self.open_btn.setObjectName("accent")
        self.open_btn.clicked.connect(self.app.manual_open)
        sv.addWidget(self.open_btn)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(6, 2, 6, 0)
        sv.addWidget(self.status)
        root.addWidget(side)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.built = False                 # pages are made the first time the window is actually shown
        self.welcome = None
        self.home = None
        welcome = not app.cfg.get("general", {}).get("onboarded", False)
        self._keys: list[str] = [p[0] for p in self.PAGES if p[0] != "welcome" or welcome]
        self._fill_nav()
        root.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self._page)

        self.results = QListWidget(self)               # search results, floating under the search field
        self.results.setObjectName("results")
        self.results.setAccessibleName("Search results")
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(self._go)
        self.results.hide()
        self._index: list[tuple[str, str, str, QWidget | None]] | None = None    # (key, title, where, widget)

        find = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        for n in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
            sc.activated.connect(lambda n=n: self._keys[n - 1:n] and self.show_page(self._keys[n - 1]))
        self.setWindowIcon(app.app_icon)
        self.tab_focus_only(self)

    # ------------------------------------------------------------------ sidebar
    def info(self, key: str) -> tuple[str, str, str, str]:
        return next(p[:4] for p in self.PAGES if p[0] == key)

    def _fill_nav(self) -> None:
        self.nav.blockSignals(True)
        current = self.current_key()
        self.nav.clear()
        group = None
        k = widgets.UI_SCALE
        for key in self._keys:
            _key, label, glyph, color, g = next(p for p in self.PAGES if p[0] == key)
            if g != group:
                group = g
                if g:
                    head = QListWidgetItem(g)
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    head.setForeground(white(0.42))
                    head.setFont(font(TEXT, 11 * k, QFont.Weight.DemiBold))
                    head.setSizeHint(QSize(0, int(30 * k)))
                    self.nav.addItem(head)
            it = QListWidgetItem(glyph_tile(glyph, color), "  " + label)
            it.setSizeHint(QSize(0, int(38 * k)))
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setData(Qt.ItemDataRole.AccessibleTextRole, label)
            self.nav.addItem(it)
        self.nav.blockSignals(False)
        if current:
            self._select_nav(current)

    def _row_of(self, key: str) -> int:
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                return i
        return -1

    def _select_nav(self, key: str) -> None:
        row = self._row_of(key)
        if row >= 0:
            self.nav.setCurrentRow(row)

    def current_key(self) -> str | None:
        it = self.nav.currentItem() if hasattr(self, "nav") else None
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    # ------------------------------------------------------------------ search
    def _build_index(self) -> list[tuple[str, str, str, QWidget | None]]:
        """Everything a search can find: each section, and every titled setting on each page."""
        out = []
        for key in self._keys:
            _k, label, *_ = self.info(key)
            out.append((key, label, SECTION_BLURBS.get(key, ""), None))
            page = self.pages.get(key)
            if page is None or key in ("home", "welcome"):
                continue
            for row in page.findChildren(Row):
                card = row.parent()
                while card is not None and not isinstance(card, Card):
                    card = card.parent()
                where = label + (f" › {card.title.capitalize() if card.title.isupper() else card.title}"
                                 if card is not None and card.title else "")
                out.append((key, row.title.text(), where, row))
            for tile in page.findChildren(PresetTile):
                out.append((key, tile.title, label, tile))
        return out

    def _search(self, text: str) -> None:
        q = text.strip().casefold()
        self.results.clear()
        if not q or not self.built:
            self.results.hide()
            return
        if self._index is None:
            self._index = self._build_index()
        words = q.split()
        seen = set()
        hits = []
        for key, title, where, w in self._index:
            subtitle = w.subtitle.text() if isinstance(w, Row) and w.subtitle is not None else ""
            hay = f"{title} {where} {subtitle}".casefold()
            if all(word in hay for word in words) and (key, title, where) not in seen:
                seen.add((key, title, where))
                score = (0 if title.casefold().startswith(q) else 1 if q in title.casefold() else 2)
                hits.append((score, key, title, where, w))
        hits.sort(key=lambda h: h[0])
        for _s, key, title, where, w in hits[:8]:
            _k, _l, glyph, color = self.info(key)
            it = QListWidgetItem(glyph_tile(glyph, color, 24), f"{title}\n{where}")
            it.setData(Qt.ItemDataRole.UserRole, (key, w))
            it.setSizeHint(QSize(0, int(46 * widgets.UI_SCALE)))
            self.results.addItem(it)
        if not hits:
            it = QListWidgetItem(f"No results for “{text.strip()}”")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(it)
        else:
            self.results.setCurrentRow(0)
        self._place_results()

    def _place_results(self) -> None:
        g = self.search.geometry()
        top_left = self.search.mapTo(self, g.bottomLeft() - g.topLeft())
        n = self.results.count()
        h = sum(self.results.sizeHintForRow(i) for i in range(n)) + 12
        self.results.setGeometry(top_left.x(), top_left.y() + 6, 360, min(h, 420))
        self.results.show()
        self.results.raise_()

    def _go(self, item: QListWidgetItem | None) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return
        key, w = data
        self.search.clear()
        self.results.hide()
        self.show_page(key)
        if w is not None:
            QTimer.singleShot(60, lambda: self._reveal(w))

    def _reveal(self, w: QWidget) -> None:
        """Scroll a setting into view and flash the card it's in."""
        from PySide6.QtWidgets import QScrollArea
        sa = w.parent()
        while sa is not None and not isinstance(sa, QScrollArea):
            sa = sa.parent()
        if sa is not None:
            sa.ensureWidgetVisible(w, 0, 120)
        glass = w
        while glass is not None and not isinstance(glass, widgets.Panel):
            glass = glass.parent()
        if glass is not None:
            glass.flash()
        (w.control if isinstance(w, Row) and w.control is not None else w).setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.Type.KeyPress:
            key = e.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.results.isVisible():
                step = 1 if key == Qt.Key.Key_Down else -1
                self.results.setCurrentRow(max(0, min(self.results.count() - 1, self.results.currentRow() + step)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.results.isVisible():
                self._go(self.results.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.search.clear()
                self.results.hide()
                self.stack.setFocus()
                return True
        if obj is self.search and e.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(150, lambda: None if self.search.hasFocus() else self.results.hide())
        return super().eventFilter(obj, e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.results.isVisible():
            self._place_results()

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), QColor(widgets.BG))

    def restyle(self) -> None:
        """After a change of font or text size: style sheets, the sidebar and the pages' drawn text."""
        self.setStyleSheet(stylesheet())
        self._fill_nav()
        if self.built:
            self.rotary.list.doItemsLayout()
            self.home.fill_tiles()
            for w in self.findChildren(QWidget):
                w.update()
        self.update()

    # ------------------------------------------------------------------ pages
    def ensure_pages(self) -> None:
        if self.built:
            return
        self.built = True
        self.apps = AppsPage(self)
        self.rotary = RotaryPage(self)
        self.gestures = GesturesPage(self)
        self.notch = NotchPage(self)
        self.assist = AssistPage(self)
        self.accessibility = AccessibilityPage(self)
        self.camera = CameraPage(self)
        self.general = GeneralPage(self)
        self.home = HomePage(self)
        if "welcome" in self._keys:
            self.welcome = WelcomePage(self)
        for key in self._keys:
            page = self.page(key)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.tab_focus_only(self.stack)
        self.app.window_built()
        self.nav.setFocus(Qt.FocusReason.OtherFocusReason)     # not the search field: typing shouldn't land there
        if self.nav.currentRow() < 0:
            self._select_nav(self._keys[0])
        else:
            self._page(self.nav.currentRow())

    def page(self, key: str) -> QWidget | None:
        """A page if the window has been opened at least once (the app checks before updating one)."""
        return getattr(self, key, None) if self.built else None

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and self.isVisible() and not self.isMinimized():
            self.ensure_pages()

    @staticmethod
    def tab_focus_only(root: QWidget) -> None:
        """Clicking a button shouldn't leave a focus ring on it; Tab still reaches everything."""
        from PySide6.QtWidgets import QAbstractButton, QSlider
        for w in root.findChildren(QAbstractButton) + root.findChildren(QSlider) + root.findChildren(Segmented):
            w.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def _page(self, i: int) -> None:
        it = self.nav.item(i) if 0 <= i < self.nav.count() else None
        key = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        if not self.built or key not in self.pages:
            return
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if page is self.gestures:
            self.gestures.refresh()
        elif page is self.accessibility:
            self.accessibility.refresh()
        elif page is self.apps:
            self.apps.sync_switches()
        elif page is self.notch:
            self.notch.refresh()
        elif page is self.assist:
            self.assist.refresh()
        elif page is self.camera:
            self.camera.refresh()
        elif page is self.home:
            self.home.refresh()

    def show_page(self, key: str) -> None:
        self.ensure_pages()
        if key in self._keys:
            self._select_nav(key)

    def finish_welcome(self) -> None:
        if self.welcome is None:
            return
        self._keys.remove("welcome")
        self.stack.removeWidget(self.welcome)
        self.welcome.deleteLater()
        self.pages.pop("welcome", None)
        self.welcome = None
        self._index = None
        self._fill_nav()
        self.rotary.reload()
        self.apps.sync_switches()
        self.show_page("home")

    def apply_text_scale(self, k: float) -> None:
        widgets.UI_SCALE = k
        self.restyle()

    def wants_frames(self) -> bool:
        return (self.built and self.isVisible() and not self.isMinimized()
                and self.stack.currentWidget() is self.camera)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        if self.home is not None:
            self.home.set_status(text)
        if self.welcome is not None:
            self.welcome.set_camera(text.lstrip("● ") or "Looking for a camera…")

    def showEvent(self, e):
        super().showEvent(e)
        if not self.isMinimized():
            self.ensure_pages()
        if not self._dark_done and sys.platform == "win32":
            self._dark_done = True
            try:
                hwnd = int(self.winId())
                on = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)   # dark title bar
                cap = ctypes.c_int(0x00191916)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), 4)
            except Exception:
                pass

    def closeEvent(self, e):
        if self.app.quitting:
            e.accept()
            return
        e.ignore()
        if self.app.cfg.get("general", {}).get("keep_in_taskbar", True):
            self.showMinimized()
        else:
            self.hide()
