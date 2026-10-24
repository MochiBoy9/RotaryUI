"""The settings window's look: the bundled font, the font picker and its plain panels."""
import sys

from PySide6.QtGui import QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication

from rotary.ui import glass, widgets

app = QApplication.instance() or QApplication([])


def test_inter_ships_with_the_app_and_is_the_default_outside_macos():
    glass.load_fonts()
    assert glass.BUNDLED_TEXT in QFontDatabase.families()
    glass.set_text_font("")
    if sys.platform != "darwin":
        assert glass.family() == glass.BUNDLED_TEXT
        assert QFontInfo(glass.font(glass.TEXT, 13)).family() == glass.BUNDLED_TEXT


def test_picking_a_font_changes_the_shared_lists_in_place():
    text, display = glass.TEXT, glass.DISPLAY
    try:
        glass.set_text_font("Some Font")
        assert glass.TEXT is text and glass.DISPLAY is display     # the rotary and notch hold these lists
        assert text[0] == "Some Font" and display[0] == "Some Font"
        assert glass.family() != "Some Font"                       # not installed: falls back, never blank
        glass.set_text_font("inter")
        assert glass.family() == glass.BUNDLED_TEXT
    finally:
        glass.set_text_font("")


def test_font_choices_start_with_the_two_built_in_looks():
    keys = [k for k, _ in glass.font_choices()]
    assert keys[0] == "" and keys[1] in ("inter", "system")
    assert glass.BUNDLED_TEXT not in keys and "Rotary Icons" not in keys


def test_style_sheet_names_the_chosen_font():
    try:
        glass.set_text_font("inter")
        assert f"font-family: '{glass.BUNDLED_TEXT}'" in widgets.stylesheet()
    finally:
        glass.set_text_font("")


def test_window_panels_are_solid_not_glass():
    """Liquid glass is for the rotary; the window's cards and sidebar are plain, opaque panels."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(255, 0, 0))
    p = QPainter(img)
    widgets.paint_panel(p, QRectF(0, 0, 80, 80), 8)
    p.end()
    assert img.pixelColor(40, 40) == QColor(widgets.CARD)          # nothing behind shows through
