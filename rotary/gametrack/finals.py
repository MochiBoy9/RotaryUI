"""THE FINALS (Embark Studios): where its HUD puts things, and the words it uses.

Read only from the screen, like a viewer of a stream would: nothing touches the game's memory or files, so
Easy Anti-Cheat has nothing to object to. The capture works with the game in Borderless or Windowed mode; in
exclusive Fullscreen Windows hands other apps a black screen.

What's on the screen in Season 12 (as at 1920 × 1080; the HUD leans in at the sides, so the corners are read
levelled):

  * Top left, the teams' cash, best first: "1 [badge] $37,500 + $18,000" (the "+" is cash on its way to a
    cashout). Your team's rank sits in a filled blue box. In a knockout round a KNOCKOUT ZONE line separates
    the teams going through from the rest.
  * Top middle, the round's clock ("12:39", "+0:17" in overtime) under the compass.
  * Top right, the elimination feed: "Killer [weapon] > Victim", a club tag in a box before some names
    ("[KSDAD] TheRealDevon_1"). Your teammates are blue, the others red or orange.
  * Under the crosshair, when you eliminate someone, the headline "ELIMINATED Tempest" (ASSISTED for an
    assist) and under it the tally "ELIMINATION | +250". Things in the world look a lot like these and are
    left alone: REVIVE over a teammate's statue, CASHOUT $10000 over a station, the stadium screens'
    "ELIMINATION STREAK 4", CASHOUT STOLEN +1000.
  * When you're out: a red bar along the bottom, "ELIMINATED BY: [PR75] SWEETSPICYSALTY", then "TEAM
    RESPAWNING IN 12", FREE CAMERA, Change loadout; often a card on the right, ELIMINATED BY and the name over
    the damage dealt each way ("144 by BusinessLock", "85 to ShrewdFace"). Eliminations read while it's up
    aren't yours unless the feed says so (the tracker sees to that).
  * Bottom left, your health ("250/250") with your name under it, and your teammates above.
  * Big in the middle at the end of a round: QUALIFIED (through to the next round), a placement, or a win or
    loss banner (VICTORY, DEFEAT, a lone ELIMINATED in the simpler modes, WINNER over the winning team).

Modes (Embark's help centre and the patch notes, Season 12): Quick Cash (3 teams of 3, cash out to win), Cashout
(the tournament: 8 teams of 3, two knockout rounds of 4 teams with the top 2 going through, then a final of 2
teams; "World Tour" before Season 9) and Ranked Cashout, Solo Showdown (12 players, the 4 with the least cash
out after each of 3 rounds), Team Deathmatch and Power Shift (5 v 5), Point Break (8 v 8, attack and defend),
and the older Bank It and Terminal Attack, which come back for events.
"""
from __future__ import annotations

import re

from .profile import (DEAD, FEED_ASSIST, FEED_DEATH, FEED_KILL, FEED_LINE, FEED_REVIVE, HEAD_ASSIST, HEAD_KILL,
                      IN_MATCH, MENU, MODE, RESULT, REVIVED_BY, ROUND, SCORES, SELF_NAME, TALLY_ASSIST, TALLY_KILL,
                      TALLY_REVIVE, TALLY_WIPE, Mode, Observation, Profile, Zone)
from .text import (Row, Word, best_name, board_cash, cash_values, clean, count_mark, find_phrase, fold, name_in,
                   run_in, similar)

ZONES = {
    "feed": Zone(0.64, 0.0, 1.0, 0.16, angle=-5, scale=2, slant=0.15),     # killfeed, top right, leaning up
    "board": Zone(0.0, 0.03, 0.2, 0.32, angle=5, scale=2, every=2, slant=0.15),   # teams' cash, leaning down
    "timer": Zone(0.36, 0.02, 0.64, 0.1, scale=1.5),                       # the clock (and the menus' tabs)
    "center": Zone(0.25, 0.2, 0.75, 0.88, slant=0.15),                     # headline and tally
    "banner": Zone(0.15, 0.25, 0.85, 0.75, scale=0.33, block=True, slant=0.2),    # QUALIFIED, results, rounds
    "bottom": Zone(0.0, 0.9, 1.0, 1.0, scale=2, slant=0.15),               # the death bar
    "card": Zone(0.66, 0.2, 1.0, 0.6, scale=1.2, every=2),                 # ELIMINATED BY card
    "self": Zone(0.02, 0.84, 0.25, 0.97, scale=2, every=4),                # your health and name
}

MODES = {
    "quick_cash": Mode("quick_cash", "Quick Cash", ("QUICK CASH",), teams=3, team_size=3),
    "cashout": Mode("cashout", "Cashout", ("CASHOUT", "WORLD TOUR", "CASHOUT TOURNAMENT"), teams=4, team_size=3,
                    knockout=True, qualify=2),
    "ranked": Mode("ranked", "Ranked Cashout", ("RANKED CASHOUT", "RANKED TOURNAMENT"), teams=4,
                   team_size=3, knockout=True, qualify=2),
    "solo_showdown": Mode("solo_showdown", "Solo Showdown", ("SOLO SHOWDOWN",), teams=12, team_size=1,
                          knockout=True, qualify=8),
    "tdm": Mode("tdm", "Team Deathmatch", ("TEAM DEATHMATCH",), teams=2, team_size=5),
    "power_shift": Mode("power_shift", "Power Shift", ("POWER SHIFT", "POWERSHIFT"), teams=2, team_size=5,
                        scored=False),
    "point_break": Mode("point_break", "Point Break", ("POINT BREAK",), teams=2, team_size=8, scored=False),
    "bank_it": Mode("bank_it", "Bank It", ("BANK IT", "SOLO BANK IT"), teams=4, team_size=3),
    "terminal_attack": Mode("terminal_attack", "Terminal Attack", ("TERMINAL ATTACK",), teams=2, team_size=5,
                            scored=False),
}

WORDS = {
    # only up while you're out: the death bar, the card, spectating
    "dead": ["ELIMINATED BY", "KILLED BY", "YOU WERE ELIMINATED", "YOU HAVE BEEN ELIMINATED", "RESPAWN IN",
             "RESPAWNING IN", "TEAM RESPAWNING IN", "RESPAWNING", "USE RESPAWN COIN", "WAITING FOR REVIVE",
             "BEING REVIVED", "SPECTATING", "YOUR TEAM WAS WIPED", "YOUR TEAM WIPED"],
    # on the death bar too, but maybe not only there: they keep you dead, they don't make you dead
    "dead_weak": ["FREE CAMERA", "CHANGE LOADOUT", "RESPAWN COIN"],
    "revived_by": ["REVIVED BY"],
    # the headline over the tally: the word, then the player's name
    "head_kill": ["ELIMINATED"],
    "head_assist": ["ASSISTED"],
    # the tally: the word, then the points
    "kill": ["ELIMINATION", "KILL", "TAKEDOWN"],
    "assist": ["ASSIST", "ELIMINATION ASSIST", "KILL ASSIST"],
    "revive": ["REVIVE", "TEAMMATE REVIVED", "REVIVED"],
    "wipe": ["TEAM WIPE", "TEAM WIPED", "SQUAD WIPE"],
    # in the world or on the stadium's screens, not about you: a row with one of these isn't the tally
    "world": ["STREAK", "STOLEN", "CASHOUT", "BONUS", "VAULT", "STATION", "HOLD", "PRESS", "REVIVING", "WAITING",
              "BEING", "YOUR", "TEAMMATE", "BY"],
    # end of a round or the match (big letters only)
    "lost": ["DEFEAT", "YOU LOST", "ELIMINATED", "TEAM ELIMINATED", "KNOCKED OUT", "DISQUALIFIED",
             "NOT QUALIFIED", "ELIMINATED FROM THE TOURNAMENT", "GAME OVER"],
    "won": ["VICTORY", "YOU WON", "YOU WIN", "YOUR TEAM WON", "CHAMPIONS", "TOURNAMENT WON", "TOURNAMENT WINNERS"],
    "winner": ["WINNER", "WINNERS"],
    "qualified": ["QUALIFIED", "YOU QUALIFIED"],
    "draw": ["DRAW"],
    # the menus between matches
    "menu": ["PLAY", "LOADOUT", "CAREER", "STORE", "SHOP", "CONTRACTS", "CUSTOMIZE", "EVENTS", "SOCIAL",
             "BATTLE PASS", "SEARCHING FOR MATCH", "FINDING MATCH", "MATCHMAKING", "READY UP"],
    "round": ["ROUND", "FINAL ROUND"],
}

BIG = 0.035             # banner letters at least this tall (part of the frame's height); the headline's are ~2.3 %
TIMER = re.compile(r"^\+?\d{1,2}[:.]\d{2}$")
POINTS = re.compile(r"^\+?\d{2,4}$")
DISTANCE = re.compile(r"^\d{1,4}M$")                 # "15m" over a player or an objective
ARROW = re.compile(r"[>»›→]")
HEALTH = re.compile(r"^(\d{2,3})[/|IL1]?(\d{3})$")   # "250/250" (the "/" can come out as a 1 or an l)
DAMAGE = re.compile(r"^\d{1,4}$")


def words(ctx: dict, key: str) -> list[str]:
    """The phrases for `key`, with any the user added in config.json ("gametrack" → "words")."""
    extra = (ctx.get("words") or {}).get(key) or []
    return WORDS[key] + [str(w).upper() for w in extra if str(w).strip()]


def any_phrase(tokens: list[str], phrases: list[str]):
    """The first of `phrases` found in `tokens`: (phrase, (start, end)), or None."""
    for ph in sorted(phrases, key=lambda p: -len(p)):          # longest first: "ELIMINATED BY" before "ELIMINATED"
        at = find_phrase(tokens, ph)
        if at is not None:
            return ph, at
    return None


def _starts(tokens: list[str], phrases: list[str], within: int = 1):
    """One of `phrases` starting in the first `within` + 1 tokens: (phrase, (start, end)), or None."""
    for ph in sorted(phrases, key=lambda p: -len(p)):
        for i in range(min(len(tokens), within + 1)):
            at = find_phrase(tokens[i:i + len(ph.split()) + 2], ph)
            if at is not None and at[0] == 0:
                return ph, (i, i + at[1])
    return None


def _rest(row: Row, skip: tuple[int, int]) -> str:
    """The words of a row outside `skip`, minus points ("+100"), counts ("×2") and cash: usually a name."""
    keep = []
    for i, w in enumerate(row.words):
        if skip[0] <= i < skip[1]:
            continue
        c = clean(w.text)
        if not c or c.startswith("+") or c.startswith("$") or re.fullmatch(r"[X×]?\d+[X×]?|\d+", c):
            continue
        keep.append(w.text.strip(" +:·-|"))
    return " ".join(k for k in keep if k)


def _mine(text: str, names: list[str]) -> bool:
    return any(name_in(text, n) for n in names)


def _clean_rows(rows: list[Row]) -> list[Row]:
    """Rows without distance markers ("15m"); rows left empty dropped."""
    out = []
    for r in rows:
        keep = [w for w in r.words if clean(w.text) and not DISTANCE.match(clean(w.text))]
        if keep:
            ids = {id(w) for w in keep}
            out.append(Row(keep, [s for s in ([w for w in seg if id(w) in ids] for seg in r.segments) if s]))
    return out


# ---------------------------------------------------------------------------------------------- the killfeed

def feed_sides(row: Row) -> tuple[str, str, list[str]] | None:
    """A killfeed line split into (killer, victim, anything between): at the ">" if OCR saw it, else at the
    gaps the weapon icon leaves."""
    ws = row.words
    for i in range(len(ws) - 1, -1, -1):
        if ARROW.search(ws[i].text):
            parts = ARROW.split(ws[i].text)
            left = " ".join([w.text for w in ws[:i]] + [parts[0]])
            right = " ".join([parts[-1]] + [w.text for w in ws[i + 1:]])
            if clean(left) and clean(right):
                return left.strip(), right.strip(), []
            return None
    segs = [s for s in row.segment_texts() if clean(s)]
    if len(segs) >= 2:
        return segs[0], segs[-1], segs[1:-1]
    return None


def read_feed(rows: list[Row], names: list[str], ctx: dict) -> list[Observation]:
    out = []
    for row in _clean_rows(rows):
        toks = row.tokens
        if not toks or any(cash_values(w.text) or TIMER.match(w.text.strip()) for w in row.words):
            continue                                  # something else poking into the corner
        revived = any_phrase(toks, ["REVIVED"])
        sides = feed_sides(row)
        if names and _mine(row.text, names):
            if revived is not None:
                before = " ".join(w.text for w in row.words[:revived[1][0]])
                if _mine(before, names):
                    out.append(Observation(FEED_REVIVE, best_name(_rest(row, (0, revived[1][1]))), text=row.text))
                continue
            if sides is not None:
                killer, victim, middle = sides
                if _mine(killer, names) and not _mine(victim, names):
                    out.append(Observation(FEED_KILL, best_name(victim), text=row.text))
                elif _mine(victim, names) and not _mine(killer, names):
                    out.append(Observation(FEED_DEATH, best_name(killer), text=row.text))
                elif any(_mine(m, names) for m in middle):
                    out.append(Observation(FEED_ASSIST, best_name(victim), text=row.text))
                elif _mine(victim, names):
                    out.append(Observation(FEED_DEATH, "", text=row.text))       # took themselves out
                continue
            # one run of words (neither the ">" nor the icon's gap was seen): which end is the name at?
            ws = row.words
            if len(ws) > 1 and _mine(ws[0].text, names):
                out.append(Observation(FEED_KILL, best_name(" ".join(w.text for w in ws[1:])), text=row.text))
            elif len(ws) > 1 and _mine(ws[-1].text, names):
                out.append(Observation(FEED_DEATH, best_name(" ".join(w.text for w in ws[:-1])), text=row.text))
            continue
        if sides is not None and revived is None:
            killer, victim = best_name(sides[0]), best_name(sides[1])
            if len(clean(killer)) >= 3 and len(clean(victim)) >= 3:
                out.append(Observation(FEED_LINE, victim, values=(killer, victim), text=row.text))
    return out


# ---------------------------------------------------------------------------------- headline and tally

def read_center(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    out = []
    H = size[1]
    for row in _clean_rows(rows):
        toks = row.tokens
        if not toks:
            continue
        if row.h >= BIG * H:                           # a banner (the banner zone reads the biggest ones)
            found = result_of(row, ctx) or mode_of(row) or round_of(row)
            if found is not None:
                out.append(found)
            continue
        hit = any_phrase(toks, words(ctx, "dead"))
        if hit is not None:
            out.append(_dead(row, hit))
            continue
        hit = any_phrase(toks, words(ctx, "revived_by"))
        if hit is not None:
            out.append(Observation(REVIVED_BY, best_name(_rest(row, (0, hit[1][1]))), text=row.text))
            continue
        if any_phrase(toks, words(ctx, "world")) is not None:
            continue
        found = _headline(row, ctx) or _tally(row, ctx)
        if found is not None:
            out.append(found)
    return out


def _headline(row: Row, ctx: dict) -> Observation | None:
    """ "ELIMINATED Tempest" / "ASSISTED Tempest": the word first, then a name (not "BY": that one's you)."""
    toks = row.tokens
    first = fold(toks[0])
    for kind, key in ((HEAD_KILL, "head_kill"), (HEAD_ASSIST, "head_assist")):
        for ph in words(ctx, key):
            if similar(toks[0], ph):
                if len(toks) < 2 or similar(toks[1], "BY"):
                    return None
                name = best_name(_rest(row, (0, 1)))
                return Observation(kind, name, text=row.text) if len(clean(name)) >= 2 else None
            n = len(fold(ph))
            if len(first) >= n + 3 and similar(first[:n], ph) and not first[n:].startswith("BY"):
                return Observation(kind, row.words[0].text.strip()[n:], text=row.text)    # run together
    return None


def _clipped(tokens: list[str], phrases: list[str]):
    """A long word with its first letter or two lost ("IMINATION"): OCR drops letters at a line's start."""
    for ph in phrases:
        whole = fold(ph)
        if " " in ph or len(whole) < 10:
            continue
        for i, t in enumerate(tokens[:2]):
            ft = fold(t)
            if len(whole) - 2 <= len(ft) < len(whole) and whole.endswith(ft):
                return ph, (i, i + 1)
    return None


def _tally(row: Row, ctx: dict) -> Observation | None:
    """ "ELIMINATION | +250": the word at the start of the row and the points after it (or "×2"). Without
    the points it's a marker in the world (the REVIVE over a statue)."""
    toks = row.tokens
    for kind, key in ((TALLY_WIPE, "wipe"), (TALLY_ASSIST, "assist"), (TALLY_KILL, "kill"),
                      (TALLY_REVIVE, "revive")):
        hit = _starts(toks, words(ctx, key)) or _clipped(toks, words(ctx, key))
        if hit is None:
            continue
        after = toks[hit[1][1]:]
        count = count_mark(after)
        if count == 1 and not any(POINTS.match(t) for t in after):
            return None
        return Observation(kind, best_name(_rest(row, (0, hit[1][1]))), count=count, text=row.text)
    return None


def _dead(row: Row, hit) -> Observation:
    killer = best_name(_rest(row, (0, hit[1][1]))) if hit[0].endswith("BY") else ""
    return Observation(DEAD, killer, text=row.text)


# --------------------------------------------------------------------------- the death bar and the card

def read_bottom(rows: list[Row], ctx: dict) -> list[Observation]:
    out = []
    for row in _clean_rows(rows):
        toks = row.tokens
        hit = any_phrase(toks, words(ctx, "revived_by"))
        if hit is not None:
            out.append(Observation(REVIVED_BY, best_name(_rest(row, (0, hit[1][1]))), text=row.text))
            continue
        hit = any_phrase(toks, words(ctx, "dead"))
        if hit is not None:
            out.append(_dead(row, hit))
        elif any(run_in(toks, ph) for ph in ("TEAM RESPAWNING", "RESPAWNING IN", "ELIMINATED BY")):
            out.append(Observation(DEAD, text=row.text))                 # the bar, its spaces lost
        elif any_phrase(toks, words(ctx, "dead_weak")) is not None:
            out.append(Observation(DEAD, values=("weak",), text=row.text))
    return out


def _damage_row(row: Row) -> tuple[str, str] | None:
    """ "144 by BusinessLock" / "85 to ShrewdFace" on the death card: ("BY" or "TO", the name)."""
    toks = row.tokens
    for i in range(min(2, len(toks) - 2) + 1):
        if DAMAGE.match(toks[i]) and toks[i + 1] in ("BY", "TO"):
            name = best_name(" ".join(w.text for w in row.words[i + 2:]))
            if len(clean(name)) >= 2:
                return toks[i + 1], name
    return None


def read_card(rows: list[Row], ctx: dict) -> list[Observation]:
    """The ELIMINATED BY card: its heading, who, then the damage either way."""
    rows = _clean_rows(rows)
    killer, heading, damage = "", False, 0
    for i, row in enumerate(rows):
        if any_phrase(row.tokens, ["ELIMINATED BY", "KILLED BY"]) is not None:
            heading = True
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt is not None and _damage_row(nxt) is None:
                killer = best_name(nxt.text)
            continue
        side = _damage_row(row)
        if side is not None:
            damage += 1
            if side[0] == "BY" and not killer:
                killer = side[1]
    if heading or damage >= 2:
        return [Observation(DEAD, killer, text="card: " + " / ".join(r.text for r in rows))]
    return []


# ----------------------------------------------------------------------------- banners, modes and rounds

def read_banner(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    out = []
    for row in rows:
        if not row.tokens or row.h < BIG * size[1]:
            continue
        found = result_of(row, ctx) or mode_of(row) or round_of(row)
        if found is not None:
            out.append(found)
    return out


def result_of(row: Row, ctx: dict) -> Observation | None:
    toks = row.tokens
    if len(toks) <= 3:
        for t in toks:
            m = re.fullmatch(r"#?([1-9]|1[0-2])(ST|ND|RD|TH)?", t)
            if m and (m.group(2) or "PLACE" in toks):
                return Observation(RESULT, "place", count=int(m.group(1)), text=row.text)
    if len(toks) > 5:
        return None                                    # a sentence, not a banner
    for kind in ("lost", "won", "qualified", "winner", "draw"):
        hit = any_phrase(toks, words(ctx, kind))
        if hit is None:
            continue
        if hit[0] == "ELIMINATED" and len(toks) > 1:
            continue                                   # "ELIMINATED <name>" is news, not the end
        if hit[0] == "QUALIFIED" and similar(toks[0], "NOT"):
            continue
        # A lone ELIMINATED or WINNER could be about another team: the tracker double-checks those.
        unsure = kind == "winner" or (kind == "lost" and hit[0] in ("ELIMINATED", "TEAM ELIMINATED"))
        return Observation(RESULT, kind, values=("unsure",) if unsure else (), text=row.text)
    return None


def mode_of(row: Row) -> Observation | None:
    """A mode's name on a row of its own (the match intro, the scoreboard's header). A one-word name must be
    the whole row: "CASHOUT COMPLETE" is news in any mode, not the Cashout tournament."""
    toks = row.tokens
    best = None
    for m in MODES.values():
        for ph in m.words:
            at = find_phrase(toks, ph)
            extra = len(toks) - (at[1] - at[0]) if at is not None else 99
            if at is not None and extra <= (0 if " " not in ph else 2):
                if best is None or len(ph) > best[1]:
                    best = (m.key, len(ph))
    if best is None:
        return None
    return Observation(MODE, best[0], text=row.text)


def round_of(row: Row) -> Observation | None:
    toks = row.tokens
    if find_phrase(toks, "FINAL ROUND") is not None:
        return Observation(ROUND, "final", count=99, text=row.text)
    at = find_phrase(toks, "ROUND")
    if at is not None and at[1] < len(toks) and re.fullmatch(r"[1-9]", toks[at[1]].replace("I", "1")):
        n = int(toks[at[1]].replace("I", "1"))
        return Observation(ROUND, str(n), count=n, text=row.text)
    return None


# ------------------------------------------------------------------------------------------ the teams' cash

def _blue(frame, word: Word) -> float:
    """How much of the rank box left of this amount (past the team's badge) is your team's blue, 0–1."""
    if frame is None:
        return 0.0
    H, W = frame.shape[:2]
    h = max(4.0, word.h)
    x0, x1 = int(max(0, word.x - 4.8 * h)), int(max(0, word.x - 2.6 * h))
    y0, y1 = int(max(0, word.cy - 0.7 * h)), int(min(H, word.cy + 0.7 * h))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return 0.0
    reg = frame[y0:y1, x0:x1, :3].astype(int)
    b, g, r = reg[..., 0], reg[..., 1], reg[..., 2]
    return float(((b > 70) & (b - r > 40) & (b >= g)).mean())     # dimmer when the screen greys out (dead)


def _yellow(frame, word: Word) -> bool:
    """Is this amount written in yellow, as cash on its way to a cashout is (banked cash is white)?"""
    if frame is None:
        return False
    H, W = frame.shape[:2]
    x0, x1 = int(max(0, word.x)), int(min(W, word.right))
    y0, y1 = int(max(0, word.y)), int(min(H, word.y + word.h))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return False
    reg = frame[y0:y1, x0:x1, :3].astype(int)
    b, g, r = reg[..., 0], reg[..., 1], reg[..., 2]
    ink = (r > 150) & (g > 130)                           # the letters (white or yellow), not the backdrop
    if ink.sum() < 6:
        return False
    return float((b[ink] < 0.6 * g[ink]).mean()) > 0.5


def read_board(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    """The teams' cash, top left, best first. Each row's first amount is the team's (a "+ $…" after it is cash
    on its way to a cashout, not banked yet), KNOCKOUT ZONE marks how many go through, and the row with its
    rank in a blue box is yours. Teams are known by their rank ("#2"), as the board writes it before the
    badge; a row whose rank wasn't read takes the one after the row above."""
    W = size[0]
    teams, cutoff, rank = [], 0, 0
    for row in _clean_rows(rows):
        toks = row.tokens
        if any_phrase(toks, ["KNOCKOUT ZONE", "KNOCKOUT"]) is not None:
            cutoff = rank
            continue
        if any_phrase(toks, ["CASHOUT", "VAULT"]) is not None:
            continue                                   # a cashout station's marker
        first = re.fullmatch(r"(\d{1,2})\D*", toks[0].replace("I", "1").replace("L", "1"))
        cash_at = next((i for i, w in enumerate(row.words) if board_cash(w.text) is not None
                        and w.x <= 0.13 * W), None)
        if cash_at is None:
            continue
        n = int(first.group(1)) if first and cash_at > 0 and 1 <= int(first.group(1)) <= 12 else 0
        rank = n if n > rank else rank + 1
        w = row.words[cash_at]
        if cash_at > 0 and clean(row.words[cash_at - 1].text).endswith("+") or _yellow(ctx.get("frame"), w):
            continue                                   # a pending amount (yellow): its banked one wasn't read
        v = board_cash(w.text)
        if 0 < v < 100:
            continue                                   # cut short ("$7,"): read it again next time
        teams.append((f"#{rank}", v, w))
    if len(teams) < 2:
        return []
    vals = tuple((k, v) for k, v, _w in teams)
    blue = [_blue(ctx.get("frame"), w) for _k, _v, w in teams]
    best = max(range(len(blue)), key=lambda i: blue[i])
    own = vals[best][0] if blue[best] >= 0.45 and sum(1 for b in blue if b >= 0.25) == 1 else ""
    return [Observation(SCORES, own, count=cutoff, values=vals,
                        text=" ".join(f"{k}={v}" for k, v in vals) + (f" (yours {own})" if own else ""))]


# ------------------------------------------------------------------------------------------------ the rest

def read_self(rows: list[Row], ctx: dict) -> list[Observation]:
    """Your name, under your health ("250/250")."""
    rows = _clean_rows(rows)
    for i, row in enumerate(rows):
        m = HEALTH.match("".join(w.text for w in row.words).replace(" ", "").upper())
        if m is None or int(m.group(2)) < 100 or i + 1 >= len(rows):
            continue
        nxt = rows[i + 1]
        if nxt.y - (row.y + row.h) > 3 * row.h:
            continue
        name = best_name(nxt.text)
        if len(clean(name)) >= 3 and not clean(name).isdigit():
            return [Observation(SELF_NAME, name, text=nxt.text)]
    return []


def read_top(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    """The round's clock, which is only up in a match."""
    W = size[0]
    for row in rows:
        for w in row.words:
            if TIMER.match(w.text.strip().replace("O", "0")) and 0.38 * W <= w.x + w.w / 2 <= 0.62 * W:
                return [Observation(IN_MATCH, text="clock " + w.text)]
    return []


def read_menu(zones: dict[str, list[Row]], ctx: dict) -> list[Observation]:
    seen = set()
    for name in ("timer", "feed", "board", "center", "banner"):
        for row in zones.get(name, []):
            for ph in words(ctx, "menu"):
                if find_phrase(row.tokens, ph) is not None:
                    seen.add(ph)
    return [Observation(MENU, text=", ".join(sorted(seen)))] if len(seen) >= 3 else []


def read(zones: dict[str, list[Row]], size: tuple[int, int], ctx: dict) -> list[Observation]:
    """Everything worth knowing on one frame of the game."""
    names = [n for n in (ctx.get("names") or []) if str(n).strip()]
    out = read_feed(zones.get("feed", []), names, ctx)
    out += read_center(zones.get("center", []), size, ctx)
    out += read_banner(zones.get("banner", []), size, ctx)
    out += read_bottom(zones.get("bottom", []), ctx)
    out += read_card(zones.get("card", []), ctx)
    out += read_board(zones.get("board", []), size, ctx)
    out += read_self(zones.get("self", []), ctx)
    clock = read_top(zones.get("timer", []), size, ctx)
    out += clock or read_menu(zones, ctx)
    if not clock and any(o.kind in (FEED_KILL, FEED_DEATH, FEED_ASSIST, TALLY_KILL, TALLY_ASSIST, HEAD_KILL,
                                    SCORES) or (o.kind == DEAD and not o.values) for o in out):
        out.append(Observation(IN_MATCH, text="combat"))
    return out


PROFILE = Profile(
    key="the_finals", name="THE FINALS", exe=("discovery.exe",), titles=("the finals",), zones=ZONES,
    modes=MODES, read=read, elimination_cash=(500, 1000), words=WORDS,
    notes="Set THE FINALS to Borderless or Windowed (Settings → Video → Window mode): in exclusive Fullscreen "
          "Windows doesn't let other apps see the game.")
