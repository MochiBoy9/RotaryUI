"""Finding the game's window and copying what's on it.

On Windows the picture is copied from the screen with GDI (BitBlt), the same way a screenshot tool does: it
doesn't touch the game process at all. It needs the game in Borderless or Windowed mode; in exclusive
Fullscreen other apps get a black picture (which is noticed and reported). Elsewhere, Qt grabs the screen.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)
WIN = sys.platform == "win32"


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]          # left, top, right, bottom on the screen (physical pixels)
    front: bool                              # it's the window in front

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]


def find_window(exes: tuple[str, ...], titles: tuple[str, ...]) -> GameWindow | None:
    """The game's main window, if it's running (and not minimised)."""
    if not WIN:
        return None
    from ctypes import wintypes
    from .. import winapi
    user32 = winapi.user32
    fg = int(user32.GetForegroundWindow() or 0)
    for exe in exes:
        for hwnd in winapi.windows_of(exe):
            title = winapi._title(hwnd)
            if titles and not any(t in title.lower() for t in titles):
                continue
            if user32.IsIconic(hwnd):
                continue
            rc = wintypes.RECT()
            if not user32.GetClientRect(hwnd, ctypes.byref(rc)) or rc.right - rc.left < 320:
                continue
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return GameWindow(int(hwnd), title, (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom), int(hwnd) == fg)
    return None


def front_app() -> tuple[str, str]:
    """(program, title) in front, for platforms without find_window."""
    try:
        from .. import modes
        return modes.foreground()
    except Exception:
        return "", ""


_gdi = None


def _gdi_setup():
    global _gdi
    if _gdi is not None:
        return _gdi
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    H = wintypes.HANDLE
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], H
    user32.ReleaseDC.argtypes = [wintypes.HWND, H]
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [H], H
    gdi32.CreateCompatibleBitmap.argtypes, gdi32.CreateCompatibleBitmap.restype = [H, ctypes.c_int, ctypes.c_int], H
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [H, H], H
    gdi32.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, ctypes.c_int,
                             ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [H]
    gdi32.DeleteDC.argtypes = [H]
    _gdi = (user32, gdi32)
    return _gdi


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def grab(rect: tuple[int, int, int, int]) -> np.ndarray | None:
    """The screen inside `rect` as a BGRA array (Windows; safe on any thread)."""
    if not WIN:
        return None
    user32, gdi32 = _gdi_setup()
    x, y, r, b = rect
    w, h = r - x, b - y
    if w <= 0 or h <= 0:
        return None
    sdc = user32.GetDC(None)
    if not sdc:
        return None
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    try:
        old = gdi32.SelectObject(mdc, bmp)
        ok = gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020)      # SRCCOPY (no CAPTUREBLT: no flicker)
        gdi32.SelectObject(mdc, old)
        if not ok:
            return None
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)   # top-down
        buf = np.empty((h, w, 4), np.uint8)
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf.ctypes.data, ctypes.byref(bih), 0) != h:
            return None
        return buf
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)


def qt_grab(rect: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
    """The screen (under the mouse, or `rect` of it) through Qt, as BGRA. On the UI thread only."""
    from PySide6.QtGui import QCursor, QGuiApplication, QImage
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pix = screen.grabWindow(0)
    if pix.isNull():
        return None
    img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine() // 4, 4)
    arr = arr[:, :w].copy()                    # ARGB32 is B, G, R, A in memory on little-endian machines
    if rect is not None:
        x, y, r, b = rect
        arr = arr[max(0, y):b, max(0, x):r]
    return arr


def is_blank(frame: np.ndarray) -> bool:
    """A picture with nothing in it: what exclusive full screen hands other apps (or a game still loading)."""
    small = frame[::16, ::16, :3]
    return small.size == 0 or int(small.max()) - int(small.min()) < 10


def crop(frame: np.ndarray, zone: tuple[float, float, float, float]) -> tuple[np.ndarray, int, int]:
    """Part of a frame: (pixels, its left, its top)."""
    h, w = frame.shape[:2]
    x0, y0 = int(zone[0] * w), int(zone[1] * h)
    x1, y1 = max(x0 + 1, int(zone[2] * w)), max(y0 + 1, int(zone[3] * h))
    return frame[y0:y1, x0:x1], x0, y0


def save_png(frame: np.ndarray, path: str) -> bool:
    import cv2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return bool(cv2.imwrite(path, frame[..., :3]))
