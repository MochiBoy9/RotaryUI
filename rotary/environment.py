"""The room the camera looks into: is it dark, is there a bright window behind you, are other people in view?

The tracking thread hands over a SceneSample about once a second, measured on the raw camera picture (before
any brightening). EnvironmentMonitor keeps the last half minute of them and, once a problem has lasted, suggests
the setting that helps. Nothing changes until you accept it, and every fix happens inside RotaryUI: the picture
the hand tracker sees is brightened in software, or only your own hands are followed. The camera's and the
computer's settings are never touched.

Two fixes:
- Low-light boost: a stronger, always-on version of the brightening (gamma + local contrast, with a little
  noise smoothing in a very dark room). Also evens out a face lit from behind by a window.
- Focus on me: follow only the hands of the person nearest the camera (the largest face), so people walking
  past or sitting behind you can't open or turn the rotary.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

DEFAULTS = {"suggest": True, "low_light_boost": False, "focus_on_me": False, "never": []}

WINDOW = 30               # samples (about a second apart) the monitor judges by
MIN_SAMPLES = 20          # …and how many it needs before it suggests anything
DARK = 45.0               # raw picture brightness (0-255) below which a room counts as dark
BACKLIT_ROOM = 95.0       # a bright picture…
BACKLIT_FACE = 0.55       # …with the face this much darker than the rest of it: a window or lamp behind you
SHARE = 0.7               # how much of the window a problem has to fill to count
CROWD_SHARE = 0.4         # more than one face in this share of samples: other people about
COOLDOWN = 25 * 60        # seconds before a suggestion turned down with "Not now" may come back


def settings(cfg: dict | None) -> dict:
    got = (cfg or {}).get("camera") or {}
    out = {k: got.get(k, v) for k, v in DEFAULTS.items()}
    out["never"] = [n for n in (out["never"] or []) if isinstance(n, str)]
    return out


@dataclass
class SceneSample:
    t: float
    light: float                  # mean brightness of the raw picture, 0-255
    faces: int                    # faces in view
    face_light: float = -1.0      # mean raw brightness of the nearest face (-1: no face)


@dataclass(frozen=True)
class Suggestion:
    kind: str                     # dark | backlit | crowded
    setting: str                  # the camera setting that helps: low_light_boost | focus_on_me
    title: str
    text: str
    reading: str                  # what was measured, for the log and the Camera page


SUGGESTIONS = {
    "dark": ("low_light_boost", "It's dark in here",
             "Your hands are harder to see in a dark room. Turn on Low-light boost? RotaryUI brightens the "
             "picture it tracks on its own; your camera's settings stay as they are."),
    "backlit": ("low_light_boost", "There's bright light behind you",
                "A window or lamp behind you leaves your face and hands in shadow. Turn on Low-light boost? "
                "RotaryUI evens out the picture it tracks on its own; your camera's settings stay as they are."),
    "crowded": ("focus_on_me", "Other people are in view",
                "Turn on Focus on me? RotaryUI will follow only the hands of the person closest to the camera, "
                "so people behind or beside you can't open the rotary."),
}


class EnvironmentMonitor:
    """Judges the last WINDOW samples and suggests, once per problem, the setting that helps."""

    def __init__(self):
        self.samples: deque[SceneSample] = deque(maxlen=WINDOW)
        self._declined: dict[str, float] = {}       # kind → when "Not now" was picked
        self.pending: Suggestion | None = None       # asked, not answered yet

    def reset(self) -> None:
        self.samples.clear()

    def add(self, s: SceneSample) -> None:
        self.samples.append(s)

    def conditions(self) -> dict[str, float]:
        """The share of recent samples with each problem (empty until there are enough samples)."""
        n = len(self.samples)
        if n < MIN_SAMPLES:
            return {}
        dark = sum(s.light < DARK for s in self.samples) / n
        backlit = sum(s.face_light >= 0 and s.light > BACKLIT_ROOM and s.face_light < s.light * BACKLIT_FACE
                      for s in self.samples) / n
        crowded = sum(s.faces >= 2 for s in self.samples) / n
        return {"dark": dark, "backlit": backlit, "crowded": crowded}

    def reading(self) -> str:
        """A short description of the room for the Camera page: "Dim · 3 people in view"."""
        if not self.samples:
            return "Measuring…"
        recent = list(self.samples)[-5:]
        light = sorted(s.light for s in recent)[len(recent) // 2]
        faces = max(s.faces for s in recent)
        parts = ["Dark" if light < DARK else "Dim" if light < 80 else "Well lit"]
        if any(s.face_light >= 0 and s.light > BACKLIT_ROOM and s.face_light < s.light * BACKLIT_FACE
               for s in recent):
            parts.append("lit from behind")
        if faces >= 2:
            parts.append(f"{faces} people in view")
        return " · ".join(parts)

    def suggest(self, cfg_camera: dict, now: float) -> Suggestion | None:
        """The suggestion to put in front of the user now, if any. Returns each one only once until answered."""
        if self.pending is not None or not cfg_camera.get("suggest", True):
            return None
        cond = self.conditions()
        if not cond:
            return None
        never = set(cfg_camera.get("never") or [])
        order = [("dark", cond["dark"] >= SHARE), ("backlit", cond["backlit"] >= SHARE),
                 ("crowded", cond["crowded"] >= CROWD_SHARE)]
        for kind, hit in order:
            setting, title, text = SUGGESTIONS[kind]
            if not hit or kind in never or cfg_camera.get(setting):
                continue
            if now - self._declined.get(kind, -math.inf) < COOLDOWN:
                continue
            self.pending = Suggestion(kind, setting, title, text, self.reading())
            return self.pending
        return None

    def answered(self, accepted: bool, now: float) -> None:
        """The user answered the pending suggestion. Declining holds it back for COOLDOWN."""
        if self.pending is not None and not accepted:
            self._declined[self.pending.kind] = now
        self.pending = None
        self.samples.clear()                         # judge the room afresh from here on


# --------------------------------------------------------------------------- focus on me
REACH = 4.5               # how far from the face, in face widths, a hand of that person can be
NEARER = 0.6              # a hand smaller than this, against the face, belongs to someone further back
FACE_SIZE_M = 0.16        # the face box's real size, metres (as in app.face_depth)


def _area(face) -> float:
    return max(face[2] - face[0], 0.0) * max(face[3] - face[1], 0.0)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def primary_face(faces: list, prev=None):
    """The face of the person using the rotary: the one we followed before while it's still there and not much
    smaller than the largest, otherwise the largest (nearest the camera)."""
    if not faces:
        return None
    biggest = max(faces, key=_area)
    if prev is not None:
        same = max(faces, key=lambda f: _iou(f, prev))
        if _iou(same, prev) > 0.3 and _area(same) > 0.6 * _area(biggest):
            return same
    return biggest


HOLD = 3.0                # seconds to keep following a face that vanished (a hand held up in front of it)


class FocusOnMe:
    """Keeps track of whose hands to follow while Focus on me is on."""

    def __init__(self):
        self.face = None
        self._seen = -math.inf

    def reset(self) -> None:
        self.face, self._seen = None, -math.inf

    def update(self, faces: list, t: float):
        """The user's face now. A hand held up in front of your face can hide it from the face detector: for a
        moment then, a smaller face further back doesn't take over."""
        best = primary_face(faces, self.face)
        if (best is not None and self.face is not None and t - self._seen < HOLD
                and _iou(best, self.face) <= 0.3 and _area(best) < 0.5 * _area(self.face)):
            return self.face
        if best is not None:
            self.face, self._seen = best, t
        elif t - self._seen >= HOLD:
            self.face = None
        return self.face

    def keep(self, hands: list, faces: list, aspect: float, t: float) -> list[int]:
        """Indices of the hands (HandFeatures) that are the user's. With no face to go by, all of them."""
        me = self.update(faces, t)
        if me is None:
            return list(range(len(hands)))
        return [i for i, f in enumerate(hands) if belongs_to(f.center, f.px_per_m, me, aspect)]


def belongs_to(center: tuple[float, float], px_per_m: float, face, aspect: float) -> bool:
    """Could this hand be the face's owner's? Its palm centre (image-height units) has to be within arm's reach
    of the face, and the hand no further from the camera than the face (a hand of someone sitting behind you
    looks small next to your face)."""
    fx0, fy0, fx1, fy1 = face
    fw = max((fx1 - fx0) * aspect, fy1 - fy0, 1e-6)          # face size, image-height units
    cx, cy = (fx0 + fx1) / 2 * aspect, (fy0 + fy1) / 2
    if math.hypot(center[0] - cx, center[1] - cy) > REACH * fw:
        return False
    return px_per_m <= 0 or px_per_m / (fw / FACE_SIZE_M) >= NEARER
