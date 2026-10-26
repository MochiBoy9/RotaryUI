"""Your matches, kept on this computer: an SQLite database (game_stats.sqlite3 in the settings folder) with one
row per match and every counted event, so the history and the totals survive restarts. Nothing is sent anywhere."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from pathlib import Path

from .engine import STATS, Match

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL,
    ended REAL,
    seconds REAL NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '',
    place INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    manual INTEGER NOT NULL DEFAULT 0,
    rounds INTEGER NOT NULL DEFAULT 1,
    kills INTEGER NOT NULL DEFAULT 0,
    deaths INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    revives INTEGER NOT NULL DEFAULT 0,
    revived INTEGER NOT NULL DEFAULT 0,
    wipes INTEGER NOT NULL DEFAULT 0,
    team_score INTEGER,
    best_other INTEGER,
    events TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS matches_game ON matches(game, started);
"""
COLUMNS = ("id", "game", "mode", "started", "ended", "seconds", "result", "place", "estimated", "manual", "rounds",
           *STATS, "team_score", "best_other")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def save(self, m: Match, standing: dict | None = None, now: float = 0.0) -> int:
        """Write a match (a new row the first time, the same row after that) and return its id."""
        st = standing or {}
        row = {"game": m.game, "mode": m.mode, "started": m.started, "ended": m.ended or None,
               "seconds": round(m.seconds(now), 1), "result": m.result, "place": m.place,
               "estimated": int(m.estimated), "manual": int(m.manual), "rounds": m.round,
               **{k: int(m.stats.get(k, 0)) for k in STATS},
               "team_score": st.get("ours"), "best_other": st.get("best"), "events": json.dumps(m.events[-500:])}
        with self._lock:
            if m.id:
                sets = ", ".join(f"{k} = ?" for k in row)
                self._db.execute(f"UPDATE matches SET {sets} WHERE id = ?", (*row.values(), m.id))
            else:
                cols = ", ".join(row)
                cur = self._db.execute(f"INSERT INTO matches ({cols}) VALUES ({', '.join('?' * len(row))})",
                                       tuple(row.values()))
                m.id = int(cur.lastrowid)
            self._db.commit()
        return m.id

    def recent(self, game: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(COLUMNS)} FROM matches WHERE game = ? AND ended IS NOT NULL "
                                    "ORDER BY started DESC LIMIT ?", (game, limit)).fetchall()
        return [dict(r) for r in rows]

    def totals(self, game: str) -> dict:
        with self._lock:
            r = self._db.execute(
                "SELECT COUNT(*) AS matches, SUM(result = 'won' OR (result = 'place' AND place = 1)) AS wins, "
                "SUM(result = 'lost') AS losses, " + ", ".join(f"COALESCE(SUM({k}), 0) AS {k}" for k in STATS)
                + ", COALESCE(SUM(seconds), 0) AS seconds FROM matches WHERE game = ? AND ended IS NOT NULL",
                (game,)).fetchone()
        out = {k: (r[k] or 0) for k in r.keys()}
        out["kd"] = out["kills"] / max(1, out["deaths"])
        return out

    def delete(self, match_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM matches WHERE id = ?", (match_id,))
            self._db.commit()

    def clear(self, game: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM matches WHERE game = ?", (game,))
            self._db.commit()

    def export_csv(self, game: str, path: Path) -> int:
        rows = self.recent(game, limit=1_000_000)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(COLUMNS))
            w.writeheader()
            w.writerows(rows)
        return len(rows)
