"""Opening and turning the rotary with the mouse, from any app, without disturbing it.

A mouse button opens the rotary (Settings → Accessibility → Keyboard & mouse):

  * middle: press and hold the wheel button. A quick click still reaches the app as an ordinary middle
    click, and pressing it and moving (panning in a 3D app, say) still drags as usual;
  * back / forward: the mouse's side buttons; a click opens the rotary, another closes it.

Whenever the rotary is on screen, however it was opened (a hand, the mouse, the keyboard, the notch), the mouse
works on it directly: over the ring the wheel turns it, a press and drag turns it like a knob, a click on an
item or the centre opens it, and a right-click goes back. These are caught here, before any window sees them,
so they work the same over a browser, a game or the desktop, and the app underneath never gets them. While
it's open from the mouse, the keyboard or the notch, the wheel also turns it wherever the pointer is, a click
outside it closes it, and a right-click goes back; opened by a hand, the mouse outside the ring is left alone.
(The rotary's own window never takes the keyboard focus either.)

Mouse users can also pick a button that opens whatever is highlighted and one that closes the rotary, wherever
the pointer is (Settings → Accessibility → Keyboard & mouse): left click to open and right click to close, say,
so you can scroll to an item and click without aiming at it. Over the ring itself a left click still opens the
item under the pointer; opened by a hand, these buttons only count over the ring.

Windows: a low-level mouse hook on its own thread. macOS: a Quartz event tap through pynput (it needs the
Accessibility permission, like pressing keys). Linux (X11): pynput can watch the mouse but not hold events
back, so there the clicks and scrolls also reach the app underneath, and a rotary window that takes the mouse
itself is left to do so.

`MouseLogic` is the decision-making, with no system calls, so it can be tested."""
from __future__ import annotations

import logging
import math
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

HOLD_SECONDS = 0.35            # middle button held this long opens the rotary
DRAG_PIXELS = 10               # moving further than this while holding is a drag, not a hold
TAG = 0x47524F54               # 'GROT': marks the clicks we replay, so we let them through

BUTTONS = {"middle", "back", "forward"}
SELECT_BUTTONS = {"left", "middle", "back", "forward"}     # opens the highlighted item while the rotary is open
CLOSE_BUTTONS = {"right", "middle", "back", "forward"}     # closes it


class MouseLogic:
    """Feed it mouse events; it says whether to hold each one back from the app underneath and what the rotary
    should do. Events out: "toggle" (open, or close if open), "open", "turn" (±1 per wheel notch: +1 = next
    item), "close", "back", "choose" (open the highlighted item). Pointer events out, for pressing and dragging on the rotary: ("press" | "drag" |
    "release", x, y) in the hook's coordinates. `replay` is set to "middle" when a held-back quick middle click
    should be sent on to the app after all."""

    def __init__(self, button: str = "middle", select: str = "", close: str = ""):
        self.button = button if button in BUTTONS else ""
        self.select = select if select in SELECT_BUTTONS else ""     # "" = off
        self.close = close if close in CLOSE_BUTTONS else ""
        self.active = False                  # the rotary is on screen
        self.manual = False                  # …opened for the mouse / keyboard: the mouse outside it is ours too
        self.window = False                  # its own window takes the mouse over it (Linux, mouse / keyboard)
        self.circle: tuple[float, float, float] | None = None     # the rotary on screen: x, y, radius
        self.zones: tuple[tuple[float, float, float, float], ...] = ()   # its other parts: (left, top, right, bottom)
        self._pending: tuple[float, float, float] | None = None   # middle pressed: (time, x, y)
        self._eat_up: set[str] = set()       # swallow these buttons' next release
        self._wheel = 0
        self.pressed = False                 # the left button went down on the rotary and is still down
        self.replay: str | None = None
        self.out: list[tuple[str, int]] = []
        self.pointer: list[tuple[str, float, float]] = []

    @property
    def wants_moves(self) -> bool:
        """Moves matter only while the middle button is held or the rotary is being dragged."""
        return self._pending is not None or self.pressed

    def _inside(self, x: float, y: float) -> bool:
        c = self.circle
        if c is not None and math.hypot(x - c[0], y - c[1]) <= c[2]:
            return True
        return any(z[0] <= x <= z[2] and z[1] <= y <= z[3] for z in self.zones)

    def set_active(self, on: bool, circle: tuple[float, float, float] | None = None,
                   zones: tuple[tuple[float, float, float, float], ...] = (), manual: bool = True,
                   window: bool = False) -> None:
        self.active = on
        self.manual = bool(on and manual)
        self.window = bool(on and window)
        self.circle = circle if on else None
        self.zones = tuple(zones) if on else ()
        self._wheel = 0
        if not on and self.pressed:
            self.pressed = False
            self._eat_up.add("left")         # its release was never the app's

    def tick(self, now: float) -> None:
        """Call regularly (every ~50 ms) while the middle button may be held."""
        p = self._pending
        if p is not None and now - p[0] >= HOLD_SECONDS:
            self._pending = None
            self._eat_up.add("middle")
            if not self.active:
                self.out.append(("open", 0))

    def _turn(self, delta: int) -> None:
        self._wheel += delta
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel < 0 else -1          # wheel down = next item
            self._wheel += 120 if self._wheel < 0 else -120
            self.out.append(("turn", step))

    def feed(self, kind: str, button: str = "", x: float = 0.0, y: float = 0.0, delta: int = 0,
             now: float | None = None) -> bool:
        """kind: "down" | "up" | "move" | "wheel". Returns True to hold the event back."""
        now = time.monotonic() if now is None else now
        self.replay = None
        if kind == "move":
            p = self._pending
            if p is not None and math.hypot(x - p[1], y - p[2]) > DRAG_PIXELS:
                self._pending = None          # a drag: give the app its press now; the release follows as usual
                self.replay = "middle_down"
            if self.pressed:
                self.pointer.append(("drag", x, y))
            return False                      # never hold a move back: the pointer would freeze

        # the buttons chosen to open the highlighted item and to close the rotary (ahead of the trigger button,
        # so one side button can open the rotary and then open the item)
        if kind == "down" and button and self.active and button in (self.select, self.close):
            inside = self._inside(x, y)
            if (self.manual or inside) and not (inside and (self.window or button == "left")):
                self._eat_up.add(button)
                self.out.append(("choose", 0) if button == self.select else ("close", 0))
                return True

        # the trigger button
        if button and button == self.button:
            if kind == "down":
                # Open for the mouse: it closes it. Opened by a hand: only on the ring itself; elsewhere it's
                # still the app's middle click (or the usual hold).
                if self.manual or (self.active and self._inside(x, y)):
                    self._eat_up.add(button)
                    self.out.append(("close", 0))
                    return True
                if button == "middle":
                    self._pending = (now, x, y)
                else:
                    self._eat_up.add(button)
                    self.out.append(("toggle", 0))
                return True
            if kind == "up":
                if self._pending is not None:  # a quick click: it was meant for the app
                    self._pending = None
                    self.replay = "middle"
                    return True
                if button in self._eat_up:
                    self._eat_up.discard(button)
                    return True
                return False

        if kind == "up" and button == "left" and self.pressed:
            self.pressed = False
            self.pointer.append(("release", x, y))
            return True
        if kind == "up" and button in self._eat_up:
            self._eat_up.discard(button)
            return True

        if not self.active:
            return False
        inside = self._inside(x, y)
        if inside and self.window:
            return False                      # the rotary's own window takes the mouse over it
        if kind == "wheel":
            if not (inside or self.manual):
                return False                  # opened by a hand: scrolling elsewhere is the app's
            self._turn(delta)
            return True
        if kind == "down":
            if inside:
                if button == "left":
                    self.pressed = True
                    self.pointer.append(("press", x, y))
                    return True
                if button == "right":
                    self._eat_up.add(button)
                    self.out.append(("back", 0))
                    return True
                return False
            if self.manual:
                self._eat_up.add(button)
                self.out.append(("back", 0) if button == "right" else ("close", 0))
                return True
        return False

    def take(self) -> list[tuple[str, int]]:
        out, self.out = self.out, []
        return out

    def take_pointer(self) -> list[tuple[str, float, float]]:
        out, self.pointer = self.pointer, []
        return out


class MouseTrigger(QObject):
    """Runs MouseLogic against the real mouse and reports to the UI thread through `event`."""
    event = Signal(str, int)
    pointer = Signal(str, float, float)    # "press" | "drag" | "release" on the rotary, in the hook's coordinates
    # Windows and macOS hold the events we use back from the app; on Linux they reach it as well.
    holds_back = sys.platform in ("win32", "darwin")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = MouseLogic("")
        self._lock = threading.Lock()
        self._backend = None
        self._failed = False
        self.button = ""

    def configure(self, button: str, select: str = "off", close: str = "off") -> None:
        """button: "middle" | "back" | "forward" | "off". The mouse keeps working on the rotary either way.
        select / close: the button that opens the highlighted item / closes the rotary while it's open
        ("left" / "right" / "middle" / "back" / "forward" | "off")."""
        button = button if button in BUTTONS else ""
        if button != self.button:
            self._failed = False          # a new setting (or a permission granted since): worth another try
        self.button = button
        with self._lock:
            self.logic.button = button
            self.logic.select = select if select in SELECT_BUTTONS else ""
            self.logic.close = close if close in CLOSE_BUTTONS else ""
        if self._backend is None and not self._failed:
            try:
                self._backend = _WindowsHook(self) if sys.platform == "win32" else _PynputWatch(self)
            except Exception:
                log.warning("can't watch the mouse on this system", exc_info=True)
                self._backend = None
                self._failed = True       # don't try again on every settings change

    @property
    def running(self) -> bool:
        return self._backend is not None

    def set_active(self, on: bool, circle: tuple[float, float, float] | None = None,
                   zones: tuple[tuple[float, float, float, float], ...] = (), manual: bool = True,
                   window: bool = False) -> None:
        with self._lock:
            self.logic.set_active(on, circle, zones, manual, window)

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()
            self._backend = None

    # -- from the backend's thread
    def handle(self, kind: str, button: str, x: float, y: float, delta: int = 0) -> tuple[bool, str | None]:
        with self._lock:
            swallow = self.logic.feed(kind, button, x, y, delta)
            replay = self.logic.replay
            out = self.logic.take()
            pts = self.logic.take_pointer()
        for name, n in out:
            self.event.emit(name, n)
        for name, px, py in pts:
            self.pointer.emit(name, px, py)
        return swallow, replay

    def tick(self) -> None:
        with self._lock:
            self.logic.tick(time.monotonic())
            out = self.logic.take()
        for name, n in out:
            self.event.emit(name, n)


# --------------------------------------------------------------------------- Windows: low-level mouse hook
class _WindowsHook:
    WM_QUIT, WM_TIMER, WM_APP = 0x0012, 0x0113, 0x8000
    MSGS = {0x0201: ("down", "left"), 0x0202: ("up", "left"), 0x0204: ("down", "right"), 0x0205: ("up", "right"),
            0x0207: ("down", "middle"), 0x0208: ("up", "middle"), 0x020A: ("wheel", ""), 0x0200: ("move", "")}

    def __init__(self, owner: MouseTrigger):
        self.owner = owner
        self.tid = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mouse-hook")
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_size_t]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.tid = kernel32.GetCurrentThreadId()
        owner = self.owner
        hook = [None]

        def proc(code, wparam, lparam):
            if code == 0:
                try:
                    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    msg = int(wparam)
                    if msg == 0x0200 and not owner.logic.wants_moves:
                        pass                       # moves only matter while the middle button is held or dragging
                    elif not (info.flags & 1 and info.dwExtraInfo == TAG):      # not our own replay
                        kind, button = self.MSGS.get(msg, ("", ""))
                        if msg in (0x020B, 0x020C):                               # side buttons
                            kind = "down" if msg == 0x020B else "up"
                            button = {1: "back", 2: "forward"}.get((info.mouseData >> 16) & 0xFFFF, "")
                        if kind:
                            delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value if kind == "wheel" else 0
                            swallow, replay = owner.handle(kind, button, info.pt.x, info.pt.y, delta)
                            if replay:
                                user32.PostThreadMessageW(self.tid, self.WM_APP, 1 if replay == "middle" else 2, 0)
                            if swallow:
                                return 1
                except Exception:
                    log.debug("mouse hook error", exc_info=True)
            return user32.CallNextHookEx(hook[0], code, wparam, lparam)

        self._proc = HOOKPROC(proc)            # keep a reference: the hook calls it for as long as it's set
        hook[0] = user32.SetWindowsHookExW(14, self._proc, kernel32.GetModuleHandleW(None), 0)   # WH_MOUSE_LL
        if not hook[0]:
            log.warning("couldn't watch the mouse (error %d)", ctypes.get_last_error())
            self._ready.set()
            return
        user32.SetTimer(None, 0, 50, None)     # for the press-and-hold
        self._ready.set()
        log.info("mouse button opens the rotary (%s)", owner.button)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_TIMER:
                owner.tick()
            elif msg.message == self.WM_APP:
                # replay the middle button to the app: a whole click, or just the press of a drag
                user32.mouse_event(0x0020, 0, 0, 0, TAG)                         # MOUSEEVENTF_MIDDLEDOWN
                if msg.wParam == 1:
                    user32.mouse_event(0x0040, 0, 0, 0, TAG)                     # MOUSEEVENTF_MIDDLEUP
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook[0])

    def stop(self) -> None:
        if self.tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.tid, self.WM_QUIT, 0, 0)


# --------------------------------------------------------------------------- macOS / Linux: pynput
class _PynputWatch:
    def __init__(self, owner: MouseTrigger):
        from pynput import mouse
        self.owner = owner
        self._stop = threading.Event()
        names = {mouse.Button.left: "left", mouse.Button.right: "right", mouse.Button.middle: "middle"}
        for attr, name in (("x1", "back"), ("x2", "forward"), ("button8", "back"), ("button9", "forward")):
            b = getattr(mouse.Button, attr, None)
            if b is not None:
                names[b] = name
        self._names = names
        kw = {}
        if sys.platform == "darwin":
            kw["darwin_intercept"] = self._intercept
        else:
            kw.update(on_move=lambda x, y: owner.logic.wants_moves and self._on("move", "", x, y),
                      on_click=self._on_click,
                      on_scroll=lambda x, y, dx, dy: self._on("wheel", "", x, y, int(dy * 120)))
        self.listener = mouse.Listener(**kw)
        self.listener.daemon = True
        self.listener.start()
        threading.Thread(target=self._ticker, daemon=True, name="mouse-hold").start()

    def _ticker(self) -> None:
        while not self._stop.wait(0.05):
            self.owner.tick()

    def _on_click(self, x, y, button, pressed) -> None:
        self._on("down" if pressed else "up", self._names.get(button, ""), x, y)

    def _on(self, kind: str, button: str, x: float, y: float, delta: int = 0) -> None:
        # Linux can't hold events back: they've reached the app already, so there's nothing to replay either.
        self.owner.handle(kind, button, x, y, delta)

    def _intercept(self, event_type, event):
        """macOS event tap: return None to hold the event back."""
        import Quartz
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == TAG:
            return event
        loc = Quartz.CGEventGetLocation(event)
        kinds = {Quartz.kCGEventLeftMouseDown: ("down", "left"), Quartz.kCGEventLeftMouseUp: ("up", "left"),
                 Quartz.kCGEventRightMouseDown: ("down", "right"), Quartz.kCGEventRightMouseUp: ("up", "right"),
                 Quartz.kCGEventOtherMouseDown: ("down", ""), Quartz.kCGEventOtherMouseUp: ("up", ""),
                 Quartz.kCGEventScrollWheel: ("wheel", ""), Quartz.kCGEventMouseMoved: ("move", ""),
                 Quartz.kCGEventOtherMouseDragged: ("move", ""), Quartz.kCGEventLeftMouseDragged: ("move", "")}
        kind, button = kinds.get(event_type, ("", ""))
        if not kind:
            return event
        delta = 0
        if kind in ("down", "up") and not button:
            n = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
            button = {2: "middle", 3: "back", 4: "forward"}.get(int(n), "")
        if kind == "wheel":
            delta = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventDeltaAxis1)) * 120
        swallow, replay = self.owner.handle(kind, button, loc.x, loc.y, delta)
        if replay:
            for t in ((Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp) if replay == "middle"
                      else (Quartz.kCGEventOtherMouseDown,)):
                ev = Quartz.CGEventCreateMouseEvent(None, t, loc, 2)
                Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, TAG)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        return None if swallow else event

    def stop(self) -> None:
        self._stop.set()
        try:
            self.listener.stop()
        except Exception:
            pass
