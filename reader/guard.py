"""Extreme mode: two processes that keep each other alive.

Reader holds the mutex Local\\OrthodoxReader; the guard (Reader --guard) holds
Local\\OrthodoxReaderGuard. Each checks the other every few seconds and starts
it again when it is gone. A Reader killed in the middle of a break comes back
and the break starts over (App._resume).

Nothing here needs administrator rights, and none of it touches Windows itself:
Ctrl+Alt+Del, signing out and shutting down all still work.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import time

from . import settings

POLL = 3
MAIN = r"Local\OrthodoxReader"
GUARD = r"Local\OrthodoxReaderGuard"
SYNCHRONIZE = 0x00100000
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: not killed with whatever started us

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenMutexW.restype = ctypes.c_void_p
kernel32.CreateMutexW.restype = ctypes.c_void_p
log = logging.getLogger("reader")


def _exists(name: str) -> bool:
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return True
    return False


def alive() -> bool:
    return _exists(GUARD)


def spawn(*args: str) -> None:
    cmd = settings.launch_args(*args)
    for flags in (DETACHED | BREAKAWAY, DETACHED):  # breakaway is refused inside some jobs
        try:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
            log.info("guard: started %s", " ".join(args))
            return
        except OSError:
            continue
    log.error("guard: could not start %s", " ".join(args))


def run() -> None:
    """The guard's whole life: while extreme, keep Reader running."""
    kernel32.CreateMutexW(None, False, GUARD)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: a guard is already watching
        return
    log.info("guard: watching")
    while settings.load()["extreme"]:
        if not _exists(MAIN):
            time.sleep(1)  # Reader may just be restarting itself
            if not _exists(MAIN):
                spawn("--tray")
                time.sleep(POLL * 2)  # give it time to take its mutex
        time.sleep(POLL)
