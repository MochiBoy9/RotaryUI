"""Pressing a button inside another app's window through UI Automation — the same route a screen reader
takes, so it needs no shortcut set up in that app and doesn't bring its window to the front.

Just enough of the IUIAutomation COM interfaces, called through their vtables with ctypes. Windows only;
everywhere else press_button() says it couldn't."""
from __future__ import annotations

import ctypes
import sys
import time
import uuid
from ctypes import wintypes

WIN = sys.platform == "win32"

CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
IID_IUIAutomationInvokePattern = "{fb377fbe-8ea6-46d5-9c73-6499642d3059}"
UIA_InvokePatternId = 10000
UIA_ControlTypePropertyId, UIA_NamePropertyId = 30003, 30005
UIA_ButtonControlTypeId = 50000
TreeScope_Descendants = 4
VT_I4, VT_BSTR = 3, 8

# vtable slots (IUnknown takes 0–2)
_RELEASE = 2
_ELEMENT_FROM_HANDLE, _CREATE_PROPERTY_CONDITION, _CREATE_AND_CONDITION = 6, 23, 25   # IUIAutomation
_FIND_FIRST, _GET_CURRENT_PATTERN_AS = 5, 14                                          # IUIAutomationElement
_INVOKE = 3                                                                           # IUIAutomationInvokePattern


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        g = cls()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
        return g


class VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("value", ctypes.c_void_p), ("extra", ctypes.c_void_p)]


def _method(obj: ctypes.c_void_p, slot: int, *argtypes):
    """A COM method of `obj` (an interface pointer); calling it raises OSError on a failed HRESULT."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[slot])
    return lambda *args: fn(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[_RELEASE])(obj)


def press_button(hwnd: int, names: tuple[str, ...], tries: int = 4) -> str:
    """Press the first button in the window `hwnd` named one of `names` (its accessible name, compared exactly).
    Returns the name pressed, or "" if none was found or UI Automation isn't available. Chromium-based apps
    (Discord, Teams, browsers) build their accessibility tree only once something asks for it, so the first
    look can come back empty: it looks again a few times."""
    if not WIN or not hwnd or not names:
        return ""
    ole32 = ctypes.WinDLL("ole32")
    oleaut32 = ctypes.WinDLL("oleaut32")
    oleaut32.SysAllocString.argtypes = [wintypes.LPCWSTR]
    oleaut32.SysAllocString.restype = ctypes.c_void_p
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    inited = ole32.CoInitializeEx(None, 0x2) >= 0          # COINIT_APARTMENTTHREADED; S_FALSE is fine too
    held: list[ctypes.c_void_p] = []
    try:
        auto = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(GUID.of(CLSID_CUIAutomation)), None, 1,       # CLSCTX_INPROC_SERVER
                                  ctypes.byref(GUID.of(IID_IUIAutomation)), ctypes.byref(auto)) < 0 or not auto:
            return ""
        held.append(auto)
        out = ctypes.POINTER(ctypes.c_void_p)
        root = ctypes.c_void_p()
        _method(auto, _ELEMENT_FROM_HANDLE, ctypes.c_void_p, out)(ctypes.c_void_p(hwnd), ctypes.byref(root))
        if not root:
            return ""
        held.append(root)
        prop_cond = _method(auto, _CREATE_PROPERTY_CONDITION, ctypes.c_int, VARIANT, out)
        and_cond = _method(auto, _CREATE_AND_CONDITION, ctypes.c_void_p, ctypes.c_void_p, out)
        button = ctypes.c_void_p()
        prop_cond(UIA_ControlTypePropertyId, VARIANT(vt=VT_I4, value=UIA_ButtonControlTypeId), ctypes.byref(button))
        held.append(button)
        wanted = []
        for name in names:
            bstr = oleaut32.SysAllocString(name)
            try:
                named, both = ctypes.c_void_p(), ctypes.c_void_p()
                prop_cond(UIA_NamePropertyId, VARIANT(vt=VT_BSTR, value=bstr), ctypes.byref(named))
                held.append(named)
                and_cond(button, named, ctypes.byref(both))
                held.append(both)
                wanted.append((name, both))
            finally:
                oleaut32.SysFreeString(bstr)
        find = _method(root, _FIND_FIRST, ctypes.c_int, ctypes.c_void_p, out)
        for attempt in range(tries):
            for name, cond in wanted:
                found = ctypes.c_void_p()
                find(TreeScope_Descendants, cond, ctypes.byref(found))
                if not found:
                    continue
                held.append(found)
                pattern = ctypes.c_void_p()
                _method(found, _GET_CURRENT_PATTERN_AS, ctypes.c_int, ctypes.POINTER(GUID), out)(
                    UIA_InvokePatternId, ctypes.byref(GUID.of(IID_IUIAutomationInvokePattern)), ctypes.byref(pattern))
                if not pattern:
                    continue
                held.append(pattern)
                _method(pattern, _INVOKE)()
                return name
            if attempt < tries - 1:
                time.sleep(0.35)
        return ""
    except OSError:
        return ""
    finally:
        for obj in reversed(held):
            _release(obj)
        if inited:
            ole32.CoUninitialize()
