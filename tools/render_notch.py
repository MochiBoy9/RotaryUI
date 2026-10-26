"""Dev tool: render the notch's states over a synthetic screen top into one PNG (no window needed)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rotary import agents  # noqa: E402
from rotary.live import LiveState, NowPlaying  # noqa: E402
from rotary.ui import liquid  # noqa: E402
from rotary.ui.notch import AMBER, BLUE, GREEN, NotchWindow  # noqa: E402


def screen_top(w: int, h: int, light: bool) -> QImage:
    """A browser-ish strip: tabs and page text."""
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#f3f4f8" if light else "#1b2a6b"))
    g.setColorAt(1, QColor("#dfe6f5" if light else "#e2803a"))
    p.fillRect(0, 0, w, h, g)
    for i in range(5):
        p.setBrush(QColor("#ffffff") if light else QColor(255, 255, 255, 40))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(10 + i * 140, 5, 130, 28), 8, 8)
        p.setPen(QColor("#333") if light else QColor("#eee"))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRectF(22 + i * 140, 5, 110, 28), Qt.AlignmentFlag.AlignVCenter, f"Tab number {i + 1}")
    p.setPen(QColor("#222") if light else QColor(255, 255, 255, 200))
    p.setFont(QFont("Segoe UI", 12))
    for i in range(3):
        p.drawText(20, 62 + i * 22, "The quick brown fox jumps over the lazy dog — page text " * 2)
    p.setBrush(QColor("#ff375f"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(w * 0.58, 30), 22, 22)
    p.end()
    return img


def fake_art() -> bytes:
    img = QImage(300, 300, QImage.Format.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 300, 300)
    g.setColorAt(0, QColor("#ff2d55"))
    g.setColorAt(1, QColor("#5e5ce6"))
    p.fillRect(0, 0, 300, 300, g)
    r = QRadialGradient(QPointF(200, 90), 120)
    r.setColorAt(0, QColor(255, 220, 120, 220))
    r.setColorAt(1, QColor(255, 220, 120, 0))
    p.fillRect(0, 0, 300, 300, r)
    p.end()
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def shot(n: NotchWindow, bg: QImage, seconds: float = 1.0) -> QImage:
    for _ in range(int(seconds * 60)):
        for sp in n._springs:
            sp.step(1 / 60)
    img = bg.copy()
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                     | QPainter.RenderHint.TextAntialiasing)
    n.paint(p)
    p.end()
    return img


def notch(light: bool, **opts) -> tuple[NotchWindow, QImage]:
    n = NotchWindow()
    n.set_options(opts)
    bg = screen_top(n.width(), n.height(), light)
    n._bd = liquid.Backdrop.from_pixmap(QPixmap.fromImage(bg))
    n._bd_ver += 1
    n._retarget()
    return n, bg


app = QApplication([])
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
song = NowPlaying("Spotify.exe", "Spotify", "Midnight City", "M83", True, 83.0, 243.0, time.monotonic(), fake_art())
frames = []
for light in (False, True):
    n, bg = notch(light)
    frames.append(shot(n, bg))                                                 # clock
    n.set_live(LiveState(media=song))
    frames.append(shot(n, bg))                                                 # now playing
    n.hovered = True
    n._retarget()
    frames.append(shot(n, bg))                                                 # hover: the media card
    n.hovered = False
    n.set_live(LiveState(media=song, mic=["Discord"], camera=["Teams"]))
    frames.append(shot(n, bg))                                                 # + mic & camera dots
n, bg = notch(False, clock="date")
n.start_timer(4 * 60 + 32)
frames.append(shot(n, bg))                                                     # timer
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # timer card
n, bg = notch(False)
n.set_live(LiveState(media=song, mic=["Discord"], call=("Discord", time.time() - 754)))
frames.append(shot(n, bg))                                                     # Discord call
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # call card
n, bg = notch(False)
n.set_live(LiveState(clipping="VALORANT"))
frames.append(shot(n, bg))                                                     # Medal recording
n.set_status("Clip saved · Medal", "clip", GREEN)
frames.append(shot(n, bg))                                                     # clip saved
n, bg = notch(True, look="glass")
n.set_live(LiveState(media=song))
frames.append(shot(n, bg))                                                     # glass look
n.hovered = True
n._retarget()
frames.append(shot(n, bg))
hub = agents.Hub()                                                             # AI agents
hub.update("c", "Claude Code", state="working", text="Editing notch.py", step="Designing the question card",
           where="Gesture_Rotary")
hub._sessions["c"].started -= 312
hub.update("o", "Ollama", state="done", text="Answered", where="llama3.2")
n, bg = notch(False)
n.set_agents(*hub.snapshot())
frames.append(shot(n, bg))                                                     # a new step slides in
n._flash_until = 0
n._retarget()
frames.append(shot(n, bg))                                                     # at work
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # hover: every agent
n.hovered = False
q = agents.Question("q", "c", "Claude Code", "Run the tests?", "uv run pytest -q tests/test_agents.py",
                    ["Allow", "Always allow", "Deny"], True, "Gesture_Rotary")
n.set_agents(hub.snapshot()[0], [q])
frames.append(shot(n, bg))                                                     # a question
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # hover to answer it
game = {"state": "playing", "game": "THE FINALS", "mode": "Quick Cash", "seconds": 312.0, "dead": False,   # game
        "line": "K 5  D 2  A 1  +$2.5k", "round": 1, "final": False, "manual": False, "status": "Watching",
        "tiles": [("Eliminations", "5"), ("Deaths", "2"), ("Assists", "1"), ("Lead or behind by", "+$2.5k"),
                  ("Revives", "3")], "sure": True}
n, bg = notch(False)
n.set_game(game, True)
frames.append(shot(n, bg))                                                     # a match on
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # hover: still just the stats
n.hovered = False
n.set_game({**game, "dead": True, "killer": "SweetSpicySalty"}, True)
frames.append(shot(n, bg))                                                     # eliminated: it opens
n.set_game({"state": "ended", "game": "THE FINALS", "mode": "Cashout", "seconds": 1310.0, "result": "lost",
            "headline": "Out · 3rd–4th", "estimated": True, "line": "Out · 3rd–4th · 7/4/2",
            "tiles": [("Eliminations", "7"), ("Deaths", "4"), ("Assists", "2"), ("Revives", "1"),
                      ("K/D ratio", "1.75"), ("Match time", "21:50")], "status": "Watching"}, True)
frames.append(shot(n, bg))                                                     # the match is over
n.hovered = True
n._retarget()
frames.append(shot(n, bg))                                                     # hover: the summary
W = max(f.width() for f in frames)
H = sum(f.height() + 4 for f in frames)
sheet = QImage(W, H, QImage.Format.Format_RGB32)
sheet.fill(QColor("#000"))
p = QPainter(sheet)
y = 0
for f in frames:
    p.drawImage(0, y, f)
    y += f.height() + 4
p.end()
sheet.save(str(out / "notch_states.png"))
print("ok", len(frames))
