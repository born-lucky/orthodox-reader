"""Reader: breaks with prayers, Scripture and the lives of the saints.

    python main.py          open the window
    python main.py --tray   start quietly in the tray (how Windows starts it at logon)
"""

from __future__ import annotations

import ctypes
import logging
import sys

from reader import settings


def single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, r"Local\OrthodoxReader")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


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
    if not single_instance():
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
    App(start_hidden="--tray" in sys.argv).run()


if __name__ == "__main__":
    main()
