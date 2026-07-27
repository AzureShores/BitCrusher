"""Windows taskbar button progress (ITaskbarList3), stdlib-only.

ITaskbarList3 has no IDispatch/typelib - it's a plain C++ vtable interface
(shobjidl.h), so pywin32's win32com.client.Dispatch can't wrap it (verified:
raises "No such interface supported") and there's no way to reach it without
either a new dependency (comtypes) or raw ctypes vtable calls. This uses
ctypes only - already a hard dependency of the interpreter, so this feature
adds nothing to requirements.txt.

No-ops everywhere but Windows, and no-ops (never raises) if COM init or the
taskbar object fails for any reason - this is a cosmetic nice-to-have, never
worth crashing or blocking the app over.
"""
from __future__ import annotations

import ctypes
import os

_ENABLED = (os.name == "nt")

STATE_NONE = 0          # TBPF_NOPROGRESS
STATE_INDETERMINATE = 1  # TBPF_INDETERMINATE
STATE_NORMAL = 2         # TBPF_NORMAL
STATE_ERROR = 4          # TBPF_ERROR
STATE_PAUSED = 8         # TBPF_PAUSED

_obj = None       # cached ITaskbarList3 vtable wrapper, or False if unavailable
_com_inited = False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(a, b, c, d):
    return _GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))


_CLSID_TaskbarList = _guid(0x56FDF344, 0xFD6D, 0x11D0,
                           (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
_IID_ITaskbarList3 = _guid(0xEA1AFB91, 0x9E28, 0x4B86,
                           (0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF))

CLSCTX_INPROC_SERVER = 0x1


class _ITaskbarList3:
    """Thin manual vtable wrapper for the handful of methods used here.
    IUnknown (QueryInterface=0, AddRef=1, Release=2) + ITaskbarList
    (HrInit=3) + ... + ITaskbarList3 (SetProgressValue=9, SetProgressState=10)."""

    def __init__(self, ptr):
        self._ptr = ptr
        vtbl_type = ctypes.POINTER(ctypes.c_void_p)
        self._vtbl = ctypes.cast(ptr, ctypes.POINTER(vtbl_type))[0]

    def _call(self, index, restype, argtypes, *args):
        fn_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        fn = fn_type(self._vtbl[index])
        return fn(self._ptr, *args)

    def hr_init(self):
        return self._call(3, ctypes.c_long, [])

    def set_progress_value(self, hwnd, completed, total):
        return self._call(9, ctypes.c_long,
                          [ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong],
                          hwnd, completed, total)

    def set_progress_state(self, hwnd, state):
        return self._call(10, ctypes.c_long, [ctypes.c_void_p, ctypes.c_int],
                          hwnd, state)


def _get_obj():
    """Lazily create (once) and cache the ITaskbarList3 COM object. Returns
    None if unavailable for any reason (non-Windows, COM failure, ...)."""
    global _obj, _com_inited
    if _obj is not None:
        return _obj or None
    if not _ENABLED:
        _obj = False
        return None
    try:
        ole32 = ctypes.windll.ole32
        if not _com_inited:
            # COINIT_APARTMENTTHREADED; tolerate "already initialized" (S_FALSE/
            # RPC_E_CHANGED_MODE) since Tkinter/other libs may have inited COM already.
            ole32.CoInitializeEx(None, 0x2)
            _com_inited = True
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_TaskbarList), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_ITaskbarList3), ctypes.byref(ptr))
        if hr != 0 or not ptr:
            _obj = False
            return None
        wrapper = _ITaskbarList3(ptr)
        wrapper.hr_init()
        _obj = wrapper
        return wrapper
    except Exception:
        _obj = False
        return None


def _root_hwnd(hwnd: int) -> int:
    """Normalize a Tk window handle to the top-level HWND that actually owns
    the taskbar button.

    Tk's winfo_id() returns the INNER window, not the frame Windows puts on
    the taskbar - measured on this app: winfo_id() is class "TkChild" while
    its GA_ROOT ancestor is class "TkTopLevel". ITaskbarList3 silently
    ignores a child HWND (returns S_OK, draws nothing), which is why the
    progress fill never appeared. Walking up to GA_ROOT here rather than at
    the call sites means every caller is fixed at once and passing an
    already-top-level HWND stays correct (GetAncestor returns it unchanged).
    """
    try:
        root = ctypes.windll.user32.GetAncestor(ctypes.c_void_p(int(hwnd)), 2)  # GA_ROOT
        return int(root) or int(hwnd)
    except Exception:
        return int(hwnd)


def set_progress(hwnd: int, completed: int, total: int) -> None:
    """Show a determinate progress fill on the taskbar button for `hwnd`."""
    try:
        obj = _get_obj()
        if obj is None or not hwnd or total <= 0:
            return
        hwnd = _root_hwnd(hwnd)
        obj.set_progress_state(hwnd, STATE_NORMAL)
        obj.set_progress_value(hwnd, max(0, int(completed)), max(1, int(total)))
    except Exception:
        pass


def set_state(hwnd: int, state: int) -> None:
    try:
        obj = _get_obj()
        if obj is None or not hwnd:
            return
        obj.set_progress_state(_root_hwnd(hwnd), state)
    except Exception:
        pass


def clear(hwnd: int) -> None:
    set_state(hwnd, STATE_NONE)
