"""Active Game Tracking (experimental): watches the game you're playing, the way you see it, and keeps your
stats: eliminations, deaths, assists, revives, how far ahead or behind your team is, and how each match ended.

  * Screen only. It reads the game's HUD off the screen with OCR (ocr.py, capture.py), never its memory or
    files, so anti-cheat has nothing to object to. Nothing leaves this computer.
  * One game for now: THE FINALS (finals.py). A game is a profile (profile.py): where its HUD puts things and
    the words it uses. The tracker (engine.py) and everything around it is the same for every game.
  * The stats show on the notch (pick which), a summary at the end of each match, and every match is kept in
    a local database (store.py) for the history on the Game tracking page.
  * Everything can be done by hand too: start or end a match, say who won, add or take away an elimination.
"""
from __future__ import annotations

from . import finals
from .profile import Profile

GAMES: dict[str, Profile] = {finals.PROFILE.key: finals.PROFILE}

DEFAULTS = {
    "enabled": False,             # watching the screen while the game is in front
    "game": "the_finals",
    "names": [],                  # your in-game name(s), to read the elimination feed by
    "learn_name": True,           # work your name out from the feed and the tally
    "learned_name": "",
    "auto_matches": True,         # start and end matches from what's on screen
    "rate": 3,                    # looks a second
    "notch_stats": ["kills", "deaths", "assists", "lead"],
    "show_over_game": True,       # the notch stays up over the game (borderless) while a match is on
    "flash": True,                # each elimination, death… slides in on the notch
    "zones": {},                  # zone → [left, top, right, bottom] (0–1), to move where it reads
    "words": {},                  # more phrases per kind ("dead": ["ELIMINADO POR"]) for other languages
}

# What can be shown: key → (name in Settings, short label on the notch).
STAT_CHOICES = {
    "kills": ("Eliminations", "K"),
    "deaths": ("Deaths", "D"),
    "assists": ("Assists", "A"),
    "revives": ("Revives", "R"),
    "kd": ("K/D ratio", "K/D"),
    "wipes": ("Team wipes", "TW"),
    "revived": ("Times revived", "Rv"),
    "score": ("Team cash", ""),
    "lead": ("Lead or behind by", ""),
    "to_lead": ("Needed to take the lead", ""),
    "to_qualify": ("Needed to qualify", ""),
    "rank": ("Team place", ""),
    "time": ("Match time", ""),
}
MAX_NOTCH_STATS = 4
RESULT_WORDS = {"won": "Victory", "lost": "Defeat", "draw": "Draw", "left": "Match over", "ended": "Match over"}


def settings(cfg: dict | None) -> dict:
    s = {**DEFAULTS, **((cfg or {}).get("gametrack") or {})}
    if isinstance(s.get("names"), str):
        s["names"] = [n.strip() for n in s["names"].split(",") if n.strip()]
    s["notch_stats"] = [k for k in (s.get("notch_stats") or []) if k in STAT_CHOICES][:MAX_NOTCH_STATS]
    return s


def profile(cfg: dict | None) -> Profile:
    return GAMES.get(settings(cfg)["game"]) or finals.PROFILE


def names_for(cfg: dict | None) -> list[str]:
    """The names the feed is read for: the ones you gave, else the one it learned."""
    s = settings(cfg)
    names = [str(n) for n in s["names"] if str(n).strip()]
    if not names and s["learn_name"] and s["learned_name"]:
        names = [s["learned_name"]]
    return names


def money(v: int, signed: bool = False) -> str:
    """$12,500 → "$12.5k"; signed: "+$2.5k" / "−$1k"."""
    sign = ("+" if v > 0 else "−" if v < 0 else "±") if signed else ("−" if v < 0 else "")
    a = abs(int(v))
    if a >= 1000:
        k = a / 1000
        body = f"{k:.0f}k" if k >= 100 or k == int(k) else f"{k:.1f}k"
    else:
        body = str(a)
    return f"{sign}${body}"


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def stat_value(key: str, stats: dict, standing: dict, seconds: float) -> str | None:
    """A stat as the notch writes it ("5", "1.67", "+$2.5k"), or None when it isn't known (yet)."""
    from .engine import ordinal
    if key in ("kills", "deaths", "assists", "revives", "wipes", "revived"):
        return str(int(stats.get(key, 0)))
    if key == "kd":
        return f"{stats.get('kills', 0) / max(1, stats.get('deaths', 0)):.2f}"
    if key == "time":
        return mmss(seconds)
    if not standing:
        return None
    if key == "score":
        return money(standing["ours"])
    if key == "lead":
        return money(standing["lead"], signed=True)
    if key == "to_lead":
        return "Leading" if standing["lead"] > 0 else money(standing["to_lead"])
    if key == "to_qualify":
        return None if "to_qualify" not in standing else ("Through" if standing["to_qualify"] == 0
                                                          else money(standing["to_qualify"]))
    if key == "rank":
        return f"{ordinal(standing['rank'])} of {standing['teams']}"
    return None


def notch_line(keys: list[str], stats: dict, standing: dict, seconds: float) -> str:
    """The resting notch's side: "K 5  D 2  A 1  +$2.5k"."""
    parts = []
    for k in keys:
        v = stat_value(k, stats, standing, seconds)
        if v is None:
            continue
        short = STAT_CHOICES[k][1]
        parts.append(f"{short} {v}" if short else v)
    return "  ".join(parts)
