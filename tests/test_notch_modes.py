"""Opened from the notch, the rotary opens as the slides or video ring of the app the mouse user is in."""
import time
import types

from PySide6.QtWidgets import QApplication

from rotary import app as rotary_app
from rotary import modes
from rotary.gestures import Phase

qapp = QApplication.instance() or QApplication([])

SLIDES = modes.classify("powerpnt.exe", "Quarterly review - PowerPoint")


class _Exec:
    def submit(self, fn):
        fn()


def _stub(in_front: modes.Context, win: bool, monkeypatch):
    monkeypatch.setattr(rotary_app, "WIN", win)
    stub = types.SimpleNamespace(engine=types.SimpleNamespace(phase=Phase.IDLE), _front=None, _open_ctx=None,
                                 _mode_token=0, _mode_exec=_Exec(), entered=[], opened=[], asked=[])
    stub._detect_context = lambda: in_front
    stub.mode_data = types.SimpleNamespace(emit=lambda *a: rotary_app.RotaryApp._on_mode_data(stub, *a))
    stub._enter_mode = stub.entered.append

    def open_manual():                     # the rotary opening: it looks at what's in front
        stub.engine.phase = Phase.OPEN
        stub.opened.append(rotary_app.RotaryApp._watch_context(stub))
    stub.open_manual = open_manual
    stub._notch_open = lambda: rotary_app.RotaryApp._notch_open(stub)
    return stub


def _approach_and_click(stub):
    rotary_app.RotaryApp._on_notch_approach(stub)
    rotary_app.RotaryApp._on_notch_click(stub)


def test_notch_click_opens_the_slides_ring_seen_on_approach(monkeypatch):
    # macOS / Linux: by the time the rotary opens, the app in front may be RotaryUI itself
    stub = _stub(SLIDES, win=False, monkeypatch=monkeypatch)
    rotary_app.RotaryApp._on_notch_approach(stub)
    stub._detect_context = lambda: modes.Context()
    rotary_app.RotaryApp._on_notch_click(stub)
    assert stub.entered == [SLIDES] and stub.opened == [False]
    assert stub._front is None and stub._open_ctx is None          # used once


def test_windows_goes_by_the_live_window_and_falls_back_to_the_notch(monkeypatch):
    stub = _stub(SLIDES, win=True, monkeypatch=monkeypatch)
    _approach_and_click(stub)
    assert stub.entered == [SLIDES]
    other = modes.classify("explorer.exe", "Downloads")             # another app came forward: that wins
    stub = _stub(SLIDES, win=True, monkeypatch=monkeypatch)
    rotary_app.RotaryApp._on_notch_approach(stub)
    stub._detect_context = lambda: other
    rotary_app.RotaryApp._on_notch_click(stub)
    assert stub.entered == [] and stub.opened == [True]
    stub = _stub(SLIDES, win=True, monkeypatch=monkeypatch)        # only our own windows to be seen
    rotary_app.RotaryApp._on_notch_approach(stub)
    stub._detect_context = lambda: modes.Context()
    rotary_app.RotaryApp._on_notch_click(stub)
    assert stub.entered == [SLIDES]


def test_an_old_look_is_not_trusted(monkeypatch):
    stub = _stub(SLIDES, win=False, monkeypatch=monkeypatch)
    rotary_app.RotaryApp._on_notch_approach(stub)
    stub._front = (time.monotonic() - rotary_app.FRONT_SECONDS - 1, SLIDES)
    stub._detect_context = lambda: modes.Context()
    stub._mode_exec = types.SimpleNamespace(submit=lambda fn: None)   # the usual slower look, left pending
    rotary_app.RotaryApp._on_notch_click(stub)
    assert stub.entered == [] and stub.opened == [True]
