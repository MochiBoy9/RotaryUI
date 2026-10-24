"""macOS and Linux counterparts of winapi.py: opening things, processes, switching to apps, pressing keys,
global hotkeys, battery, the clock format, start at login and secrets.

Nothing here runs on Windows. On macOS, pressing keys in other apps needs the Accessibility permission
(System Settings → Privacy & Security → Accessibility); the global shortcut needs no permission. On Linux,
keys and the shortcut go through X11 (XWayland apps only under Wayland)."""
from __future__ import annotations

import base64
import ctypes
import logging
import os
import plistlib
import select
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import LINUX, MAC, keys

log = logging.getLogger(__name__)


def _out(args: list[str], timeout: float = 5.0) -> str:
    """A command's output ('' if it's missing or fails)."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _spawn(args: list[str], cwd: str | None = None) -> None:
    """Start a program that outlives us, with no ties to our terminal."""
    subprocess.Popen(args, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


# --------------------------------------------------------------------------- opening things
def bundle_executable(app: str) -> str:
    """The program inside a macOS .app bundle ('' if it isn't one)."""
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            name = plistlib.load(f).get("CFBundleExecutable", "")
    except (OSError, ValueError):
        return ""
    exe = os.path.join(app, "Contents", "MacOS", name)
    return exe if name and os.path.isfile(exe) else ""


def desktop_entry(path: str) -> dict[str, str]:
    """The [Desktop Entry] section of a Linux .desktop file."""
    out: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    section = ""
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("["):
            section = ln
        elif section == "[Desktop Entry]" and "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


def desktop_exec(entry: dict[str, str], args: str = "") -> list[str]:
    """A .desktop Exec= line as a command, with its %f / %u placeholders dropped (or filled with args)."""
    try:
        parts = shlex.split(entry.get("Exec", ""))
    except ValueError:
        return []
    cmd: list[str] = []
    extra = shlex.split(args) if args else []
    for p in parts:
        if p in ("%f", "%F", "%u", "%U"):
            cmd += extra
            extra = []
        elif not (len(p) == 2 and p.startswith("%")):
            cmd.append(p.replace("%%", "%"))
    return cmd + extra


def is_uri(target: str) -> bool:
    low = target.lower()
    return "://" in target or low.startswith(("http:", "https:", "mailto:", "x-apple.", "steam:", "discord:"))


def open_target(target: str, args: str = "", cwd: str | None = None) -> None:
    """Open an app, file, folder or link the way the desktop would (Finder / the file manager).
    With `args`, the program gets them on its command line."""
    target = os.path.expanduser(os.path.expandvars(target))
    if MAC:
        if target.endswith(".app") and os.path.isdir(target):
            exe = bundle_executable(target)
            if args and exe:     # a running app forwards these to itself (new window, a project…)
                _spawn([exe] + shlex.split(args), cwd)
            else:
                _spawn(["open", "-a", target])
        elif os.path.isfile(target) and os.access(target, os.X_OK) and "." not in os.path.basename(target):
            _spawn([target] + shlex.split(args), cwd)
        else:
            _spawn(["open", target])
        return
    if target.endswith(".desktop") and os.path.isfile(target):
        launcher = shutil.which("gtk-launch")
        if launcher and not args and os.path.dirname(target).endswith("applications"):
            _spawn([launcher, os.path.basename(target)[:-8]])
            return
        cmd = desktop_exec(desktop_entry(target), args)
        if not cmd:
            raise FileNotFoundError(target)
        _spawn(cmd, cwd)
        return
    if not is_uri(target) and os.path.isfile(target) and os.access(target, os.X_OK):
        _spawn([target] + shlex.split(args), cwd)
        return
    opener = shutil.which("xdg-open") or shutil.which("gio")
    if not opener:
        raise FileNotFoundError("xdg-open")
    _spawn([opener, "open", target] if opener.endswith("gio") else [opener, target])


def terminal_command(command: str) -> list[str]:
    """Run a shell command in a new, visible terminal window that stays open."""
    if MAC:
        script = command.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'tell application "Terminal" to do script "{script}"',
                "-e", 'tell application "Terminal" to activate']
    keep = f"{command}; exec \"${{SHELL:-sh}}\""
    for term, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"),
                       ("xfce4-terminal", "-x"), ("kitty", ""), ("alacritty", "-e"), ("xterm", "-e")):
        if shutil.which(term):
            return [term] + ([flag] if flag else []) + ["sh", "-c", keep]
    raise FileNotFoundError("no terminal app found")


# --------------------------------------------------------------------------- processes
def _norm(name: str) -> str:
    """'Discord.exe' / '/Applications/Discord.app/Contents/MacOS/Discord' → 'discord'."""
    n = name.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    return n[:-4] if n.endswith(".exe") else n


def processes() -> dict[int, str]:
    """pid → lower-case program name (without .exe) for every running process."""
    out: dict[int, str] = {}
    for ln in _out(["ps", "-axo", "pid=,comm="]).splitlines():
        pid, _, comm = ln.strip().partition(" ")
        if pid.isdigit() and comm:
            out[int(pid)] = _norm(comm)
    return out


def running_processes() -> set[str]:
    return set(processes().values())


def is_running(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    names = running_processes()
    return n in names or (LINUX and len(n) > 15 and n[:15] in names)     # Linux cuts names at 15 characters


def command_line(pid: int) -> str:
    return _out(["ps", "-o", "args=", "-p", str(pid)]).strip()


# --------------------------------------------------------------------------- switching to apps
def _mac_apps():
    from AppKit import NSWorkspace
    return list(NSWorkspace.sharedWorkspace().runningApplications())


def _mac_find(name: str, bundle: str = ""):
    """A running macOS app by its bundle path, or by its name / program name."""
    want, bundle = _norm(name), os.path.normpath(bundle) if bundle else ""
    for a in _mac_apps():
        try:
            path = str(a.bundleURL().path()) if a.bundleURL() else ""
            exe = str(a.executableURL().path()) if a.executableURL() else ""
            if (bundle and path == bundle) or (want and want in (_norm(exe), str(a.localizedName() or "").lower())):
                return a
        except Exception:
            continue
    return None


def frontmost() -> object:
    """Something that identifies the app in front, for switching back to it."""
    if MAC:
        try:
            from AppKit import NSWorkspace
            return NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception:
            return None
    if shutil.which("xdotool"):
        return _out(["xdotool", "getactivewindow"]).strip() or None
    return None


def _activate(app) -> bool:
    if MAC:
        try:
            app.activateWithOptions_(1 << 1)              # NSApplicationActivateIgnoringOtherApps
            path = str(app.bundleURL().path()) if app.bundleURL() else ""
            if path:
                subprocess.run(["open", "-a", path], timeout=5, capture_output=True)
            return True
        except Exception:
            return False
    if isinstance(app, str) and shutil.which("xdotool"):
        return subprocess.run(["xdotool", "windowactivate", "--sync", app], capture_output=True,
                              timeout=5).returncode == 0
    return False


def focus_app(name: str, bundle: str = "") -> bool:
    """Bring a running app to the front. False if it isn't running (or that can't be done here)."""
    if MAC:
        app = _mac_find(name, bundle)
        return app is not None and _activate(app)
    if shutil.which("wmctrl"):
        return subprocess.run(["wmctrl", "-x", "-a", _norm(name)], capture_output=True,
                              timeout=5).returncode == 0
    return False


# --------------------------------------------------------------------------- resizing the window in front
def _ax(element, attr: str):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, value = AXUIElementCopyAttributeValue(element, attr, None)
    return None if err else value


def _ax_value(value, kind):
    from ApplicationServices import AXValueGetValue
    ok, out = AXValueGetValue(value, kind, None)
    return out if ok else None


def _mac_active_window():
    from AppKit import NSScreen, NSWorkspace
    from ApplicationServices import AXUIElementCreateApplication, kAXValueCGPointType, kAXValueCGSizeType

    from .resize import ActiveWindow, Rect
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None or app.processIdentifier() == os.getpid():
        return None
    win = _ax(AXUIElementCreateApplication(app.processIdentifier()), "AXFocusedWindow")
    if win is None or _ax(win, "AXFullScreen") or _ax(win, "AXMinimized"):
        return None
    pos, size = _ax(win, "AXPosition"), _ax(win, "AXSize")
    pos = _ax_value(pos, kAXValueCGPointType) if pos is not None else None
    size = _ax_value(size, kAXValueCGSizeType) if size is not None else None
    if pos is None or size is None:
        return None
    rect = Rect(int(pos.x), int(pos.y), int(size.width), int(size.height))
    # The Dock- and menu-bar-free part of the window's screen, turned into the same top-left-origin points.
    screens = list(NSScreen.screens())
    top = screens[0].frame().size.height if screens else 0
    cx, cy = rect.x + rect.w / 2, top - (rect.y + rect.h / 2)
    work = None
    for s in screens:
        f, v = s.frame(), s.visibleFrame()
        if f.origin.x <= cx < f.origin.x + f.size.width and f.origin.y <= cy < f.origin.y + f.size.height:
            work = Rect(int(v.origin.x), int(top - v.origin.y - v.size.height), int(v.size.width), int(v.size.height))
    title = _ax(win, "AXTitle") or app.localizedName() or ""
    return ActiveWindow(win, rect, work or Rect(0, 0, 0, 0), str(title))


def _mac_set_window_rect(win, rect) -> bool:
    import Quartz
    from ApplicationServices import AXUIElementSetAttributeValue, AXValueCreate, kAXValueCGPointType, kAXValueCGSizeType
    size = AXValueCreate(kAXValueCGSizeType, Quartz.CGSizeMake(rect.w, rect.h))
    pos = AXValueCreate(kAXValueCGPointType, Quartz.CGPointMake(rect.x, rect.y))
    # Size, then position, then size again: some apps clamp the size against the screen edge first.
    ok = AXUIElementSetAttributeValue(win, "AXSize", size) == 0
    AXUIElementSetAttributeValue(win, "AXPosition", pos)
    AXUIElementSetAttributeValue(win, "AXSize", size)
    return ok


_x11_last: dict[str, tuple[int, int]] = {}      # window id → the position we last asked for


def _x11_active_window():
    from .resize import ActiveWindow, Rect
    if not shutil.which("xdotool"):
        return None
    wid = _out(["xdotool", "getactivewindow"], 2).strip()
    if not wid:
        return None
    pid = _out(["xdotool", "getwindowpid", wid], 2).strip()
    if pid.isdigit() and int(pid) == os.getpid():
        return None
    if shutil.which("xprop"):
        props = _out(["xprop", "-id", wid, "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE"], 2)
        if any(k in props for k in ("_MAXIMIZED_", "_FULLSCREEN", "_HIDDEN", "_TYPE_DESKTOP", "_TYPE_DOCK")):
            return None
    geo = dict(line.split("=", 1) for line in _out(["xdotool", "getwindowgeometry", "--shell", wid], 2).split()
               if "=" in line)
    try:
        rect = Rect(int(geo["X"]), int(geo["Y"]), int(geo["WIDTH"]), int(geo["HEIGHT"]))
    except (KeyError, ValueError):
        return None
    work = Rect(0, 0, 0, 0)
    if shutil.which("xprop"):
        nums = [int(n) for n in _out(["xprop", "-root", "_NET_WORKAREA"], 2).split("=")[-1].replace(",", " ").split()
                if n.isdigit()]
        if len(nums) >= 4:
            work = Rect(*nums[:4])
    _x11_last[wid] = (rect.x, rect.y)
    return ActiveWindow(wid, rect, work, _out(["xdotool", "getwindowname", wid], 2).strip())


def _x11_set_window_rect(wid: str, rect) -> bool:
    # Window managers disagree on whether a move places the frame or the client, so move by the
    # difference from last time instead of to an absolute spot: the title bar can't make it jump.
    lx, ly = _x11_last.get(wid, (rect.x, rect.y))
    _x11_last[wid] = (rect.x, rect.y)
    args = ["xdotool", "windowsize", wid, str(rect.w), str(rect.h)]
    if (rect.x, rect.y) != (lx, ly):
        args += ["windowmove", "--relative", wid, str(rect.x - lx), str(rect.y - ly)]
    try:
        return subprocess.run(args, capture_output=True, timeout=2, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def active_window():
    """The window in front if it's an ordinary windowed one (not full screen, maximised or minimised), as an
    ActiveWindow, or None. macOS needs the Accessibility permission; Linux needs xdotool (X11 / XWayland)."""
    try:
        return _mac_active_window() if MAC else _x11_active_window() if LINUX else None
    except Exception:
        log.debug("couldn't read the window in front", exc_info=True)
        return None


def set_window_rect(handle, rect) -> bool:
    try:
        return _mac_set_window_rect(handle, rect) if MAC else _x11_set_window_rect(handle, rect) if LINUX else False
    except Exception:
        log.debug("couldn't resize the window", exc_info=True)
        return False


def send_keys_to(name: str, keys, restore: bool = True, label: str = "", bundle: str = "") -> None:
    """Switch to an app, press a shortcut there, then (optionally) switch back."""
    if MAC:
        app = _mac_find(name, bundle)
        if app is None:
            raise RuntimeError(f"{label or _norm(name)} isn't running")
    elif not (shutil.which("wmctrl") and is_running(name)):
        raise RuntimeError(f"{label or _norm(name)} isn't running" if shutil.which("wmctrl")
                           else "Install wmctrl to press shortcuts inside other apps")
    prev = frontmost() if restore else None
    if not focus_app(name, bundle):
        raise RuntimeError(f"couldn't switch to {label or _norm(name)}")
    time.sleep(0.25)
    press_keys(keys)
    time.sleep(0.12)
    if prev is not None:
        _activate(prev)


# --------------------------------------------------------------------------- pressing keys (pynput)
# macOS virtual key codes for an ANSI (US) keyboard. Sending codes rather than characters keeps
# shortcuts like Cmd+, working on every layout, and keeps pynput off the macOS text-input APIs.
MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
}
_PYNPUT_KEYS = {
    "win": "cmd", "ctrl": "ctrl", "shift": "shift", "alt": "alt", "tab": "tab", "enter": "enter", "esc": "esc",
    "space": "space", "backspace": "backspace", "left": "left", "up": "up", "right": "right", "down": "down",
    "delete": "delete", "insert": "insert", "printscreen": "print_screen", "home": "home", "end": "end",
    "pageup": "page_up", "pagedown": "page_down", "capslock": "caps_lock", "volume_mute": "media_volume_mute",
    "volume_down": "media_volume_down", "volume_up": "media_volume_up", "media_next": "media_next",
    "media_prev": "media_previous", "media_play_pause": "media_play_pause", "media_stop": "media_stop",
    **{f"f{i}": f"f{i}" for i in range(1, 21)},
}
_controller = None
_controller_lock = threading.Lock()


def prepare_keyboard() -> None:
    """Create the key presser now. On macOS this must happen on the main thread (pynput reads the
    keyboard layout through APIs that only work there); actions later press keys from worker threads."""
    global _controller
    with _controller_lock:
        if _controller is None:
            try:
                from pynput.keyboard import Controller
                _controller = Controller()
            except Exception as e:
                log.info("can't press keys on this desktop: %s", e)
                _controller = False


def _pynput_key(name: str):
    from pynput.keyboard import Key, KeyCode
    if name in _PYNPUT_KEYS:
        k = getattr(Key, _PYNPUT_KEYS[name], None)
        if k is None:
            raise ValueError(f"the {name} key isn't available on this computer")
        return k
    if len(name) == 1:
        if MAC:
            if name in MAC_KEYCODES:
                return KeyCode.from_vk(MAC_KEYCODES[name])
        return KeyCode.from_char(name)
    raise ValueError(f"unknown key '{name}'")


def accessibility_allowed(ask: bool = True) -> bool:
    """macOS: may we press keys in other apps? With `ask`, macOS shows its "allow in System Settings" prompt."""
    if not MAC:
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: ask}))
    except Exception:
        return True          # can't tell: try anyway


def press_keys(names: list[str]) -> None:
    """Press a combination (already normalised by winapi.parse_keys), then let go in reverse order."""
    if not names:
        return
    if _controller is None:
        prepare_keyboard()
    if not _controller:
        raise RuntimeError("pressing keys isn't supported on this desktop")
    if not accessibility_allowed():
        raise RuntimeError("Allow RotaryUI in System Settings → Privacy & Security → Accessibility")
    keys = [_pynput_key(n) for n in names]
    pressed = []
    try:
        for k in keys:
            _controller.press(k)
            pressed.append(k)
            time.sleep(0.01)
    finally:
        for k in reversed(pressed):
            _controller.release(k)
            time.sleep(0.01)


# --------------------------------------------------------------------------- global hotkeys
# The callers speak Windows' RegisterHotKey language (MOD_* flags and virtual-key codes); these translate.
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
_VK_NAMES = {vk: name for name, vk in keys.KEYS.items()}     # 0x20 → "space", 0x41 → "a", 0x70 → "f1", …
_MAC_SPECIAL = {"space": 49, "left": 123, "right": 124, "down": 125, "up": 126, "enter": 36, "esc": 53,
                "equal": 24, "minus": 27, "kp_add": 69, "kp_subtract": 78, "tab": 48, "backspace": 51,
                "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "insert": 114, "delete": 117,
                "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
                "f10": 109, "f11": 103, "f12": 111}
_X_SPECIAL = {"space": "space", "left": "Left", "right": "Right", "down": "Down", "up": "Up", "enter": "Return",
              "esc": "Escape", "equal": "equal", "minus": "minus", "kp_add": "KP_Add", "kp_subtract": "KP_Subtract",
              "tab": "Tab", "backspace": "BackSpace", "home": "Home", "end": "End", "pageup": "Prior",
              "pagedown": "Next", "insert": "Insert", "delete": "Delete",
              **{f"f{n}": f"F{n}" for n in range(1, 13)}}


def _vk_name(vk: int) -> str:
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    raise ValueError(f"no hotkey for key code {vk:#x}")


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class CarbonHotkeys:
    """macOS: system-wide shortcuts through Carbon's RegisterEventHotKey. No permission needed, the key is
    swallowed (like Windows' RegisterHotKey), and the callback runs on the main thread from Qt's event loop."""

    def __init__(self, on_hotkey: Callable[[int], None]):
        self.on_hotkey = on_hotkey
        c = self.c = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_ulong, ctypes.POINTER(_EventTypeSpec),
                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        c.InstallEventHandler.restype = ctypes.c_int32
        c.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        c.RegisterEventHotKey.restype = ctypes.c_int32
        c.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        c.UnregisterEventHotKey.restype = ctypes.c_int32
        c.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                        ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        c.GetEventParameter.restype = ctypes.c_int32
        self._sig = _fourcc("GRot")
        self._refs: dict[int, ctypes.c_void_p] = {}
        self._cb = _HANDLER(self._handle)          # kept alive as long as we are
        spec = _EventTypeSpec(_fourcc("keyb"), 5)  # kEventClassKeyboard, kEventHotKeyPressed
        self._handler_ref = ctypes.c_void_p()
        err = c.InstallEventHandler(c.GetApplicationEventTarget(), self._cb, 1, ctypes.byref(spec), None,
                                    ctypes.byref(self._handler_ref))
        if err:
            raise OSError(err, "InstallEventHandler failed")

    def _handle(self, _call, event, _data) -> int:
        hk = _EventHotKeyID()
        if self.c.GetEventParameter(event, _fourcc("----"), _fourcc("hkid"), None, ctypes.sizeof(hk), None,
                                    ctypes.byref(hk)) == 0 and hk.signature == self._sig:
            try:
                self.on_hotkey(int(hk.id))
            except Exception:
                log.exception("hotkey handler failed")
        return 0

    def register(self, hid: int, mods: int, vk: int) -> bool:
        name = _vk_name(vk)
        code = _MAC_SPECIAL.get(name, MAC_KEYCODES.get(name))
        if code is None:
            return False
        cmods = ((0x800 if mods & MOD_ALT else 0) | (0x1000 if mods & MOD_CONTROL else 0)
                 | (0x200 if mods & MOD_SHIFT else 0) | (0x100 if mods & MOD_WIN else 0))
        ref = ctypes.c_void_p()
        err = self.c.RegisterEventHotKey(code, cmods, _EventHotKeyID(self._sig, hid),
                                         self.c.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if err:
            return False
        self._refs[hid] = ref
        return True

    def unregister(self, hid: int) -> None:
        ref = self._refs.pop(hid, None)
        if ref is not None:
            self.c.UnregisterEventHotKey(ref)


class X11Hotkeys:
    """Linux: system-wide shortcuts through an X11 key grab (the key is swallowed). The callback runs on a
    background thread — hand it to the UI thread with a queued Qt signal."""

    def __init__(self, on_hotkey: Callable[[int], None]):
        from Xlib import X, XK, display
        self.X, self.XK = X, XK
        self.on_hotkey = on_hotkey
        self.d = display.Display()
        self.root = self.d.screen().root
        self.root.change_attributes(event_mask=X.KeyPressMask)
        self._lock = threading.Lock()
        self._grabs: dict[int, tuple[int, int]] = {}     # hid → (keycode, modifiers)
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="hotkeys").start()

    def _variants(self, mods: int) -> list[int]:
        X = self.X
        return [mods | extra for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)]  # Caps / Num Lock

    def register(self, hid: int, mods: int, vk: int) -> bool:
        from Xlib import error
        name = _vk_name(vk)
        keycode = self.d.keysym_to_keycode(self.XK.string_to_keysym(_X_SPECIAL.get(name, name)))
        if not keycode:
            return False
        X = self.X
        xmods = ((X.Mod1Mask if mods & MOD_ALT else 0) | (X.ControlMask if mods & MOD_CONTROL else 0)
                 | (X.ShiftMask if mods & MOD_SHIFT else 0) | (X.Mod4Mask if mods & MOD_WIN else 0))
        catch = error.CatchError(error.BadAccess)
        with self._lock:
            for m in self._variants(xmods):
                self.root.grab_key(keycode, m, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
            self.d.sync()
            if catch.get_error():
                for m in self._variants(xmods):
                    self.root.ungrab_key(keycode, m)
                self.d.sync()
                return False
            self._grabs[hid] = (keycode, xmods)
        return True

    def unregister(self, hid: int) -> None:
        with self._lock:
            g = self._grabs.pop(hid, None)
            if g is not None:
                for m in self._variants(g[1]):
                    self.root.ungrab_key(g[0], m)
                self.d.sync()

    def _loop(self) -> None:
        X, fd = self.X, self.d.fileno()
        ignore = X.LockMask | X.Mod2Mask
        while not self._stop.is_set():
            try:
                select.select([fd], [], [], 0.25)
                hits = []
                with self._lock:
                    while self.d.pending_events():
                        ev = self.d.next_event()
                        if ev.type == X.KeyPress:
                            state = ev.state & ~ignore
                            hits += [hid for hid, (kc, m) in self._grabs.items() if kc == ev.detail and m == state]
                for hid in hits:
                    self.on_hotkey(hid)
            except Exception:
                log.debug("hotkey loop error", exc_info=True)
                time.sleep(0.5)


def hotkeys(on_hotkey: Callable[[int], None]):
    """The global-shortcut backend for this desktop, or None."""
    try:
        if MAC:
            return CarbonHotkeys(on_hotkey)
        if os.environ.get("DISPLAY"):
            return X11Hotkeys(on_hotkey)
        log.info("no X11 display: the keyboard shortcut is off (use the notch or the tray icon)")
    except Exception:
        log.warning("global keyboard shortcut unavailable", exc_info=True)
    return None


# --------------------------------------------------------------------------- battery
def battery() -> tuple[int, bool] | None:
    """(percent, plugged in), or None without a battery."""
    if MAC:
        txt = _out(["pmset", "-g", "batt"])
        if "InternalBattery" not in txt:
            return None
        import re
        m = re.search(r"(\d+)%", txt)
        if not m:
            return None
        return int(m.group(1)), "AC Power" in txt.splitlines()[0]
    base = Path("/sys/class/power_supply")
    try:
        bats = [p for p in base.iterdir() if (p / "type").read_text().strip() == "Battery"
                and (p / "capacity").exists()]
        if not bats:
            return None
        pct = int((bats[0] / "capacity").read_text().strip())
        status = (bats[0] / "status").read_text().strip().lower()
        return max(0, min(100, pct)), status != "discharging"
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- clock
def force_24h() -> bool | None:
    """macOS's own 24-hour setting when it's set (None = follow the locale)."""
    if MAC:
        v = _out(["defaults", "read", "-g", "AppleICUForce24HourTime"]).strip()
        if v in ("0", "1"):
            return v == "1"
    return None


# --------------------------------------------------------------------------- start at login
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "app.gesturerotary.plist"
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "gesture-rotary.desktop"


def login_item_command() -> list[str] | None:
    """What starts with the computer, if that's set up."""
    try:
        if MAC:
            with open(LAUNCH_AGENT, "rb") as f:
                return list(plistlib.load(f).get("ProgramArguments") or [])
        if AUTOSTART.exists():
            return shlex.split(desktop_entry(str(AUTOSTART)).get("Exec", ""))
    except (OSError, ValueError):
        pass
    return None


def set_login_item(cmd: list[str] | None, cwd: str = "") -> None:
    """Start `cmd` when the user logs in (None turns that off)."""
    path = LAUNCH_AGENT if MAC else AUTOSTART
    if cmd is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if MAC:
        plist = {"Label": "app.gesturerotary", "ProgramArguments": cmd, "RunAtLoad": True,
                 "ProcessType": "Interactive"}
        if cwd:
            plist["WorkingDirectory"] = cwd
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
    else:
        write_desktop_file(path, cmd, cwd, autostart=True)


def desktop_quote(arg: str) -> str:
    """Quote an Exec= argument the way the Desktop Entry spec wants (double quotes only)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def write_desktop_file(path: Path, cmd: list[str], cwd: str = "", icon: str = "", autostart: bool = False) -> None:
    lines = ["[Desktop Entry]", "Type=Application", "Name=RotaryUI",
             "Comment=A rotary launcher you turn with your hand, the mouse or the keyboard",
             "Exec=" + " ".join(desktop_quote(c) for c in cmd), "Terminal=false", "Categories=Utility;",
             "StartupWMClass=gesture-rotary"]
    if cwd:
        lines.append(f"Path={cwd}")
    if icon:
        lines.append(f"Icon={icon}")
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- secrets
# macOS: the login Keychain (Security.framework). Linux: the Secret Service (secret-tool), when it's there.
# Otherwise the token is only base64 — readable by anyone who can read the config file.
SERVICE = "Gesture Rotary"     # the name secrets were saved under before RotaryUI: keep it, or they're lost


def _security():
    sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    u32, vp = ctypes.c_uint32, ctypes.c_void_p
    sec.SecKeychainFindGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
                                                   ctypes.POINTER(u32), ctypes.POINTER(vp), ctypes.POINTER(vp)]
    sec.SecKeychainAddGenericPassword.argtypes = [vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p, u32,
                                                  ctypes.c_char_p, ctypes.POINTER(vp)]
    sec.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, ctypes.c_char_p]
    sec.SecKeychainItemFreeContent.argtypes = [vp, vp]
    return sec


def _keychain_get(account: str, new_value: bytes | None = None) -> str | None:
    """The stored secret (None if there isn't one); with `new_value`, replace it and return the old one."""
    sec, s, a = _security(), SERVICE.encode(), account.encode()
    n, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    if sec.SecKeychainFindGenericPassword(None, len(s), s, len(a), a, ctypes.byref(n), ctypes.byref(data),
                                          ctypes.byref(item)) != 0:
        return None
    try:
        old = ctypes.string_at(data, n.value).decode("utf-8", "replace")
        if new_value is not None and sec.SecKeychainItemModifyAttributesAndData(item, None, len(new_value),
                                                                                  new_value) != 0:
            raise OSError("couldn't update the Keychain item")
        return old
    finally:
        sec.SecKeychainItemFreeContent(None, data)
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease(item)


def protect(text: str, account: str) -> str:
    """Store a secret; returns the token to keep in the config file."""
    if MAC:
        try:
            sec, s, a, pw = _security(), SERVICE.encode(), account.encode(), text.encode("utf-8")
            err = 0
            if _keychain_get(account, pw) is None:
                err = sec.SecKeychainAddGenericPassword(None, len(s), s, len(a), a, len(pw), pw, None)
            if err == 0:
                return f"keychain:{account}"
            log.info("Keychain refused the secret (%s); keeping it in the config file", err)
        except Exception:
            log.info("Keychain unavailable", exc_info=True)
    elif shutil.which("secret-tool"):
        try:
            r = subprocess.run(["secret-tool", "store", f"--label={SERVICE}", "service", SERVICE, "account", account],
                               input=text.encode("utf-8"), capture_output=True, timeout=10)
            if r.returncode == 0:
                return f"secret-service:{account}"
        except (OSError, subprocess.SubprocessError):
            log.info("Secret Service unavailable", exc_info=True)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def unprotect(token: str) -> str:
    if token.startswith("keychain:"):
        if not MAC:
            return ""
        try:
            return _keychain_get(token.split(":", 1)[1]) or ""
        except Exception:
            return ""
    if token.startswith("secret-service:"):
        return _out(["secret-tool", "lookup", "service", SERVICE, "account", token.split(":", 1)[1]], 10).rstrip("\n")
    try:
        return base64.b64decode(token).decode("utf-8")
    except ValueError:
        return ""


# --------------------------------------------------------------------------- misc
def platform_python() -> str:
    """A Python 3 the user installed themselves ('' if none)."""
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p and not (MAC and p == "/usr/bin/python3" and not Path("/Library/Developer/CommandLineTools").exists()):
            return p                                     # macOS's /usr/bin/python3 is a stub until Xcode tools
    return ""

