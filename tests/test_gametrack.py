"""Active Game Tracking: reading THE FINALS' HUD (from OCR words), turning frames into stats without counting
anything twice, matches starting and ending, the database, the notch and the controller."""
import shutil
import time

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rotary import gametrack
from rotary.actions import DEFAULT_CONFIG
from rotary.gametrack import finals, ocr, text
from rotary.gametrack.engine import Tracker
from rotary.gametrack.profile import (DEAD, FEED_DEATH, FEED_KILL, FEED_LINE, IN_MATCH, MENU, MODE, RESULT,
                                      REVIVED_BY, SCORES, TALLY_ASSIST, TALLY_KILL, TALLY_REVIVE, Observation)
from rotary.gametrack.store import Store
from rotary.gametrack.text import Word, rows_from
from rotary.ui.notch import NotchWindow

app = QApplication.instance() or QApplication([])
W, H = 1920, 1080


def words(y, parts, x=1300, h=18):
    """Words on one line; "|" is a gap (the killfeed's weapon icon)."""
    out = []
    for p in parts:
        if p == "|":
            x += 70
            continue
        for t in p.split():
            out.append(Word(t, x, y, len(t) * 10, h))
            x += len(t) * 10 + 8
    return out


def read(feed=(), center=(), top=(), names=("MochiBoy#2222",)):
    zones = {"feed": rows_from([w for line in feed for w in line]),
             "center": rows_from([w for line in center for w in line]),
             "top": rows_from([w for line in top for w in line])}
    return finals.read(zones, (W, H), {"names": list(names)})


def kinds(obs):
    return [(o.kind, o.other) for o in obs]


# ------------------------------------------------------------------ text
def test_ocr_mistakes_still_match():
    assert text.similar("EL1MINATION", "ELIMINATION")
    assert text.similar("ASSlST", "ASSIST")
    assert not text.similar("ASSIST", "REVIVE")
    assert text.find_phrase(["ELIMINATEDBY", "ZED"], "ELIMINATED BY") == (0, 1)       # run together
    assert text.find_phrase(["YOU", "WERE", "ELIMINATED"], "ELIMINATED") == (2, 3)
    assert text.name_in("M0CHIB0Y", "MochiBoy#1234")                  # O/0, and the Embark ID's number
    assert text.name_in("MochiBpy", "MochiBoy")                        # a letter off, in a long name
    assert not text.name_in("Al", "Alex") and not text.name_in("Alice", "Al")   # short names must be exact
    assert text.cash_values("$12,500  $7.5K  $900") == [12500, 7500, 900]
    assert text.count_mark(["ELIMINATION", "X2"]) == 2 and text.count_mark(["ASSIST"]) == 1
    assert text.same_player("MMM Dexter_77", "DEXTER_77") and not text.same_player("Dexter", "Kraken")
    assert text.best_name("MochiBoy MMMM") == "MochiBoy"


def test_rows_split_at_the_icon():
    rows = rows_from(words(80, ["MochiBoy", "|", "Dexter"]) + words(110, ["Bob", "|", "Alice"]))
    assert [r.segment_texts() for r in rows] == [["MochiBoy", "Dexter"], ["Bob", "Alice"]]


# ------------------------------------------------------------------ reading THE FINALS
def test_feed_with_your_name():
    obs = read(feed=[words(80, ["MochiBoy", "|", "Dexter"]), words(110, ["Zed", "|", "M0chiBoy"]),
                     words(140, ["Kraken", "|", "Luke"])])
    assert kinds(obs)[:3] == [(FEED_KILL, "Dexter"), (FEED_DEATH, "Zed"), (FEED_LINE, "Luke")]


def test_tally_death_screen_and_revives():
    obs = read(center=[words(600, ["ELIMINATION", "Dexter", "+100"], x=1000), words(630, ["ASSIST", "+50"], x=1000),
                       words(660, ["REVIVE", "Ana"], x=1000)])
    assert (TALLY_KILL, "Dexter") in kinds(obs) and (TALLY_ASSIST, "") in kinds(obs)
    assert (TALLY_REVIVE, "Ana") in kinds(obs)
    assert kinds(read(center=[words(700, ["ELIMINATED", "BY", "Zed"], x=800)]))[0] == (DEAD, "Zed")
    assert kinds(read(center=[words(700, ["REVIVED", "BY", "Ana"], x=800)]))[0] == (REVIVED_BY, "Ana")
    # a revive prompt isn't a revive; a teammate's news isn't your elimination
    assert not read(center=[words(700, ["HOLD", "F", "TO", "REVIVE"], x=800)])
    assert not [o for o in read(center=[words(700, ["TEAMMATE", "ELIMINATED"], x=800)]) if o.kind == TALLY_KILL]


def test_banners_need_big_letters():
    big = H * 0.06
    assert kinds(read(center=[words(400, ["VICTORY"], x=800, h=big)])) == [(RESULT, "won")]
    o = read(center=[words(400, ["WINNER"], x=800, h=big)])[0]
    assert (o.kind, o.other) == (RESULT, "winner") and "unsure" in o.values
    assert read(center=[words(400, ["2ND", "PLACE"], x=800, h=big)])[0].count == 2
    assert kinds(read(center=[words(400, ["QUICK", "CASH"], x=800, h=big)])) == [(MODE, "quick_cash")]
    assert not [o for o in read(center=[words(400, ["CASHOUT", "COMPLETE"], x=800, h=big)]) if o.kind == MODE]
    assert not [o for o in read(center=[words(400, ["VICTORY"], x=800)]) if o.kind == RESULT]   # small: not a banner


def test_scoreboard_and_menus():
    obs = read(top=[words(20, ["$4,500"], x=600) + words(20, ["12:31"], x=930) + words(20, ["$6,000"], x=1200)])
    sc = next(o for o in obs if o.kind == SCORES)
    assert [v for _k, v in sc.values] == [4500, 6000] and (IN_MATCH, "") in kinds(obs)
    menu = read(top=[words(30, ["PLAY", "|", "LOADOUT", "|", "CAREER", "|", "STORE"], x=200)])
    assert (MENU, "") in kinds(menu)


# ------------------------------------------------------------------ the tracker
def frames(tr, obs, start, seconds, fps=3):
    out = []
    t = start
    while t < start + seconds:
        out += tr.feed(t, obs)
        t += 1 / fps
    return out


def ob(kind, other="", count=1, values=()):
    return Observation(kind, other, count, values)


def test_one_elimination_seen_in_the_feed_and_the_tally_counts_once():
    tr = Tracker(finals.PROFILE)
    frames(tr, [ob(FEED_KILL, "Dexter"), ob(TALLY_KILL, "DEXTER")], 0, 5)       # five seconds on screen
    assert tr.match is not None and tr.match.stats["kills"] == 1
    frames(tr, [], 5, 4)
    frames(tr, [ob(TALLY_KILL, "Luke")], 9, 2)                                   # then another
    assert tr.match.stats["kills"] == 2


def test_merged_tally_counts_each_elimination():
    tr = Tracker(finals.PROFILE)
    frames(tr, [ob(TALLY_KILL, "Dexter")], 0, 1)
    frames(tr, [ob(TALLY_KILL, "Luke", 2)], 1, 1)        # "ELIMINATION Luke ×2"
    frames(tr, [ob(TALLY_KILL, "Ana", 3)], 2, 1)
    assert tr.match.stats["kills"] == 3


def test_a_single_misread_frame_counts_nothing():
    tr = Tracker(finals.PROFILE)
    tr.feed(0, [ob(TALLY_KILL, "Dexter")])
    frames(tr, [], 0.3, 4)
    assert tr.match is None


def test_death_screen_is_one_death_however_long_it_stays():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, manual=False)
    frames(tr, [ob(FEED_DEATH, "Zed")], 0, 1)
    frames(tr, [ob(DEAD, "Zed")], 1, 25)                   # the respawn countdown
    assert tr.match.stats["deaths"] == 1 and tr.dead
    frames(tr, [], 26, 8)
    assert not tr.dead
    frames(tr, [ob(DEAD)], 40, 3)
    assert tr.match.stats["deaths"] == 2


def test_revived_by_a_teammate():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, manual=False)
    frames(tr, [ob(DEAD, "Zed")], 0, 3)
    frames(tr, [ob(REVIVED_BY, "Ana")], 3, 1)
    assert not tr.dead and tr.match.stats["revived"] == 1 and tr.match.stats["deaths"] == 1


def test_matches_start_and_end_by_themselves():
    tr = Tracker(finals.PROFILE)
    frames(tr, [ob(MODE, "quick_cash")], 0, 1)
    frames(tr, [ob(IN_MATCH)], 1, 1)
    assert tr.match is None                                # not yet: a moment isn't a match
    frames(tr, [ob(IN_MATCH)], 2, 3)
    assert tr.match is not None and tr.match.mode == "quick_cash"
    frames(tr, [ob(RESULT, "won")], 300, 1)
    assert tr.match is None and tr.last.result == "won" and not tr.last.estimated
    frames(tr, [ob(IN_MATCH)], 302, 5)
    assert tr.match is None                                # the end screens linger: no new match yet
    frames(tr, [ob(IN_MATCH)], 330, 4)
    assert tr.match is not None


def test_back_in_the_menus_after_another_teams_qualified_sequence():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, "cashout", manual=False)
    frames(tr, [ob(RESULT, "qualified")], 200, 1)
    assert tr.match is not None and tr.match.round == 2
    frames(tr, [ob(MENU)], 220, 8)
    assert tr.last.result == "lost" and tr.last.estimated
    assert tr.last.place_text(finals.PROFILE) == "3rd–4th"


def test_winner_banner_goes_by_the_scoreboard():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, "quick_cash", manual=False)
    board = lambda a, b, c: ob(SCORES, values=(("#1", a), ("#2", b), ("#3", c)))  # noqa: E731
    frames(tr, [board(4000, 6000, 1000)], 1, 1)
    frames(tr, [ob(TALLY_KILL, "Dexter")], 2, 1)
    frames(tr, [board(4500, 6000, 1000)], 3, 1)            # +$500 for the elimination: #1 is us
    assert tr.match.team == "#1"
    st = tr.match.standing(finals.PROFILE)
    assert st["lead"] == -1500 and st["to_lead"] == 1501 and st["rank"] == 2
    frames(tr, [ob(RESULT, "winner", values=("unsure",))], 10, 1)
    assert tr.last.result == "lost"


def test_knockout_needs_to_qualify():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, "cashout", manual=False)
    m = tr.match
    m.scores, m.team = [("#1", 3000), ("#2", 9000), ("#3", 5000), ("#4", 1000)], "#1"
    assert m.standing(finals.PROFILE)["to_qualify"] == 2001


def test_your_name_is_learned_from_the_feed():
    tr = Tracker(finals.PROFILE)
    for i, victim in enumerate(("Dexter", "Luke")):
        frames(tr, [ob(TALLY_KILL, victim), ob(FEED_LINE, victim, values=("MochiBoy MMM", victim))], i * 10, 1)
    assert tr.learned_name == "MochiBoy"


def test_by_hand():
    tr = Tracker(finals.PROFILE, auto_start=False)
    frames(tr, [ob(TALLY_KILL, "Dexter")], 0, 2)
    assert tr.match is None                                # auto off: nothing starts by itself
    tr.start_match(0, "tdm")
    tr.adjust("kills", +1, 1)
    tr.adjust("deaths", -1, 1)
    assert tr.match.stats["kills"] == 1 and tr.match.stats["deaths"] == 0
    tr.end_match(60, "place", 1, manual=True)
    assert tr.last.result == "won"
    tr.fix_result("lost")
    assert tr.last.result == "lost"


# ------------------------------------------------------------------ the database
def test_store_keeps_matches(tmp_path):
    st = Store(tmp_path / "s.sqlite3")
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, "quick_cash")
    tr.adjust("kills", 3, 1)
    st.save(tr.match, now=1)
    tr.end_match(100, "won")
    st.save(tr.last, now=100)
    rows = st.recent("the_finals")
    assert len(rows) == 1 and rows[0]["kills"] == 3 and rows[0]["result"] == "won"
    tot = st.totals("the_finals")
    assert tot["matches"] == 1 and tot["wins"] == 1 and tot["kills"] == 3
    assert st.export_csv("the_finals", tmp_path / "m.csv") == 1
    st.clear("the_finals")
    assert st.recent("the_finals") == []


# ------------------------------------------------------------------ settings and what the notch says
def test_settings_and_lines():
    assert DEFAULT_CONFIG["gametrack"]["enabled"] is False
    s = gametrack.settings({"gametrack": {"names": "A, B", "notch_stats": ["kills", "nope", "lead"]}})
    assert s["names"] == ["A", "B"] and s["notch_stats"] == ["kills", "lead"]
    assert gametrack.names_for({"gametrack": {"learned_name": "Mochi"}}) == ["Mochi"]
    assert gametrack.money(2500, signed=True) == "+$2.5k" and gametrack.money(-1000, signed=True) == "−$1k"
    line = gametrack.notch_line(["kills", "deaths", "lead"], {"kills": 5, "deaths": 2},
                                {"ours": 1, "lead": 2500, "to_lead": 0, "best": 0, "rank": 1, "teams": 3}, 0)
    assert line == "K 5  D 2  +$2.5k"
    assert gametrack.notch_line(["lead"], {}, {}, 0) == ""          # lead unknown until your team is found


def test_notch_shows_the_match_and_its_buttons():
    n = NotchWindow()
    snap = {"state": "playing", "game": "THE FINALS", "mode": "Quick Cash", "seconds": 61.0, "dead": False,
            "line": "K 5  D 2", "tiles": [("Eliminations", "5"), ("Deaths", "2")], "round": 1, "final": False}
    n.set_game(snap, True)
    assert n.activity() == "game"
    n.hovered = True
    n._retarget()
    assert n._card_kind() == "game"
    for sp in n._springs:
        sp.snap(sp.target)
    img = QImage(n.width(), n.height(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#334"))
    p = QPainter(img)
    n.paint(p)
    p.end()
    assert "g_add_kills" in n._buttons and "g_end" in n._buttons
    got = []
    n.game_command.connect(lambda c, a: got.append((c, a)))
    n._game_cmd("add", "kills")
    assert got == [("add", "kills")]
    n.set_game({**snap, "state": "ended", "result": "won", "headline": "Victory", "estimated": True}, True)
    assert [k for k, _l in n._game_buttons()][:2] == ["g_fix_won", "g_fix_lost"]
    n.set_game(None)
    assert n.activity() != "game"


def test_watcher_by_hand(tmp_path):
    from rotary.gametrack.watcher import GameWatcher
    cfg = {"gametrack": {"enabled": False}}
    w = GameWatcher(lambda: cfg, db_path=tmp_path / "w.sqlite3")
    w.start_match("tdm")
    w.adjust("kills", 1)
    snap = w.snapshot()
    assert snap["state"] == "playing" and snap["mode"] == "Team Deathmatch" and "K 1" in snap["line"]
    w.end_match("won")
    snap = w.snapshot()
    assert snap["state"] == "ended" and snap["headline"] == "Victory"
    assert w.store.recent("the_finals")[0]["kills"] == 1
    w.dismiss_summary()
    assert w.snapshot() is None
    w.stop()


# ------------------------------------------------------------------ the whole way: pixels → words → stats
@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs Tesseract")
def test_a_drawn_hud_is_read():
    from rotary.gametrack.watcher import read_frame
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    rng = np.random.default_rng(3)
    for i in range(40):                                    # a busy, bright-ish picture behind the HUD
        p.fillRect(QRectF(*rng.uniform(0, W, 2), *rng.uniform(40, 400, 2)), QColor(*rng.integers(30, 190, 3)))
    f = QFont("Inter", 13)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))

    def feed_line(y, a, b):
        p.fillRect(QRectF(1500, y - 18, 400, 26), QColor(10, 10, 14))
        p.drawText(QPointF(1510, y), a)
        p.fillRect(QRectF(1640, y - 12, 50, 12), QColor("#cccccc"))
        p.drawText(QPointF(1720, y), b)

    feed_line(90, "MochiBoy", "Dexter77")
    feed_line(124, "Zed", "MochiBoy")
    f.setPointSize(16)
    p.setFont(f)
    p.drawText(QPointF(1000, 620), "ELIMINATION  DEXTER77  +100")
    p.drawText(QPointF(700, 40), "$4,500")
    p.drawText(QPointF(935, 40), "12:31")
    p.drawText(QPointF(1120, 40), "$6,000")
    p.end()
    ptr = img.constBits()
    frame = np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(H, W, 4).copy()
    obs, _texts = read_frame(frame, finals.PROFILE, ocr.Tesseract(), {"names": ["MochiBoy"]})
    got = kinds(obs)
    assert (FEED_KILL, "Dexter77") in got and (FEED_DEATH, "Zed") in got
    assert any(k == TALLY_KILL for k, _o in got)
    assert [v for _k, v in next(o for o in obs if o.kind == SCORES).values] == [4500, 6000]
