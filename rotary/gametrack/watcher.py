"""Active Game Tracking running: a background thread looks at the game a few times a second (only while it's
the window in front), the tracker turns that into stats on the UI thread, the database keeps each match, and
the notch and the Game tracking page are told what changed."""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .. import DATA_DIR, WIN
from . import RESULT_WORDS, STAT_CHOICES, capture, names_for, notch_line, ocr, profile, settings, stat_value
from .engine import STATS, Event, Tracker, ordinal
from .profile import Observation, Profile
from .store import Store
from .text import Word, rows_from

log = logging.getLogger(__name__)
DB_PATH = DATA_DIR / "game_stats.sqlite3"
DEBUG_DIR = DATA_DIR / "gametrack"


def read_frame(frame, prof: Profile, engine: ocr.Engine, ctx: dict, zones: dict | None = None) \
        -> tuple[list[Observation], dict[str, list[str]]]:
    """OCR each zone of one frame and read it: (observations, the text of each zone row by row)."""
    h, w = frame.shape[:2]
    scale = ocr.scale_for(h)
    by_zone, texts = {}, {}
    for name, zone in {**prof.zones, **(zones or {})}.items():
        try:
            z = tuple(float(v) for v in zone)
            if len(z) != 4:
                continue
        except (TypeError, ValueError):
            continue
        pix, x0, y0 = capture.crop(frame, z)
        found = engine.read(pix, scale)
        rows = rows_from([Word(wd.text, wd.x + x0, wd.y + y0, wd.w, wd.h) for wd in found])
        by_zone[name] = rows
        texts[name] = [" | ".join(r.segment_texts()) for r in rows]
    return prof.read(by_zone, (w, h), ctx), texts


class _Reader(threading.Thread):
    """Grabs and reads the game's frames off the UI thread. Frames come from the screen itself on Windows, or
    are handed over (`frames`) by the UI thread elsewhere."""

    def __init__(self, owner: "GameWatcher"):
        super().__init__(daemon=True, name="game-tracking")
        self.owner = owner
        self.frames: queue.Queue = queue.Queue(maxsize=1)
        self.stop_flag = threading.Event()
        self.one_shot: queue.Queue = queue.Queue()         # "check the screen now" requests

    def run(self) -> None:
        engine = ocr.best_engine()
        self.owner.ocr_name, self.owner.ocr_error = engine.name, ("" if engine.available() else engine.error)
        try:
            while not self.stop_flag.is_set():
                cfg = self.owner.cfg_fn()
                s = settings(cfg)
                prof = profile(cfg)
                period = 1.0 / max(1, min(6, int(s.get("rate", 3) or 3)))
                t0 = time.monotonic()
                shot = None
                try:
                    shot = self.one_shot.get_nowait()
                except queue.Empty:
                    pass
                if not s["enabled"] and shot is None:
                    self.stop_flag.wait(0.5)
                    continue
                if shot is not None and not isinstance(shot, bool):
                    frame, status = shot, "A screenshot"            # "Check a screenshot…"
                else:
                    frame, status = self._frame(prof, force=shot is not None)
                if frame is not None and engine.available():
                    ctx = {"names": names_for(cfg), "words": s.get("words") or {}}
                    try:
                        obs, texts = read_frame(frame, prof, engine, ctx, s.get("zones") or {})
                    except Exception as e:
                        log.exception("couldn't read the game's screen")
                        obs, texts, status = [], {}, f"Couldn't read the screen: {e}"
                    if shot is not None:
                        self.owner.checked.emit({"texts": texts, "obs": [(o.kind, o.other, o.count) for o in obs],
                                                 "size": frame.shape[1::-1], "frame": frame})
                    if s["enabled"] and (shot is None or isinstance(shot, bool)):
                        self.owner.observed.emit(time.monotonic(), obs, status)
                else:
                    if shot is not None:
                        self.owner.checked.emit({"error": engine.error if not engine.available() else status})
                    if s["enabled"]:
                        self.owner.observed.emit(time.monotonic(), [], status if engine.available() else
                                                 engine.error)
                wait = (period if frame is not None else 1.0) - (time.monotonic() - t0)
                self.stop_flag.wait(max(0.05, wait))
        finally:
            engine.close()

    def _frame(self, prof: Profile, force: bool):
        """(frame or None, status line)."""
        if WIN:
            win = capture.find_window(prof.exe, prof.titles)
            if win is None:
                return None, f"Waiting for {prof.name}"
            if not win.front and not force:
                return None, f"{prof.name} isn't in front"
            frame = capture.grab(win.rect)
            if frame is None or capture.is_blank(frame):
                return None, "Can't see the game: set it to Borderless or Windowed"
            return frame, f"Watching {prof.name}"
        try:
            frame = self.frames.get(timeout=1.0)
        except queue.Empty:
            return None, f"Waiting for {prof.name}"
        if frame is None:
            return None, f"{prof.name} isn't in front"
        return frame, f"Watching {prof.name}"


class GameWatcher(QObject):
    changed = Signal()                     # stats, the match or the status changed
    event = Signal(object)                 # an engine.Event worth showing (an elimination, a match ending…)
    observed = Signal(float, object, str)  # from the reader thread: (time, observations, status)
    checked = Signal(object)               # "check the screen now": what it read

    def __init__(self, cfg_fn, parent=None, db_path: Path = DB_PATH):
        super().__init__(parent)
        self.cfg_fn = cfg_fn
        self.db_path = db_path
        self._store: Store | None = None
        self.tracker = Tracker(profile(cfg_fn()), auto_start=settings(cfg_fn())["auto_matches"])
        self.status = "Off"
        self.ocr_name, self.ocr_error = "", ""
        self.summary_open = False              # the last match's summary is on the notch
        self._reader: _Reader | None = None
        self._save_due = 0.0
        self.observed.connect(self._on_observed)
        self._tick = QTimer(self)              # time passing (death screens fading, matches petering out)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._grab = QTimer(self)              # macOS / Linux: frames come from Qt on this thread
        self._grab.timeout.connect(self._qt_frame)

    # ------------------------------------------------------------------ on / off
    @property
    def enabled(self) -> bool:
        return bool(settings(self.cfg_fn())["enabled"])

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.db_path)
        return self._store

    def apply(self) -> None:
        """Settings changed: start or stop watching."""
        s = settings(self.cfg_fn())
        prof = profile(self.cfg_fn())
        if prof is not self.tracker.profile:
            self.tracker = Tracker(prof, auto_start=s["auto_matches"])
        self.tracker.auto_start = bool(s["auto_matches"])
        if s["enabled"]:
            self._ensure_reader()
            self._tick.start()
            if not WIN:
                self._grab.start(int(1000 / max(1, min(6, int(s["rate"] or 3)))))
            if self.status == "Off":
                self.status = f"Waiting for {prof.name}"
        else:
            self._tick.stop()
            self._grab.stop()
            if self.tracker.match is not None and not self.tracker.match.manual:
                self._handle(self.tracker.end_match(time.monotonic(), "left"))
            if self._reader is not None:
                self._reader.stop_flag.set()
                self._reader = None
            self.status = "Off"
        self.changed.emit()

    def _ensure_reader(self) -> None:
        if self._reader is None or not self._reader.is_alive():
            self._reader = _Reader(self)
            self._reader.start()

    def stop(self) -> None:
        if self._reader is not None:
            self._reader.stop_flag.set()
            self._reader = None
        if self.tracker.match is not None:
            self._save(self.tracker.match)
        if self._store is not None:
            self._store.close()
            self._store = None

    def check_now(self) -> None:
        """Read the screen once (even with tracking off, and even if the game isn't in front): for the
        Game tracking page's "Check the screen"."""
        self._ensure_reader()
        if not WIN:
            try:
                self._reader.frames.put_nowait(capture.qt_grab())
            except Exception:
                pass
        self._reader.one_shot.put(True)

    def check_image(self, path: str) -> None:
        """Read a screenshot of the game from a file, to see what tracking would make of it."""
        import cv2
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            self.checked.emit({"error": "Couldn't open that picture"})
            return
        self._ensure_reader()
        self._reader.one_shot.put(frame)

    def _qt_frame(self) -> None:
        prof = self.tracker.profile
        exe, title = capture.front_app()
        front = any(t in title.lower() for t in prof.titles) or exe.lower() in prof.exe
        frame = capture.qt_grab() if front else None
        try:
            self._reader.frames.put_nowait(frame)
        except (queue.Full, AttributeError):
            pass

    # ------------------------------------------------------------------ what was seen
    def _on_observed(self, t: float, obs: list, status: str) -> None:
        if not self.enabled:
            return
        changed = status != self.status
        self.status = status
        if self.tracker.learned_name == "" and settings(self.cfg_fn())["learned_name"]:
            self.tracker.learned_name = settings(self.cfg_fn())["learned_name"]
        events = self.tracker.feed(t, obs)
        if events or changed:
            self._handle(events)
        if obs and self.tracker.match is not None and time.monotonic() > self._save_due:
            self._save(self.tracker.match)

    def _on_tick(self) -> None:
        self._handle(self.tracker.tick(time.monotonic()), quiet=True)

    def _handle(self, events: list[Event], quiet: bool = False) -> None:
        for e in events:
            if e.kind == "match_start":
                self.summary_open = False
                self._save(self.tracker.match)
            elif e.kind == "match_end" and self.tracker.last is not None:
                self._save(self.tracker.last)
                self.summary_open = True
            elif e.kind == "name":
                cfg = self.cfg_fn()
                cfg.setdefault("gametrack", {})["learned_name"] = e.text
            elif e.kind in ("stat", "round", "mode", "team"):
                self._save_due = min(self._save_due or 1e18, time.monotonic() + 5.0)
            self.event.emit(e)
        if events or not quiet:
            self.changed.emit()
        elif self.tracker.match is not None:
            self.changed.emit()                # the match clock

    def _save(self, m) -> None:
        if m is None:
            return
        try:
            self.store.save(m, m.standing(self.tracker.profile), time.monotonic())
        except Exception:
            log.exception("couldn't save the match")
        self._save_due = time.monotonic() + 30.0

    # ------------------------------------------------------------------ by hand
    def start_match(self, mode: str = "") -> None:
        self._handle(self.tracker.start_match(time.monotonic(), mode, manual=True))

    def end_match(self, result: str = "ended", place: int = 0) -> None:
        self._handle(self.tracker.end_match(time.monotonic(), result, place, manual=True))

    def fix_result(self, result: str, place: int = 0) -> None:
        self.tracker.fix_result(result, place)
        self._save(self.tracker.last)
        self.changed.emit()

    def adjust(self, stat: str, delta: int) -> None:
        events = self.tracker.adjust(stat, delta, time.monotonic())
        target = self.tracker.match or self.tracker.last
        if events and target is not None:
            self._save(target)
        self._handle(events)

    def dismiss_summary(self) -> None:
        self.summary_open = False
        self.changed.emit()

    def set_mode(self, mode: str) -> None:
        self.tracker.set_mode(mode)
        self._save(self.tracker.match)
        self.changed.emit()

    # ------------------------------------------------------------------ for the notch and the page
    def snapshot(self) -> dict | None:
        """What the notch shows: the match on now, else the last one's summary; None when there's neither."""
        s = settings(self.cfg_fn())
        tr = self.tracker
        prof = tr.profile
        now = time.monotonic()
        keys = s["notch_stats"] or ["kills", "deaths", "assists"]
        if tr.match is not None:
            m = tr.match
            st = m.standing(prof)
            return {"state": "playing", "game": prof.name, "mode": tr.mode_name(m.mode), "seconds": m.seconds(now),
                    "dead": tr.dead, "line": notch_line(keys, m.stats, st, m.seconds(now)) or "Tracking",
                    "tiles": self._tiles(keys, m.stats, st, m.seconds(now)), "round": m.round, "final": m.final,
                    "manual": m.manual, "status": self.status, "sure": st.get("sure", True) if st else True}
        if tr.last is not None and self.summary_open:
            m = tr.last
            st = m.standing(prof)
            place = m.place_text(prof)
            head = RESULT_WORDS.get(m.result, "Match over")
            if m.result == "place" and m.place:
                head = f"{ordinal(m.place)} place"
            elif m.result == "lost" and place:
                head = f"Out · {place}"
            line = f"{head} · {m.stats['kills']}/{m.stats['deaths']}/{m.stats['assists']}"
            return {"state": "ended", "game": prof.name, "mode": tr.mode_name(m.mode), "seconds": m.seconds(now),
                    "result": m.result, "headline": head, "estimated": m.estimated, "line": line,
                    "tiles": self._tiles(["kills", "deaths", "assists", "revives", "kd", "time"], m.stats, st,
                                         m.seconds(now)), "status": self.status}
        return None

    @staticmethod
    def _tiles(keys: list[str], stats: dict, standing: dict, seconds: float) -> list[tuple[str, str]]:
        out = []
        for k in list(dict.fromkeys(keys + ["kills", "deaths", "assists", "revives"]))[:6]:
            v = stat_value(k, stats, standing, seconds)
            if v is not None:
                out.append((STAT_CHOICES[k][0], v))
        return out

    def live_stats(self) -> dict:
        m = self.tracker.match or self.tracker.last
        return dict(m.stats) if m is not None else dict.fromkeys(STATS, 0)
