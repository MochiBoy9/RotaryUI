"""Keyboard shortcuts as text (keys.py), the keys the hotkey backends know, and recording one in Settings."""
import pytest

from PySide6.QtWidgets import QApplication

from rotary import MAC, WIN, keys, posix

qapp = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("combo,want", [
    ("ctrl+alt+space", (keys.MOD_CONTROL | keys.MOD_ALT, 0x20)),
    ("Alt + Ctrl + Space", (keys.MOD_CONTROL | keys.MOD_ALT, 0x20)),
    ("win+shift+k", (keys.MOD_WIN | keys.MOD_SHIFT, 0x4B)),
    ("cmd+option+r", (keys.MOD_WIN | keys.MOD_ALT, 0x52)),
    ("f8", (0, 0x77)),
    ("ctrl+pageup", (keys.MOD_CONTROL, 0x21)),
    ("7", (0, 0x37)),
])
def test_parse(combo, want):
    assert keys.parse(combo) == want


@pytest.mark.parametrize("combo", ["", "off", "ctrl+alt", "ctrl+a+b", "ctrl+ctrl+a", "hyper+a", "f13", None])
def test_not_a_shortcut(combo):
    assert keys.parse(combo) is None and keys.label(combo) == "" and keys.normalize(combo) == ""


def test_labels_follow_the_system():
    if MAC:
        assert keys.label("ctrl+alt+space") == "⌃⌥Space"
        assert keys.label("win+alt+space") == "⌥⌘Space"
        assert keys.label("alt+shift+…") == "⌥⇧…"
    else:
        win = "Win" if WIN else "Super"
        assert keys.label("ctrl+alt+space") == "Ctrl + Alt + Space"
        assert keys.label("win+shift+f5") == f"Shift + {win} + F5"
        assert keys.label("ctrl+…") == "Ctrl + …"
    assert keys.normalize("Shift+Ctrl+K") == "ctrl+shift+k"


def test_a_shortcut_from_any_app_needs_a_modifier():
    assert keys.global_problem("k")
    assert keys.global_problem("shift+k")                  # Shift + K is just a capital K
    assert keys.global_problem("left")
    assert not keys.global_problem("ctrl+k")
    assert not keys.global_problem("f9")


def test_every_key_works_on_every_system():
    for name, vk in keys.KEYS.items():
        got = posix._vk_name(vk)
        assert got == name
        assert got in posix._MAC_SPECIAL or got in posix.MAC_KEYCODES, name     # macOS (Carbon)
        assert len(got) == 1 or got in posix._X_SPECIAL, name                    # Linux (X11 keysym)


def test_keys_on_the_open_rotary():
    assert keys.rotary_keys({}) == keys.ROTARY_KEYS
    got = keys.rotary_keys({"rotary_keys": {"next": "D", "prev": "off", "back": "nonsense"}})
    assert got == {"prev": "", "next": "d", "select": "enter", "back": "esc"}


def test_recording_a_shortcut():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from rotary.ui.widgets import KeyRecorder
    mod = Qt.KeyboardModifier
    ctrl = mod.MetaModifier if MAC else mod.ControlModifier          # Qt calls ⌃ Meta on a Mac

    def press(w, key, mods=mod.NoModifier):
        qapp.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, key, mods))

    rec = KeyRecorder("ctrl+alt+space")
    got, paused = [], []
    rec.changed.connect(got.append)
    rec.recording.connect(paused.append)
    rec.click()
    assert rec.active and paused == [True]
    press(rec, Qt.Key.Key_K)                                  # a plain letter can't work from any app
    assert rec.active and got == []
    press(rec, Qt.Key.Key_K, ctrl | mod.AltModifier)
    assert got == ["ctrl+alt+k"] and not rec.active and paused == [True, False]
    rec.click()
    press(rec, Qt.Key.Key_Escape)                             # cancels
    assert got == ["ctrl+alt+k"] and not rec.active
    rec.click()
    press(rec, Qt.Key.Key_Backspace)                          # turns it off
    assert got[-1] == "off" and rec.text() == "Off"

    plain = KeyRecorder("left", shortcut=False)               # a key on the open rotary
    plain.changed.connect(got.append)
    plain.click()
    press(plain, Qt.Key.Key_Escape)
    assert got[-1] == "esc"
