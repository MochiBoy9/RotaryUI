"""THE FINALS (Embark Studios): where its HUD puts things, and the words it uses.

Read only from the screen, like a viewer of a stream would: nothing touches the game's memory or files, so
Easy Anti-Cheat has nothing to object to. The capture works with the game in Borderless or Windowed mode; in
exclusive Fullscreen Windows hands other apps a black screen.

What's on the screen (Season 12, "PRIME TIME", October 2026: the HUD was rebuilt, can be scaled and has an
ultrawide setting, so the zones below are generous and every phrase is matched loosely):

  * The elimination feed, top right: "KILLER  [weapon]  VICTIM", one line per elimination, for a few seconds.
    With your name set (or learned), a line with you on the left is your elimination, on the right your death,
    and between the two (after a "+") an assist.
  * The crosshair scoring tally, in the middle: ELIMINATION (with the name, "×2" when entries merge), ASSIST,
    REVIVE, TEAM WIPE, each with the points. Seen even without a name.
  * The death screen: "ELIMINATED BY …", the respawn countdown, "waiting for revive" / respawn coin, spectating.
    Dead players turn into a revive statue a teammate can bring back ("REVIVED BY …").
  * The scoreboard across the top: each team's cash ($) and the round timer; in Team Deathmatch the two teams'
    scores. Your team is found from your own eliminations (its cash goes up by $500, $1,000 in a Death Match).
  * End of a round: WINNER / QUALIFIED (knockout rounds: the top 2 of 4 teams go through), ELIMINATED, or a
    placement; back in the menus (PLAY, LOADOUT, CAREER…) the match is over.

Modes (Embark's help centre and the patch notes, Season 12): Quick Cash (3 teams of 3, cash out to win), Cashout
(the tournament: 8 teams of 3, two knockout rounds of 4 teams with the top 2 going through, then a final of 2
teams; "World Tour" before Season 9) and Ranked Cashout, Solo Showdown (12 players, the 4 with the least cash
out after each of 3 rounds), Team Deathmatch and Power Shift (5 v 5), Point Break (8 v 8, attack and defend),
and the older Bank It and Terminal Attack, which come back for events.
"""
from __future__ import annotations

import re

from .profile import (DEAD, FEED_ASSIST, FEED_DEATH, FEED_KILL, FEED_LINE, FEED_REVIVE, IN_MATCH, MENU, MODE,
                      RESULT, REVIVED_BY, ROUND, SCORES, TALLY_ASSIST, TALLY_KILL, TALLY_REVIVE, TALLY_WIPE,
                      Mode, Observation, Profile)
from .text import Row, best_name, cash_values, clean, count_mark, find_phrase, fold, name_in

ZONES = {
    "feed": (0.60, 0.0, 1.0, 0.42),       # elimination feed, top right
    "top": (0.0, 0.0, 1.0, 0.17),         # scoreboard, timer, round and mode banners
    "center": (0.22, 0.22, 0.78, 0.90),   # scoring tally, death screen, end of round
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
    # the death screen, and anything else that only shows while you're out
    "dead": ["ELIMINATED BY", "KILLED BY", "YOU WERE ELIMINATED", "YOU HAVE BEEN ELIMINATED", "RESPAWN IN",
             "RESPAWNING IN", "RESPAWNING", "RESPAWN COIN", "USE RESPAWN COIN", "WAITING FOR REVIVE",
             "BEING REVIVED", "SPECTATING", "YOUR TEAM WAS WIPED", "YOUR TEAM WIPED"],
    "revived_by": ["REVIVED BY"],
    # the crosshair tally
    "kill": ["ELIMINATION", "ELIMINATED", "KILL", "TAKEDOWN"],
    "not_kill": ["TEAM", "TEAMMATE", "YOUR", "BY", "ALLY"],      # someone else's news, not your elimination
    "assist": ["ASSIST", "ELIMINATION ASSIST", "KILL ASSIST"],
    "revive": ["REVIVE", "REVIVED", "TEAMMATE REVIVED"],
    "not_revive": ["HOLD", "TO REVIVE", "REVIVING", "WAITING", "BEING", "PRESS"],
    "wipe": ["TEAM WIPE", "TEAM WIPED", "SQUAD WIPE"],
    # end of a round or the match (big letters only)
    "lost": ["DEFEAT", "YOU LOST", "ELIMINATED", "TEAM ELIMINATED", "KNOCKED OUT", "DISQUALIFIED",
             "NOT QUALIFIED", "ELIMINATED FROM THE TOURNAMENT"],
    "won": ["VICTORY", "YOU WON", "YOU WIN", "YOUR TEAM WON", "CHAMPIONS", "TOURNAMENT WON"],
    "winner": ["WINNER", "WINNERS"],
    "qualified": ["QUALIFIED", "YOU QUALIFIED"],
    "draw": ["DRAW"],
    # the menus between matches
    "menu": ["PLAY", "LOADOUT", "CAREER", "STORE", "SHOP", "CONTRACTS", "CUSTOMIZE", "EVENTS", "SOCIAL",
             "BATTLE PASS", "SEARCHING FOR MATCH", "FINDING MATCH", "MATCHMAKING", "READY UP"],
    "round": ["ROUND", "FINAL ROUND"],
}

BIG = 0.035             # banner letters at least this tall (part of the frame's height); the tally's are ~2 %
TIMER = re.compile(r"^\d{1,2}[:.]\d{2}$")


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


def _rest(row: Row, skip: tuple[int, int]) -> str:
    """The words of a row outside `skip`, minus points ("+100"), counts ("×2") and cash: usually a name."""
    toks = row.words
    keep = []
    for i, w in enumerate(toks):
        if skip[0] <= i < skip[1]:
            continue
        c = clean(w.text)
        if not c or c.startswith("+") or c.startswith("$") or re.fullmatch(r"[X×]?\d+[X×]?|\d+", c):
            continue
        keep.append(w.text.strip(" +:·-"))
    return " ".join(k for k in keep if k)


def _mine(text: str, names: list[str]) -> bool:
    return any(name_in(text, n) for n in names)


def read_feed(rows: list[Row], names: list[str], ctx: dict) -> list[Observation]:
    out = []
    for row in rows:
        toks = row.tokens
        if not toks or any(cash_values(w.text) or TIMER.match(w.text.strip()) for w in row.words):
            continue                                  # the scoreboard poking into the corner
        segs = [s for s in row.segment_texts() if clean(s)]
        revived = any_phrase(toks, ["REVIVED"])
        if names and _mine(row.text, names):
            if revived is not None:
                before = " ".join(w.text for w in row.words[:revived[1][0]])
                if _mine(before, names):
                    out.append(Observation(FEED_REVIVE, _rest(row, (0, revived[1][1])), text=row.text))
                continue
            if len(segs) >= 2:
                killer, victim, middle = segs[0], segs[-1], segs[1:-1]
                if _mine(killer, names) and not _mine(victim, names):
                    out.append(Observation(FEED_KILL, best_name(victim), text=row.text))
                elif _mine(victim, names) and not _mine(killer, names):
                    out.append(Observation(FEED_DEATH, best_name(killer), text=row.text))
                elif any(_mine(m, names) for m in middle):
                    out.append(Observation(FEED_ASSIST, best_name(victim), text=row.text))
                elif _mine(victim, names):
                    out.append(Observation(FEED_DEATH, "", text=row.text))       # took themselves out
                continue
            # one run of words (the gap wasn't seen): which end is the name at?
            ws = row.words
            if _mine(ws[0].text, names) and len(ws) > 1:
                out.append(Observation(FEED_KILL, best_name(" ".join(w.text for w in ws[1:])), text=row.text))
            elif _mine(ws[-1].text, names) and len(ws) > 1:
                out.append(Observation(FEED_DEATH, best_name(" ".join(w.text for w in ws[:-1])), text=row.text))
            elif len(ws) == 1:
                out.append(Observation(FEED_DEATH, "", text=row.text))
            continue
        if len(segs) >= 2 and revived is None:
            out.append(Observation(FEED_LINE, segs[-1].strip(), values=(segs[0].strip(), segs[-1].strip()),
                                   text=row.text))
    return out


def read_center(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    out = []
    H = size[1]
    for row in rows:
        toks = row.tokens
        if not toks:
            continue
        big = row.h >= BIG * H
        hit = any_phrase(toks, words(ctx, "dead"))
        if hit is not None:
            by = find_phrase(toks, "BY")
            killer = _rest(row, (0, by[1])) if by is not None and hit[0].endswith("BY") else ""
            out.append(Observation(DEAD, killer, text=row.text))
            continue
        hit = any_phrase(toks, words(ctx, "revived_by"))
        if hit is not None:
            out.append(Observation(REVIVED_BY, _rest(row, (0, hit[1][1])), text=row.text))
            continue
        if big:
            res = result_of(row, ctx)
            if res is not None:
                out.append(res)
                continue
            md = mode_of(row)
            if md is not None:
                out.append(md)
                continue
            rd = round_of(row)
            if rd is not None:
                out.append(rd)
                continue
        if any_phrase(toks, ["YOUR TEAM"]) is None:
            hit = any_phrase(toks, words(ctx, "wipe"))
            if hit is not None:
                out.append(Observation(TALLY_WIPE, count=count_mark(toks), text=row.text))
                continue
        if big:
            continue                                   # a banner, not the tally
        hit = any_phrase(toks, words(ctx, "assist"))
        if hit is not None:
            out.append(Observation(TALLY_ASSIST, _rest(row, hit[1]), count=count_mark(toks), text=row.text))
            continue
        hit = any_phrase(toks, words(ctx, "kill"))
        if hit is not None and any_phrase(toks, words(ctx, "not_kill")) is None:
            out.append(Observation(TALLY_KILL, _rest(row, hit[1]), count=count_mark(toks), text=row.text))
            continue
        hit = any_phrase(toks, words(ctx, "revive"))
        if hit is not None and any_phrase(toks, words(ctx, "not_revive")) is None:
            out.append(Observation(TALLY_REVIVE, _rest(row, hit[1]), count=count_mark(toks), text=row.text))
    return out


def result_of(row: Row, ctx: dict) -> Observation | None:
    toks = row.tokens
    for t in toks:
        m = re.fullmatch(r"([1-9]|1[0-2])(ST|ND|RD|TH)", t)
        if m and len(toks) <= 3:
            return Observation(RESULT, "place", count=int(m.group(1)), text=row.text)
    if len(toks) > 5:
        return None                                    # a sentence, not a banner
    for kind in ("lost", "won", "qualified", "winner", "draw"):
        hit = any_phrase(toks, words(ctx, kind))
        if hit is not None:
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
        return Observation(ROUND, toks[at[1]].replace("I", "1"), count=int(toks[at[1]].replace("I", "1")),
                           text=row.text)
    return None


def read_top(rows: list[Row], size: tuple[int, int], ctx: dict) -> list[Observation]:
    out = []
    W, H = size
    scores = []
    timer = False
    menu = set()
    for row in rows:
        toks = row.tokens
        for w in row.words:
            if TIMER.match(w.text.strip().replace("O", "0")) and 0.3 * W <= w.x + w.w / 2 <= 0.7 * W:
                timer = True
        for ph in words(ctx, "menu"):
            if find_phrase(toks, ph) is not None:
                menu.add(ph)
        md = mode_of(row)
        if md is not None:
            out.append(md)
        rd = round_of(row)
        if rd is not None:
            out.append(rd)
        if row.h >= BIG * H:
            res = result_of(row, ctx)
            if res is not None:
                out.append(res)
        # cash on this row: each amount with the words just before it (a team's name, if it has one)
        for seg in row.segments:
            label = []
            for w in seg:
                vals = cash_values(w.text)
                if not vals:
                    label.append(w.text)
                    continue
                for v in vals:
                    if 0 <= v < 1_000_000:
                        scores.append((w.x, w.y, v, fold(" ".join(label))))
                label = []
    if len(scores) >= 2:
        scores.sort(key=lambda s: (round(s[1] / max(1.0, 0.03 * H)), s[0]))   # rows of the board, left to right
        vals = []
        for i, (_x, _y, v, label) in enumerate(scores):
            key = label if len(label) >= 3 and not label.isdigit() else f"#{i + 1}"
            vals.append((key, v))
        out.append(Observation(SCORES, values=tuple(vals), text=" ".join(f"{k}={v}" for k, v in vals)))
    if len(menu) >= 3 and not timer:
        out.append(Observation(MENU, text=", ".join(sorted(menu))))
    elif timer and (len(scores) >= 2 or out):
        out.append(Observation(IN_MATCH, text="timer" + (" + scores" if scores else "")))
    return out


def read(zones: dict[str, list[Row]], size: tuple[int, int], ctx: dict) -> list[Observation]:
    """Everything worth knowing on one frame of the game."""
    names = [n for n in (ctx.get("names") or []) if str(n).strip()]
    out = read_feed(zones.get("feed", []), names, ctx)
    out += read_center(zones.get("center", []), size, ctx)
    out += read_top(zones.get("top", []), size, ctx)
    if any(o.kind in (FEED_KILL, FEED_DEATH, FEED_ASSIST, TALLY_KILL, TALLY_ASSIST, DEAD) for o in out) and not any(
            o.kind == IN_MATCH for o in out):
        out.append(Observation(IN_MATCH, text="combat"))
    return out


PROFILE = Profile(
    key="the_finals", name="THE FINALS", exe=("discovery.exe",), titles=("the finals",), zones=ZONES,
    modes=MODES, read=read, elimination_cash=(500, 1000), words=WORDS,
    notes="Set THE FINALS to Borderless or Windowed (Settings → Video → Window mode): in exclusive Fullscreen "
          "Windows doesn't let other apps see the game.")
