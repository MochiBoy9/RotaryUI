"""Small Apple-style widget kit for the settings window: plain dark panels."""
from __future__ import annotations

import re

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QColorDialog, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget)

from .glass import DISPLAY, ICONS, TEXT, family, font, white

UI_SCALE = 1.0   # text size multiplier for the settings window (Accessibility → Text size)


def focus_ring(p: QPainter, r: QRectF, radius: float) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(ACCENT), 2))
    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)

ACCENT = "#0A84FF"
GREEN = "#30D158"
BG = "#141418"
SIDEBAR = "#1d1d21"
CARD = "#232328"
HAIRLINE = "rgba(255,255,255,0.08)"
POPUP = "#26262c"            # menus, drop-downs and pop-ups: solid, since they float over other windows

APPLE_COLORS = ["#0A84FF", "#32ADE6", "#64D2FF", "#30D158", "#FFD60A", "#FF9F0A",
                "#FF453A", "#FF375F", "#BF5AF2", "#5E5CE6", "#AC8E68", "#8E8E93"]

# Segoe Fluent Icons code points offered in the icon picker.
GLYPHS = [
    ("E774", "Globe"), ("E8B7", "Folder"), ("E756", "Terminal"), ("E713", "Settings"), ("E768", "Play"),
    ("E769", "Pause"), ("E893", "Next"), ("E892", "Previous"), ("E8D6", "Music"), ("E714", "Video"),
    ("E722", "Camera"), ("E8B9", "Picture"), ("E7F4", "Monitor"), ("E7F8", "Laptop"), ("E80F", "Home"),
    ("E715", "Mail"), ("E8BD", "Message"), ("E717", "Phone"), ("E77B", "Person"), ("E716", "People"),
    ("E721", "Search"), ("E734", "Star"), ("EB51", "Heart"), ("E787", "Calendar"), ("E823", "Clock"),
    ("E916", "Stopwatch"), ("E8EF", "Calculator"), ("E8A5", "Document"), ("E70F", "Edit"), ("E8C8", "Copy"),
    ("E77F", "Paste"), ("E74E", "Save"), ("E74D", "Delete"), ("E72E", "Lock"), ("E7E8", "Power"),
    ("E708", "Moon"), ("E706", "Sun"), ("E767", "Volume"), ("E74F", "Mute"), ("E720", "Microphone"),
    ("E7FC", "Game"), ("E719", "Store"), ("E7BF", "Cart"), ("E701", "Wi-Fi"), ("E702", "Bluetooth"),
    ("E71B", "Link"), ("E943", "Code"), ("E90F", "Tools"), ("E945", "Lightning"), ("E8A7", "Open"),
    ("E946", "Info"), ("E897", "Help"), ("E7BA", "Warning"), ("E8FB", "Check"), ("E710", "Add"),
    ("E72C", "Refresh"), ("E8F1", "Library"), ("E7C3", "Page"), ("E909", "World"), ("E81D", "Location"),
]


def stylesheet(scale: float | None = None) -> str:
    k = UI_SCALE if scale is None else scale
    css = _stylesheet()
    return re.sub(r"font-size: ([\d.]+)px", lambda m: f"font-size: {float(m.group(1)) * k:.1f}px", css)


def _stylesheet() -> str:
    text, display = family(TEXT), family(DISPLAY)
    return f"""
    QWidget {{ color: rgba(255,255,255,0.92); font-family: '{text}'; font-size: 13px; }}
    QWidget#window {{ background: {BG}; }}
    QDialog {{ background: {BG}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QStackedWidget, QWidget#page {{ background: transparent; }}
    QLabel#h1 {{ font-family: '{display}'; font-size: 28px; font-weight: 700; color: white; }}
    QLabel#h2 {{ font-size: 11.5px; font-weight: 600; color: rgba(255,255,255,0.50); padding-left: 14px;
        letter-spacing: 0.4px; }}
    QLabel#brand {{ font-family: '{display}'; font-size: 15px; font-weight: 700; color: white; }}
    QLabel#navhead {{ font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.42); padding: 10px 10px 2px 10px; }}
    QLabel#muted {{ color: rgba(255,255,255,0.55); font-size: 12px; }}
    QLabel#rowtitle {{ font-size: 13.5px; }}
    QLabel#value {{ color: rgba(255,255,255,0.60); font-size: 12.5px; }}
    QLabel#tiletitle {{ font-size: 13.5px; font-weight: 600; color: white; }}
    QLabel#hero {{ font-family: '{display}'; font-size: 22px; font-weight: 700; color: white; }}
    QFrame#hairline {{ background: {HAIRLINE}; max-height: 1px; min-height: 1px; border: none; margin-left: 14px; }}
    QLineEdit, QPlainTextEdit, QComboBox, QKeySequenceEdit {{
        background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.11); border-radius: 9px;
        padding: 6px 10px; selection-background-color: {ACCENT}; }}
    QLineEdit:hover, QComboBox:hover {{ background: rgba(255,255,255,0.10); }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; background: rgba(0,0,0,0.18); }}
    QLineEdit#search {{ border-radius: 10px; padding: 6px 10px; background: rgba(255,255,255,0.08); }}
    QPlainTextEdit {{ font-family: 'Cascadia Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono', monospace; font-size: 12.5px; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {POPUP}; border: 1px solid rgba(255,255,255,0.12);
        selection-background-color: {ACCENT}; outline: none; padding: 4px; }}
    QMenu {{ background: {POPUP}; border: 1px solid rgba(255,255,255,0.12); border-radius: 10px; padding: 5px; }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.10); margin: 4px 8px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.28);
        background: rgba(255,255,255,0.06); }}
    QCheckBox::indicator:hover {{ border: 1px solid rgba(255,255,255,0.45); }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid rgba(255,255,255,0.30);
        image: url({_check_png()}); }}
    QCheckBox::indicator:disabled {{ background: rgba(255,255,255,0.03); border-color: rgba(255,255,255,0.12); }}
    QCheckBox:disabled {{ color: rgba(255,255,255,0.4); }}
    QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.15),
        stop:1 rgba(255,255,255,0.08)); border: 1px solid rgba(255,255,255,0.13);
        border-top-color: rgba(255,255,255,0.24); border-radius: 8px; padding: 6px 16px; min-height: 18px; }}
    QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,0.21),
        stop:1 rgba(255,255,255,0.12)); }}
    QPushButton:pressed {{ background: rgba(255,255,255,0.22); }}
    QPushButton:disabled {{ color: rgba(255,255,255,0.3); background: rgba(255,255,255,0.05); }}
    QPushButton#accent {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3D9BFF, stop:1 #0A7AF0);
        border: 1px solid rgba(255,255,255,0.18); border-top-color: rgba(255,255,255,0.40); color: white;
        font-weight: 600; }}
    QPushButton#accent:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #55A8FF, stop:1 #1F88FA); }}
    QPushButton#accent:pressed {{ background: #0A6FDB; }}
    QPushButton#danger {{ color: #FF6961; }}
    QToolButton#icon {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px; font-family: 'Segoe Fluent Icons', 'Rotary Icons'; font-size: 13px;
        min-width: 30px; min-height: 28px; }}
    QToolButton#icon:hover {{ background: rgba(255,255,255,0.15); }}
    QToolButton#icon:disabled {{ color: rgba(255,255,255,0.25); }}
    QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.16); border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e9e9ee);
        width: 22px; height: 22px; margin: -9px 0; border-radius: 11px; border: 1px solid rgba(0,0,0,0.22); }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ border-radius: 9px; padding: 0px; margin: 1px 0px; }}
    QListWidget::item:selected {{ background: rgba(255,255,255,0.12); }}
    QListWidget::item:hover:!selected {{ background: rgba(255,255,255,0.06); }}
    QListWidget#nav::item {{ padding: 6px 6px; border-radius: 9px; color: rgba(255,255,255,0.88); }}
    QListWidget#nav::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255,0.20), stop:1 rgba(255,255,255,0.11)); color: white; }}
    QListWidget#nav::item:disabled {{ background: transparent; }}
    QListWidget#results {{ background: {POPUP}; border: 1px solid rgba(255,255,255,0.12); border-radius: 12px;
        padding: 4px; }}
    QListWidget#results::item {{ padding: 6px 8px; border-radius: 7px; }}
    QListWidget#results::item:selected {{ background: {ACCENT}; color: white; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.20); border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {POPUP}; color: white; border: 1px solid rgba(255,255,255,0.12); padding: 4px 8px; }}
    QPushButton:focus, QToolButton:focus {{ border: 2px solid {ACCENT}; }}
    QSlider:focus {{ background: rgba(10,132,255,0.10); border-radius: 6px; }}
    QListWidget:focus {{ outline: none; }}
    """


_CHECK: str | None = None


def _check_png() -> str:
    """A white tick for checked check boxes, drawn once into the temp folder (style sheets want a file;
    Qt picks the @2x copy on high-DPI screens)."""
    global _CHECK
    if _CHECK is None:
        import tempfile
        from pathlib import Path
        from PySide6.QtGui import QImage
        base = Path(tempfile.gettempdir()) / "gesture-rotary-check"
        _CHECK = ""
        for k, suffix in ((1, ""), (2, "@2x")):
            img = QImage(18 * k, 18 * k, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.scale(k, k)
            path = QPainterPath(QPointF(4.5, 9.5))
            path.lineTo(7.5, 12.5)
            path.lineTo(13.5, 5.5)
            pen = QPen(QColor("white"), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
            p.end()
            if img.save(str(base) + suffix + ".png") and not suffix:
                _CHECK = (str(base) + ".png").replace("\\", "/")
    return _CHECK


# --------------------------------------------------------------------------- panels
def paint_panel(p: QPainter, r: QRectF, radius: float, tint: float = 0.0, highlight: float = 0.0,
                hover: bool = False) -> None:
    """A plain dark panel: a solid fill and a hairline edge. (Liquid glass is kept for the rotary itself.)"""
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    base = QColor(SIDEBAR if tint > 0.5 else CARD)
    if hover:
        base = base.lighter(118)
    p.fillPath(path, base)
    if highlight > 0:
        p.fillPath(path, QColor(10, 132, 255, int(70 * highlight)))
    p.setPen(QPen(white(0.08), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


class Panel(QFrame):
    """A frame drawn as a plain panel (see paint_panel): cards, the sidebar, the home page's hero."""

    def __init__(self, radius: float = 14, tint: float = 0.0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.tint = tint             # 0 (cards) or 1 (the sidebar)
        self.highlight = 0.0         # 0..1: search flashes the card holding what it found
        self._flash = QVariantAnimation(self)
        self._flash.setDuration(1600)
        self._flash.setKeyValueAt(0, 0.0)
        self._flash.setKeyValueAt(0.15, 1.0)
        self._flash.setKeyValueAt(0.6, 1.0)
        self._flash.setKeyValueAt(1, 0.0)
        self._flash.valueChanged.connect(lambda v: (setattr(self, "highlight", float(v)), self.update()))

    def flash(self) -> None:
        self._flash.stop()
        self._flash.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_panel(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.tint, self.highlight)


# --------------------------------------------------------------------------- switch
class Switch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(50, 32)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def set_silent(self, on: bool) -> None:
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(50, 32)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            focus_ring(p, QRectF(self.rect()), self.height() / 2)
        r = QRectF(3, 4, self.width() - 6, self.height() - 8)
        off, on = QColor(255, 255, 255, 40), QColor(GREEN)
        t = self._pos
        col = QColor.fromRgbF(off.redF() + (on.redF() - off.redF()) * t, off.greenF() + (on.greenF() - off.greenF()) * t,
                              off.blueF() + (on.blueF() - off.blueF()) * t, off.alphaF() + (1 - off.alphaF()) * t)
        if not self.isEnabled():
            col.setAlphaF(col.alphaF() * 0.4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + t * (r.width() - d - 4)
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


# --------------------------------------------------------------------------- segmented
class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.options = options
        self.index = max(0, [k for k, _ in options].index(current) if current in [k for k, _ in options] else 0)
        self._x = float(self.index)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_x", float(v)), self.update()))
        self.setFixedHeight(34)
        self.setMinimumWidth(80 * len(options))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription(self.options[self.index][1])

    def keyPressEvent(self, e):
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Up: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Down: 1}.get(e.key())
        if step is None:
            return super().keyPressEvent(e)
        i = max(0, min(len(self.options) - 1, self.index + step))
        if i != self.index:
            self.set_value(self.options[i][0], animate=True)
            self.changed.emit(self.value)

    @property
    def value(self) -> str:
        return self.options[self.index][0]

    def set_value(self, key: str, animate: bool = False) -> None:
        keys = [k for k, _ in self.options]
        if key in keys:
            i = keys.index(key)
            if animate:
                self._anim.stop()
                self._anim.setStartValue(self._x)
                self._anim.setEndValue(float(i))
                self._anim.start()
            else:
                self._x = float(i)
            self.index = i
            self.setAccessibleDescription(self.options[i][1])
            self.update()

    def mousePressEvent(self, e):
        w = self.width() / len(self.options)
        i = int(max(0, min(len(self.options) - 1, e.position().x() // w)))
        if i != self.index:
            self.set_value(self.options[i][0], animate=True)
            self.changed.emit(self.value)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(r, 9, 9)
        w = r.width() / len(self.options)
        pill = QRectF(r.left() + 2 + self._x * w, r.top() + 2, w - 4, r.height() - 4)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(pill.translated(0, 1), 7, 7)
        p.setBrush(QColor(255, 255, 255, 52))
        p.drawRoundedRect(pill, 7, 7)
        if self.hasFocus():
            focus_ring(p, r, 9)
        p.setFont(font(TEXT, 12.5 * UI_SCALE, QFont.Weight.DemiBold))
        for i, (_, label) in enumerate(self.options):
            p.setPen(white(0.95 if i == self.index else 0.62))
            p.drawText(QRectF(r.left() + i * w, r.top(), w, r.height()), Qt.AlignmentFlag.AlignCenter, label)


# --------------------------------------------------------------------------- layout helpers
def hairline() -> QFrame:
    f = QFrame()
    f.setObjectName("hairline")
    return f


class Card(QWidget):
    """Section title + rounded card holding rows separated by hairlines (iOS inset-grouped list)."""

    def __init__(self, title: str | None = None, footer: str | None = None):
        super().__init__()
        self.title = title or ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("h2")
            outer.addWidget(t)
        self.frame = Panel()
        self.frame.setObjectName("card")
        self.lay = QVBoxLayout(self.frame)
        self.lay.setContentsMargins(0, 4, 0, 4)
        self.lay.setSpacing(0)
        outer.addWidget(self.frame)
        self.footer = None
        if footer:
            self.footer = QLabel(footer)
            self.footer.setObjectName("muted")
            self.footer.setWordWrap(True)
            self.footer.setContentsMargins(14, 0, 14, 0)
            outer.addWidget(self.footer)
        self._rows = 0

    def add(self, w: QWidget) -> QWidget:
        if self._rows:
            self.lay.addWidget(hairline())
        self.lay.addWidget(w)
        self._rows += 1
        return w


class Row(QWidget):
    def __init__(self, title: str, subtitle: str | None = None, control: QWidget | None = None,
                 wide: bool = False):
        super().__init__()
        lay = QHBoxLayout(self) if not wide else QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(12 if not wide else 8)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("rowtitle")
        txt.addWidget(self.title)
        self.subtitle = None
        if subtitle is not None:
            self.subtitle = QLabel(subtitle)
            self.subtitle.setObjectName("muted")
            self.subtitle.setWordWrap(True)
            txt.addWidget(self.subtitle)
        lay.addLayout(txt, 1)
        if control is not None:
            lay.addWidget(control, 0 if not wide else 0)
            if not control.accessibleName():
                control.setAccessibleName(title)
            if subtitle and not control.accessibleDescription():
                control.setAccessibleDescription(subtitle)
        self.control = control
        self.setMinimumHeight(44)


class SliderControl(QWidget):
    changed = Signal(float)

    def __init__(self, lo: float, hi: float, step: float, value: float, fmt, width: int = 220, invert: bool = False):
        super().__init__()
        self.lo, self.hi, self.step, self.fmt, self.invert = lo, hi, step, fmt, invert
        self.n = int(round((hi - lo) / step))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.label = QLabel()
        self.label.setObjectName("value")
        self.label.setMinimumWidth(92)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self.n)
        self.slider.setFixedWidth(width)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.label)
        lay.addWidget(self.slider)
        self.set_value(value)
        self.slider.valueChanged.connect(self._moved)

    def _to_value(self, i: int) -> float:
        i = self.n - i if self.invert else i
        return round(self.lo + i * self.step, 4)

    def set_value(self, v: float) -> None:
        i = int(round((max(self.lo, min(self.hi, v)) - self.lo) / self.step))
        i = self.n - i if self.invert else i
        self.slider.blockSignals(True)
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self.label.setText(self.fmt(self._to_value(i)))

    def value(self) -> float:
        return self._to_value(self.slider.value())

    def _moved(self, i: int) -> None:
        v = self._to_value(i)
        self.label.setText(self.fmt(v))
        self.changed.emit(v)


def scroll_page(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


def icon_button(glyph: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setObjectName("icon")
    b.setText(glyph)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


# --------------------------------------------------------------------------- pickers
class Swatch(QAbstractButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(color)
        self.setAccessibleName(f"Colour {color}")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)
        if self.isChecked() or self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(ACCENT if self.hasFocus() and not self.isChecked() else "white"), 2))
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))


class ColorPicker(QWidget):
    changed = Signal(str)

    def __init__(self, color: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.swatches = []
        for c in APPLE_COLORS:
            s = Swatch(c)
            s.clicked.connect(lambda _=False, c=c: self._pick(c))
            lay.addWidget(s)
            self.swatches.append(s)
        self.custom = Swatch("#ffffff")
        self.custom.setToolTip("Custom colour…")
        self.custom.clicked.connect(self._custom)
        lay.addWidget(self.custom)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = color
        known = False
        for s in self.swatches:
            on = s.color.lower() == color.lower()
            s.setChecked(on)
            known |= on
        self.custom.color = color if not known else "#3a3a40"
        self.custom.setChecked(not known)
        self.custom.update()

    def _pick(self, c: str) -> None:
        self.set_color(c)
        self.changed.emit(c)

    def _custom(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, "Item colour")
        if c.isValid():
            self._pick(c.name().upper())
        else:
            self.set_color(self.color)


class GlyphPicker(QDialog):
    picked = Signal(str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setStyleSheet(stylesheet() + f"QDialog {{ background: {POPUP}; border: 1px solid rgba(255,255,255,0.12);"
                                          " border-radius: 10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search symbols")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        grid_w = QWidget()
        self.grid = QGridLayout(grid_w)
        self.grid.setSpacing(4)
        self.buttons = []
        for i, (code, name) in enumerate(GLYPHS):
            b = QToolButton()
            b.setText(chr(int(code, 16)))
            b.setToolTip(name)
            b.setFixedSize(40, 40)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            sel = code.upper() == (current or "").upper()
            b.setStyleSheet("QToolButton { font-family: 'Segoe Fluent Icons', 'Rotary Icons'; font-size: 18px; border: none;"
                            f" border-radius: 8px; background: {'#0A84FF' if sel else 'rgba(255,255,255,0.05)'}; }}"
                            "QToolButton:hover { background: rgba(255,255,255,0.16); }")
            b.clicked.connect(lambda _=False, c=code: (self.picked.emit(c), self.accept()))
            self.buttons.append((b, name))
            self.grid.addWidget(b, i // 10, i % 10)
        lay.addWidget(grid_w)
        hint = QLabel("Any Segoe Fluent Icons code point can also be typed into config.json.")
        hint.setObjectName("muted")
        lay.addWidget(hint)

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        k = 0
        for b, name in self.buttons:
            self.grid.removeWidget(b)
            vis = not t or t in name.lower()
            b.setVisible(vis)
            if vis:
                self.grid.addWidget(b, k // 10, k % 10)
                k += 1


class PresetTile(QAbstractButton):
    """A big, friendly choice card (title, description, symbol) used for quick setups."""

    def __init__(self, glyph: str, title: str, text: str, tint: str = ACCENT):
        super().__init__()
        self.glyph, self.title, self.text, self.tint = glyph, title, text, tint
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.setMinimumSize(200, int(132 * UI_SCALE))

    def sizeHint(self) -> QSize:
        return QSize(230, int(140 * UI_SCALE))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        p.setPen(QPen(QColor(self.tint) if on else QColor(255, 255, 255, 22), 2 if on else 1))
        p.setBrush(QColor(255, 255, 255, 26 if on else (18 if self.underMouse() else 12)))
        p.drawRoundedRect(r, 14, 14)
        if self.hasFocus() and not on:
            focus_ring(p, r, 14)
        c = QRectF(r.left() + 16, r.top() + 16, 34, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.tint))
        p.drawEllipse(c)
        p.setPen(QColor("white"))
        p.setFont(font(ICONS, 16))
        p.drawText(c, Qt.AlignmentFlag.AlignCenter, chr(int(self.glyph, 16)))
        if on:
            p.setFont(font(ICONS, 14))
            p.setPen(QColor(self.tint))
            p.drawText(QRectF(r.right() - 34, r.top() + 14, 20, 20), Qt.AlignmentFlag.AlignCenter, "\uE73E")
        tf = font(TEXT, 14.5 * UI_SCALE, QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(white(0.95))
        th = QFontMetricsF(tf).height()
        p.drawText(QRectF(r.left() + 16, r.top() + 58, r.width() - 32, th + 2), Qt.AlignmentFlag.AlignLeft, self.title)
        bf = font(TEXT, 12 * UI_SCALE)
        p.setFont(bf)
        p.setPen(white(0.6))
        p.drawText(QRectF(r.left() + 16, r.top() + 62 + th, r.width() - 32, r.height() - 70 - th),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.text)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)
