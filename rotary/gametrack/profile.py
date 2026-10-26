"""What a game profile is: where on the screen to read, and how to turn what was read into observations
("someone's name is on the left of a killfeed line", "the death screen is up"). The tracker (engine.py) is the
same for every game; adding a game means writing one of these (see finals.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, NamedTuple

from .text import Row

# Kinds of observation a profile can report. `other` is the other player's name (or the mode, the result…) and
# `count` a merged entry's count ("ELIMINATION ×2") or a placement.
FEED_KILL, FEED_DEATH, FEED_ASSIST, FEED_REVIVE = "feed_kill", "feed_death", "feed_assist", "feed_revive"
TALLY_KILL, TALLY_ASSIST, TALLY_REVIVE, TALLY_WIPE = "tally_kill", "tally_assist", "tally_revive", "tally_wipe"
DEAD, REVIVED_BY = "dead", "revived_by"
MODE, ROUND, IN_MATCH, MENU, RESULT, SCORES = "mode", "round", "in_match", "menu", "result", "scores"
FEED_LINE = "feed_line"         # a killfeed line that isn't about you: (killer, victim) — used to learn your name
HEAD_KILL, HEAD_ASSIST = "head_kill", "head_assist"     # the headline under the crosshair: "ELIMINATED <name>"
SELF_NAME = "self_name"         # your own name, where the HUD shows it (under your health)


class Zone(NamedTuple):
    """A part of the frame to read: left, top, right, bottom (0–1 of the frame), and how. `angle` levels text
    the HUD draws tilted (degrees, counter-clockwise), `scale` enlarges (or shrinks: big banners) it on top of
    what the frame's size calls for, `block` says it's a few big lines rather than scattered words, `every`
    reads it only every so many frames (things that change slowly) and `slant` stands italic letters up (how
    far the top of a letter leans right, per its height): OCR reads upright letters much better."""
    left: float
    top: float
    right: float
    bottom: float
    angle: float = 0.0
    scale: float = 1.0
    block: bool = False
    every: int = 1
    slant: float = 0.0

    @classmethod
    def of(cls, value) -> "Zone | None":
        """A zone from the profile or from config.json ([left, top, right, bottom, …]), or None if it's wrong."""
        try:
            v = list(value)
            if not 4 <= len(v) <= 9:
                return None
            z = cls(*(float(x) for x in v[:6]))
            if len(v) > 6:
                z = z._replace(block=bool(v[6]))
            if len(v) > 7:
                z = z._replace(every=max(1, int(v[7])))
            if len(v) > 8:
                z = z._replace(slant=float(v[8]))
        except (TypeError, ValueError):
            return None
        if not (0 <= z.left < z.right <= 1 and 0 <= z.top < z.bottom <= 1) or not 0.1 <= z.scale <= 4:
            return None
        return z


@dataclass(frozen=True)
class Observation:
    kind: str
    other: str = ""
    count: int = 1
    values: tuple = ()            # SCORES: ((team key, amount), …), `other` your team's key if it was seen and
                                  # `count` how many teams go through (0: not shown); FEED_LINE: (killer, victim)
    text: str = ""                # what was read, for the log

    @property
    def key(self) -> tuple:
        """What makes it the same thing seen again in the next frame."""
        return self.kind, self.other.upper()


@dataclass(frozen=True)
class Mode:
    key: str
    name: str                     # as the notch says it
    words: tuple[str, ...]        # how the game writes it on screen
    teams: int = 0                # teams in a round (0: unknown)
    team_size: int = 0
    knockout: bool = False        # a tournament: the top teams go through each round
    qualify: int = 0              # how many teams go through
    target: int = 0               # cash that wins outright (0: none)
    scored: bool = True           # its HUD shows team scores to compare


@dataclass
class Profile:
    key: str
    name: str
    exe: tuple[str, ...]                       # its program's file name(s), lower case
    titles: tuple[str, ...]                    # words in its window title, lower case
    zones: dict[str, Zone]                     # where to read, by name
    modes: dict[str, Mode]
    read: Callable[[dict[str, list[Row]], tuple[int, int], dict], list[Observation]]
    elimination_cash: tuple[int, ...] = ()     # what an elimination adds to the team's score: finds your team
    notes: str = ""
    words: dict = field(default_factory=dict)  # every phrase the reader looks for (can be added to from config)
