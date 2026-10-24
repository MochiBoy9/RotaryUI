"""Win32 helpers: keyboard input, finding and focusing app windows, process checks, DPAPI.

On macOS and Linux the same functions hand over to posix.py, so callers don't need to care
which system they're on."""
from __future__ import annotations

import base64
import ctypes
import os
import sys
import time
from ctypes import wintypes

WIN = sys.platform == "win32"

# --------------------------------------------------------------------------- keys
VK = {
    "win": 0x5B, "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "delete": 0x2E, "insert": 0x2D, "printscreen": 0x2C, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "capslock": 0x14,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
ALIASES = {"control": "ctrl", "meta": "win", "windows": "win", "super": "win", "cmd": "win", "escape": "esc",
           "return": "enter", "del": "delete", "pgup": "pageup", "pgdown": "pagedown", "pgdn": "pagedown",
           "print": "printscreen", "prtsc": "printscreen", "option": "alt", "period": ".", "comma": ",",
           "minus": "-", "plus": "=", "equals": "=", "slash": "/", "semicolon": ";", "quote": "'",
           "backquote": "`", "playpause": "media_play_pause", "next": "media_next", "previous": "media_prev",
           "prev": "media_prev", "mute": "volume_mute"}
_EXTENDED = {0x5B, 0x25, 0x26, 0x27, 0x28, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x2C,
             0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def parse_keys(keys) -> list[str]:
    """"ctrl+shift+m" or ["ctrl", "shift", "m"] -> normalised key names."""
    if isinstance(keys, str):
        s = keys.replace(" ", "").lower()
        parts, cur = [], ""
        for i, ch in enumerate(s):          # a literal "+" key is written "plus"; "ctrl++" also works
            if ch == "+" and cur:
                parts.append(cur)
                cur = ""
            elif ch == "+" and not cur and i == len(s) - 1:
                parts.append("=")
            else:
                cur += ch
        if cur:
            parts.append(cur)
        keys = parts
    return [ALIASES.get(str(k).lower(), str(k).lower()) for k in keys if str(k)]


def vk_code(name: str) -> int:
    n = ALIASES.get(name.lower(), name.lower())
    if n in VK:
        return VK[n]
    if len(n) == 1 and n.isalnum():
        return ord(n.upper())
    raise ValueError(f"unknown key '{name}'")


def press_keys(keys) -> None:
    names = parse_keys(keys)
    codes = [vk_code(k) for k in names]
    if not WIN:
        posix.press_keys(names)
        return
    if not codes:
        return
    for c in codes:
        user32.keybd_event(c, 0, 0x1 if c in _EXTENDED else 0, 0)
        time.sleep(0.01)
    for c in reversed(codes):
        user32.keybd_event(c, 0, (0x1 if c in _EXTENDED else 0) | 0x2, 0)
        time.sleep(0.01)


# --------------------------------------------------------------------------- Win32 bindings
if not WIN:
    from . import posix
else:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    for fn in ("IsWindowVisible", "IsIconic", "BringWindowToTop", "SetForegroundWindow", "GetWindowTextLengthW",
               "IsWindow"):
        getattr(user32, fn).argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(DATA_BLOB)]

SW_HIDE, SW_SHOW, SW_RESTORE = 0, 5, 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80


# --------------------------------------------------------------------------- processes & windows
def running_processes() -> set[str]:
    """Lower-case exe names of every running process (without .exe on macOS / Linux)."""
    if not WIN:
        return posix.running_processes()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return set()
    names: set[str] = set()
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            names.add(e.szExeFile.lower())
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return names


def processes() -> dict[int, str]:
    """pid → lower-case exe name for every running process."""
    if not WIN:
        return posix.processes()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out: dict[int, str] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = e.szExeFile.lower()
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


def process_tree() -> dict[int, tuple[str, int]]:
    """pid → (lower-case exe name, parent pid) for every running process (Windows; empty elsewhere)."""
    if not WIN:
        return {}
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out: dict[int, tuple[str, int]] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (e.szExeFile.lower(), int(e.th32ParentProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


def command_line(pid: int) -> str:
    """A process's command line ('' if it can't be read). Same-user processes only."""
    if not WIN:
        return posix.command_line(pid)
    h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = ctypes.c_ulong(0)
        buf = ctypes.create_string_buffer(8192)
        status = ctypes.windll.ntdll.NtQueryInformationProcess(wintypes.HANDLE(h), 60, buf, ctypes.sizeof(buf),
                                                               ctypes.byref(size))   # ProcessCommandLineInformation
        if status != 0:
            return ""

        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                        ("Buffer", ctypes.c_void_p)]
        us = UNICODE_STRING.from_buffer(buf)
        return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""
    finally:
        kernel32.CloseHandle(h)


def is_running(exe_name: str) -> bool:
    if not WIN:
        return posix.is_running(exe_name)
    return bool(exe_name) and exe_name.lower() in running_processes()


def process_path(pid: int) -> str:
    if not WIN:
        return ""
    h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def process_started(pid: int) -> float:
    """When a process started (Unix time), or 0 if that can't be read."""
    if not WIN:
        return 0.0
    h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0.0
    try:
        times = [wintypes.FILETIME() for _ in range(4)]   # created, exited, kernel, user
        if not kernel32.GetProcessTimes(h, *[ctypes.byref(t) for t in times]):
            return 0.0
        ft = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
        return ft / 1e7 - 11644473600.0 if ft else 0.0
    finally:
        kernel32.CloseHandle(h)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def windows_of(exe_name: str, prefer_title: str = "", include_hidden: bool = False) -> list[int]:
    """Top-level app windows owned by processes named `exe_name`, front-most first.
    Visible windows come before hidden ones; windows whose title contains `prefer_title` come first."""
    if not WIN or not exe_name:
        return []
    want = os.path.basename(exe_name).lower()
    found: list[tuple[int, int, int]] = []      # (rank, z, hwnd)
    pid_names: dict[int, str] = {}
    z = [0]

    def cb(hwnd, _lp):
        z[0] += 1
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        title = _title(hwnd)
        if not title:
            return True
        visible = bool(user32.IsWindowVisible(hwnd))
        if not visible and not include_hidden:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = pid_names.get(pid.value)
        if name is None:
            name = os.path.basename(process_path(pid.value)).lower()
            pid_names[pid.value] = name
        if name == want:
            rank = (0 if visible else 2) + (0 if (prefer_title and prefer_title.lower() in title.lower()) else 1)
            found.append((rank, z[0], int(hwnd)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return [h for _, _, h in sorted(found)]


def window_titles() -> list[tuple[int, str]]:
    """(pid, title) of every visible titled top-level window, front-most first — pop-ups and tool windows too
    (a call app's "incoming call" toast is one)."""
    if not WIN:
        return []
    out: list[tuple[int, str]] = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _title(hwnd)
        if title:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((int(pid.value), title))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0) if WIN else 0


def focus_window(hwnd: int) -> bool:
    """Bring a window to the front even though our process isn't in the foreground."""
    if not WIN or not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
    fg = user32.GetForegroundWindow()
    if fg and int(fg) == hwnd:
        return True
    cur = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != cur and user32.AttachThreadInput(cur, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_tid, False)
    if int(user32.GetForegroundWindow() or 0) != hwnd:
        # Last resort: a lone Alt tap lifts the foreground lock.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def focus_app(exe_name: str, prefer_title: str = "", bundle: str = "") -> bool:
    if not WIN:
        return posix.focus_app(exe_name, bundle)
    for hwnd in windows_of(exe_name, prefer_title)[:1]:
        return focus_window(hwnd)
    return False


SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
GWL_STYLE = -16
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x4, 0x10, 0x200

if WIN:
    # wintypes' own RECT: other modules call GetWindowRect with it, and ctypes only accepts the declared class.
    RECT = wintypes.RECT

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]


def active_window():
    """The window in front, if it's an ordinary windowed one that can be resized (not maximised, minimised,
    full screen, the desktop or the taskbar, and not one of ours). An ActiveWindow, or None."""
    if not WIN:
        return posix.active_window()
    from .resize import ActiveWindow, Rect
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if cls.value in SKIP_CLASSES or not style & WS_THICKFRAME:
        return None
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))     # MONITOR_DEFAULTTONEAREST
    m, wk = mi.rcMonitor, mi.rcWork
    if (r.left, r.top, r.right, r.bottom) == (m.left, m.top, m.right, m.bottom) and not style & WS_CAPTION:
        return None                                  # borderless full screen
    return ActiveWindow(int(hwnd), Rect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                        Rect(wk.left, wk.top, wk.right - wk.left, wk.bottom - wk.top), _title(hwnd))


def set_window_rect(handle, rect) -> bool:
    if not WIN:
        return posix.set_window_rect(handle, rect)
    if not user32.IsWindow(handle) or user32.IsZoomed(handle):
        return False
    return bool(user32.SetWindowPos(handle, None, rect.x, rect.y, rect.w, rect.h,
                                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER))


def send_keys_to(exe_name: str, keys, restore: bool = True, prefer_title: str = "", label: str = "",
                 bundle: str = "") -> None:
    """Switch to an app's window, press a shortcut there, then (optionally) switch back."""
    if not WIN:
        posix.send_keys_to(exe_name, keys, restore, label, bundle)
        return
    wins = windows_of(exe_name, prefer_title, include_hidden=True)
    if not wins:
        raise RuntimeError(f"{label or exe_name} isn't running")
    hwnd = wins[0]
    hidden = not user32.IsWindowVisible(hwnd)
    prev = foreground_window()
    if not focus_window(hwnd):
        raise RuntimeError(f"couldn't switch to {label or exe_name}")
    time.sleep(0.15)
    press_keys(keys)
    time.sleep(0.12)
    if hidden:
        user32.ShowWindow(hwnd, SW_HIDE)
    if restore and prev and prev != hwnd:
        focus_window(prev)


# --------------------------------------------------------------------------- DPAPI (per-user encryption)
def protect(text: str, account: str = "password") -> str:
    """Encrypt a secret for this user. On macOS it goes to the Keychain (and Linux's Secret Service when
    there is one) under `account`; the returned token is what the config file keeps."""
    if not text:
        return ""
    if not WIN:
        return posix.protect(text, account)
    data = text.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))
    inp = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(inp), "GestureRotary", None, None, None, 0x1, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        enc = ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
    return base64.b64encode(enc).decode("ascii")


def unprotect(token: str) -> str:
    if not token:
        return ""
    if not WIN:
        return posix.unprotect(token)
    raw = base64.b64decode(token)
    buf = ctypes.create_string_buffer(raw, len(raw))
    inp = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):
        return ""
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
