"""Rotary items: configuration, and running actions on Windows, macOS and Linux.

Action types (config.json → "action": {...}):
  app          {"target": "C:\\…\\App.exe" | ".lnk" | "shell:AppsFolder\\<AppID>", "args": "", "process": "App.exe"}
               (macOS: "/Applications/App.app"; Linux: a program or a .desktop file)
               switches to the app when it's already open (unless it has arguments)
  quicknav     {"app": "steam"}                      opens a second ring with that app's actions
  browser      {"url": "https://…"}                  default browser (URL optional)
  open         {"target": "ms-settings:"}            a URI, file or folder
  keys         {"keys": "win+d"}                     a key combination
  window_keys  {"process": "Discord.exe", "keys": "ctrl+shift+m", "restore": true}
               switch to an app, press its shortcut, switch back
  obs          {"request": "ToggleRecord", "data": {}}   OBS Studio (see obs.py)
  shell        {"command": "…", "hidden": true}      cmd.exe (macOS / Linux: sh)
  powershell   {"script": "…"}                       Windows PowerShell (macOS / Linux: pwsh, if installed)
  python       {"code": "…"}                         runs in its own process
  assist       {}                                    screen assist: an AI answers what's on screen (assist.py)
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import DATA_DIR, FROZEN, MAC, WIN, apps, winapi
from .winapi import parse_keys, press_keys  # noqa: F401  (re-exported for callers)

log = logging.getLogger(__name__)
CONFIG_PATH = DATA_DIR / "config.json"
CONFIG_VERSION = 4
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ACTION_TYPES = {
    "app": "Open an app",
    "quicknav": "Quick nav for an app",
    "browser": "Open a website",
    "open": "Open a file, folder or link",
    "keys": "Press a keyboard shortcut",
    "window_keys": "Press a shortcut inside an app",
    "obs": "Control OBS Studio",
    "shell": "Run a command",
    "powershell": "Run a PowerShell script",
    "python": "Run Python code",
    "assist": "Solve what's on screen (AI)",
}

ASSIST_ITEM = {"name": "Screen assist", "subtitle": "Answer what's on screen", "glyph": "E897", "tint": "#BF5AF2",
               "action": {"type": "assist"}}

WINDOWS_ITEMS = [
    {"name": "Browser", "subtitle": "Open your browser", "glyph": "E774", "tint": "#0A84FF",
     "action": {"type": "browser"}},
    {"name": "Files", "subtitle": "File Explorer", "glyph": "E8B7", "tint": "#32ADE6",
     "action": {"type": "app", "target": "explorer.exe"}},
    {"name": "Terminal", "subtitle": "Command line", "glyph": "E756", "tint": "#30D158",
     "action": {"type": "app", "target": "wt.exe", "fallback": "cmd.exe"}},
    {"name": "Settings", "subtitle": "Windows Settings", "glyph": "E713", "tint": "#8E8E93",
     "action": {"type": "open", "target": "ms-settings:"}},
    {"name": "Play / Pause", "subtitle": "Media", "glyph": "E768", "tint": "#FF375F",
     "action": {"type": "keys", "keys": ["media_play_pause"]}},
    {"name": "Next Track", "subtitle": "Media", "glyph": "E893", "tint": "#BF5AF2",
     "action": {"type": "keys", "keys": ["media_next"]}},
    {"name": "Screenshot", "subtitle": "Snip a region", "glyph": "E722", "tint": "#FF9F0A",
     "action": {"type": "keys", "keys": "win+shift+s"}},
    {"name": "Desktop", "subtitle": "Show desktop", "glyph": "E7F4", "tint": "#5E5CE6",
     "action": {"type": "keys", "keys": ["win", "d"]}},
]

MAC_ITEMS = [
    {"name": "Browser", "subtitle": "Open your browser", "glyph": "E774", "tint": "#0A84FF",
     "action": {"type": "browser"}},
    {"name": "Finder", "subtitle": "Your files", "glyph": "E8B7", "tint": "#32ADE6",
     "action": {"type": "app", "target": "/System/Library/CoreServices/Finder.app"}},
    {"name": "Terminal", "subtitle": "Command line", "glyph": "E756", "tint": "#30D158",
     "action": {"type": "app", "target": "/System/Applications/Utilities/Terminal.app"}},
    {"name": "Settings", "subtitle": "System Settings", "glyph": "E713", "tint": "#8E8E93",
     "action": {"type": "open", "target": "x-apple.systempreferences:"}},
    {"name": "Play / Pause", "subtitle": "Media", "glyph": "E768", "tint": "#FF375F",
     "action": {"type": "keys", "keys": ["media_play_pause"]}},
    {"name": "Next Track", "subtitle": "Media", "glyph": "E893", "tint": "#BF5AF2",
     "action": {"type": "keys", "keys": ["media_next"]}},
    {"name": "Screenshot", "subtitle": "Capture the screen", "glyph": "E722", "tint": "#FF9F0A",
     "action": {"type": "keys", "keys": "cmd+shift+5"}},
    {"name": "Mission Control", "subtitle": "All your windows", "glyph": "E7F4", "tint": "#5E5CE6",
     "action": {"type": "keys", "keys": "ctrl+up"}},
]

LINUX_ITEMS = [
    {"name": "Browser", "subtitle": "Open your browser", "glyph": "E774", "tint": "#0A84FF",
     "action": {"type": "browser"}},
    {"name": "Files", "subtitle": "Your home folder", "glyph": "E8B7", "tint": "#32ADE6",
     "action": {"type": "open", "target": "~"}},
    {"name": "Terminal", "subtitle": "Command line", "glyph": "E756", "tint": "#30D158",
     "action": {"type": "app", "target": "x-terminal-emulator", "fallback": "gnome-terminal"}},
    {"name": "Settings", "subtitle": "System settings", "glyph": "E713", "tint": "#8E8E93",
     "action": {"type": "app", "target": "gnome-control-center", "fallback": "systemsettings"}},
    {"name": "Play / Pause", "subtitle": "Media", "glyph": "E768", "tint": "#FF375F",
     "action": {"type": "keys", "keys": ["media_play_pause"]}},
    {"name": "Next Track", "subtitle": "Media", "glyph": "E893", "tint": "#BF5AF2",
     "action": {"type": "keys", "keys": ["media_next"]}},
    {"name": "Screenshot", "subtitle": "Capture the screen", "glyph": "E722", "tint": "#FF9F0A",
     "action": {"type": "keys", "keys": "printscreen"}},
    {"name": "Overview", "subtitle": "All your windows", "glyph": "E7F4", "tint": "#5E5CE6",
     "action": {"type": "keys", "keys": "win"}},
]

DEFAULT_ITEMS = WINDOWS_ITEMS if WIN else MAC_ITEMS if MAC else LINUX_ITEMS

DEFAULT_CONFIG = {
    "version": CONFIG_VERSION,
    "items": DEFAULT_ITEMS,
    "gestures": {
        "still_seconds": 3.0,
        "degrees_per_item": 22.0,
        "dwell_seconds": 1.6,
        "intent": "balanced",
        "flip_handedness": True,
    },
    "appearance": {
        "dial_size": 540,        # was 600 before config version 3
        "status_pill": True,
        "live_glass": False,
        "sounds": True,
        "volume": 0.6,
        "text_scale": 1.0,
        "high_contrast": False,
        "reduce_motion": False,
        "speak": False,
        "dial_clock": "12h",     # the clock under the rotary: 12h | 24h | off
        "dial_weather": True,    # with the weather beside it (from wttr.in)
        "weather_place": "",     # where, for the weather ('' = worked out from the internet address)
    },
    "general": {
        "keep_in_taskbar": False,    # closing the window hides it to the tray; the rotary keeps working
        "close_hint_shown": False,   # told once that closing the window leaves RotaryUI running
        "start_minimized": True,
        "onboarded": False,
        "tutorial_done": False,      # the first-run tour has been finished or skipped (Home replays it)
        "hotkey": "ctrl+alt+space",
        "python": "auto",
        "camera": True,              # hand gestures through the camera
        "notch": "auto",             # auto (when there's no camera) | on | off
        "notch_peek": False,         # tuck the notch away until the mouse reaches the top
        "notch_hide_fullscreen": True,
        "auto_update": True,         # install new releases from GitHub by itself (update.py)
    },
    "island": {                  # what the notch shows, Dynamic-Island style
        "clock": "time",         # off | time | date
        "clock_24h": None,       # None = follow the system
        "media": True,           # now playing (YouTube, Spotify, Apple Music, …)
        "media_art": True,
        "clips": True,           # Medal recording; clips saved by Medal / Game Bar / NVIDIA / OBS
        "calls": True,           # time on a Discord / Teams / Zoom / … call (the app holding the mic)
        "privacy": True,         # microphone / camera in use
        "battery": True,         # plugged in, unplugged, running low
        "look": "dark",          # dark | glass
        "alerts_over_games": True,
    },
    "agents": {                  # AI agents on the notch (agents.py)
        "enabled": True,         # the local server Claude Code, Codex, MCP clients and models report to
        "port": 47823,           # on 127.0.0.1 only
        "token": "",             # the secret every address starts with (made at first start)
        "ask_seconds": 120,      # how long a question waits on the notch before the agent asks the usual way
        "flash": True,           # each new step slides in on the notch for a moment
        "proxies": {},           # name → base URL: more model services for the pass-through
    },
    "integrations": {"obs": {"host": "localhost", "port": 4455}},
    "camera": {
        "preferred": "",
        "suggest": True,             # offer a fix when the room is dark, lit from behind or busy (environment.py)
        "low_light_boost": False,    # brighten the picture tracking sees, in software
        "focus_on_me": False,        # follow only the hands of the person nearest the camera
        "never": [],                 # suggestions answered "Don't ask again": dark | backlit | crowded
    },
    "assist": {                  # screen assist (assist.py)
        "provider": "gemini",    # gemini | groq | openrouter | ollama | anthropic | openai
        "models": {},            # provider → model ('' = its default)
        "urls": {},              # provider → API address ('' = its default)
        "keys": {},              # provider → API key, encrypted like the OBS password
        "seconds": 20,           # how long the answer stays on the notch
        # win+alt+a | ctrl+alt+a | ctrl+shift+a | off. Not Ctrl + Alt + a letter on Windows: that's AltGr there.
        "hotkey": "ctrl+alt+a" if MAC else "win+alt+a",
    },
}

MEDIA_PRESETS = {
    "media_play_pause": "Play / Pause", "media_next": "Next track", "media_prev": "Previous track",
    "volume_up": "Volume up", "volume_down": "Volume down", "volume_mute": "Mute",
}


@dataclass
class Item:
    name: str
    subtitle: str
    glyph: str          # Segoe Fluent Icons code point, e.g. "E774", or a literal character
    tint: str
    action: dict
    icon: str = ""      # optional file whose real icon is shown (exe / lnk / ico / png / jpg)

    @property
    def glyph_char(self) -> str:
        g = self.glyph or "E8A7"
        if len(g) in (4, 5) and all(c in "0123456789abcdefABCDEF" for c in g):
            return chr(int(g, 16))
        return g

    @property
    def kind(self) -> str:
        return str(self.action.get("type", ""))

    def to_dict(self) -> dict:
        d = {"name": self.name, "subtitle": self.subtitle, "glyph": self.glyph, "tint": self.tint,
             "action": dict(self.action)}
        if self.icon:
            d["icon"] = self.icon
        return d


def back_item(parent: str) -> Item:
    return Item("Back", f"Leave {parent}" if parent else "All items", "E72B", "#8E8E93", {"type": "back"})


# --------------------------------------------------------------------------- config
def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        save_config(DEFAULT_CONFIG, path)
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except Exception as e:
        # Keep the broken file so nothing the user wrote is lost.
        backup = path.with_name(f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            path.replace(backup)
        except OSError:
            pass
        log.error("config.json was unreadable (%s) — saved it as %s and started fresh", e, backup.name)
        save_config(DEFAULT_CONFIG, path)
        return copy.deepcopy(DEFAULT_CONFIG)
    cfg = _merge(DEFAULT_CONFIG, data)
    if isinstance(data.get("items"), list):
        cfg["items"] = data["items"]
    if "onboarded" not in (data.get("general") or {}):
        cfg["general"]["onboarded"] = False         # existing users get the (short) welcome once
    if "tutorial_done" not in (data.get("general") or {}):
        # The tour is for new users: anyone who already set the app up has been using it without one.
        cfg["general"]["tutorial_done"] = bool((data.get("general") or {}).get("onboarded", False))
    _repair_items(cfg.get("items", []))
    ap = cfg.get("appearance") or {}
    if int(data.get("version", 0) or 0) < 3 and ap.get("dial_size") == 600:
        ap["dial_size"] = 540                       # the old default: the rotary is a little smaller now
    if int(data.get("version", 0) or 0) < 4:
        cfg["general"]["keep_in_taskbar"] = False   # 2.2.3: closing the window goes to the tray, not the taskbar
    cfg["version"] = CONFIG_VERSION
    return cfg


def _repair_items(items: list) -> None:
    """Fix settings written by older versions."""
    for d in items:
        a = d.get("action") if isinstance(d, dict) else None
        if not isinstance(a, dict):
            continue
        # v1's editor kept the Terminal item's "fall back to cmd.exe" after its app was changed,
        # so e.g. a Steam item would open a command prompt if Steam's shortcut ever went missing.
        if a.get("type") == "app" and a.get("fallback") and \
                os.path.basename(str(a.get("target", ""))).lower() not in ("wt.exe", "wt"):
            a.pop("fallback", None)


def _clean(obj):
    """Drop editor-only keys (leading underscore) before saving."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(_clean(cfg), indent=2)
    # Only this user can read it: it can hold secrets where there's no keyring to put them in (Linux).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if not WIN:
        os.chmod(tmp, 0o600)                   # also when an older version made it readable
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:                # Windows: a virus scanner or the file watcher has it open
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def item_from(d: dict) -> Item | None:
    if not isinstance(d, dict) or not isinstance(d.get("action"), dict):
        return None
    return Item(name=str(d.get("name", "Item")), subtitle=str(d.get("subtitle", "")),
                glyph=str(d.get("glyph", "E8A7")), tint=str(d.get("tint", "#0A84FF")),
                action=dict(d["action"]), icon=str(d.get("icon", "")))


def build_items(cfg: dict) -> list[Item]:
    items = [i for i in (item_from(d) for d in cfg.get("items", [])) if i is not None]
    return items or build_items({"items": DEFAULT_ITEMS})


# --------------------------------------------------------------------------- helpers
def default_browser() -> str | None:
    if not WIN:
        return None              # macOS / Linux: opening the link picks the default browser
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = shlex.split(cmd, posix=False)[0].strip('"')
        return exe if os.path.exists(exe) else None
    except Exception:
        return None


def _resolve(target: str) -> str | None:
    target = os.path.expanduser(os.path.expandvars((target or "").strip().strip('"')))
    if not target:
        return None
    if os.path.exists(target):
        return target
    return shutil.which(target)


# Shell processes own windows that aren't "the app" (the desktop is explorer.exe), so never switch to those.
NO_FOCUS = {"explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "rundll32.exe", "mmc.exe", "conhost.exe"}


def open_target(target: str, args: str = "", cwd: str | None = None) -> None:
    """Open a program, file, folder or link with the system (os.startfile on Windows)."""
    if WIN:
        if args or cwd:
            os.startfile(target, "open", args, cwd)
        else:
            os.startfile(target)
        return
    from . import posix
    posix.open_target(target, args, cwd)


def _launch_app(action: dict) -> None:
    target = str(action.get("target", "")).strip()
    if not target:
        raise FileNotFoundError("no app chosen")
    args = str(action.get("args", ""))
    proc = action.get("process") or ""
    if not proc:
        cand = action.get("exe") or target
        if cand.lower().endswith(".exe"):
            proc = os.path.basename(cand)
    if WIN and action.get("focus", not args) and proc and proc.lower() not in NO_FOCUS:
        if winapi.focus_app(proc):
            return
    low = target.lower()
    if low.startswith("shell:") or "://" in target or low.startswith(("http:", "https:", "mailto:")):
        open_target(target)
        return
    exe = _resolve(target) or _resolve(action.get("fallback", ""))
    if not exe:
        raise FileNotFoundError(target)
    cwd = os.path.dirname(exe) if exe.lower().endswith(".exe") else None   # e.g. OBS needs its own folder
    open_target(exe, args, cwd)       # macOS / Linux: opening a running app switches to it


SCRIPTS = DATA_DIR / "cache" / "scripts"


def python_command(code: str, cfg: dict | None) -> tuple[list[str], Path | None]:
    """How to run a Python snippet: the user's own Python when they have one (their packages),
    otherwise the bundled interpreter. Returns (command, error-file or None)."""
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    f = SCRIPTS / f"action_{uuid.uuid4().hex[:10]}.py"
    f.write_text(code, encoding="utf-8")
    pref = ((cfg or {}).get("general") or {}).get("python", "auto")
    if pref in ("auto", "system"):
        if WIN:
            sys_py = apps.which_cli("py", allow_store_alias=False) or apps.which_cli("python", allow_store_alias=False)
        else:
            from . import posix
            sys_py = posix.platform_python()
        if sys_py:
            return ([sys_py, "-3", str(f)] if sys_py.lower().endswith("py.exe") else [sys_py, str(f)]), None
    err = f.with_suffix(".err")
    if FROZEN:
        return [sys.executable, "--run-python", str(f), "--error-file", str(err)], err
    py = Path(sys.executable)
    py = py.with_name("python.exe") if py.with_name("python.exe").exists() else py   # not pythonw.exe
    return [str(py), str(f)], None


def cleanup_scripts(max_age_s: float = 86400) -> None:
    if not SCRIPTS.exists():
        return
    now = time.time()
    for p in SCRIPTS.iterdir():
        try:
            if now - p.stat().st_mtime > max_age_s:
                p.unlink()
        except OSError:
            pass


def _run_checked(args: list[str], what: str, grace: float = 4.0, err_file: Path | None = None) -> None:
    """Start a process; if it fails quickly, raise with its error output."""
    # Its errors go to a temporary file, not a pipe: a long-running script that writes a lot to stderr would
    # otherwise fill the pipe nobody reads any more, and hang.
    with tempfile.TemporaryFile() as errs:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errs, stdin=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW, cwd=str(Path.home()))
        try:
            rc = proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return  # still running — fine (long scripts)
        if rc == 0:
            return
        err = ""
        if err_file is not None and err_file.exists():
            err = err_file.read_text(encoding="utf-8", errors="replace")
        if not err:
            size = errs.seek(0, os.SEEK_END)
            errs.seek(max(0, size - 256_000))       # the end: that's where the error itself is
            err = errs.read().decode("utf-8", "replace")
    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    raise RuntimeError(f"{what} failed: {lines[-1] if lines else f'exit code {rc}'}")


def execute(action: dict, cfg: dict | None = None) -> None:
    kind = action.get("type", "open")
    log.info("execute %s", {k: (v if len(str(v)) < 120 else str(v)[:117] + "...") for k, v in action.items()})
    if kind == "keys":
        press_keys(action.get("keys", []))
    elif kind in ("browser", "url"):
        url = str(action.get("url", "")).strip()
        if url and "://" not in url and not url.startswith(("about:", "mailto:")):
            url = "https://" + url
        exe = default_browser()
        if exe:
            subprocess.Popen([exe] + ([url] if url else []), close_fds=True)
        else:
            open_target(url or "https://www.google.com")
    elif kind == "app":
        _launch_app(action)
    elif kind == "open":
        open_target(os.path.expanduser(os.path.expandvars(action["target"])))
    elif kind == "window_keys":
        winapi.send_keys_to(action.get("process", ""), action.get("keys", ""), bool(action.get("restore", True)),
                            action.get("title", ""), action.get("label", ""), action.get("bundle", ""))
    elif kind == "obs":
        from . import integrations, obs
        a = integrations.get("obs")
        obs.run(action, cfg, a.exe if a else "")
    elif kind == "shell":
        cmd = action.get("command", "")
        if not WIN:
            from . import posix
            if action.get("hidden", True):
                _run_checked([os.environ.get("SHELL") or "/bin/sh", "-c", cmd], "Command")
            else:
                subprocess.Popen(posix.terminal_command(cmd), start_new_session=True)
        elif action.get("hidden", True):
            _run_checked(["cmd.exe", "/c", cmd], "Command")
        else:
            subprocess.Popen(["cmd.exe", "/k", cmd], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif kind == "powershell":
        enc = base64.b64encode(action.get("script", "").encode("utf-16-le")).decode("ascii")
        if WIN:
            _run_checked(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-EncodedCommand", enc], "PowerShell")
        elif shutil.which("pwsh"):
            _run_checked(["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], "PowerShell")
        else:
            raise FileNotFoundError("PowerShell (pwsh) isn't installed")
    elif kind == "python":
        args, err = python_command(action.get("code", ""), cfg)
        _run_checked(args, "Python", err_file=err)
    elif kind in ("quicknav", "back", "assist"):
        raise ValueError("runs inside RotaryUI")
    else:
        raise ValueError(f"unknown action type '{kind}'")
