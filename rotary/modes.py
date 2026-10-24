"""Rotary modes for what's in front: a slides ring over a slideshow and a video ring over a video player.

When the rotary opens, `foreground()` + `classify()` look at the app in front (its program and window
title). Over PowerPoint, Google Slides (the site or its installed app), Keynote, LibreOffice Impress and
other slideshow apps the rotary opens as a slides ring; over Netflix, YouTube, Hulu, VLC and other video
players, as a video ring. Everything else gets the usual ring of items.

Slides change with Page Down / Page Up: what presentation clickers send, so every slideshow app knows them,
while presenting and while editing. `slide_info()` asks PowerPoint (Windows, macOS) and Keynote how many
slides there are and which one is showing; elsewhere the count is unknown and the ring just steps.

Videos jump through the system's media controls (`MediaLink`: Windows' media session, MPRIS on Linux),
which also tell how long the video is and where it's at. Where they can't (macOS, or a player that doesn't
report itself), the video ring falls back to the player's own seek keys."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass

from . import winapi

log = logging.getLogger(__name__)


@dataclass
class Context:
    kind: str = ""                 # "slides" | "video" | ""
    app: str = ""                  # what to call it ("PowerPoint", "YouTube", …)
    exe: str = ""                  # the program in front, lower case, without .exe
    title: str = ""                # its window title
    browser: bool = False          # a site in a browser (or an installed web app)


BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "opera_gx", "vivaldi", "arc", "zen", "safari",
            "chromium", "chromium-browser", "google-chrome", "google chrome", "microsoft edge", "librewolf",
            "waterfox", "floorp", "yandex", "thorium", "orion", "sidekick", "whale", "browser", "firefox-esr",
            "firefox-bin", "geckomain", "chrome_proxy"}

SLIDE_APPS = {
    "powerpnt": "PowerPoint", "microsoft powerpoint": "PowerPoint", "keynote": "Keynote",
    "soffice.bin": "Impress", "simpress": "Impress", "libreoffice": "Impress", "wpp": "WPS Presentation",
    "wpspresentation": "WPS Presentation", "prezi": "Prezi", "pitch": "Pitch", "pdfpc": "pdfpc",
    "impressive": "Impressive", "onlyoffice": "ONLYOFFICE",
}
# Slideshow sites, told apart by the browser tab's title.
SLIDE_SITES = (("Google Slides", "Google Slides"), ("PowerPoint", "PowerPoint"), ("Keynote", "Keynote"),
               ("Pitch", "Pitch"), ("Prezi", "Prezi"), ("Gamma", "Gamma"))
SLIDE_NEEDS_TITLE = {"soffice.bin", "libreoffice", "onlyoffice"}     # these also open documents, sheets…

VIDEO_APPS = {
    "vlc": "VLC", "mpv": "mpv", "mpc-hc64": "MPC-HC", "mpc-hc": "MPC-HC", "mpc-be64": "MPC-BE", "mpc-be": "MPC-BE",
    "potplayermini64": "PotPlayer", "potplayermini": "PotPlayer", "potplayer": "PotPlayer", "kmplayer": "KMPlayer",
    "wmplayer": "Windows Media Player", "microsoft.media.player": "Media Player", "video.ui": "Movies & TV",
    "netflix": "Netflix", "hulu": "Hulu", "disneyplus": "Disney+", "primevideo": "Prime Video", "plex": "Plex",
    "plex htpc": "Plex", "kodi": "Kodi", "smplayer": "SMPlayer", "mplayer": "MPlayer", "totem": "Videos",
    "celluloid": "Celluloid", "haruna": "Haruna", "dragon": "Dragon Player", "parole": "Parole", "iina": "IINA",
    "quicktime player": "QuickTime Player", "tv": "TV", "infuse": "Infuse", "elmedia player": "Elmedia",
    "jellyfin media player": "Jellyfin", "stremio": "Stremio", "gom": "GOM Player", "5kplayer": "5KPlayer",
    "showtime": "Videos", "clapper": "Clapper", "mediaplayer": "Media Player", "applemedia": "TV",
}
VIDEO_TITLES = (("YouTube", "YouTube"), ("Netflix", "Netflix"), ("Hulu", "Hulu"), ("Disney+", "Disney+"),
                ("Prime Video", "Prime Video"), ("Twitch", "Twitch"), ("Vimeo", "Vimeo"), ("Crunchyroll", "Crunchyroll"),
                ("Peacock", "Peacock"), ("Paramount+", "Paramount+"), ("Apple TV", "Apple TV"), ("Max", "Max"),
                ("Plex", "Plex"), ("Jellyfin", "Jellyfin"), ("Dailymotion", "Dailymotion"), ("Kick", "Kick"),
                ("Tubi", "Tubi"), ("Pluto TV", "Pluto TV"), ("Bilibili", "Bilibili"), ("Rumble", "Rumble"),
                ("Nebula", "Nebula"), ("Stan", "Stan"), ("BBC iPlayer", "BBC iPlayer"), ("Mubi", "MUBI"))


def _norm(exe: str) -> str:
    n = exe.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    return n[:-4] if n.endswith(".exe") else n


def _title_has(title: str, word: str) -> bool:
    """Whole-word match, so 'Max' doesn't match 'Maximum' and 'Slides' doesn't match 'Slideshare'."""
    return re.search(r"(?<![\w+])" + re.escape(word) + r"(?![\w+])", title, re.IGNORECASE) is not None


def classify(exe: str, title: str, slides: bool = True, video: bool = True) -> Context:
    """Which ring suits the app in front."""
    name = _norm(exe)
    title = title or ""
    browser = name in BROWSERS
    if slides:
        if name in SLIDE_APPS:
            low = title.lower()
            if name not in SLIDE_NEEDS_TITLE or any(w in low for w in ("impress", "presentation", ".odp", ".ppt")):
                return Context("slides", SLIDE_APPS[name], name, title)
        if browser:
            for word, app in SLIDE_SITES:
                if _title_has(title, word):
                    return Context("slides", app, name, title, browser=True)
    if video:
        if name in VIDEO_APPS:
            return Context("video", VIDEO_APPS[name], name, title)
        if browser:
            for word, app in VIDEO_TITLES:
                if _title_has(title, word):
                    return Context("video", app, name, title, browser=True)
    return Context("", "", name, title, browser)


# --------------------------------------------------------------------------- the app in front
def foreground() -> tuple[str, str]:
    """(program, window title) of the app in front ('' when it can't be told)."""
    try:
        if sys.platform == "win32":
            return _foreground_windows()
        if sys.platform == "darwin":
            return _foreground_mac()
        return _foreground_x11()
    except Exception:
        log.debug("couldn't tell which app is in front", exc_info=True)
        return "", ""


def _foreground_windows() -> tuple[str, str]:
    import ctypes
    from ctypes import wintypes
    user32 = winapi.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # Opened from the notch, the tray or our own window, the app in front is us: look at the window under it.
    hops = 0
    while pid.value == os.getpid() and hops < 64:
        hwnd = user32.GetWindow(hwnd, 2)                  # GW_HWNDNEXT: next window down the z-order
        if not hwnd:
            return "", ""
        hops += 1
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER
            continue
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != os.getpid() and not winapi._title(hwnd):
            pid.value = os.getpid()                       # untitled helper windows (tooltips, shadows): skip
    if pid.value == os.getpid():
        return "", ""
    exe = os.path.basename(winapi.process_path(pid.value))
    title = winapi._title(hwnd)
    if exe.lower() == "applicationframehost.exe":
        # Store apps (Netflix, Media Player, Movies & TV) live inside a frame window: name the app by its title.
        low = title.lower()
        for key, app in (("netflix", "netflix"), ("hulu", "hulu"), ("disney", "disneyplus"),
                         ("prime video", "primevideo"), ("media player", "microsoft.media.player"),
                         ("movies & tv", "video.ui"), ("films & tv", "video.ui"), ("plex", "plex")):
            if key in low:
                return app, title
    return exe, title


_MAC_TAB_TITLE = {
    "Safari": 'tell application "Safari" to return name of current tab of front window',
    "Google Chrome": 'tell application "Google Chrome" to return title of active tab of front window',
    "Microsoft Edge": 'tell application "Microsoft Edge" to return title of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to return title of active tab of front window',
    "Arc": 'tell application "Arc" to return title of active tab of front window',
    "Vivaldi": 'tell application "Vivaldi" to return title of active tab of front window',
}


def _foreground_mac() -> tuple[str, str]:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return "", ""
    name = str(app.localizedName() or "")
    title = ""
    script = _MAC_TAB_TITLE.get(name)
    if script:                     # the tab's title tells YouTube from Google Slides (asks once for permission)
        title = _out(["osascript", "-e", script], 1.5).strip()
    exe = {"Google Chrome": "chrome", "Microsoft Edge": "msedge", "Brave Browser": "brave"}.get(name, name)
    return exe, title


def _foreground_x11() -> tuple[str, str]:
    if not shutil.which("xprop"):
        return "", ""
    m = re.search(r"window id # (0x[0-9a-fA-F]+)", _out(["xprop", "-root", "_NET_ACTIVE_WINDOW"], 1.0))
    if not m or int(m.group(1), 16) == 0:
        return "", ""
    out = _out(["xprop", "-id", m.group(1), "_NET_WM_NAME", "_NET_WM_PID", "WM_CLASS"], 1.0)
    title = (re.search(r'_NET_WM_NAME\([^)]*\) = "(.*)"', out) or [None, ""])[1]
    pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", out)
    exe = ""
    if pid:
        try:
            exe = open(f"/proc/{pid.group(1)}/comm", encoding="utf-8").read().strip()
        except OSError:
            exe = ""
    if not exe:
        cls = re.search(r'WM_CLASS\(STRING\) = "[^"]*", "([^"]*)"', out)
        exe = cls.group(1) if cls else ""
    return exe, title


def _out(args: list[str], timeout: float = 3.0) -> str:
    try:
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}     # CREATE_NO_WINDOW
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              **kw).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------- slides
_PPT_WIN = r"""
$ErrorActionPreference = 'Stop'
$p = [Runtime.InteropServices.Marshal]::GetActiveObject('PowerPoint.Application')
try { $w = $p.SlideShowWindows.Item(1); "$($w.View.CurrentShowPosition) $($w.Presentation.Slides.Count)" }
catch { $a = $p.ActiveWindow; "$($a.View.Slide.SlideIndex) $($a.Presentation.Slides.Count)" }
"""
_KEYNOTE = ('tell application "Keynote" to tell front document to return '
            '((slide number of current slide) as text) & " " & ((count of slides) as text)')
_PPT_MAC = ('tell application "Microsoft PowerPoint"\n'
            'try\nset i to slide index of slide of slide show view of slide show window 1\n'
            'on error\nset i to slide index of slide of view of document window 1\nend try\n'
            'return (i as text) & " " & ((count of slides of active presentation) as text)\nend tell')


def parse_slide_info(text: str) -> tuple[int, int] | None:
    """'4 20' → (4, 20): the slide showing (from 1) and how many there are."""
    m = re.search(r"(\d+)\D+(\d+)", text or "")
    if not m:
        return None
    cur, count = int(m.group(1)), int(m.group(2))
    if count < 1:
        return None
    return max(1, min(cur, count)), count


def slide_info(ctx: Context) -> tuple[int, int] | None:
    """(current slide, number of slides) where the app will say; None elsewhere. Takes up to a second
    or two (it asks the app), so call it off the UI thread."""
    if ctx.kind != "slides" or ctx.browser:
        return None
    if sys.platform == "win32" and ctx.app == "PowerPoint":
        return parse_slide_info(_out(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PPT_WIN], 6.0))
    if sys.platform == "darwin" and ctx.app == "Keynote":
        return parse_slide_info(_out(["osascript", "-e", _KEYNOTE], 3.0))
    if sys.platform == "darwin" and ctx.app == "PowerPoint":
        return parse_slide_info(_out(["osascript", "-e", _PPT_MAC], 3.0))
    return None


def step_slides(delta: int) -> None:
    """Next (+) or previous (−) slide, |delta| times, in the app in front. Page Down / Page Up are what
    presentation clickers send, so they work while presenting and while editing."""
    key = "pagedown" if delta > 0 else "pageup"
    for _ in range(min(abs(delta), 200)):
        winapi.press_keys(key)


# --------------------------------------------------------------------------- video
# Seek keys and how far one press jumps, for players that don't take a position from the media controls.
SEEK_KEYS = {"VLC": ("alt+right", "alt+left", 10.0), "mpv": ("right", "left", 5.0),
             "YouTube": ("right", "left", 5.0), "MPC-HC": ("right", "left", 5.0), "MPC-BE": ("right", "left", 5.0),
             "PotPlayer": ("right", "left", 5.0), "QuickTime Player": ("right", "left", 1.0),
             "IINA": ("right", "left", 5.0), "Twitch": ("right", "left", 10.0)}
DEFAULT_SEEK = ("right", "left", 10.0)                # Netflix, Hulu, Disney+, Prime Video, Media Player…


def seek_keys(app: str) -> tuple[str, str, float]:
    if sys.platform == "darwin" and app == "VLC":
        return "cmd+alt+right", "cmd+alt+left", 10.0
    return SEEK_KEYS.get(app, DEFAULT_SEEK)


def press_seek(app: str, presses: int) -> None:
    fwd, back, _secs = seek_keys(app)
    for _ in range(min(abs(presses), 60)):
        winapi.press_keys(fwd if presses > 0 else back)


def fmt_time(seconds: float) -> str:
    """1:05, 12:34, 1:02:03."""
    s = int(max(0.0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def media_matches(ctx: Context, app_id: str, app_name: str) -> bool:
    """Is the media session the system reports the video in front? (Not, say, Spotify in the background.)"""
    low = (app_id or "").lower().replace(" ", "")
    exe = ctx.exe.replace(" ", "")
    if exe and exe in low:
        return True
    if ctx.app and ctx.app.lower().replace(" ", "") in (low + (app_name or "").lower().replace(" ", "")):
        return True
    browsers = ("chrome", "msedge", "edge", "firefox", "brave", "opera", "vivaldi", "arc", "zen", "chromium")
    return ctx.browser and any(b in low for b in browsers) and any(b in exe for b in browsers)


class MediaLink:
    """The system's media controls, on a thread of their own (the Windows API is asyncio-based). read()
    and seek() return Futures, so the UI never waits on them."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._api = None
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def warm(self) -> None:
        """Start the thread now (loading the media API takes a moment), without waiting for it."""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="media-link")
                self._thread.start()

    @property
    def available(self) -> bool:
        """Does this system have media controls to ask? (Known once the thread has started.)"""
        return self._api is not None

    def _ensure(self) -> bool:
        self.warm()
        self._started.wait(3.0)
        return self._loop is not None and self._api is not None

    def _run(self) -> None:
        if sys.platform == "win32":
            try:
                import winrt.runtime
                winrt.runtime.init_apartment(winrt.runtime.MTA)
            except Exception:
                log.debug("COM already set up on the media thread", exc_info=True)
        from .live import media_source
        try:
            self._api = media_source()
        except Exception:
            log.debug("no media controls here", exc_info=True)
            self._api = None
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _submit(self, coro_fn) -> Future:
        fut: Future = Future()
        if not self._ensure():
            fut.set_result(None)
            return fut

        async def wrap():
            try:
                fut.set_result(await coro_fn())
            except Exception:
                log.debug("media controls call failed", exc_info=True)
                fut.set_result(None)
        asyncio.run_coroutine_threadsafe(wrap(), self._loop)
        return fut

    def read(self) -> Future:
        """→ NowPlaying (or None)."""
        return self._submit(lambda: self._api.read(False))

    def seek(self, seconds: float) -> Future:
        """→ True if the player took the new position."""
        return self._submit(lambda: self._api.seek(seconds))
