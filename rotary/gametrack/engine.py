"""The tracker: observations from each frame (profile.py) in, a match's stats out.

OCR on a moving game is noisy, so nothing counts the moment it's read:

  * Seen twice. A thing counts once it has been read in two frames (at three frames a second the tally and
    the elimination feed stay up for several), and counts once for as long as it stays on screen: a killfeed
    line that's there for five seconds is one elimination, not fifteen.
  * One event, many places. Your elimination shows in the feed and in the tally; your death in the feed and
    on the death screen. Sightings of the same thing a few seconds apart (and with the same name, when both
    have one) are merged into one.
  * Death is a state. Once the death screen is up you're dead until it has been gone a while (or a teammate
    revives you), so the countdown ticking away doesn't add deaths.
  * Matches start when the in-match HUD has been up for a few seconds (or the first fight), and end on the
    end-of-round banners, or when the game is back in its menus.

Everything here is plain Python driven by the caller's clock, so it can be tested frame by frame.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from .profile import (DEAD, FEED_ASSIST, FEED_DEATH, FEED_KILL, FEED_LINE, FEED_REVIVE, IN_MATCH, MENU, MODE,
                      RESULT, REVIVED_BY, ROUND, SCORES, TALLY_ASSIST, TALLY_KILL, TALLY_REVIVE, TALLY_WIPE,
                      Observation, Profile)
from .text import best_name, fold, name_key, same_player, similar

STATS = ("kills", "deaths", "assists", "revives", "revived", "wipes")
NEED = {MENU: 3, IN_MATCH: 3, SCORES: 1}       # frames before a thing counts (2 for everything else)
FORGET = 2.5         # s unseen: it's gone (seen again later, it's new)
MERGE = 5.0          # s: two sightings of one elimination / assist / revive
DEATH_MERGE = 10.0   # s: the feed line and the death screen of one death
ALIVE_AFTER = 6.0    # s without the death screen: back in the game
START_AFTER = 2.0    # s of the in-match HUD before a match starts by itself
MENU_END = 6.0       # s in the menus and the match is over
COOLDOWN = 20.0      # s after a match ends before the next can start by itself (end screens linger)
IDLE_END = 300.0     # s with no sign of a match and it's over
NAME_PAIR = 4.0      # s: a tally elimination and a feed line about the same player
RESULTS = ("won", "lost", "draw", "place", "left", "ended")


@dataclass
class Event:
    kind: str                 # stat | match_start | match_end | round | mode | dead | alive | name | team
    stat: str = ""
    text: str = ""
    value: int = 0


@dataclass
class _Seen:
    first: float
    last: float
    frames: int = 0
    counted: int = 0          # how much of it has been counted (a merged tally entry grows: ×2, ×3)
    fired: bool = False
    obs: Observation | None = None


@dataclass
class Match:
    game: str
    started: float                       # wall clock
    t0: float                            # tracker clock
    mode: str = ""
    manual: bool = False
    round: int = 1
    final: bool = False
    stats: dict = field(default_factory=lambda: dict.fromkeys(STATS, 0))
    scores: list = field(default_factory=list)       # [(team, amount)] as last read
    team: str = ""                                   # which of those is yours
    team_sure: bool = False
    votes: Counter = field(default_factory=Counter)
    result: str = ""                                 # won | lost | draw | place | left | ended
    place: int = 0
    estimated: bool = False                          # the result is a best guess: check it
    ended: float = 0.0                               # wall clock
    t_end: float = 0.0
    qualified: int = 0                               # knockout rounds got through
    pending: str = ""                                # "qualified": seen go through, waiting for the next round
    events: list = field(default_factory=list)       # (seconds in, kind, detail, source)
    id: int = 0                                      # its row in the database

    def seconds(self, now: float) -> float:
        return max(0.0, (self.t_end or now) - self.t0)

    def kd(self) -> float:
        return self.stats["kills"] / max(1, self.stats["deaths"])

    def standing(self, profile: Profile) -> dict:
        """Your team's score against the others: ours, best, lead (− behind), to_lead, to_qualify, to_win."""
        if not self.team or not self.scores:
            return {}
        vals = dict(self.scores)
        if self.team not in vals:
            return {}
        ours = vals[self.team]
        others = sorted((v for k, v in self.scores if k != self.team), reverse=True)
        if not others:
            return {}
        out = {"ours": ours, "best": others[0], "lead": ours - others[0], "to_lead": max(0, others[0] - ours + 1),
               "rank": 1 + sum(1 for v in others if v > ours), "teams": len(others) + 1, "sure": self.team_sure}
        mode = profile.modes.get(self.mode)
        if mode is not None and mode.knockout and mode.qualify and not self.final:
            q = mode.qualify
            out["to_qualify"] = max(0, others[q - 1] - ours + 1) if len(others) >= q else 0
        if mode is not None and mode.target:
            out["to_win"] = max(0, mode.target - ours)
        return out

    def place_text(self, profile: Profile) -> str:
        if self.result == "place" and self.place:
            return ordinal(self.place)
        mode = profile.modes.get(self.mode)
        if self.result == "won":
            return "1st"
        if self.result == "lost" and mode is not None and mode.knockout and mode.key != "solo_showdown":
            # the Cashout bracket: out in the first round 5th–8th, the second 3rd–4th, the final 2nd
            return "2nd" if self.final else ("3rd–4th" if self.round >= 2 else "5th–8th")
        return ""


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


class Tracker:
    def __init__(self, profile: Profile, auto_start: bool = True, clock=time.time):
        self.profile = profile
        self.auto_start = auto_start
        self.clock = clock                       # wall clock, for the database
        self.match: Match | None = None
        self.last: Match | None = None           # the match that just ended (the summary)
        self.dead = False
        self._dead_since = 0.0
        self._dead_seen = 0.0
        self._seen: dict[tuple, _Seen] = {}
        self._recent: dict[str, list] = {}       # stat → [[t, other, sources]] for merging sightings
        self._mode_hint = ""
        self._last_sign = 0.0                    # last sign of a match going on
        self._ended_at = -1e9
        self._pending_scores: dict[str, tuple[int, int]] = {}
        self._before_kill: dict | None = None    # the scores just before your last elimination
        self._kill_t = -1e9
        self._tally_victims: list = []           # (t, victim) from the tally, to learn your name from the feed
        self._feed_lines: list = []              # (t, killer, victim)
        self._death_killers: list = []           # (t, killer)
        self.name_votes: Counter = Counter()
        self.learned_name = ""

    # ------------------------------------------------------------------ manual control
    def start_match(self, now: float, mode: str = "", manual: bool = True) -> list[Event]:
        out = []
        if self.match is not None:
            out += self.end_match(now, "left")
        self.match = Match(self.profile.key, self.clock(), now, mode=mode or self._mode_hint, manual=manual)
        self._mode_hint = ""
        self.dead = False
        self._recent.clear()
        self._pending_scores.clear()
        self._last_sign = now
        out.append(Event("match_start", text=self.mode_name(self.match.mode)))
        return out

    def end_match(self, now: float, result: str = "ended", place: int = 0, estimated: bool = False,
                  manual: bool = False) -> list[Event]:
        m = self.match
        if m is None:
            return []
        if result == "place" and place == 1:
            result = "won"
        m.result, m.place, m.estimated = result, place, estimated
        m.ended, m.t_end = self.clock(), now
        m.events.append((round(m.seconds(now), 1), "end", result + (f" {place}" if place else ""),
                         "you" if manual else "screen"))
        self.last, self.match = m, None
        self.dead = False
        self._ended_at = now
        self._seen.clear()
        return [Event("match_end", text=result, value=place)]

    def fix_result(self, result: str, place: int = 0) -> None:
        """The summary's result was a guess and the user put it right."""
        m = self.last
        if m is not None:
            m.result, m.place, m.estimated = ("won" if result == "place" and place == 1 else result), place, False

    def adjust(self, stat: str, delta: int, now: float) -> list[Event]:
        m = self.match or self.last
        if m is None or stat not in STATS:
            return []
        m.stats[stat] = max(0, m.stats[stat] + delta)
        m.events.append((round(m.seconds(now), 1), stat, f"{delta:+d}", "you"))
        return [Event("stat", stat, "Changed by hand", delta)]

    def set_mode(self, mode: str) -> None:
        if self.match is not None:
            self.match.mode = mode

    def mode_name(self, key: str) -> str:
        m = self.profile.modes.get(key)
        return m.name if m is not None else ""

    # ------------------------------------------------------------------ frames
    def feed(self, now: float, observations: list[Observation]) -> list[Event]:
        """One frame's observations."""
        out: list[Event] = []
        frame = {}
        for o in observations:
            frame.setdefault(o.key, o)               # once per frame
        for key, o in frame.items():
            if o.kind == DEAD:
                self._dead_seen = now
            if o.kind == SCORES:
                out += self._scores(now, o)
                continue
            if o.kind == FEED_LINE:
                self._feed_lines.append((now, o.values[0], o.values[1]))
                out += self._learn(now)
                continue
            s = self._seen.get(key)
            if s is None:
                s = self._seen[key] = _Seen(now, now)
                s.counted = self._chain(o, now)
            s.last, s.frames, s.obs = now, s.frames + 1, o
            if s.frames < NEED.get(o.kind, 2):
                continue
            out += self._confirmed(now, o, s)
        out += self.tick(now)
        return out

    def tick(self, now: float) -> list[Event]:
        """Time passing: things that went away, coming back to life, matches that petered out."""
        out = []
        for key in [k for k, s in self._seen.items() if now - s.last > FORGET]:
            del self._seen[key]
        for stat in self._recent:
            self._recent[stat] = [r for r in self._recent[stat] if now - r[0] <= max(MERGE, DEATH_MERGE)]
        if self.dead and now - self._dead_seen > ALIVE_AFTER:
            self.dead = False
            out.append(Event("alive"))
        if self.match is not None and not self.match.manual and now - self._last_sign > IDLE_END:
            out += self.end_match(now, "left")
        return out

    def _chain(self, o: Observation, now: float) -> int:
        """A merged tally entry with a new name ("ELIMINATION ×2"): what the entry before it counted already."""
        if o.count <= 1 or not o.kind.startswith("tally_"):
            return 0
        prev = [s.counted for (k, _), s in self._seen.items() if k == o.kind and now - s.last <= FORGET]
        return max(prev, default=0)

    def _confirmed(self, now: float, o: Observation, s: _Seen) -> list[Event]:
        k = o.kind
        out: list[Event] = []
        if k in (IN_MATCH, MODE, ROUND, RESULT) or k.startswith(("feed_", "tally_")) or k == DEAD:
            self._last_sign = now if k != RESULT else self._last_sign
        if k == MENU:
            m = self.match
            if m is not None and not m.manual and now - s.first >= MENU_END and not s.fired:
                s.fired = True
                # Back in the menus straight after a QUALIFIED sequence: it was the other teams going through.
                out += self.end_match(now, "lost", estimated=True) if m.pending == "qualified" else \
                    self.end_match(now, "left")
            return out
        if k == MODE:
            if not s.fired:
                s.fired = True
                m = self.match
                if m is not None and (not m.mode or now - m.t0 < 90):
                    if m.mode != o.other:
                        m.mode = o.other
                        out.append(Event("mode", text=self.mode_name(o.other)))
                else:
                    self._mode_hint = o.other
            return out
        if k == IN_MATCH:
            if self.match is None and self._can_start(now) and now - s.first >= START_AFTER:
                out += self.start_match(now, manual=False)
            return out
        if k == RESULT:
            if not s.fired:
                s.fired = True
                out += self._result(now, o)
            return out
        if self.match is None:
            if not self._can_start(now) or k == REVIVED_BY:
                return out
            out += self.start_match(now, manual=False)          # a fight: there's a match on
        if k == ROUND:
            if not s.fired:
                s.fired = True
                m = self.match
                if o.other == "final":
                    m.final = True
                elif o.count > m.round:
                    m.round = o.count
                out.append(Event("round", text="Final round" if m.final else f"Round {m.round}", value=m.round))
            return out
        if k == DEAD:
            if not s.fired:
                s.fired = True
                if o.other:
                    self._death_killers.append((now, o.other))
                    out += self._learn(now)
                if not self.dead:
                    self.dead, self._dead_since = True, now
                    out += self._count("deaths", now, o.other, "screen", DEATH_MERGE)
                    out.append(Event("dead", text=o.other))
            return out
        if k == FEED_DEATH:
            if not s.fired:
                s.fired = True
                if self.dead and now - self._dead_since <= DEATH_MERGE:
                    self._merge_into("deaths", now, o.other, "feed")
                else:
                    out += self._count("deaths", now, o.other, "feed", DEATH_MERGE)
                    if not self.dead:
                        self.dead, self._dead_since, self._dead_seen = True, now, now
                        out.append(Event("dead", text=o.other))
            return out
        if k == REVIVED_BY:
            if not s.fired:
                s.fired = True
                self.match.stats["revived"] += 1
                self._log(now, "revived", o.other, "screen")
                if self.dead:
                    self.dead = False
                out.append(Event("stat", "revived", o.other, 1))
            return out
        stat = {FEED_KILL: "kills", TALLY_KILL: "kills", FEED_ASSIST: "assists", TALLY_ASSIST: "assists",
                FEED_REVIVE: "revives", TALLY_REVIVE: "revives", TALLY_WIPE: "wipes"}.get(k)
        if stat is None:
            return out
        source = "feed" if k.startswith("feed_") else "tally"
        self.match.pending = ""                              # playing on: a new round started
        new = max(1, o.count) - s.counted if source == "tally" else (0 if s.fired else 1)
        s.fired = True
        if new <= 0:
            return out
        s.counted += new
        if k == TALLY_KILL and o.other:
            self._tally_victims.append((now, o.other))
            out += self._learn(now)
        for i in range(new):
            out += self._count(stat, now, o.other if new == 1 else "", source, MERGE)
        return out

    def _can_start(self, now: float) -> bool:
        return self.auto_start and now - self._ended_at >= COOLDOWN

    # ------------------------------------------------------------------ counting
    def _count(self, stat: str, now: float, other: str, source: str, window: float) -> list[Event]:
        """One more of `stat`, unless it's another sighting of one just counted."""
        m = self.match
        if m is None:
            return []
        if self._merge_into(stat, now, other, source, window):
            return []
        self._recent.setdefault(stat, []).append([now, other, {source}])
        m.stats[stat] += 1
        self._log(now, stat, other, source)
        if stat == "kills":
            self._kill_t = now
            self._before_kill = dict(m.scores)
        return [Event("stat", stat, other, 1)]

    def _merge_into(self, stat: str, now: float, other: str, source: str, window: float = MERGE) -> bool:
        for r in reversed(self._recent.get(stat, [])):
            if now - r[0] > window or source in r[2]:
                continue
            if not same_player(r[1], other):
                continue
            r[2].add(source)
            r[1] = r[1] or other
            return True
        if stat == "deaths" and source == "feed" and self.dead:
            return True
        return False

    def _log(self, now: float, stat: str, other: str, source: str) -> None:
        m = self.match
        if m is not None:
            m.events.append((round(m.seconds(now), 1), stat, other, source))

    def _result(self, now: float, o: Observation) -> list[Event]:
        m = self.match
        if m is None:
            return []
        unsure = "unsure" in o.values
        if o.other == "qualified":
            if m.pending == "qualified":
                return []                                    # the same sequence, another team's turn
            m.qualified += 1
            m.round += 1
            m.pending = "qualified"
            self._log(now, "qualified", f"round {m.round - 1}", "screen")
            return [Event("round", text="Qualified", value=m.round)]
        if o.other == "place":
            return self.end_match(now, "place", o.count)
        if o.other == "winner":
            # The winner's sequence shows whoever won, so who won comes from the scoreboard (a guess to check).
            st = m.standing(self.profile)
            if st and st.get("rank") == 1:
                return self.end_match(now, "won", estimated=not st.get("sure"))
            if st:
                return self.end_match(now, "lost", estimated=not st.get("sure"))
            return self.end_match(now, "ended", estimated=True)
        return self.end_match(now, o.other, estimated=unsure)

    # ------------------------------------------------------------------ the scoreboard
    def _scores(self, now: float, o: Observation) -> list[Event]:
        m = self.match
        if m is None:
            return []
        self._last_sign = now
        known = [k for k, _ in m.scores]
        fresh = []
        for key, v in o.values:
            if not key.startswith("#"):
                key = next((k for k in known if not k.startswith("#") and similar(k, key)), key)
            # a value counts once it's been read the same twice (OCR misreads a digit now and then)
            pv, n = self._pending_scores.get(key, (None, 0))
            n = n + 1 if pv == v else 1
            self._pending_scores[key] = (v, n)
            old = dict(m.scores).get(key)
            fresh.append((key, v if n >= 2 or old is None else old))
        m.scores = fresh
        out = []
        if self._before_kill is not None and now - self._kill_t <= 6.0:
            for key, v in fresh:
                before = self._before_kill.get(key)
                if before is not None and v - before in self.profile.elimination_cash:
                    m.votes[key] += 1
                    self._before_kill = None                 # one vote per elimination
                    break
        if m.votes:
            team, n = m.votes.most_common(1)[0]
            sure = n >= 2 or len(m.votes) == 1
            if (team, sure) != (m.team, m.team_sure):
                m.team, m.team_sure = team, sure
                out.append(Event("team", text=team))
        elif not m.team and len(fresh) == 2:
            m.team, m.team_sure = fresh[0][0], False        # 5 v 5: yours is on the left
        return out

    # ------------------------------------------------------------------ learning your name
    def _learn(self, now: float) -> list[Event]:
        """The tally says you eliminated V and the feed says K eliminated V at the same moment: K is you.
        (Or the death screen says K eliminated you and the feed says K eliminated X: X is you.) Twice the same,
        and that's the name the feed is read for."""
        self._feed_lines = [x for x in self._feed_lines if now - x[0] <= NAME_PAIR]
        self._tally_victims = [x for x in self._tally_victims if now - x[0] <= NAME_PAIR]
        self._death_killers = [x for x in self._death_killers if now - x[0] <= NAME_PAIR]
        out = []
        found = set()
        for _t, killer, victim in self._feed_lines:
            killer, victim = best_name(killer), best_name(victim)
            for _t2, v in self._tally_victims:
                if same_player(v, victim) and len(fold(killer)) >= 3 and name_key(v) and name_key(victim):
                    found.add(("k", killer, victim))
            for _t2, k in self._death_killers:
                if same_player(k, killer) and len(fold(victim)) >= 3 and name_key(k) and name_key(killer):
                    found.add(("d", victim, killer))
        for _how, name, _who in found:
            key = name_key(name)
            self.name_votes[key] += 1
            if self.name_votes[key] >= 2 and not self.learned_name:
                self.learned_name = name
                out.append(Event("name", text=name))
        if found:
            self._feed_lines.clear()
            self._tally_victims.clear()
            self._death_killers.clear()
        return out
