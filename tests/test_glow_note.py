"""Screen assist's glow around the screen while the AI answers, and the optional note that goes with the question."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from rotary import assist
from rotary.actions import DEFAULT_CONFIG
from rotary.ui import glow
from rotary.ui.glow import ScreenGlow
from rotary.ui.note import NoteBox

app = QApplication.instance() or QApplication([])
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


# ------------------------------------------------------------------ the glow's colours
def test_the_glow_is_siri_purple_blue_by_default():
    assert DEFAULT_CONFIG["assist"]["glow"] == "siri" == assist.DEFAULTS["glow"]
    assert DEFAULT_CONFIG["assist"]["glow_colors"] == assist.GLOW_COLORS
    colors = assist.glow_colors({})
    assert colors == list(assist.GLOWS["siri"][1])
    hues = [QColor(c).hslHue() for c in colors]
    assert all(190 <= h <= 290 for h in hues)          # blues and purples


def test_the_glow_can_be_another_look_custom_or_off():
    assert assist.glow_colors({"assist": {"glow": "sunset"}}) == list(assist.GLOWS["sunset"][1])
    mine = ["#112233", "#445566"]
    assert assist.glow_colors({"assist": {"glow": "custom", "glow_colors": mine}}) == mine
    # a broken colour is skipped; none left falls back to the default colours
    assert assist.glow_colors({"assist": {"glow": "custom", "glow_colors": ["red", "#12"]}}) == assist.GLOW_COLORS
    assert assist.glow_colors({"assist": {"glow": "off"}}) == []
    assert assist.glow_colors({"assist": {"glow": "nonsense"}}) == []


# ------------------------------------------------------------------ the glow window
def _paint(g: ScreenGlow) -> QImage:
    img = QImage(g.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    g.render(img)
    return img


def test_the_glow_paints_only_the_edges_of_the_screen():
    g = ScreenGlow()
    g.setGeometry(QRect(0, 0, 800, 500))
    g.colors = list(assist.GLOWS["siri"][1])
    g.vis.snap(1.0)
    img = _paint(g)
    edge = img.pixelColor(2, 250)
    assert edge.alpha() > 120 and edge.blue() > edge.green()        # bright and blue-purple on the edge
    assert img.pixelColor(glow.BAND // 2, 250).alpha() > 0           # fading in towards the middle
    assert img.pixelColor(400, 250).alpha() == 0                     # the middle is left alone
    assert img.pixelColor(400, glow.BAND + 10).alpha() == 0


def test_the_glow_stays_until_the_answer_then_fades_out():
    g = ScreenGlow()
    g.start(assist.glow_colors({}), app.primaryScreen())
    assert g.isVisible() and g.glowing
    for _ in range(60):                                  # it doesn't go by itself, however long the AI takes
        g._last -= 0.05
        g._tick()
    assert g.isVisible() and g.vis.value > 0.9
    g.stop()
    for _ in range(200):
        if not g.isVisible():
            break
        g._last -= 0.05
        g._tick()
    assert not g.isVisible() and not g.glowing


def test_no_glow_when_it_is_switched_off():
    g = ScreenGlow()
    g.start(assist.glow_colors({"assist": {"glow": "off"}}), app.primaryScreen())
    assert not g.isVisible()


def test_reduce_motion_keeps_the_glow_still():
    g = ScreenGlow()
    g.setGeometry(QRect(0, 0, 400, 300))
    g.colors = list(assist.GLOWS["siri"][1])
    g.vis.snap(1.0)
    g.reduce_motion = True
    a = _paint(g)
    g._t0 -= 1.7
    assert _paint(g) == a


# ------------------------------------------------------------------ the note
def test_the_note_goes_with_the_question():
    ask = assist.ask_text("chrome", "Quiz 3", "only the second question")
    assert ask.startswith(assist.ASK) and ask.endswith("My note: only the second question")
    assert assist.ask_text("chrome", "Quiz 3", "  ") == assist.ask_text("chrome", "Quiz 3")
    assert len(assist.ask_text(note="x" * 5000)) < len(assist.ASK) + assist.NOTE_MAX + 20
    assert "note" in assist.SYSTEM


def test_the_kept_note_and_this_times_note_are_both_optional():
    assert assist.note_for({}) == ""
    assert assist.note_for({}, "answer in Spanish") == "answer in Spanish"
    cfg = {"assist": {"note": "I'm in 10th grade chemistry"}}
    assert assist.note_for(cfg) == "I'm in 10th grade chemistry"
    assert assist.note_for(cfg, "only question 4") == "I'm in 10th grade chemistry only question 4"
    assert DEFAULT_CONFIG["assist"]["note"] == "" and DEFAULT_CONFIG["assist"]["ask_note"] is False


class _Server:
    def __init__(self, reply: dict):
        seen = self.seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                out = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *_a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_solve_sends_the_note():
    srv = _Server({"message": {"content": '{"found": true, "answer": "4"}'}})
    try:
        cfg = {"assist": {"provider": "ollama", "urls": {"ollama": srv.url}, "note": "maths homework"}}
        assist.solve(JPEG, cfg, note=assist.note_for(cfg, "question 2"))
    finally:
        srv.close()
    assert srv.seen["body"]["messages"][1]["content"].endswith("My note: maths homework question 2")


def test_the_note_box_asks_with_or_without_a_note():
    box = NoteBox()
    got = []
    box.asked.connect(got.append)
    box.cancelled.connect(lambda: got.append(None))
    box.open()
    box.edit.setText("  which one is the noble gas  ")
    box._ask()
    assert got == ["which one is the noble gas"] and not box.isVisible()
    box.open()
    assert box.edit.text() == ""
    box._ask()                                   # empty is fine: it asks anyway
    box._ask()                                   # once
    assert got[1:] == [""]
    box.open()
    box.cancel()
    assert got[2:] == [None] and not box.isVisible()
