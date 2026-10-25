"""What's installed: every Start-menu app (desktop, Store and packaged), plus Steam libraries.

`start_apps()` mirrors Start → All apps (via Get-StartApps) and is cached on disk, so it's
instant after the first run and refreshed in the background. On macOS it lists the .app bundles in
the Applications folders; on Linux, the apps menu's .desktop entries."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import DATA_DIR, MAC, WIN

log = logging.getLogger(__name__)
CACHE_DIR = DATA_DIR / "cache"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", ""),
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", ""),
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows"),
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
    "{D65231B0-B2F1-4857-A4CE-A8E7C6EA7D27}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64"),
    "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}": os.environ.get("LOCALAPPDATA", ""),
    "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}": os.environ.get("APPDATA", ""),
    "{5E6C858F-0E22-4760-9AFE-EA3317B67173}": os.environ.get("USERPROFILE", ""),
    "{6365D5A7-0F0D-45E5-87F6-0DA56B6A4F7D}": os.environ.get("CommonProgramW6432", ""),
}


@dataclass
class StartApp:
    name: str
    app_id: str
    exe: str = ""          # resolved program, when the AppID is a path
    url: str = ""          # when the AppID is a URL (e.g. a Steam game shortcut)
    packaged: bool = False  # Store / MSIX app

    @property
    def launch_target(self) -> str:
        if self.url:
            return self.url
        if self.exe:
            return self.exe
        return "shell:AppsFolder\\" + self.app_id


def _resolve_app_id(app_id: str) -> tuple[str, str, bool]:
    """(exe, url, packaged) for a Get-StartApps AppID."""
    if "://" in app_id or app_id.lower().startswith(("http:", "https:", "steam:")):
        return "", app_id, False
    m = re.match(r"^(\{[0-9A-Fa-f-]{36}\})\\(.*)$", app_id)
    if m:
        base = KNOWN_FOLDERS.get(m.group(1).upper(), "")
        p = os.path.join(base, m.group(2)) if base else ""
        return (p if p and os.path.exists(p) else ""), "", False
    if re.match(r"^[A-Za-z]:\\", app_id):
        return (app_id if os.path.exists(app_id) else ""), "", False
    return "", "", "!" in app_id


_lock = threading.Lock()
_start_cache: list[StartApp] | None = None


def _ps_json(script: str, timeout: float = 20.0):
    full = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", full], capture_output=True,
                         timeout=timeout, creationflags=_NO_WINDOW)
    txt = out.stdout.decode("utf-8", "replace").strip()
    return json.loads(txt) if txt else None


def _load_start_apps_from_ps() -> list[StartApp]:
    data = _ps_json("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress") or []
    if isinstance(data, dict):
        data = [data]
    apps = []
    for d in data:
        name, app_id = str(d.get("Name") or ""), str(d.get("AppID") or "")
        if not name or not app_id:
            continue
        exe, url, packaged = _resolve_app_id(app_id)
        apps.append(StartApp(name, app_id, exe, url, packaged))
    return apps


MAC_APP_DIRS = ["/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
                "~/Applications"]


def _load_mac_apps() -> list[StartApp]:
    """Every .app in the Applications folders (and one folder down, e.g. Adobe's)."""
    found: dict[str, StartApp] = {}
    for d in MAC_APP_DIRS:
        root = Path(os.path.expanduser(d))
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for p in entries:
            bundles = [p] if p.suffix == ".app" else []
            if not bundles and p.is_dir() and not p.name.startswith("."):
                try:
                    bundles = sorted(q for q in p.iterdir() if q.suffix == ".app")
                except OSError:
                    pass
            for b in bundles:
                found.setdefault(b.stem.lower(), StartApp(b.stem, str(b), exe=str(b)))
    return list(found.values())


def linux_app_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    roots = [data_home] + data_dirs + [str(Path.home() / ".local/share/flatpak/exports/share"),
                                       "/var/lib/flatpak/exports/share"]
    return [Path(r) / "applications" for r in dict.fromkeys(roots) if r] + [Path("/var/lib/snapd/desktop/applications")]


def _load_linux_apps() -> list[StartApp]:
    """Every app in the desktop's apps menu (its .desktop files; the first one with a given id wins)."""
    from . import posix
    seen: set[str] = set()
    out: list[StartApp] = []
    for root in linux_app_dirs():
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.desktop")):
            desktop_id = str(f.relative_to(root)).replace("/", "-")
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            e = posix.desktop_entry(str(f))
            if e.get("Type", "Application") != "Application" or e.get("NoDisplay") == "true" \
                    or e.get("Hidden") == "true" or not e.get("Name") or not e.get("Exec"):
                continue
            out.append(StartApp(e["Name"], desktop_id, exe=str(f)))
    return out


def start_apps(refresh: bool = False, wait: bool = True) -> list[StartApp]:
    """Every app in Start → All apps. Uses the on-disk cache when present (refreshing it in the
    background); `refresh=True` re-queries Windows now."""
    global _start_cache
    cache_file = CACHE_DIR / "start_apps.json"
    with _lock:
        if _start_cache is None and cache_file.exists() and not refresh:
            try:
                _start_cache = [StartApp(**d) for d in json.loads(cache_file.read_text(encoding="utf-8"))]
                threading.Thread(target=start_apps, kwargs={"refresh": True}, daemon=True).start()
            except Exception:
                _start_cache = None
        if _start_cache is not None and not refresh:
            return list(_start_cache)
    if not wait and not refresh:
        threading.Thread(target=start_apps, kwargs={"refresh": True}, daemon=True).start()
        return []
    try:
        apps = _load_start_apps_from_ps() if WIN else _load_mac_apps() if MAC else _load_linux_apps()
    except Exception:
        log.warning("couldn't list Start apps", exc_info=True)
        return list(_start_cache or [])
    with _lock:
        _start_cache = apps
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps([asdict(a) for a in apps]), encoding="utf-8")
    except OSError:
        pass
    return list(apps)


def name_matches(name: str, want: str) -> bool:
    """"OBS Studio (64bit)", "Blender 4.2", "AutoCAD 2025 - English" and "MATLAB R2024a" match "OBS Studio" /
    "Blender" / "AutoCAD" / "MATLAB"; "EA Sports FC" doesn't match "EA"."""
    n, w = name.lower().strip(), want.lower().strip()
    if n == w:
        return True
    if not n.startswith(w + " "):
        return False
    rest = n[len(w):].strip()
    return rest.startswith("(") or re.fullmatch(r"(v?[\d.]+|r\d{4}[ab])( lts| beta| preview| sp[\d.]+| x64| - [a-z]+)?",
                                                rest) is not None


def find_start_app(*names: str) -> StartApp | None:
    """First Start app matching one of `names` (in order of preference)."""
    apps = start_apps()
    for want in names:
        for a in apps:
            if name_matches(a.name, want):
                return a
    return None


# --------------------------------------------------------------------------- Start-menu shortcuts
def start_menu_shortcuts() -> dict[str, str]:
    """name (lower) -> .lnk path, for icons and for resolving AUMID-only apps."""
    if not WIN:
        return {}
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    out: dict[str, str] = {}
    for root in roots:
        if root.exists():
            for p in root.rglob("*.lnk"):
                out.setdefault(p.stem.lower(), str(p))
    return out


_lnk_cache: dict[str, str] | None = None


def shortcut_targets(paths: list[str]) -> dict[str, str]:
    """Resolve many .lnk files at once (one PowerShell call), cached on disk."""
    global _lnk_cache
    cache_file = CACHE_DIR / "shortcut_targets.json"
    if _lnk_cache is None:
        try:
            _lnk_cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            _lnk_cache = {}
    todo = [p for p in paths if p.lower().endswith(".lnk") and p not in _lnk_cache]
    if todo:
        tmp = CACHE_DIR / "lnk_query.txt"
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text("\n".join(todo), encoding="utf-8")
            quoted = str(tmp).replace("'", "''")           # a user folder like C:\Users\O'Brien
            script = (f"$sh = New-Object -ComObject WScript.Shell; $o = @{{}}; "
                      f"foreach ($p in Get-Content -LiteralPath '{quoted}' -Encoding UTF8) "
                      f"{{ try {{ $o[$p] = $sh.CreateShortcut($p).TargetPath }} catch {{}} }}; "
                      f"$o | ConvertTo-Json -Compress")
            res = _ps_json(script) or {}
            if not isinstance(res, dict):
                res = {}
            for p in todo:
                _lnk_cache[p] = str(res.get(p) or "")
            cache_file.write_text(json.dumps(_lnk_cache), encoding="utf-8")
        except Exception:
            log.debug("shortcut resolution failed", exc_info=True)
    return {p: _lnk_cache.get(p, "") for p in paths}


def shortcut_target(path: str) -> str:
    if not path.lower().endswith(".lnk"):
        return path
    t = shortcut_targets([path]).get(path, "")
    return t if t and os.path.exists(t) else path


# --------------------------------------------------------------------------- Steam
def parse_vdf(text: str) -> dict:
    """Parser for Valve's text KeyValues (libraryfolders.vdf, appmanifest_*.acf)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = [{}]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = quoted.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return stack[0]


def steam_path() -> str:
    if not WIN:
        home = Path.home()
        for p in ([home / "Library/Application Support/Steam"] if MAC else
                  [home / ".steam/steam", home / ".local/share/Steam",
                   home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]):
            if (p / "steamapps").is_dir():
                return str(p.resolve())
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamPath")[0]
        p = os.path.normpath(p)
        if os.path.isdir(p):
            return p
    except Exception:
        pass
    p = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam")
    return p if os.path.isdir(p) else ""


NOT_GAMES = {"228980"}       # Steamworks Common Redistributables
NOT_GAME_WORDS = ("redistributable", "proton", "steam linux runtime", "steamworks", "dedicated server", "sdk")


@dataclass
class SteamApp:
    appid: str
    name: str
    install_dir: str
    last_played: int
    icon: str


def steam_library(root: str | None = None) -> list[SteamApp]:
    """Installed Steam apps (games and tools such as OBS), most recently played first."""
    root = root or steam_path()
    if not root:
        return []
    libs = [root]
    try:
        data = parse_vdf(Path(root, "steamapps", "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace"))
        for v in (data.get("libraryfolders") or {}).values():
            if isinstance(v, dict) and v.get("path"):
                libs.append(os.path.normpath(v["path"]))
    except OSError:
        pass
    seen, out = set(), []
    for lib in dict.fromkeys(libs):
        sa = Path(lib, "steamapps")
        if not sa.is_dir():
            continue
        for acf in sa.glob("appmanifest_*.acf"):
            try:
                st = parse_vdf(acf.read_text(encoding="utf-8", errors="replace")).get("AppState") or {}
            except OSError:
                continue
            appid, name = str(st.get("appid", "")), str(st.get("name", ""))
            if not appid or appid in seen or appid in NOT_GAMES or not name:
                continue
            if any(w in name.lower() for w in NOT_GAME_WORDS):
                continue
            try:
                if not int(st.get("StateFlags", "4")) & 4:
                    continue
            except ValueError:
                pass
            seen.add(appid)
            out.append(SteamApp(appid, name, str(sa / "common" / st.get("installdir", "")),
                                int(st.get("LastPlayed", "0") or 0), steam_icon(root, appid)))
    out.sort(key=lambda a: (-a.last_played, a.name.lower()))
    return out


def steam_icon(root: str, appid: str) -> str:
    cache = Path(root, "appcache", "librarycache")
    old = cache / f"{appid}_icon.jpg"
    if old.exists():
        return str(old)
    folder = cache / appid
    if folder.is_dir():
        for p in folder.glob("*.jpg"):
            if re.fullmatch(r"[0-9a-f]{40}", p.stem):
                return str(p)
    return ""


# --------------------------------------------------------------------------- misc
def protocol_registered(scheme: str) -> bool:
    try:
        import winreg
        for hive, path in ((winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}"), (winreg.HKEY_CLASSES_ROOT, scheme)):
            try:
                with winreg.OpenKey(hive, path) as k:
                    winreg.QueryValueEx(k, "URL Protocol")
                    return True
            except OSError:
                continue
    except ImportError:
        pass
    return False


def first_existing(*patterns: str) -> str:
    """First existing path among patterns (env vars and ~ expanded, `*` globs pick the newest match)."""
    import glob
    for pat in patterns:
        p = os.path.expanduser(os.path.expandvars(pat))
        if "*" in p:
            hits = sorted(glob.glob(p), key=lambda x: os.path.getmtime(x), reverse=True)
            if hits:
                return hits[0]
        elif os.path.exists(p):
            return p
    return ""


def which_cli(name: str, allow_store_alias: bool = True) -> str:
    """A command on PATH. Store app aliases (WindowsApps\\*.exe) are allowed unless told otherwise —
    that's how wt.exe works, but it's also where Windows' fake 'python.exe' installer lives."""
    import shutil
    p = shutil.which(name)
    if p and not allow_store_alias and "\\windowsapps\\" in p.lower():
        return ""
    return p or ""


def clear_caches() -> None:
    global _start_cache, _lnk_cache
    with _lock:
        _start_cache = None
    _lnk_cache = None
    for f in ("start_apps.json", "shortcut_targets.json"):
        try:
            (CACHE_DIR / f).unlink()
        except OSError:
            pass


def age_of_cache() -> float:
    f = CACHE_DIR / "start_apps.json"
    return time.time() - f.stat().st_mtime if f.exists() else float("inf")
