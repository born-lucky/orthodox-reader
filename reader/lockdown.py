"""Locks the mouse and keyboard during a break, with low-level input hooks.

No administrator rights are needed. Ctrl+Alt+Del always still works (Windows
does not let any program block it), so you can never be truly locked out.

Safety:
  * Emergency exit: hold Ctrl+Alt+Shift+End for 3 seconds (if enabled).
  * Dead-man timer: the lock releases itself after `limit` seconds no matter what.
  * The hooks die with the process, so a crash also releases the lock.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_END = 0x23
VK_SHIFT = (0xA0, 0xA1, 0x10)
VK_CONTROL = (0xA2, 0xA3, 0x11)
VK_MENU = (0xA4, 0xA5, 0x12)
HOLD = 3.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class Lock:
    def __init__(self, limit: float, emergency: bool, on_emergency=None) -> None:
        self.limit = limit
        self.emergency = emergency
        self.on_emergency = on_emergency
        self._down: set[int] = set()
        self._combo_since: float | None = None
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hooks: list = []
        self._procs = (HOOKPROC(self._mouse), HOOKPROC(self._keyboard))  # keep references alive
        self.released = threading.Event()

    # ------------------------------------------------------------ hooks (keep them fast)

    def _mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code < 0:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        return 1  # swallow: the cursor stays still and clicks go nowhere

    def _keyboard(self, code: int, wparam: int, lparam: int) -> int:
        if code < 0:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        vk = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
        if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self._down.add(vk)
        elif wparam in (WM_KEYUP, WM_SYSKEYUP):
            self._down.discard(vk)
        if self.emergency:
            combo = (VK_END in self._down and any(k in self._down for k in VK_SHIFT)
                     and any(k in self._down for k in VK_CONTROL) and any(k in self._down for k in VK_MENU))
            if combo:
                self._combo_since = self._combo_since or time.monotonic()
            else:
                self._combo_since = None
        return 1

    # ------------------------------------------------------------ start / stop

    def start(self) -> None:
        if self._thread:
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="reader-lock", daemon=True)
        self._thread.start()
        ready.wait(2)
        threading.Thread(target=self._watch, name="reader-lock-watch", daemon=True).start()

    def _run(self, ready: threading.Event) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        module = kernel32.GetModuleHandleW(None)
        for kind, proc in ((WH_MOUSE_LL, self._procs[0]), (WH_KEYBOARD_LL, self._procs[1])):
            hook = user32.SetWindowsHookExW(kind, proc, module, 0)
            if hook:
                self._hooks.append(hook)
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for hook in self._hooks:
            user32.UnhookWindowsHookEx(hook)
        self._hooks.clear()
        self.released.set()

    def _watch(self) -> None:
        """The dead-man timer and the held emergency combo, outside the hook callback."""
        start = time.monotonic()
        while not self.released.is_set():
            if time.monotonic() - start > self.limit:
                self.stop()
                return
            since = self._combo_since
            if since and time.monotonic() - since >= HOLD:
                if self.on_emergency:
                    self.on_emergency()
                self.stop()
                return
            time.sleep(0.1)

    def stop(self) -> None:
        if self._tid and not self.released.is_set():
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self.released.wait(2)

    @property
    def active(self) -> bool:
        return bool(self._hooks) and not self.released.is_set()
