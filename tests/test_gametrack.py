"""Active Game Tracking: reading THE FINALS' HUD (from OCR words), turning frames into stats without counting
anything twice, matches starting and ending, the database, the notch and the controller."""
import shutil

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rotary import gametrack
from rotary.actions import DEFAULT_CONFIG
from rotary.gametrack import finals, ocr, text
from rotary.gametrack.engine import Tracker
from rotary.gametrack.profile import (DEAD, FEED_DEATH, FEED_KILL, FEED_LINE, HEAD_ASSIST, HEAD_KILL, IN_MATCH,
                                      MENU, MODE, RESULT, REVIVED_BY, SCORES, SELF_NAME, TALLY_ASSIST, TALLY_KILL,
                                      TALLY_REVIVE, Observation, Zone)
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


def read(names=("MochiBoy#2222",), frame=None, **zones):
    """finals.read on lines of words, by zone (feed=…, center=…, bottom=…)."""
    rows = {z: rows_from([w for line in lines for w in line]) for z, lines in zones.items()}
    return finals.read(rows, (W, H), {"names": list(names), "frame": frame})


def kinds(obs):
    return [(o.kind, o.other) for o in obs]


# ------------------------------------------------------------------ text
def test_ocr_mistakes_still_match():
    assert text.similar("EL1MINATION", "ELIMINATION")
    assert text.similar("ASSlST", "ASSIST")
    assert not text.similar("ASSIST", "REVIVE")
    assert text.find_phrase(["ELIMINATEDBY", "ZED"], "ELIMINATED BY") == (0, 1)       # run together
    assert text.find_phrase(["YOU", "WERE", "ELIMINATED"], "ELIMINATED") == (2, 3)
    # the headline isn't the death bar, the tally isn't the headline
    assert text.find_phrase(["ELIMINATED", "TEMPEST"], "ELIMINATED BY") is None
    assert not text.similar("ELIMINATED", "ELIMINATION") and not text.similar("ASSISTED", "ASSIST")
    assert text.run_in(["TEAMIRESPAWNINGIN12"], "RESPAWNING IN") and not text.run_in(["MOCHIBOY"], "BY")
    assert [text.board_cash(t) for t in ("$37,500", "s10.000", "837,500", "So", "12", "SCOREBOARD")] == \
        [37500, 10000, 37500, 0, None, None]
    assert text.name_in("M0CHIB0Y", "MochiBoy#1234")                  # O/0, and the Embark ID's number
    assert text.name_in("MochiBpy", "MochiBoy")                        # a letter off, in a long name
    assert not text.name_in("Al", "Alex") and not text.name_in("Alice", "Al")   # short names must be exact
    assert text.cash_values("$12,500  $7.5K  $900") == [12500, 7500, 900]
    assert text.count_mark(["ELIMINATION", "X2"]) == 2 and text.count_mark(["ASSIST"]) == 1
    assert text.same_player("MMM Dexter_77", "DEXTER_77") and not text.same_player("Dexter", "Kraken")
    assert text.best_name("MochiBoy MMMM") == "MochiBoy"
    assert text.best_name("[KSDAD] Al") == "Al"                           # a club tag isn't the name


def test_rows_split_at_the_icon():
    rows = rows_from(words(80, ["MochiBoy", "|", "Dexter"]) + words(110, ["Bob", "|", "Alice"]))
    assert [r.segment_texts() for r in rows] == [["MochiBoy", "Dexter"], ["Bob", "Alice"]]


# ------------------------------------------------------------------ reading THE FINALS
def test_feed_with_your_name():
    obs = read(feed=[words(80, ["MochiBoy", "|", "Dexter"]), words(110, ["Zed", "|", "M0chiBoy"]),
                     words(140, ["Kraken", "|", "Luke"])])
    assert kinds(obs)[:3] == [(FEED_KILL, "Dexter"), (FEED_DEATH, "Zed"), (FEED_LINE, "Luke")]
    # Season 12: "Killer [weapon] > Victim", a club tag in front of some names, distances over players
    obs = read(feed=[words(60, ["MochiBoy", "~7", ">", "[KSDAD]", "TheRealDevon_1"]),
                     words(90, ["[PR75]", "SweetSpicySalty", "=>", "MochiBoy"]), words(30, ["15m", "7m"])])
    assert kinds(obs)[:2] == [(FEED_KILL, "TheRealDevon_1"), (FEED_DEATH, "SweetSpicySalty")]


def test_headline_tally_and_what_isnt():
    obs = read(center=[words(733, ["ELIMINATED", "TEMPEST"], x=850, h=25),
                       words(773, ["ELIMINATION", "|", "+250"], x=840)])
    assert kinds(obs)[:2] == [(HEAD_KILL, "TEMPEST"), (TALLY_KILL, "")]
    assert kinds(read(center=[words(733, ["ASSISTED", "Dexter"], x=850, h=25)]))[0] == (HEAD_ASSIST, "Dexter")
    obs = read(center=[words(600, ["ASSIST", "+50"], x=1000), words(660, ["REVIVE", "Ana", "+75"], x=1000),
                       words(700, ["ELIMINATION", "X2", "Bob"], x=1000)])
    assert (TALLY_ASSIST, "") in kinds(obs) and (TALLY_REVIVE, "Ana") in kinds(obs)
    assert [o.count for o in obs if o.kind == TALLY_KILL] == [2]
    assert kinds(read(center=[words(700, ["ELIMINATED", "BY", "Zed"], x=800)]))[0] == (DEAD, "Zed")
    assert kinds(read(center=[words(700, ["REVIVED", "BY", "Ana"], x=800)]))[0] == (REVIVED_BY, "Ana")
    # things in the world, the stadium's screens, prompts, a teammate's news: none of them yours
    for line in (["REVIVE"], ["HOLD", "F", "TO", "REVIVE"], ["ELIMINATION", "STREAK", "4"], ["SANGUINE"],
                 ["CASHOUT", "STOLEN", "+1000"], ["CASHOUT", "STOLEN", "BONUS", "$1,000"], ["CASHOUT", "$10000"],
                 ["TEAMMATE", "ELIMINATED"], ["ELIMINATION"]):
        assert not [o for o in read(center=[words(700, line, x=800)]) if o.kind not in (IN_MATCH,)], line


def test_death_bar_and_card():
    obs = read(bottom=[words(1040, ["ELIMINATED", "BY:", "[PR75]", "SWEETSPICYSALTY"], x=710, h=34)])
    assert kinds(obs)[0] == (DEAD, "SWEETSPICYSALTY")
    obs = read(bottom=[words(1040, ["Change", "loadout"], x=60) + words(1040, ["TEAMRESPAWNINGIN12"], x=750)])
    assert kinds(obs)[0] == (DEAD, "") and not obs[0].values
    weak = read(bottom=[words(1040, ["FREE", "CAMERA"], x=1720)])[0]
    assert weak.kind == DEAD and "weak" in weak.values
    card = read(card=[words(300, ["ELIMINATED", "BY"], x=1380), words(328, ["BusinessLock"], x=1385),
                      words(378, ["144", "by", "BusinessLock"], x=1460), words(432, ["85", "to", "ShrewdFace"], x=1460)])
    assert kinds(card)[0] == (DEAD, "BusinessLock")
    # the heading misread: the damage rows are enough
    card = read(card=[words(432, ["85", "to", "BusinessLock"], x=1460), words(486, ["17", "to", "Vasiliy"], x=1460)])
    assert kinds(card)[0] == (DEAD, "")


def test_banners_need_big_letters():
    big = H * 0.06
    assert kinds(read(banner=[words(400, ["VICTORY"], x=800, h=big)])) == [(RESULT, "won")]
    assert kinds(read(banner=[words(400, ["QUALIFIED"], x=800, h=big)])) == [(RESULT, "qualified")]
    assert kinds(read(center=[words(400, ["QUALIFIED"], x=800, h=big)])) == [(RESULT, "qualified")]
    o = read(banner=[words(400, ["WINNER"], x=800, h=big)])[0]
    assert (o.kind, o.other) == (RESULT, "winner") and "unsure" in o.values
    o = read(banner=[words(400, ["ELIMINATED"], x=800, h=big)])[0]
    assert (o.kind, o.other) == (RESULT, "lost") and "unsure" in o.values
    assert not read(banner=[words(400, ["ELIMINATED", "TEMPEST"], x=800, h=big)])
    assert not [o for o in read(banner=[words(400, ["NOT", "QUALIFIED"], x=800, h=big)]) if o.other == "qualified"]
    assert read(banner=[words(400, ["2ND", "PLACE"], x=800, h=big)])[0].count == 2
    assert kinds(read(banner=[words(400, ["QUICK", "CASH"], x=800, h=big)])) == [(MODE, "quick_cash")]
    assert not [o for o in read(banner=[words(400, ["CASHOUT", "COMPLETE"], x=800, h=big)]) if o.kind == MODE]
    assert not [o for o in read(banner=[words(400, ["VICTORY"], x=800)]) if o.kind == RESULT]   # small: not a banner


def board_frame(rows: dict[int, str]):
    """A dark frame with the cash board at 1080p: for each row (its middle's y), the amount's letters at x 135
    ("white": banked, "yellow": on its way to a cashout), and "own" puts your team's blue box round the rank."""
    frame = np.full((H, W, 3), 20, np.uint8)
    for cy, how in rows.items():
        frame[cy - 9:cy + 9, 135:200] = (40, 220, 240) if "yellow" in how else (240, 240, 240)
        if "own" in how:
            frame[cy - 16:cy + 16, 50:90] = (210, 150, 40)           # BGR
    return frame


def test_teams_cash_corner():
    lines = [words(77, ["1"], x=75) + words(77, ["$37,500", "+", "$18,000"], x=135),
             words(123, ["2"], x=75) + words(123, ["$35,000"], x=135), words(166, ["KNOCKOUT", "ZONE"], x=68),
             words(206, ["3"], x=75) + words(206, ["$7,000"], x=135), words(252, ["$5,500"], x=135)]
    frame = board_frame({86: "white", 132: "white", 215: "white", 261: "white own"})
    sc = next(o for o in read(board=lines, frame=frame) if o.kind == SCORES)
    assert sc.values == (("#1", 37500), ("#2", 35000), ("#3", 7000), ("#4", 5500))
    assert sc.other == "#4" and sc.count == 2           # yours in the blue box; two go through
    # "$0 + $10,000" read as just the yellow "$10,000": not the team's cash, but still the 2nd row
    lines = [words(77, ["1", "$10,000"], x=115), words(123, ["2"], x=115) + words(123, ["$10,000"], x=135),
             words(169, ["$0"], x=135)]
    frame = board_frame({86: "white", 132: "yellow", 178: "white own"})
    sc = next(o for o in read(board=lines, frame=frame) if o.kind == SCORES)
    assert sc.values == (("#1", 10000), ("#3", 0)) and sc.other == "#3"
    # no blue box (or blue everywhere: a blue sky behind the board): no guess
    sc = next(o for o in read(board=lines, frame=board_frame({86: "white own", 178: "white own"}))
              if o.kind == SCORES)
    assert sc.other == ""


def test_clock_menus_and_your_name():
    obs = read(timer=[words(55, ["12:39"], x=935)])
    assert (IN_MATCH, "") in kinds(obs)
    assert (IN_MATCH, "") in kinds(read(timer=[words(55, ["1:45", "+0:17"], x=915)]))
    menu = read(timer=[words(30, ["PLAY", "|", "LOADOUT", "|", "CAREER", "|", "STORE"], x=700)])
    assert (MENU, "") in kinds(menu)
    me = read(self=[words(940, ["250/250"], x=70, h=30), words(1003, ["LevelCap"], x=67, h=16)])
    assert kinds(me) == [(SELF_NAME, "LevelCap")]
    assert not read(self=[words(815, ["L", "ReadyTrooper"], x=110)])                # a teammate's row


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


def test_headline_tally_and_feed_are_one_elimination():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, manual=False)
    frames(tr, [ob(HEAD_KILL, "TEMPEST"), ob(TALLY_KILL), ob(FEED_KILL, "Tempest")], 1, 3)
    assert tr.match.stats["kills"] == 1
    frames(tr, [ob(HEAD_KILL, "DEXTER"), ob(TALLY_KILL)], 6, 3)             # the next one, a new name
    assert tr.match.stats["kills"] == 2
    frames(tr, [ob(HEAD_ASSIST, "Kraken"), ob(TALLY_ASSIST)], 12, 2)
    assert tr.match.stats["assists"] == 1


def test_while_dead_only_the_feed_counts():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, manual=False)
    frames(tr, [ob(DEAD, "Zed")], 10, 2)
    assert tr.dead and tr.match.stats["deaths"] == 1
    # watching a teammate: their headline and tally aren't yours, now or once you're back
    frames(tr, [ob(DEAD, "Zed"), ob(HEAD_KILL, "Bob"), ob(TALLY_KILL), ob(TALLY_ASSIST)], 12, 3)
    frames(tr, [ob(HEAD_KILL, "Bob"), ob(TALLY_KILL)], 15, 1)
    assert tr.match.stats["kills"] == 0 and tr.match.stats["assists"] == 0
    # …but the feed saying you got one (a grenade going off after you went down) does
    frames(tr, [ob(DEAD, "Zed"), ob(FEED_KILL, "Luke")], 16, 2)
    assert tr.match.stats["kills"] == 1 and tr.match.stats["deaths"] == 1
    # FREE CAMERA keeps you dead, but on its own it doesn't kill you
    frames(tr, [ob(DEAD, values=("weak",))], 18, 4)
    assert tr.dead
    frames(tr, [], 22, 7)
    assert not tr.dead
    frames(tr, [ob(DEAD, values=("weak",))], 40, 4)
    assert not tr.dead and tr.match.stats["deaths"] == 1


def test_your_team_from_the_board():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, "cashout", manual=False)
    board = Observation(SCORES, "#4", 2, (("#1", 37500), ("#2", 35000), ("#3", 7000), ("#4", 5500)))
    frames(tr, [board], 1, 1)
    m = tr.match
    assert m.team == "#4" and m.team_sure and m.cutoff == 2
    st = m.standing(finals.PROFILE)
    assert st["rank"] == 4 and st["to_qualify"] == 29501 and st["to_lead"] == 32001
    # a row missed for a moment keeps its cash
    frames(tr, [Observation(SCORES, "#4", 2, (("#1", 37500), ("#2", 35000), ("#4", 5500)))], 3, 1)
    assert dict(m.scores)["#3"] == 7000


def test_your_name_from_under_your_health():
    tr = Tracker(finals.PROFILE)
    tr.start_match(0, manual=False)
    ev = frames(tr, [ob(SELF_NAME, "LevelCap")], 1, 1)
    assert not tr.learned_name                                  # once isn't enough
    ev = frames(tr, [ob(SELF_NAME, "LeveiCap")], 2, 0.4)         # a misread now and then
    ev += frames(tr, [ob(SELF_NAME, "LevelCap")], 3, 2)
    assert tr.learned_name == "LevelCap" and any(e.kind == "name" for e in ev)
    frames(tr, [ob(DEAD, "Zed")], 10, 1)
    frames(tr, [ob(DEAD, "Zed"), ob(SELF_NAME, "ReadyTrooper")], 11, 20)    # spectating: someone else's
    assert tr.learned_name == "LevelCap"


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
    assert n._card_kind() == ""                             # playing: the stats stay on the notch, hover or not
    n.hovered = False
    n.set_game({**snap, "dead": True, "killer": "Zed"}, True)
    assert n._card_kind() == "game"                         # eliminated: it opens by itself
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
    assert n._card_kind() == "game"                         # the summary, by itself too
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
def _hud(draw):
    """A 1080p frame: a busy, bright-ish picture with whatever `draw(painter, font)` puts over it."""
    img = QImage(W, H, QImage.Format.Format_RGB32)
    p = QPainter(img)
    rng = np.random.default_rng(3)
    for i in range(40):
        p.fillRect(QRectF(*rng.uniform(0, W, 2), *rng.uniform(40, 400, 2)), QColor(*rng.integers(30, 190, 3)))
    f = QFont("Inter", 13)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    draw(p, f)
    p.end()
    ptr = img.constBits()
    return np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(H, W, 4).copy()


def _tilted(p, x, y, angle, fn):
    p.save()
    p.translate(x, y)
    p.rotate(angle)
    fn()
    p.restore()


def test_a_drawn_hud_is_read():
    """Season 12's layout, drawn: the killfeed leaning up at the top right, the cash board leaning down at the
    top left with your rank in a blue box, the clock, the headline and tally under the crosshair."""
    from rotary.gametrack.watcher import read_frame

    def draw(p, f):
        def feed():
            p.fillRect(QRectF(-10, -60, 420, 100), QColor(10, 10, 14))
            p.drawText(QPointF(0, -20), "MochiBoy  >  Dexter77")
            p.drawText(QPointF(40, 20), "Zed  >  MochiBoy")
        _tilted(p, 1450, 80, -5, feed)

        def board():
            p.fillRect(QRectF(-20, -30, 260, 180), QColor(12, 12, 16))
            for i, (cash, own) in enumerate((("$12,500", False), ("$6,000", True), ("$1,500", False))):
                y = 46 * i
                if own:
                    p.fillRect(QRectF(-8, y - 20, 34, 30), QColor(40, 150, 215))
                p.drawText(QPointF(0, y), str(i + 1))
                p.drawText(QPointF(85, y), cash)
        _tilted(p, 60, 90, 5, board)
        p.drawText(QPointF(935, 60), "12:31")
        f.setPointSize(18)
        f.setItalic(True)
        p.setFont(f)
        p.drawText(QPointF(850, 733), "ELIMINATED  DEXTER77")
        f.setPointSize(14)
        p.setFont(f)
        p.drawText(QPointF(850, 773), "ELIMINATION  |  +250")

    obs, _texts = read_frame(_hud(draw), finals.PROFILE, ocr.Tesseract(), {"names": ["MochiBoy"]})
    got = kinds(obs)
    assert (FEED_KILL, "Dexter77") in got and (FEED_DEATH, "Zed") in got
    assert (HEAD_KILL, "DEXTER77") in got and any(k == TALLY_KILL for k, _o in got)
    sc = next(o for o in obs if o.kind == SCORES)
    assert [v for _k, v in sc.values] == [12500, 6000, 1500] and sc.other == "#2"
    assert (IN_MATCH, "") in got


def test_a_drawn_death_screen_is_read():
    from rotary.gametrack.watcher import read_frame

    def draw(p, f):
        p.fillRect(QRectF(0, 1000, W, 80), QColor(214, 32, 62))       # the red bar, white letters on it
        f.setPointSize(24)
        f.setItalic(True)
        p.setFont(f)
        p.drawText(QPointF(710, 1055), "ELIMINATED BY:  SWEETSPICYSALTY")

    obs, _texts = read_frame(_hud(draw), finals.PROFILE, ocr.Tesseract(), {"names": ["MochiBoy"]})
    assert (DEAD, "SWEETSPICYSALTY") in kinds(obs)


def test_zones_can_be_moved_in_the_config():
    from rotary.gametrack.watcher import zones_of
    z = zones_of(finals.PROFILE, {"feed": [0.5, 0.0, 1.0, 0.2], "extra": [0, 0, 0.5, 0.5, 0, 1, True], "bad": [2, 0]})
    assert z["feed"][:4] == (0.5, 0.0, 1.0, 0.2) and z["feed"].angle == finals.ZONES["feed"].angle   # keeps its tilt
    assert z["extra"] == Zone(0, 0, 0.5, 0.5, block=True) and "bad" not in z
