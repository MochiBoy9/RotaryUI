"""Popular apps: detection, ready-made actions, and Quick nav rings.

Each catalogue entry knows how to find the app (Start menu, known install paths,
Steam libraries; the Applications folder on macOS, the apps menu on Linux), how to launch
or switch to it, and which actions make sense for it.
Only installed apps are offered. A "Quick nav" item on the rotary opens a second ring
with that app's actions (its games, scenes, recent projects…)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import nturl2path
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import LINUX, MAC, OS_NAME, WIN, apps, obs

log = logging.getLogger(__name__)
QUICKNAV_MAX = 11          # + Back = 12 slots on the ring


def item(name: str, subtitle: str, glyph: str, tint: str, action: dict, icon: str = "") -> dict:
    d = {"name": name, "subtitle": subtitle, "glyph": glyph, "tint": tint, "action": action}
    if icon:
        d["icon"] = icon
    return d


def open_uri(uri: str) -> dict:
    return {"type": "open", "target": uri}


def keys(k: str) -> dict:
    return {"type": "keys", "keys": k}


def web(url: str) -> dict:
    return {"type": "browser", "url": url}


def shortcut(k: str, mac: str | None = None) -> str:
    """An app's shortcut as written for Windows; on a Mac, Ctrl becomes Cmd unless `mac` says otherwise."""
    if not MAC:
        return k
    return mac or "+".join("cmd" if p == "ctrl" else p for p in k.split("+"))


MAC_SYMBOLS = {"cmd": "⌘", "win": "⌘", "ctrl": "⌃", "alt": "⌥", "shift": "⇧", "esc": "⎋", "enter": "↩", "up": "↑",
               "down": "↓", "left": "←", "right": "→", "space": "Space", "tab": "⇥"}


def key_label(k: str) -> str:
    """'ctrl+shift+m' → 'Ctrl+Shift+M' (on a Mac, 'cmd+shift+m' → '⇧⌘M')."""
    parts = k.split("+")
    if MAC:
        order = {"ctrl": 0, "alt": 1, "shift": 2, "cmd": 3, "win": 3}
        mods = sorted((p for p in parts[:-1] if p in order), key=order.get)
        last = parts[-1]
        return "".join(MAC_SYMBOLS[m] for m in mods) + MAC_SYMBOLS.get(last, last.upper())
    names = {"pageup": "PgUp", "pagedown": "PgDn"}
    return "+".join(names.get(p) or (p.upper() if len(p) <= 3 and p not in ("ctrl", "alt", "win") else p.title())
                    for p in parts)


def user_config(*parts: str) -> str:
    """An app's settings folder: %APPDATA%\\… on Windows, ~/Library/Application Support/… on macOS, ~/.config/…"""
    if WIN:
        base = os.environ.get("APPDATA", "")
    elif MAC:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return os.path.join(base, *parts)


@dataclass
class Spec:
    id: str
    name: str
    category: str
    glyph: str
    tint: str
    start: tuple = ()          # Start-menu names
    exes: tuple = ()           # candidate program paths (env vars, * globs)
    process: str = ""          # exe name of its main window (switching / app shortcuts)
    builtin: bool = False      # part of the operating system
    steam_appid: str = ""      # also installable through Steam
    steam_exe: str = ""        # program path inside the Steam install folder
    blurb: str = ""
    actions: Callable | None = None   # (App) -> list[dict]
    dynamic: Callable | None = None   # (App, cfg) -> list[dict]   (may be slow; cached)
    extra: dict = field(default_factory=dict)
    mac: tuple = ()            # its name in the Applications folder, when that differs from `start`
    linux: tuple = ()          # its name in the Linux apps menu, when that differs
    systems: str = "win32 darwin linux"

    @property
    def names(self) -> tuple:
        return self.start + (self.mac if MAC else self.linux if LINUX else ())


@dataclass
class App:
    spec: Spec
    target: str = ""
    exe: str = ""
    installed: bool = False

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def icon(self) -> str:
        return self.exe if self.exe and os.path.exists(self.exe) else ""

    def launch(self, args: str = "") -> dict:
        if args and self.exe:
            return {"type": "app", "target": self.exe, "args": args, "focus": False}
        a = {"type": "app", "target": self.target or self.exe}
        if self.exe:
            a["exe"] = self.exe
        if self.spec.process:
            a["process"] = self.spec.process
        return a

    def keys(self, k: str, restore: bool = True, prefer_title: str = "") -> dict:
        a = {"type": "window_keys", "process": self.spec.process or os.path.basename(self.exe), "keys": k,
             "restore": restore, "label": self.spec.name}
        if prefer_title:
            a["title"] = prefer_title
        if MAC and self.exe.endswith(".app"):
            a["bundle"] = self.exe
        return a

    def it(self, name: str, subtitle: str, glyph: str, action: dict, tint: str | None = None,
           icon: str | None = None) -> dict:
        return item(name, subtitle, glyph, tint or self.spec.tint, action, self.icon if icon is None else icon)

    def open_item(self, subtitle: str = "") -> dict:
        return self.it(f"Open {self.name}", subtitle or "Launch, or switch to it", self.spec.glyph, self.launch())


# =========================================================================== action builders
def _simple(a: App) -> list[dict]:
    return [a.open_item()]


def _steam(a: App) -> list[dict]:
    return [
        a.open_item(),
        a.it("Library", "Your games", "E8F1", open_uri("steam://open/games"), icon=""),
        a.it("Big Picture", "Controller-friendly full screen", "E7F4", open_uri("steam://open/bigpicture"), icon=""),
        a.it("Friends", "Friends & chat", "E716", open_uri("steam://open/friends"), icon=""),
        a.it("Store", "Steam Store", "E719", open_uri("steam://store"), icon=""),
        a.it("Downloads", "Download queue", "E896", open_uri("steam://open/downloads"), icon=""),
    ]


def _steam_games(a: App, cfg: dict) -> list[dict]:
    out = []
    for g in apps.steam_library():
        if g.appid == "1905180":          # OBS Studio has its own entry
            continue
        out.append(item(g.name, "Play", "E7FC", a.spec.tint, open_uri(f"steam://rungameid/{g.appid}"), g.icon))
    return out


def _discord(a: App) -> list[dict]:
    mute, deafen = shortcut("ctrl+shift+m"), shortcut("ctrl+shift+d")
    return [
        a.open_item(),
        a.it("Direct messages", "Friends & DMs", "E8BD", open_uri("discord://-/channels/@me"), icon=""),
        a.it("Mute mic", f"Discord's {key_label(mute)}", "E720", a.keys(mute), icon=""),
        a.it("Deafen", f"Discord's {key_label(deafen)}", "E74F", a.keys(deafen), icon=""),
        a.it("Quick switcher", "Jump to a server or chat", "E721", a.keys(shortcut("ctrl+k"), restore=False), icon=""),
    ]


def _obs(a: App) -> list[dict]:
    o = lambda req, data=None: {"type": "obs", "request": req, **({"data": data} if data else {})}  # noqa: E731
    return [
        a.open_item(),
        a.it("Record", "Start / stop recording", "E7C8", o("ToggleRecord"), tint="#FF453A", icon=""),
        a.it("Pause recording", "Pause / resume", "E769", o("ToggleRecordPause"), icon=""),
        a.it("Stream", "Go live / end stream", "E93E", o("ToggleStream"), tint="#BF5AF2", icon=""),
        a.it("Save replay", "Replay buffer", "E74E", o("SaveReplayBuffer"), icon=""),
        a.it("Virtual camera", "On / off", "E722", o("ToggleVirtualCam"), icon=""),
        a.it("Mute mic", "Your OBS microphone", "E720", o("ToggleMic"), icon=""),
    ]


def _obs_scenes(a: App, cfg: dict) -> list[dict]:
    return [a.it(name, "Switch scene", "E7F4", {"type": "obs", "request": "SetCurrentProgramScene",
                                                 "data": {"sceneName": name}}, icon="")
            for name in obs.scenes(cfg)]


def uri_to_path(uri: str) -> str:
    """file:///c%3A/Users/x → c:\\Users\\x ('' for remote / non-file URIs)."""
    if not uri.startswith("file://"):
        return ""
    to_path = nturl2path.url2pathname if os.name == "nt" else urllib.request.url2pathname
    path = to_path(urllib.parse.unquote(urllib.parse.urlparse(uri).path))
    if os.name == "nt" and path.startswith("\\") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return path


def _recent_from_history(storage: Path) -> list[tuple[str, str]]:
    db = storage / "state.vscdb"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
        try:
            row = con.execute("SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'").fetchone()
        finally:
            con.close()
        entries = json.loads(row[0]).get("entries", []) if row else []
    except (sqlite3.Error, ValueError, AttributeError):
        return []
    out = []
    for e in entries:
        if "folderUri" in e:
            out.append((e["folderUri"], "folder"))
        elif isinstance(e.get("workspace"), dict):
            out.append((e["workspace"].get("configPath", ""), "workspace"))
    return out


def _recent_from_workspace_storage(storage: Path) -> list[tuple[str, str]]:
    """Every project VS Code has opened has a workspaceStorage folder that's touched on use."""
    ws = storage.parent / "workspaceStorage"
    if not ws.is_dir():
        return []
    found = []
    for d in ws.iterdir():
        meta = d / "workspace.json"
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            used = max((d / "state.vscdb").stat().st_mtime if (d / "state.vscdb").exists() else 0,
                       meta.stat().st_mtime)
        except (OSError, ValueError):
            continue
        if info.get("folder"):
            found.append((used, info["folder"], "folder"))
        elif info.get("workspace"):
            found.append((used, info["workspace"], "workspace"))
    return [(uri, kind) for _, uri, kind in sorted(found, reverse=True)]


def _recent_from_storage_json(storage: Path) -> list[tuple[str, str]]:
    try:
        d = json.loads((storage / "storage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = [(u, "folder") for u in (d.get("profileAssociations") or {}).get("workspaces", {})]
    out += [(f.get("folderUri", ""), "folder") for f in (d.get("backupWorkspaces") or {}).get("folders", [])]
    return out


def vscode_recents(storage: Path, limit: int = 8) -> list[tuple[str, str]]:
    """(path, kind) of recently opened folders/workspaces for VS Code-style editors (VS Code, Cursor…).
    `storage` is the editor's User\\globalStorage folder."""
    out, seen = [], set()
    for uri, kind in (_recent_from_history(storage) + _recent_from_workspace_storage(storage)
                      + _recent_from_storage_json(storage)):
        path = uri_to_path(uri)
        key = path.lower().rstrip("\\/")
        if not path or key in seen or not os.path.exists(path):
            continue
        seen.add(key)
        out.append((path, kind))
        if len(out) >= limit:
            break
    return out


def _code_like(a: App) -> list[dict]:
    palette, term = shortcut("ctrl+shift+p"), shortcut("ctrl+`", "ctrl+`")
    return [a.open_item(), a.it("New window", "Empty window", "E8A7", a.launch("--new-window"), icon=""),
            a.it("Command palette", key_label(palette), "E721", a.keys(palette, restore=False), icon=""),
            a.it("Terminal", f"Show / hide · {key_label(term)}", "E756", a.keys(term, restore=False), icon="")]


def _code_recents(a: App, cfg: dict) -> list[dict]:
    storage = Path(os.path.expandvars(a.spec.extra.get("storage", "")))
    out = []
    for path, kind in vscode_recents(storage):
        label = os.path.basename(path.rstrip("\\/")) or path
        if kind == "workspace":
            label = os.path.splitext(label)[0]
        out.append(a.it(label, os.path.dirname(path), "E8B7", {"type": "app", "target": a.exe,
                                                               "args": f'"{path}"', "focus": False}, icon=""))
    return out


def _claude(a: App) -> list[dict]:
    out = [a.open_item(), a.it("New chat", "claude.ai in your browser", "E8BD", web("https://claude.ai/new"), icon="")]
    if WIN:
        cli = apps.which_cli("claude") or apps.first_existing(r"%USERPROFILE%\.local\bin\claude.exe")
        if cli:
            term = "wt.exe" if apps.which_cli("wt") else "cmd /k"
            out.append(a.it("Claude Code", "In a new terminal", "E756",
                            {"type": "shell", "command": f'start "" {term} claude', "hidden": True}, icon=""))
    else:
        cli = apps.which_cli("claude") or apps.first_existing("~/.local/bin/claude", "/opt/homebrew/bin/claude",
                                                              "/usr/local/bin/claude")
        if cli:
            out.append(a.it("Claude Code", "In a new terminal", "E756",
                            {"type": "shell", "command": f'"{cli}"', "hidden": False}, icon=""))
    return out


def _chatgpt(a: App) -> list[dict]:
    return [a.open_item(), a.it("New chat", "chatgpt.com in your browser", "E8BD", web("https://chatgpt.com/"), icon="")]


def _browser(a: App) -> list[dict]:
    new_flag = a.spec.extra.get("new", "--new-window")
    private = a.spec.extra.get("private", "--incognito")
    return [
        a.open_item(),
        a.it("New window", "", "E8A7", a.launch(new_flag), icon=""),
        a.it("Private window", "Nothing saved", "E72E", a.launch(private), icon=""),
        a.it("Reopen tab", "Bring back the last closed tab", "E7A7", a.keys(shortcut("ctrl+shift+t"), restore=False),
             icon=""),
    ]


def _media(a: App) -> list[dict]:
    out = [a.open_item()]
    out += [a.it("Play / Pause", "", "E768", keys("media_play_pause"), icon=""),
            a.it("Next", "", "E893", keys("media_next"), icon=""),
            a.it("Previous", "", "E892", keys("media_prev"), icon="")]
    if a.spec.id == "spotify":
        out.append(a.it("Search", "Find music", "E721", a.keys(shortcut("ctrl+l"), restore=False), icon=""))
    return out


def _zoom(a: App) -> list[dict]:
    mute, video = shortcut("alt+a", "cmd+shift+a"), shortcut("alt+v", "cmd+shift+v")
    return [a.open_item(),
            a.it("Mute / unmute", f"Zoom's {key_label(mute)}", "E720", a.keys(mute, prefer_title="Meeting"), icon=""),
            a.it("Video on / off", f"Zoom's {key_label(video)}", "E714", a.keys(video, prefer_title="Meeting"),
                 icon="")]


def _teams(a: App) -> list[dict]:
    mute, cam = shortcut("ctrl+shift+m"), shortcut("ctrl+shift+o")
    return [a.open_item(),
            a.it("Mute / unmute", f"Teams' {key_label(mute)}", "E720", a.keys(mute), icon=""),
            a.it("Camera on / off", f"Teams' {key_label(cam)}", "E714", a.keys(cam), icon="")]


def _jump(label: str, k: str) -> Callable:
    def build(a: App) -> list[dict]:
        sk = shortcut(k)
        sub = f"Shortcut {key_label(sk)}" if MAC else f"Shortcut {k.replace('+', ' + ').title()}"
        return [a.open_item(), a.it(label, sub, "E721", a.keys(sk, restore=False), icon="")]
    return build


def _keyset(*rows: tuple) -> Callable:
    """Open, then one item per (name, glyph, shortcut[, Mac shortcut]) pressed inside the app, which stays in
    front: these are for while you work in it (CAD views, run / debug, upload…)."""
    def build(a: App) -> list[dict]:
        out = [a.open_item()]
        for name, glyph, k, *mac in rows:
            sk = shortcut(k, mac[0] if mac else None)
            out.append(a.it(name, f"{a.name}'s {key_label(sk)}", glyph, a.keys(sk, restore=False), icon=""))
        return out
    return build


# SOLIDWORKS' default shortcuts (Tools → Customize → Keyboard).
_solidworks = _keyset(("Rebuild", "E72C", "ctrl+b"), ("Zoom to fit", "E80F", "f"),
                      ("View orientation", "E7C4", "space"), ("Isometric", "E7C4", "ctrl+7"),
                      ("Front view", "E7C4", "ctrl+1"), ("Top view", "E7C4", "ctrl+5"),
                      ("Normal to", "E7C4", "ctrl+8"), ("Save", "E74E", "ctrl+s"))
_fusion = _keyset(("Search commands", "E721", "s"), ("Fit view", "E80F", "f6"), ("Measure", "E8EF", "i"),
                  ("Extrude", "E70F", "e"), ("Sketch dimension", "E70F", "d"))
_autocad = _keyset(("Object snap", "E8AB", "f3"), ("Grid", "E8AB", "f7"), ("Ortho", "E8AB", "f8"),
                   ("Polar tracking", "E8AB", "f10"), ("Dynamic input", "E8AB", "f12"))
_inventor = _keyset(("Home view", "E80F", "f6"), ("Zoom all", "E7C4", "home"), ("Look at", "E7C4", "pageup"),
                    ("Previous view", "E72B", "f5"))
_jetbrains = _keyset(("Run", "E768", "shift+f10", "ctrl+r"), ("Debug", "E90F", "shift+f9", "ctrl+d"),
                     ("Stop", "E71A", "ctrl+f2"), ("Find action", "E721", "ctrl+shift+a"))
_arduino = _keyset(("Verify", "E73E", "ctrl+r"), ("Upload", "E945", "ctrl+u"),
                   ("Serial monitor", "E756", "ctrl+shift+m"))
_matlab = _keyset(("Run", "E768", "f5"))
_wireshark = _keyset(("Start / stop capture", "E7C8", "ctrl+e"), ("Find packet", "E721", "ctrl+f"))
_blender = _keyset(("Render image", "E722", "f12"), ("Play animation", "E768", "space"), ("Save", "E74E", "ctrl+s"))
_editor = _keyset(("Play / Pause", "E768", "space"), ("Export", "E74E", "ctrl+m"))
_audacity = _keyset(("Record", "E7C8", "r"), ("Play / Stop", "E768", "space"))
_unreal = _keyset(("Play in editor", "E768", "alt+p"), ("Save all", "E74E", "ctrl+shift+s"))


def _medal(a: App) -> list[dict]:
    """Medal listens for its clip key everywhere, so it's pressed where you are (in the game), not in Medal."""
    from .live import _medal_folder
    return [a.open_item(),
            a.it("Clip that", "Medal's F8 (its default clip key)", "E7C8", keys("f8"), tint="#FF453A", icon=""),
            a.it("Your clips", "Medal's clip folder", "E8B7", open_uri(str(_medal_folder())), icon="")]


def _nvidia(a: App) -> list[dict]:
    t = a.spec.tint
    return [a.open_item(),
            item("Save replay", "Instant Replay · Alt+Shift+F10", "E74E", t, keys("alt+shift+f10")),
            item("Record", "Start / stop · Alt+F9", "E7C8", t, keys("alt+f9")),
            item("Screenshot", "Alt+F1", "E722", t, keys("alt+f1")),
            item("Overlay", "Alt+Z", "E7FC", t, keys("alt+z"))]


def _roblox_studio(a: App) -> list[dict]:
    play, stop = ("f5", "shift+f5") if not MAC else ("cmd+p", "shift+f5")
    return [a.open_item(),
            a.it("Play test", key_label(play) if MAC else "F5", "E768", a.keys(play, restore=False), icon=""),
            a.it("Stop", key_label(stop) if MAC else "Shift + F5", "E71A", a.keys(stop, restore=False), icon="")]


def _windows(a: App) -> list[dict]:
    t = a.spec.tint
    return [
        item("Lock", "Lock your PC", "E72E", t, {"type": "shell", "command": "rundll32.exe user32.dll,LockWorkStation"}),
        item("Desktop", "Show desktop", "E7F4", t, keys("win+d")),
        item("Task view", "All your windows", "E7C4", t, keys("win+tab")),
        item("Snip", "Screenshot a region", "E722", t, keys("win+shift+s")),
        item("Clipboard", "Clipboard history", "E77F", t, keys("win+v")),
        item("Emoji", "Emoji & symbols", "E76E", t, keys("win+.")),
        item("Quick settings", "Wi-Fi, volume, brightness", "E713", t, keys("win+a")),
        item("Task Manager", "", "E9D9", t, keys("ctrl+shift+esc")),
        item("File Explorer", "", "E8B7", t, {"type": "app", "target": "explorer.exe"}),
        item("Settings", "Windows Settings", "E713", t, open_uri("ms-settings:")),
        item("Mute", "Sound on / off", "E74F", t, keys("volume_mute")),
    ]


def _macos(a: App) -> list[dict]:
    t = a.spec.tint
    return [
        item("Lock", "Lock your Mac", "E72E", t, keys("ctrl+cmd+q")),
        item("Mission Control", "All your windows", "E7C4", t, keys("ctrl+up")),
        item("Spotlight", "Search your Mac", "E721", t, keys("cmd+space")),
        item("Screenshot", "Capture the screen", "E722", t, keys("cmd+shift+5")),
        item("Emoji", "Emoji & symbols", "E76E", t, keys("ctrl+cmd+space")),
        item("Launchpad", "All your apps", "F0E2", t, {"type": "app", "target": "/System/Applications/Launchpad.app"}),
        item("Finder", "", "E8B7", t, {"type": "app", "target": "/System/Library/CoreServices/Finder.app"}),
        item("Activity Monitor", "", "E9D9", t,
             {"type": "app", "target": "/System/Applications/Utilities/Activity Monitor.app"}),
        item("Settings", "System Settings", "E713", t, open_uri("x-apple.systempreferences:")),
        item("Mute", "Sound on / off", "E74F", t, keys("volume_mute")),
        item("Sleep", "Put your Mac to sleep", "E708", t, {"type": "shell", "command": "pmset sleepnow"}),
    ]


def _linux(a: App) -> list[dict]:
    t = a.spec.tint
    return [
        item("Lock", "Lock the screen", "E72E", t, {"type": "shell", "command": "loginctl lock-session"}),
        item("Overview", "All your windows", "E7C4", t, keys("win")),
        item("Screenshot", "Capture the screen", "E722", t, keys("printscreen")),
        item("Files", "Your home folder", "E8B7", t, open_uri("~")),
        item("Terminal", "Command line", "E756", t,
             {"type": "app", "target": "x-terminal-emulator", "fallback": "gnome-terminal"}),
        item("Settings", "System settings", "E713", t,
             {"type": "app", "target": "gnome-control-center", "fallback": "systemsettings"}),
        item("Mute", "Sound on / off", "E74F", t, keys("volume_mute")),
    ]


def _gamebar(a: App) -> list[dict]:
    t = a.spec.tint
    return [
        item("Game Bar", "Open the overlay", "E7FC", t, keys("win+g")),
        item("Record that", "Save the last 30 seconds", "E7C8", t, keys("win+alt+g")),
        item("Record", "Start / stop recording", "E714", t, keys("win+alt+r")),
        item("Screenshot", "Capture the game", "E722", t, keys("win+alt+printscreen")),
        item("Mic on / off", "While recording", "E720", t, keys("win+alt+m")),
    ]


def _media_keys(a: App) -> list[dict]:
    t = a.spec.tint
    return [item("Play / Pause", "", "E768", t, keys("media_play_pause")),
            item("Next", "", "E893", t, keys("media_next")),
            item("Previous", "", "E892", t, keys("media_prev")),
            item("Volume up", "", "E995", t, keys("volume_up")),
            item("Volume down", "", "E993", t, keys("volume_down")),
            item("Mute", "", "E74F", t, keys("volume_mute"))]


# =========================================================================== catalogue
PF, PF86, LAD, AD = r"%ProgramFiles%", r"%ProgramFiles(x86)%", r"%LOCALAPPDATA%", r"%APPDATA%"

_ALL: list[Spec] = [
    # ---- gaming
    Spec("steam", "Steam", "Gaming", "E7FC", "#3D8BD9", ("Steam",), (rf"{PF86}\Steam\steam.exe",),
         blurb="Your games (most recently played first), library, Big Picture, friends, store.",
         actions=_steam, dynamic=_steam_games),
    Spec("epic", "Epic Games", "Gaming", "E7FC", "#7A7A80", ("Epic Games Launcher",),
         (rf"{PF86}\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",), "EpicGamesLauncher.exe",
         actions=_simple),
    Spec("battlenet", "Battle.net", "Gaming", "E7FC", "#148EFF", ("Battle.net",),
         (rf"{PF86}\Battle.net\Battle.net Launcher.exe",), "Battle.net.exe", actions=_simple),
    Spec("ea", "EA app", "Gaming", "E7FC", "#FF4747", ("EA",),
         (rf"{PF}\Electronic Arts\EA Desktop\EA Desktop\EADesktop.exe",), "EADesktop.exe", actions=_simple,
         mac=("EA app",)),
    Spec("ubisoft", "Ubisoft Connect", "Gaming", "E7FC", "#2A7DFF", ("Ubisoft Connect",),
         (rf"{PF86}\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe",), "UbisoftConnect.exe", actions=_simple),
    Spec("gog", "GOG GALAXY", "Gaming", "E7FC", "#8A4BCF", ("GOG GALAXY",),
         (rf"{PF86}\GOG Galaxy\GalaxyClient.exe",), "GalaxyClient.exe", actions=_simple),
    Spec("xbox", "Xbox", "Gaming", "E7FC", "#107C10", ("Xbox",), actions=_simple, systems="win32"),
    Spec("minecraft", "Minecraft", "Gaming", "E7FC", "#5BA33B", ("Minecraft Launcher", "Minecraft"), actions=_simple),
    Spec("roblox", "Roblox", "Gaming", "E7FC", "#E2231A", ("Roblox Player", "Roblox"),
         (rf"{LAD}\Roblox\Versions\*\RobloxPlayerBeta.exe",), "RobloxPlayerBeta.exe", actions=_simple),
    Spec("roblox_studio", "Roblox Studio", "Creative", "E70F", "#00A2FF", ("Roblox Studio",),
         (rf"{LAD}\Roblox\Versions\*\RobloxStudioBeta.exe",), "RobloxStudioBeta.exe",
         blurb="Open Studio, play-test and stop.", actions=_roblox_studio, mac=("RobloxStudio",)),
    Spec("medal", "Medal", "Gaming", "E7C8", "#FFB800", ("Medal",),
         (rf"{LAD}\Medal\app-*\Medal.exe", rf"{LAD}\Medal\Medal.exe"), "Medal.exe",
         blurb="Clip the last moments of a game (Medal's clip key) and open your clips.", actions=_medal,
         systems="win32"),
    Spec("nvidia", "NVIDIA app", "Gaming", "E7FC", "#76B900", ("NVIDIA app", "GeForce Experience"),
         blurb="Save an Instant Replay, record, screenshot and the overlay (its default shortcuts).",
         actions=_nvidia, systems="win32"),
    Spec("gamebar", "Xbox Game Bar", "Gaming", "E7FC", "#107C10", builtin=True,
         blurb="Record clips, screenshots and the Game Bar overlay.", actions=_gamebar, systems="win32"),
    # ---- creative & streaming
    Spec("obs", "OBS Studio", "Creative", "E714", "#7B61FF", ("OBS Studio",),
         (rf"{PF}\obs-studio\bin\64bit\obs64.exe",), "obs64.exe", steam_appid="1905180",
         steam_exe=r"bin\64bit\obs64.exe",
         blurb="Record, stream, replay, virtual camera, mic and every scene. Works without setup when OBS "
               "is closed; turn on Tools → WebSocket Server Settings for control while it's open.",
         actions=_obs, dynamic=_obs_scenes, mac=("OBS",)),
    Spec("blender", "Blender", "Creative", "E70F", "#F5792A", ("Blender",),
         (rf"{PF}\Blender Foundation\Blender*\blender.exe",), "blender.exe", actions=_blender),
    Spec("photoshop", "Photoshop", "Creative", "E70F", "#31A8FF", ("Adobe Photoshop",),
         (rf"{PF}\Adobe\Adobe Photoshop*\Photoshop.exe",), "Photoshop.exe", actions=_simple),
    Spec("illustrator", "Illustrator", "Creative", "E70F", "#FF9A00", ("Adobe Illustrator",),
         (rf"{PF}\Adobe\Adobe Illustrator*\Support Files\Contents\Windows\Illustrator.exe",), "Illustrator.exe",
         actions=_simple),
    Spec("after_effects", "After Effects", "Creative", "E714", "#9999FF", ("Adobe After Effects",),
         (rf"{PF}\Adobe\Adobe After Effects*\Support Files\AfterFX.exe",), "AfterFX.exe", actions=_simple),
    Spec("premiere", "Premiere Pro", "Creative", "E714", "#9999FF", ("Adobe Premiere Pro",),
         (rf"{PF}\Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe",), "Adobe Premiere Pro.exe", actions=_editor),
    Spec("resolve", "DaVinci Resolve", "Creative", "E714", "#E0533E", ("DaVinci Resolve",),
         (rf"{PF}\Blackmagic Design\DaVinci Resolve\Resolve.exe",), "Resolve.exe",
         actions=_keyset(("Play / Pause", "E768", "space"))),
    Spec("capcut", "CapCut", "Creative", "E714", "#6E6E78", ("CapCut",), (rf"{LAD}\CapCut\Apps\CapCut.exe",),
         "CapCut.exe", actions=_simple),
    Spec("gimp", "GIMP", "Creative", "E70F", "#7D6F5A", ("GIMP",), (rf"{PF}\GIMP *\bin\gimp-*.exe",),
         actions=_simple),
    Spec("krita", "Krita", "Creative", "E70F", "#3BABFF", ("Krita",), (rf"{PF}\Krita (x64)\bin\krita.exe",),
         "krita.exe", actions=_simple),
    Spec("audacity", "Audacity", "Creative", "E720", "#0000CC", ("Audacity",), (rf"{PF}\Audacity\Audacity.exe",),
         "Audacity.exe", actions=_audacity),
    Spec("unreal", "Unreal Engine", "Creative", "E7FC", "#6E6E78", ("Unreal Engine",),
         (rf"{PF}\Epic Games\UE_*\Engine\Binaries\Win64\UnrealEditor.exe",), "UnrealEditor.exe",
         actions=_unreal, systems="win32"),
    Spec("figma", "Figma", "Creative", "E70F", "#A259FF", ("Figma",), (rf"{LAD}\Figma\Figma.exe",), "Figma.exe",
         actions=_simple),
    Spec("unity_hub", "Unity Hub", "Creative", "E7FC", "#6E6E78", ("Unity Hub",),
         (rf"{PF}\Unity Hub\Unity Hub.exe",), "Unity Hub.exe", actions=_simple),
    # ---- chat & calls
    Spec("discord", "Discord", "Chat", "E8BD", "#5865F2", ("Discord",), (rf"{LAD}\Discord\app-*\Discord.exe",),
         "Discord.exe", blurb="Open, DMs, mute and deafen (Discord's own shortcuts), quick switcher.",
         actions=_discord),
    Spec("slack", "Slack", "Chat", "E8BD", "#9B3FA1", ("Slack",), (rf"{LAD}\slack\slack.exe", rf"{PF}\Slack\slack.exe"),
         "slack.exe", actions=_jump("Jump to", "ctrl+k")),
    Spec("teams", "Microsoft Teams", "Chat", "E716", "#5B5FC7", ("Microsoft Teams", "Microsoft Teams (work or school)"),
         (), "ms-teams.exe", actions=_teams),
    Spec("zoom", "Zoom", "Chat", "E714", "#0B5CFF", ("Zoom Workplace", "Zoom"), (rf"{AD}\Zoom\bin\Zoom.exe",),
         "Zoom.exe", actions=_zoom, mac=("zoom.us",)),
    Spec("whatsapp", "WhatsApp", "Chat", "E8BD", "#25D366", ("WhatsApp",), (), "WhatsApp.exe", actions=_simple),
    Spec("telegram", "Telegram", "Chat", "E8BD", "#2AABEE", ("Telegram Desktop", "Telegram"),
         (rf"{AD}\Telegram Desktop\Telegram.exe",), "Telegram.exe", actions=_simple),
    Spec("signal", "Signal", "Chat", "E8BD", "#3A76F0", ("Signal",), (rf"{LAD}\Programs\signal-desktop\Signal.exe",),
         "Signal.exe", actions=_simple),
    # ---- code & AI
    Spec("vscode", "Visual Studio Code", "Code & AI", "E943", "#0078D4", ("Visual Studio Code",),
         (rf"{LAD}\Programs\Microsoft VS Code\Code.exe", rf"{PF}\Microsoft VS Code\Code.exe"), "Code.exe",
         blurb="Open, new window, and your recent projects.", actions=_code_like, dynamic=_code_recents,
         extra={"storage": user_config("Code", "User", "globalStorage")}),
    Spec("cursor", "Cursor", "Code & AI", "E943", "#6B6B75", ("Cursor",), (rf"{LAD}\Programs\cursor\Cursor.exe",),
         "Cursor.exe", actions=_code_like, dynamic=_code_recents,
         extra={"storage": user_config("Cursor", "User", "globalStorage")}),
    Spec("claude", "Claude", "Code & AI", "E8BD", "#D97757", ("Claude",), (rf"{LAD}\AnthropicClaude\claude.exe",),
         "claude.exe", blurb="The Claude app, a new chat, and Claude Code in a terminal.", actions=_claude),
    Spec("chatgpt", "ChatGPT", "Code & AI", "E8BD", "#10A37F", ("ChatGPT",), (), "ChatGPT.exe", actions=_chatgpt),
    Spec("visual_studio", "Visual Studio", "Code & AI", "E943", "#865FCF", ("Visual Studio 2022", "Visual Studio"),
         (rf"{PF}\Microsoft Visual Studio\2022\*\Common7\IDE\devenv.exe",), "devenv.exe", actions=_simple,
         systems="win32"),
    Spec("github_desktop", "GitHub Desktop", "Code & AI", "E943", "#6E40C9", ("GitHub Desktop",),
         (rf"{LAD}\GitHubDesktop\GitHubDesktop.exe",), "GitHubDesktop.exe", actions=_simple),
    Spec("terminal", "Terminal", "Code & AI", "E756", "#30D158", ("Terminal", "Windows Terminal"), (),
         "WindowsTerminal.exe", actions=_simple),
    Spec("windsurf", "Windsurf", "Code & AI", "E943", "#0B9D8C", ("Windsurf",),
         (rf"{LAD}\Programs\Windsurf\Windsurf.exe",), "Windsurf.exe", actions=_code_like, dynamic=_code_recents,
         extra={"storage": user_config("Windsurf", "User", "globalStorage")}),
    Spec("intellij", "IntelliJ IDEA", "Code & AI", "E943", "#FE315D",
         ("IntelliJ IDEA", "IntelliJ IDEA Ultimate", "IntelliJ IDEA Community Edition"),
         (rf"{PF}\JetBrains\IntelliJ IDEA*\bin\idea64.exe",), "idea64.exe",
         blurb="Run, debug, stop and Find action.", actions=_jetbrains, mac=("IntelliJ IDEA CE",)),
    Spec("pycharm", "PyCharm", "Code & AI", "E943", "#21D789",
         ("PyCharm", "PyCharm Community Edition", "PyCharm Professional Edition"),
         (rf"{PF}\JetBrains\PyCharm*\bin\pycharm64.exe",), "pycharm64.exe",
         blurb="Run, debug, stop and Find action.", actions=_jetbrains, mac=("PyCharm CE",)),
    Spec("android_studio", "Android Studio", "Code & AI", "E943", "#3DDC84", ("Android Studio",),
         (rf"{PF}\Android\Android Studio\bin\studio64.exe",), "studio64.exe", actions=_jetbrains),
    Spec("postman", "Postman", "Code & AI", "E909", "#FF6C37", ("Postman",), (rf"{LAD}\Postman\Postman.exe",),
         "Postman.exe", actions=_simple),
    Spec("wireshark", "Wireshark", "Code & AI", "E909", "#1679A7", ("Wireshark",),
         (rf"{PF}\Wireshark\Wireshark.exe",), "Wireshark.exe", actions=_wireshark),
    Spec("docker", "Docker Desktop", "Code & AI", "E943", "#1D63ED", ("Docker Desktop",),
         (rf"{PF}\Docker\Docker\Docker Desktop.exe",), "Docker Desktop.exe", actions=_simple, mac=("Docker",)),
    # ---- engineering: CAD, electronics, maths
    Spec("solidworks", "SOLIDWORKS", "Engineering", "E70F", "#E2231A", ("SOLIDWORKS",),
         (rf"{PF}\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe",), "SLDWORKS.exe",
         blurb="Rebuild, zoom to fit, the view orientation menu, standard views and save (its default shortcuts).",
         actions=_solidworks, systems="win32"),
    Spec("fusion", "Autodesk Fusion", "Engineering", "E70F", "#F37021",
         ("Autodesk Fusion", "Autodesk Fusion 360", "Fusion 360"),
         (rf"{LAD}\Autodesk\webdeploy\production\*\Fusion360.exe",), "Fusion360.exe",
         blurb="Command search, fit view, measure, extrude and sketch dimension.", actions=_fusion,
         systems="win32 darwin"),
    Spec("autocad", "AutoCAD", "Engineering", "E70F", "#E51050", ("AutoCAD",),
         (rf"{PF}\Autodesk\AutoCAD *\acad.exe",), "acad.exe",
         blurb="Object snap, grid, ortho, polar tracking and dynamic input on / off.", actions=_autocad,
         systems="win32"),
    Spec("inventor", "Inventor", "Engineering", "E70F", "#F0A30A",
         ("Autodesk Inventor Professional", "Autodesk Inventor"), (rf"{PF}\Autodesk\Inventor *\Bin\Inventor.exe",),
         "Inventor.exe", actions=_inventor, systems="win32"),
    Spec("revit", "Revit", "Engineering", "E70F", "#186BFF", ("Revit", "Autodesk Revit"),
         (rf"{PF}\Autodesk\Revit *\Revit.exe",), "Revit.exe", actions=_simple, systems="win32"),
    Spec("rhino", "Rhino", "Engineering", "E70F", "#801010", ("Rhino", "Rhinoceros"),
         (rf"{PF}\Rhino *\System\Rhino.exe",), "Rhino.exe", actions=_simple, mac=("Rhino 8", "Rhino 7")),
    Spec("sketchup", "SketchUp", "Engineering", "E70F", "#005F9E", ("SketchUp",),
         (rf"{PF}\SketchUp\SketchUp *\SketchUp.exe",), "SketchUp.exe", actions=_simple),
    Spec("freecad", "FreeCAD", "Engineering", "E70F", "#CB333B", ("FreeCAD",), (rf"{PF}\FreeCAD *\bin\freecad.exe",),
         "freecad.exe", actions=_simple),
    Spec("kicad", "KiCad", "Engineering", "E945", "#314CB0", ("KiCad",), (rf"{PF}\KiCad\*\bin\kicad.exe",),
         "kicad.exe", actions=_simple),
    Spec("altium", "Altium Designer", "Engineering", "E945", "#A5915F", ("Altium Designer",),
         (rf"{PF}\Altium\AD*\X2.EXE",), "X2.EXE", actions=_simple, systems="win32"),
    Spec("ltspice", "LTspice", "Engineering", "E945", "#1E5AA8", ("LTspice", "LTspice XVII"),
         (rf"{LAD}\Programs\ADI\LTspice\LTspice.exe", rf"{PF}\ADI\LTspice\LTspice.exe"), "LTspice.exe",
         actions=_simple),
    Spec("arduino", "Arduino IDE", "Engineering", "E945", "#00979D", ("Arduino IDE",),
         (rf"{LAD}\Programs\Arduino IDE\Arduino IDE.exe", rf"{PF}\Arduino IDE\Arduino IDE.exe"),
         "Arduino IDE.exe", blurb="Verify, upload and the serial monitor.", actions=_arduino),
    Spec("matlab", "MATLAB", "Engineering", "E8EF", "#E16737", ("MATLAB",), (rf"{PF}\MATLAB\R*\bin\matlab.exe",),
         "MATLAB.exe", blurb="Run the script in the editor.", actions=_matlab),
    # ---- browsers
    Spec("chrome", "Google Chrome", "Browsers", "E774", "#4285F4", ("Google Chrome",),
         (rf"{PF}\Google\Chrome\Application\chrome.exe", rf"{PF86}\Google\Chrome\Application\chrome.exe",
          rf"{LAD}\Google\Chrome\Application\chrome.exe"), "chrome.exe", actions=_browser),
    Spec("edge", "Microsoft Edge", "Browsers", "E774", "#0C8ADB", ("Microsoft Edge",),
         (rf"{PF86}\Microsoft\Edge\Application\msedge.exe", rf"{PF}\Microsoft\Edge\Application\msedge.exe"),
         "msedge.exe", actions=_browser, extra={"private": "--inprivate"}),
    Spec("firefox", "Firefox", "Browsers", "E774", "#FF7139", ("Firefox", "Mozilla Firefox"),
         (rf"{PF}\Mozilla Firefox\firefox.exe",), "firefox.exe", actions=_browser,
         extra={"private": "-private-window", "new": "-new-window"}, linux=("Firefox Web Browser",)),
    Spec("brave", "Brave", "Browsers", "E774", "#FB542B", ("Brave",),
         (rf"{PF}\BraveSoftware\Brave-Browser\Application\brave.exe",
          rf"{LAD}\BraveSoftware\Brave-Browser\Application\brave.exe"), "brave.exe", actions=_browser,
         mac=("Brave Browser",), linux=("Brave Web Browser",)),
    Spec("opera_gx", "Opera GX", "Browsers", "E774", "#FA1E4E", ("Opera GX Browser", "Opera GX"),
         (rf"{LAD}\Programs\Opera GX\opera.exe",), "opera.exe", actions=_browser, extra={"private": "--private"}),
    Spec("vivaldi", "Vivaldi", "Browsers", "E774", "#EF3939", ("Vivaldi",), (rf"{LAD}\Vivaldi\Application\vivaldi.exe",),
         "vivaldi.exe", actions=_browser),
    # ---- media
    Spec("spotify", "Spotify", "Media", "E8D6", "#1DB954", ("Spotify",), (rf"{AD}\Spotify\Spotify.exe",),
         "Spotify.exe", blurb="Open, play/pause, next, previous and search.", actions=_media),
    Spec("apple_music", "Apple Music", "Media", "E8D6", "#FA2D48", ("Apple Music",), (), "AppleMusic.exe",
         actions=_media, mac=("Music",), systems="win32 darwin"),
    Spec("vlc", "VLC", "Media", "E714", "#FF8800", ("VLC media player",), (rf"{PF}\VideoLAN\VLC\vlc.exe",), "vlc.exe",
         actions=_media, mac=("VLC",)),
    Spec("netflix", "Netflix", "Media", "E714", "#E50914", ("Netflix",), actions=_simple, systems="win32"),
    # ---- productivity
    Spec("notion", "Notion", "Productivity", "E8A5", "#5F5F66", ("Notion",), (rf"{LAD}\Programs\Notion\Notion.exe",),
         "Notion.exe", actions=_jump("Search", "ctrl+p")),
    Spec("obsidian", "Obsidian", "Productivity", "E8A5", "#7C3AED", ("Obsidian",),
         (rf"{LAD}\Programs\Obsidian\Obsidian.exe", rf"{LAD}\Obsidian\Obsidian.exe"), "Obsidian.exe",
         actions=_jump("Quick switcher", "ctrl+o")),
    Spec("word", "Word", "Productivity", "E8A5", "#2B579A", ("Word",), (), "WINWORD.EXE", actions=_simple,
         mac=("Microsoft Word",)),
    Spec("excel", "Excel", "Productivity", "E8A5", "#217346", ("Excel",), (), "EXCEL.EXE", actions=_simple,
         mac=("Microsoft Excel",)),
    Spec("powerpoint", "PowerPoint", "Productivity", "E8A5", "#D24726", ("PowerPoint",), (), "POWERPNT.EXE",
         actions=_simple, mac=("Microsoft PowerPoint",)),
    Spec("onenote", "OneNote", "Productivity", "E8A5", "#7719AA", ("OneNote",), (), "ONENOTE.EXE", actions=_simple,
         mac=("Microsoft OneNote",)),
    Spec("zotero", "Zotero", "Productivity", "E8A5", "#CC2936", ("Zotero",), (rf"{PF}\Zotero\zotero.exe",),
         "zotero.exe", actions=_simple),
    Spec("outlook", "Outlook", "Productivity", "E715", "#0078D4", ("Outlook", "Outlook (new)"), (), "olk.exe",
         actions=_simple, mac=("Microsoft Outlook",)),
    # ---- the operating system
    Spec("windows", "Windows", OS_NAME, "E713", "#8E8E93", builtin=True,
         blurb="Lock, desktop, task view, snip, clipboard, emoji, quick settings and more.", actions=_windows,
         systems="win32"),
    Spec("macos", "macOS", OS_NAME, "E713", "#8E8E93", builtin=True,
         blurb="Lock, Mission Control, Spotlight, screenshots, emoji, Launchpad and more.", actions=_macos,
         systems="darwin"),
    Spec("linux", "Desktop", OS_NAME, "E713", "#8E8E93", builtin=True,
         blurb="Lock, overview, screenshots, files, terminal and settings.", actions=_linux, systems="linux"),
    Spec("media_keys", "Media controls", OS_NAME, "E768", "#FF375F", builtin=True,
         blurb="Play/pause, skip and volume for whatever is playing.", actions=_media_keys),
]
# Only what exists on this system (Windows' Game Bar has no place on a Mac).
CATALOG: list[Spec] = [s for s in _ALL if ("linux" if LINUX else sys.platform) in s.systems.split()]
BY_ID = {s.id: s for s in CATALOG}
CATEGORIES = ["Gaming", "Chat", "Creative", "Code & AI", "Engineering", "Browsers", "Media", "Productivity", OS_NAME]


# =========================================================================== detection
_det_lock = threading.Lock()
_det_build = threading.Lock()
_detected: dict[str, App] | None = None
_detected_at = 0.0
_det_bg = False                 # a background rescan is running
DETECT_TTL = 300.0


def _detect_one(spec: Spec, steam: dict) -> App:
    exe = apps.first_existing(*spec.exes) if spec.exes and WIN else ""
    sa = apps.find_start_app(*spec.names) if spec.names else None
    if WIN and spec.steam_appid and spec.steam_appid in steam and not exe:
        p = os.path.join(steam[spec.steam_appid].install_dir, spec.steam_exe)
        exe = p if os.path.exists(p) else exe
    if sa and sa.exe and not exe:
        exe = sa.exe
    target = sa.launch_target if sa else exe
    return App(spec, target, exe, installed=spec.builtin or bool(target))


def detect(force: bool = False) -> dict[str, App]:
    """All catalogue apps (installed or not), cached for a few minutes. Once there's a scan to go on, an old one
    is still answered straight away while a fresh one runs in the background: the rotary and the settings ask
    from the UI thread, and a scan of the Start menu and Steam can take a few seconds."""
    with _det_lock:
        cached, age = _detected, time.time() - _detected_at
    if cached is not None and not force:
        if age >= DETECT_TTL:
            _rescan_later()
        return cached
    return _scan(force)


def _rescan_later() -> None:
    global _det_bg
    with _det_lock:
        if _det_bg:
            return
        _det_bg = True

    def work():
        global _det_bg
        try:
            _scan(False)
        except Exception:
            log.debug("app scan failed", exc_info=True)
        finally:
            with _det_lock:
                _det_bg = False

    threading.Thread(target=work, daemon=True, name="detect").start()


def _scan(force: bool) -> dict[str, App]:
    global _detected, _detected_at
    started = time.time()
    with _det_build:            # one scan at a time: anyone else asking meanwhile waits and shares it
        with _det_lock:
            if _detected is not None and _detected_at >= started - (0 if force else DETECT_TTL):
                return _detected
        try:
            steam = {g.appid: g for g in apps.steam_library()}
        except Exception:
            steam = {}
        out = {}
        for spec in CATALOG:
            try:
                out[spec.id] = _detect_one(spec, steam)
            except Exception:
                log.debug("detect %s failed", spec.id, exc_info=True)
                out[spec.id] = App(spec)
        with _det_lock:
            _detected, _detected_at = out, time.time()
    return out


def installed() -> list[App]:
    return [a for a in detect().values() if a.installed]


def get(app_id: str) -> App | None:
    return detect().get(app_id)


# =========================================================================== actions & quick nav
_dyn_lock = threading.Lock()
_dynamic: dict[str, tuple[float, list[dict]]] = {}
DYNAMIC_TTL = {"steam": 300.0}


def dynamic_cached(app_id: str) -> list[dict]:
    with _dyn_lock:
        return list(_dynamic.get(app_id, (0.0, []))[1])


def refresh_dynamic(app_id: str, cfg: dict | None, done: Callable[[str], None] | None = None,
                    force: bool = False) -> None:
    """Recompute an app's dynamic actions (games, scenes, projects) in the background."""
    a = get(app_id)
    if a is None or not a.installed or a.spec.dynamic is None:
        return
    with _dyn_lock:
        t, _ = _dynamic.get(app_id, (0.0, []))
    if not force and time.time() - t < DYNAMIC_TTL.get(app_id, 30.0):
        return

    def work():
        try:
            items = a.spec.dynamic(a, cfg or {})
        except Exception:
            log.debug("dynamic actions for %s failed", app_id, exc_info=True)
            items = []
        with _dyn_lock:
            _dynamic[app_id] = (time.time(), items)
        if done:
            done(app_id)

    threading.Thread(target=work, daemon=True, name=f"dyn-{app_id}").start()


def static_actions(app_id: str) -> list[dict]:
    a = get(app_id)
    if a is None or a.spec.actions is None:
        return []
    try:
        return a.spec.actions(a)
    except Exception:
        log.debug("actions for %s failed", app_id, exc_info=True)
        return []


def all_actions(app_id: str) -> list[dict]:
    return static_actions(app_id) + dynamic_cached(app_id)


def quicknav(app_id: str) -> list[dict]:
    """The second ring for an app: its main actions first, then games/scenes/projects."""
    return all_actions(app_id)[:QUICKNAV_MAX]


def quicknav_item(app_id: str) -> dict:
    a = get(app_id)
    spec = BY_ID[app_id]
    sub = {"steam": "Your games & more", "obs": "Record, stream, scenes", "vscode": "Recent projects",
           "cursor": "Recent projects", "windsurf": "Recent projects", "discord": "Mute, deafen, DMs",
           "medal": "Clip, your clips", "solidworks": "Rebuild & views", "fusion": "Search, fit, measure",
           "arduino": "Verify & upload", "intellij": "Run & debug", "pycharm": "Run & debug"}.get(app_id, "Quick nav")
    return item(spec.name, sub, spec.glyph, spec.tint, {"type": "quicknav", "app": app_id}, a.icon if a else "")


def status_line(app_id: str) -> str:
    a = get(app_id)
    if a is None or not a.installed:
        return "Not installed"
    n = len(dynamic_cached(app_id))
    acts = len(static_actions(app_id))
    plain = f"{acts} action{'s' if acts != 1 else ''}"
    if app_id == "steam" and n:
        return f"{n} game{'s' if n != 1 else ''} · {plain}"
    if app_id in ("vscode", "cursor", "windsurf") and n:
        return f"{n} recent project{'s' if n != 1 else ''} · {plain}"
    if app_id == "obs":
        return f"{n} scene{'s' if n != 1 else ''} · {plain}" if n else f"{plain} · scenes appear while OBS is open"
    return plain


def suggested_quicknav(limit: int = 4) -> list[str]:
    """Installed apps worth a Quick nav slot by default, best first."""
    order = ["discord", "steam", "obs", "spotify", "vscode", "claude", "cursor", "roblox_studio", "chrome", "teams",
             "zoom", "slack", "medal", "solidworks", "fusion", "pycharm", "intellij", "arduino"]
    have = {a.id for a in installed()}
    return [i for i in order if i in have][:limit]
