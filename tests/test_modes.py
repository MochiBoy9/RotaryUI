"""Slides / video rings, the volume slider's level parsing and the mouse button logic."""
import pytest

from rotary import modes
from rotary.mouse import HOLD_SECONDS, MouseLogic
from rotary.volume import parse_percent


@pytest.mark.parametrize("exe,title,kind,app", [
    ("POWERPNT.EXE", "Quarterly review - PowerPoint", "slides", "PowerPoint"),
    ("powerpnt.exe", "PowerPoint Slide Show - Quarterly review", "slides", "PowerPoint"),
    ("chrome.exe", "Team offsite - Google Slides - Google Chrome", "slides", "Google Slides"),
    ("msedge.exe", "Pitch deck.pptx - PowerPoint - Microsoft Edge", "slides", "PowerPoint"),
    ("Keynote", "", "slides", "Keynote"),
    ("soffice.bin", "talk.odp - LibreOffice Impress", "slides", "Impress"),
    ("vlc.exe", "movie.mkv - VLC media player", "video", "VLC"),
    ("chrome.exe", "Never Gonna Give You Up - YouTube - Google Chrome", "video", "YouTube"),
    ("firefox", "Netflix — Mozilla Firefox", "video", "Netflix"),
    ("msedge.exe", "Watch Hulu - Microsoft Edge", "video", "Hulu"),
    ("netflix", "Netflix", "video", "Netflix"),
    ("QuickTime Player", "", "video", "QuickTime Player"),
])
def test_classify_finds_slideshows_and_videos(exe, title, kind, app):
    ctx = modes.classify(exe, title)
    assert (ctx.kind, ctx.app) == (kind, app)


@pytest.mark.parametrize("exe,title", [
    ("soffice.bin", "budget.ods - LibreOffice Calc"),      # LibreOffice, but a spreadsheet
    ("code.exe", "youtube.py - Visual Studio Code"),        # the word, but not in a browser
    ("chrome.exe", "Maximum effort - Google Docs"),         # 'Max' inside another word
    ("explorer.exe", "Videos"),
    ("", ""),
])
def test_classify_leaves_everything_else_alone(exe, title):
    assert modes.classify(exe, title).kind == ""


def test_classify_respects_the_switches():
    assert modes.classify("vlc.exe", "", video=False).kind == ""
    assert modes.classify("powerpnt.exe", "x - PowerPoint", slides=False).kind == ""


def test_slide_info_parsing():
    assert modes.parse_slide_info("4 20\r\n") == (4, 20)
    assert modes.parse_slide_info("0 12") == (1, 12)
    assert modes.parse_slide_info("30 12") == (12, 12)
    assert modes.parse_slide_info("") is None
    assert modes.parse_slide_info("1 0") is None


def test_time_format():
    assert modes.fmt_time(65) == "1:05"
    assert modes.fmt_time(754) == "12:34"
    assert modes.fmt_time(3723) == "1:02:03"
    assert modes.fmt_time(-3) == "0:00"


def test_media_session_must_belong_to_the_app_in_front():
    yt = modes.classify("chrome.exe", "Song - YouTube - Google Chrome")
    assert modes.media_matches(yt, "Chrome", "Chrome")
    assert not modes.media_matches(yt, "Spotify.exe", "Spotify")
    vlc = modes.classify("vlc.exe", "x - VLC media player")
    assert modes.media_matches(vlc, "vlc", "VLC")
    assert not modes.media_matches(vlc, "Chrome", "Chrome")


def test_volume_percent_parsing():
    assert parse_percent("Volume: front-left: 32768 /  50% / -18.06 dB,   front-right: 32768 /  50%") == 0.5
    assert parse_percent("  Front Left: Playback 52428 [80%] [on]") == 0.8
    assert parse_percent("nothing here") is None


# ------------------------------------------------------------------ the mouse button
def test_quick_middle_click_goes_to_the_app():
    m = MouseLogic("middle")
    assert m.feed("down", "middle", 10, 10, now=0.0)          # held back while we find out
    m.tick(0.1)
    assert m.feed("up", "middle", 10, 10, now=0.12)
    assert m.replay == "middle" and m.take() == []


def test_holding_the_middle_button_opens():
    m = MouseLogic("middle")
    m.feed("down", "middle", 10, 10, now=0.0)
    m.tick(HOLD_SECONDS + 0.01)
    assert m.take() == [("open", 0)]
    assert m.feed("up", "middle", 10, 10, now=1.0)            # its release is ours too
    assert m.replay is None


def test_middle_drag_still_drags():
    m = MouseLogic("middle")
    m.feed("down", "middle", 10, 10, now=0.0)
    assert not m.feed("move", "", 40, 10, now=0.05)
    assert m.replay == "middle_down"
    m.tick(1.0)
    assert m.take() == []                                    # a drag never opens it
    assert not m.feed("up", "middle", 40, 10, now=1.2)       # the app gets the release as usual


def test_side_button_toggles():
    m = MouseLogic("back")
    assert m.feed("down", "back", 0, 0) and m.take() == [("toggle", 0)]
    assert m.feed("up", "back", 0, 0)
    assert not m.feed("down", "middle", 0, 0)                # other buttons are left alone
    assert not m.feed("down", "forward", 0, 0)


def test_while_open_clicks_and_scrolls_outside_stop_here():
    m = MouseLogic("middle")
    m.set_active(True, (500, 500, 300))
    assert m.feed("wheel", "", 50, 50, delta=-120)           # outside: turns the rotary, the app sees nothing
    assert m.take() == [("turn", 1)]
    assert m.feed("down", "left", 50, 50) and m.take() == [("close", 0)]
    assert m.feed("up", "left", 50, 50)
    assert m.feed("down", "right", 50, 50) and m.take() == [("back", 0)]
    assert m.feed("up", "right", 50, 50)
    m.set_active(False)
    assert not m.feed("wheel", "", 50, 50, delta=-120)
    assert not m.feed("down", "left", 50, 50)


def test_the_mouse_works_on_the_rotary_over_any_app():
    """Over the ring the hook takes the wheel and the clicks itself, so the browser or game underneath never
    scrolls or gets clicked, and the rotary turns even when its window lets the mouse through."""
    m = MouseLogic("middle")
    m.set_active(True, (500, 500, 300))
    assert m.feed("wheel", "", 500, 400, delta=-120)
    assert m.take() == [("turn", 1)]
    assert m.feed("down", "left", 520, 520)                  # press on the ring: the start of a drag or a click
    assert m.take_pointer() == [("press", 520, 520)]
    assert m.wants_moves
    assert not m.feed("move", "", 600, 520)                  # moves are never held back (the pointer would freeze)
    assert not m.feed("move", "", 900, 900)                  # dragging out past the ring keeps going
    assert m.feed("up", "left", 900, 900)
    assert m.take_pointer() == [("drag", 600, 520), ("drag", 900, 900), ("release", 900, 900)]
    assert not m.wants_moves
    assert m.feed("down", "right", 500, 500) and m.take() == [("back", 0)]
    assert m.feed("up", "right", 500, 500)


def test_opened_by_hand_only_the_ring_is_ours():
    m = MouseLogic("middle")
    m.set_active(True, (500, 500, 300), manual=False)
    assert not m.feed("wheel", "", 50, 50, delta=-120)       # elsewhere the app scrolls as usual
    assert not m.feed("down", "left", 50, 50)
    assert not m.feed("up", "left", 50, 50)
    assert m.take() == [] and m.take_pointer() == []
    assert m.feed("wheel", "", 500, 500, delta=120) and m.take() == [("turn", -1)]
    assert m.feed("down", "left", 500, 500) and m.take_pointer() == [("press", 500, 500)]
    m.set_active(False)                                      # closed mid-press: the release isn't the app's
    assert m.feed("up", "left", 500, 500)
    assert m.take_pointer() == []


def test_middle_button_on_a_hand_opened_rotary():
    """Opened by a hand, the middle button closes it only when pressed on the ring; elsewhere it is still the
    app's middle click (a link opened in a new tab, say)."""
    m = MouseLogic("middle")
    m.set_active(True, (500, 500, 300), manual=False)
    assert m.feed("down", "middle", 50, 50, now=0.0)          # held back while we find out, as usual
    assert m.feed("up", "middle", 50, 50, now=0.1)
    assert m.replay == "middle" and m.take() == []            # a quick click: sent on to the app
    assert m.feed("down", "middle", 500, 500, now=1.0)        # on the ring: closes it
    assert m.take() == [("close", 0)]
    assert m.feed("up", "middle", 500, 500, now=1.1)


def test_mouse_hook_tries_again_when_the_setting_changes(monkeypatch):
    from rotary import mouse
    tries = []

    def broken(owner):
        tries.append(owner.button)
        raise OSError("no permission yet")

    monkeypatch.setattr(mouse, "_WindowsHook", broken)
    monkeypatch.setattr(mouse, "_PynputWatch", broken)
    t = mouse.MouseTrigger()
    t.configure("middle")
    t.configure("middle")                                    # every settings change: not again and again
    assert tries == ["middle"] and not t.running
    t.configure("back")                                      # a new setting (permission granted since?)
    assert tries == ["middle", "back"]


def test_a_window_that_takes_the_mouse_is_left_to_it():
    m = MouseLogic("middle")                                 # Linux, opened for the mouse: its own window
    m.set_active(True, (500, 500, 300), window=True)
    assert not m.feed("wheel", "", 500, 400, delta=-120)
    assert not m.feed("down", "left", 520, 520)
    assert m.take() == [] and m.take_pointer() == []
    assert m.feed("wheel", "", 50, 50, delta=-120) and m.take() == [("turn", 1)]


def test_trigger_closes_when_open():
    m = MouseLogic("middle")
    m.set_active(True, (0, 0, 10))
    assert m.feed("down", "middle", 500, 500, now=0.0)
    assert m.take() == [("close", 0)]
    m.tick(1.0)
    assert m.take() == []
    assert m.feed("up", "middle", 500, 500, now=1.0)


def test_off_does_nothing():
    m = MouseLogic("")
    assert not m.feed("down", "middle", 0, 0)
    m.tick(5.0)
    assert m.take() == []


def test_mouse_hook_counts_the_volume_button_as_the_rotary():
    m = MouseLogic("middle")
    m.set_active(True, (500, 500, 300), ((830, 300, 880, 700),))
    assert m.feed("down", "left", 850, 500)               # on the button beside the ring: the rotary's
    assert m.take_pointer() == [("press", 850, 500)]
    assert m.feed("up", "left", 850, 500)
    assert m.take_pointer() == [("release", 850, 500)]
    assert m.take() == []
    assert m.feed("down", "left", 950, 500)               # beyond it: closes
    assert m.take() == [("close", 0)]
    m.set_active(False)
    assert m.zones == ()


def test_click_anywhere_opens_the_highlighted_item_and_right_click_closes():
    """Settings → Keyboard & mouse: left click opens what's highlighted, right click closes, wherever the
    pointer is — but over the ring a left click still opens the item under it."""
    m = MouseLogic("middle", select="left", close="right")
    m.set_active(True, (500, 500, 300))                      # opened for the mouse / keyboard
    assert m.feed("down", "left", 50, 50) and m.take() == [("choose", 0)]
    assert m.feed("up", "left", 50, 50)                      # its release is ours too
    assert m.feed("down", "right", 50, 50) and m.take() == [("close", 0)]
    assert m.feed("up", "right", 50, 50)
    assert m.feed("down", "right", 500, 500) and m.take() == [("close", 0)]     # on the ring: closes, not back
    assert m.feed("up", "right", 500, 500)
    assert m.feed("down", "left", 520, 520) and m.take() == []
    assert m.take_pointer() == [("press", 520, 520)]         # on the ring: the item under the pointer
    m.set_active(False)
    assert m.feed("up", "left", 520, 520)
    assert not m.feed("down", "left", 50, 50) and not m.feed("down", "right", 50, 50)   # closed: the app's
    assert m.take() == []


def test_choose_and_close_buttons_on_a_hand_opened_rotary_only_count_on_the_ring():
    m = MouseLogic("middle", select="forward", close="back")
    m.set_active(True, (500, 500, 300), manual=False)
    assert not m.feed("down", "back", 50, 50)                 # a browser's back button, as usual
    assert m.feed("down", "forward", 500, 500) and m.take() == [("choose", 0)]
    assert m.feed("down", "back", 500, 500) and m.take() == [("close", 0)]


def test_one_side_button_can_open_the_rotary_and_then_the_item():
    m = MouseLogic("forward", select="forward")
    assert m.feed("down", "forward", 50, 50) and m.take() == [("toggle", 0)]
    assert m.feed("up", "forward", 50, 50)
    m.set_active(True, (500, 500, 300))
    assert m.feed("down", "forward", 50, 50) and m.take() == [("choose", 0)]
    assert m.feed("up", "forward", 50, 50)
