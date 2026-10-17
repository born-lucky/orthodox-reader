"""Reader: breaks with prayers, Scripture and the lives of the saints.

    python main.py          open the window
    python main.py --tray   start quietly in the tray (how Windows starts it at logon)
    python main.py --guard  extreme mode: brings Reader back if it is closed
"""

from __future__ import annotations

import ctypes
import logging
import sys

from reader import settings


SHOW = r"Local\OrthodoxReaderShow"  # set by a second launch: the running Reader opens its window


def single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, r"Local\OrthodoxReader")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def ask_to_show() -> None:
    """Already running (in the tray): ask it to open the window, so the taskbar pin works."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.restype = ctypes.c_void_p
    event = kernel32.OpenEventW(0x0002, False, SHOW)  # EVENT_MODIFY_STATE
    if event:
        kernel32.SetEvent(ctypes.c_void_p(event))
        kernel32.CloseHandle(ctypes.c_void_p(event))


def main() -> None:
    logging.basicConfig(filename=settings.LOG, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "--check-audio" in sys.argv:  # the whole voice path, inside the built exe; result in reader.log
        from reader import speech

        path = speech.Voice(settings.load()).make("Glory to Thee, our God, glory to Thee.")
        logging.getLogger("reader").info("check-audio: %s (%s)", path, "in the room" if path.endswith("-room.wav") else "DRY")
        from reader import ambience

        logging.getLogger("reader").info("check-audio: birdsong %s", ambience.ensure(settings.load()["ambience_volume"]))
        return
    if "--guard" in sys.argv:  # extreme mode: keeps Reader running (reader/guard.py)
        from reader import guard

        guard.run()
        return
    if not single_instance():
        if "--tray" not in sys.argv:
            ask_to_show()
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so the overlay covers each monitor exactly
    except (AttributeError, OSError):
        pass
    from reader.app import App

    cfg = settings.load()
    if cfg["autostart"]:
        try:
            settings.set_autostart(True)  # keeps the path current if the exe moved
        except OSError:
            pass
    App(start_hidden="--tray" in sys.argv, show_event=SHOW).run()


if __name__ == "__main__":
    main()
