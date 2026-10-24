"""Keyboard shortcuts as text: "ctrl+alt+space", "win+shift+k", "f8", "pageup".

Settings stores shortcuts this way (Settings → Accessibility → Keyboard & mouse, and Screen assist), so any
combination can be recorded, not just the few on a list. `parse` turns one into Windows' RegisterHotKey language
(MOD_* flags and a virtual-key code), which posix.py translates for macOS and Linux; `label` writes it the way
this system shows shortcuts ("Ctrl + Alt + Space", "⌃⌥Space"). "win" is the Windows key, ⌘ on a Mac and Super
on Linux. No Qt and no system calls here, so it can be tested."""
from __future__ import annotations

from . import MAC, WIN

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
ALIASES = {"control": "ctrl", "option": "alt", "opt": "alt", "cmd": "win", "command": "win", "super": "win",
           "meta": "win", "return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "pgup": "pageup",
           "pgdn": "pagedown", "plus": "equal", "=": "equal", "-": "minus", "spacebar": "space"}

KEYS: dict[str, int] = {
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "backspace": 0x08,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "delete": 0x2E,
    "equal": 0xBB, "minus": 0xBD, "kp_add": 0x6B, "kp_subtract": 0x6D,
}
KEYS.update({chr(c): c for c in range(0x41, 0x5B)})                  # a – z (stored lower case)
KEYS.update({str(d): 0x30 + d for d in range(10)})                    # 0 – 9
KEYS.update({f"f{n}": 0x6F + n for n in range(1, 13)})                # F1 – F12
KEYS = {k.lower(): v for k, v in KEYS.items()}

_NAMES = {"space": "Space", "enter": "Enter", "esc": "Esc", "tab": "Tab", "backspace": "Backspace",
          "left": "←", "up": "↑", "right": "→", "down": "↓", "home": "Home", "end": "End", "pageup": "Page Up",
          "pagedown": "Page Down", "insert": "Insert", "delete": "Delete", "equal": "=", "minus": "−",
          "kp_add": "Keypad +", "kp_subtract": "Keypad −"}
_MAC_NAMES = {"enter": "↩", "esc": "⎋", "tab": "⇥", "backspace": "⌫", "delete": "⌦", "pageup": "⇞",
              "pagedown": "⇟", "home": "↖", "end": "↘"}
_ORDER = ("ctrl", "alt", "shift", "win")            # ⌃⌥⇧⌘ on a Mac, Apple's order too

_MAC_GLYPHS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "win": "⌘"}


def split(combo: str, partial: bool = False) -> tuple[list[str], str] | None:
    """("ctrl+alt+space") → (["ctrl", "alt"], "space"), modifiers in a fixed order; None if it isn't one.
    `partial`: the key may be "…" (still being pressed)."""
    if not combo or not isinstance(combo, str):
        return None
    parts = [ALIASES.get(p, p) for p in (s.strip().lower() for s in combo.replace(" ", "").split("+")) if p]
    mods = [p for p in parts if p in MODS]
    keys = [p for p in parts if p not in MODS]
    if len(keys) != 1 or len(set(mods)) != len(mods) or not (keys[0] in KEYS or partial and keys[0] == "…"):
        return None
    return [m for m in _ORDER if m in mods], keys[0]


def normalize(combo: str) -> str:
    """The stored spelling of a shortcut ("Alt+Ctrl+Space" → "ctrl+alt+space"), or "" if it isn't one."""
    s = split(combo)
    return "+".join(s[0] + [s[1]]) if s else ""


def parse(combo: str) -> tuple[int, int] | None:
    """A shortcut as (MOD_* flags, virtual-key code), or None ("off", empty, or not a shortcut)."""
    s = split(combo)
    if s is None:
        return None
    mods = 0
    for m in s[0]:
        mods |= MODS[m]
    return mods, KEYS[s[1]]


def key_label(key: str) -> str:
    if MAC and key in _MAC_NAMES:
        return _MAC_NAMES[key]
    if key in _NAMES:
        return _NAMES[key]
    return key.upper()


def label(combo: str) -> str:
    """How this system writes the shortcut: "Ctrl + Alt + Space", "⌃⌥Space", "Super + K"; "" if it's off.
    The key may be "…" while the modifiers are held down: "Ctrl + Alt + …"."""
    s = split(combo, partial=True)
    if s is None:
        return ""
    mods, key = s
    if MAC:
        return "".join(_MAC_GLYPHS[m] for m in _ORDER if m in mods) + key_label(key)
    win = "Win" if WIN else "Super"
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": win}
    return " + ".join([names[m] for m in mods] + [key_label(key)])


def global_problem(combo: str) -> str:
    """Why this can't be a shortcut that works from any app ('' when it can): a plain letter, digit or arrow
    would stop that key from typing anywhere, so those need Ctrl, Alt or Win (Shift alone isn't enough)."""
    s = split(combo)
    if s is None:
        return "That key can't be used for a shortcut"
    mods, key = s
    if key.startswith("f") and key[1:].isdigit():
        return ""                                          # F1 – F12 on their own are fine
    if not [m for m in mods if m != "shift"]:
        return "Add Ctrl, Alt or " + ("⌘" if MAC else "Win" if WIN else "Super") + " to that key"
    return ""


# ------------------------------------------------------------------ keys while the rotary is open
# Registered only while it's open from the keyboard, the mouse or the notch, so a plain key is fine here.
ROTARY_KEYS = {"prev": "left", "next": "right", "select": "enter", "back": "esc"}
ROTARY_KEY_NAMES = {"prev": "Previous item", "next": "Next item", "select": "Open the item", "back": "Back / close"}


def rotary_keys(general: dict | None) -> dict[str, str]:
    """The keys that move, open and go back on the open rotary: the defaults, with any the user changed."""
    got = (general or {}).get("rotary_keys") or {}
    out = {}
    for action, default in ROTARY_KEYS.items():
        combo = got.get(action, default) if isinstance(got, dict) else default
        out[action] = normalize(combo) if combo and combo != "off" else ""
        if combo and combo != "off" and not out[action]:
            out[action] = default                          # not a key we know: keep the default
    return out
