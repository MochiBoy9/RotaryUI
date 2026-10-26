"""Reading what OCR saw on a game's HUD: words with their boxes, put back into rows, and matched against phrases
and player names with room for the mistakes OCR makes on stylised game fonts (O for 0, I for 1, a lost letter)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Letters OCR mixes up with digits on condensed, glowing HUD fonts. Folding both sides the same way makes
# "EL1M1NATED" match "ELIMINATED" and "R0B0T" match "ROBOT".
_FOLD = str.maketrans({"0": "O", "Q": "O", "D": "O", "1": "I", "L": "I", "|": "I", "!": "I", "5": "S", "8": "B",
                       "2": "Z", "6": "G", "7": "T", "4": "A", "3": "E"})


@dataclass(frozen=True)
class Word:
    """One word OCR found, in pixels of the frame it read."""
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Row:
    """Words on one line of the screen, left to right, split where a gap (an icon, a column) separates them."""
    words: list[Word]
    segments: list[list[Word]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def tokens(self) -> list[str]:
        return [t for t in (clean(w.text) for w in self.words) if t]

    @property
    def x(self) -> float:
        return min(w.x for w in self.words)

    @property
    def right(self) -> float:
        return max(w.right for w in self.words)

    @property
    def y(self) -> float:
        return min(w.y for w in self.words)

    @property
    def h(self) -> float:
        """How tall its letters are (the median word): big banners have tall rows."""
        hs = sorted(w.h for w in self.words)
        return hs[len(hs) // 2]

    def segment_texts(self) -> list[str]:
        return [" ".join(w.text for w in seg) for seg in self.segments]


def clean(text: str) -> str:
    """Upper case, letters / digits / $ / + / # only: what's compared."""
    return re.sub(r"[^A-Z0-9$+#×]", "", str(text or "").upper().replace("Ø", "O"))


def fold(text: str) -> str:
    return clean(text).replace("$", "S").translate(_FOLD)


def distance(a: str, b: str, limit: int = 99) -> int:
    """Levenshtein distance (stops early past `limit`)."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = i
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def similar(a: str, b: str) -> bool:
    """The same word, as OCR may have read it: folded alike, and at most one letter in five off."""
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    allowed = len(fb) // 5          # "ASSIST": 1, "ELIMINATION": 2, "WIN": 0
    return allowed > 0 and distance(fa, fb, allowed) <= allowed


def find_phrase(tokens: list[str], phrase: str) -> tuple[int, int] | None:
    """Where the words of `phrase` appear one after the other in `tokens` (OCR may also run them together or
    split one): (first token, past the last), or None."""
    want = phrase.split()
    n = len(want)
    for i in range(len(tokens)):
        if i + n <= len(tokens) and all(similar(tokens[i + k], want[k]) for k in range(n)):
            return i, i + n
        # run together ("ELIMINATEDBY") or split ("ELIMI NATED"): compare a few tokens joined up
        for j in range(i + 1, min(len(tokens), i + n + 2) + 1):
            if similar("".join(tokens[i:j]), "".join(want)):
                return i, j
    return None


def has_phrase(tokens: list[str], phrase: str) -> bool:
    return find_phrase(tokens, phrase) is not None


def name_key(name: str) -> str:
    """A player name as it's compared: "Mochi#1234" → "MOCHI" (the Embark ID's number isn't on the HUD)."""
    return fold(re.sub(r"#\d+\s*$", "", str(name or "").strip()))


def name_in(text: str, name: str) -> bool:
    """Is this player's name in this bit of OCR text? Long names may be a letter or two off; short ones must be
    exact, so "Al" doesn't turn up in every line."""
    key, hay = name_key(name), fold(text)
    if not key or not hay:
        return False
    if len(key) < 5:
        return key in {fold(t) for t in str(text).split()} | {hay}    # a word of its own, exactly
    if key in hay:
        return True
    allowed = 1 if len(key) < 10 else 2
    n = len(key)
    for size in (n - 1, n, n + 1):          # slide a window the name's length (± a letter) along the text
        for i in range(0, max(0, len(hay) - size) + 1):
            if distance(hay[i:i + size], key, allowed) <= allowed:
                return True
    return False


def same_player(a: str, b: str) -> bool:
    """Could these two readings be the same player? Either may carry a bit of a neighbouring icon read as
    letters ("MMM DEXTER77"), so one inside the other counts. An unknown name ("") goes with anything."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return True
    if len(ka) >= 3 and len(kb) >= 3 and (ka in kb or kb in ka):
        return True
    return similar(ka, kb) or name_in(a, b) or name_in(b, a)


def best_name(text: str) -> str:
    """The likeliest player name in a feed segment: its longest word (names have no spaces; the rest is an
    icon read as letters, a squad tag…)."""
    words = [w.strip(" +:·-[]()") for w in str(text or "").split()]
    return max(words, key=lambda w: len(clean(w)), default="")


def rows_from(words: list[Word], gap: float = 1.15) -> list[Row]:
    """Group words into screen rows (by where their middles are), left to right, and split each row into
    segments where there's a gap wider than `gap` letter heights between words (a killfeed's weapon icon)."""
    rows: list[list[Word]] = []
    for w in sorted((w for w in words if clean(w.text)), key=lambda w: w.cy):
        for r in rows:
            ref = sum(x.cy for x in r) / len(r)
            hh = max(x.h for x in r)
            if abs(w.cy - ref) <= max(hh, w.h) * 0.55:
                r.append(w)
                break
        else:
            rows.append([w])
    out = []
    for r in rows:
        r.sort(key=lambda w: w.x)
        segs, cur = [], [r[0]]
        for a, b in zip(r, r[1:]):
            if b.x - a.right > gap * max(a.h, b.h):
                segs.append(cur)
                cur = []
            cur.append(b)
        segs.append(cur)
        out.append(Row(r, segs))
    out.sort(key=lambda r: r.y)
    return out


CASH = re.compile(r"\$\s?(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)(?:\s?([KkMm]))?")


def cash_values(text: str) -> list[int]:
    """Dollar amounts in a line: "$12,500" → 12500, "$7.5K" → 7500."""
    out = []
    for m in CASH.finditer(str(text or "").replace("S$", "$")):
        raw, unit = m.group(1), (m.group(2) or "").upper()
        if unit:
            try:
                v = float(raw.replace(",", "."))
            except ValueError:
                continue
            out.append(int(round(v * (1000 if unit == "K" else 1_000_000))))
        else:
            out.append(int(re.sub(r"[,.\s]", "", raw)))
    return out


def count_mark(tokens: list[str]) -> int:
    """A merged entry's count: "X2", "×3", "2X" → 2, 3, 2 (1 when there's none)."""
    for t in tokens:
        m = re.fullmatch(r"[X×](\d{1,2})|(\d{1,2})[X×]", t)
        if m:
            n = int(m.group(1) or m.group(2))
            if 1 <= n <= 12:
                return n
    return 1
