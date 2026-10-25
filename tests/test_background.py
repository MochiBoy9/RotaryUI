"""Closing the window leaves RotaryUI running, so the rotary keeps working without it."""
import json
import types

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from rotary import app as rotary_app
from rotary.actions import load_config

qapp = QApplication.instance() or QApplication([])


class _Window:
    def __init__(self):
        self.calls = []

    def hide(self):
        self.calls.append("hide")

    def showMinimized(self):
        self.calls.append("minimized")


class _Timer:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1


class _Tray:
    def __init__(self):
        self.messages = []

    def isVisible(self):
        return True

    def showMessage(self, title, text, *_):
        self.messages.append(text)


def _stub(general):
    stub = types.SimpleNamespace(cfg={"general": general}, window=_Window(), _save_timer=_Timer(), tray=_Tray(),
                                 flashes=[])
    stub._flash_msg = lambda text, *_: stub.flashes.append(text)
    stub._keep_in_taskbar = lambda: rotary_app.RotaryApp._keep_in_taskbar(stub)
    return stub


def test_closing_hides_to_the_tray_and_says_so_once(monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    stub = _stub({"keep_in_taskbar": False})
    rotary_app.RotaryApp.window_closed(stub)
    assert stub.window.calls == ["hide"]
    assert stub.cfg["general"]["close_hint_shown"] is True and stub._save_timer.started == 1
    assert len(stub.tray.messages) + len(stub.flashes) == 1
    rotary_app.RotaryApp.window_closed(stub)
    assert stub.window.calls == ["hide", "hide"]
    assert len(stub.tray.messages) + len(stub.flashes) == 1          # told once


def test_closing_minimises_when_kept_on_the_taskbar_or_there_is_no_tray(monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    stub = _stub({"keep_in_taskbar": True, "close_hint_shown": True})
    rotary_app.RotaryApp.window_closed(stub)
    assert stub.window.calls == ["minimized"]
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    stub = _stub({"keep_in_taskbar": False, "close_hint_shown": True})
    rotary_app.RotaryApp.window_closed(stub)
    assert stub.window.calls == ["minimized"]                         # nowhere else to come back from


def test_existing_settings_move_to_the_tray_once(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 3, "general": {"keep_in_taskbar": True}}))
    assert load_config(path)["general"]["keep_in_taskbar"] is False
    path.write_text(json.dumps({"version": 4, "general": {"keep_in_taskbar": True}}))
    assert load_config(path)["general"]["keep_in_taskbar"] is True    # chosen again after 2.2.3: stays
    assert load_config(tmp_path / "new.json")["general"]["keep_in_taskbar"] is False


def test_the_updater_ranks_2_2_3_above_2_2_2():
    from rotary import update
    assert update.is_newer("2.2.3", "2.2.2") and not update.is_newer("2.2.2", "2.2.3")
