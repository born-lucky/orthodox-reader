"""Sound through Windows MCI (winmm): plays MP3 and WAV with no extra libraries.

Each sound is an MCI alias. All calls for one alias should come from one thread;
the session thread owns its sounds.
"""

from __future__ import annotations

import ctypes
import itertools
import time

_winmm = ctypes.windll.winmm
_ids = itertools.count(1)


def mci(command: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(command, buf, 255, None)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        _winmm.mciGetErrorStringW(err, msg, 255)
        raise OSError(f"MCI: {msg.value} ({command})")
    return buf.value


class Sound:
    def __init__(self, path: str, volume: int = 100) -> None:
        self.alias = f"obx{next(_ids)}"
        mci(f'open "{path}" type mpegvideo alias {self.alias}')
        self.volume(volume)

    def volume(self, percent: float) -> None:
        level = int(max(0, min(100, percent)) * 10)
        try:
            mci(f"setaudio {self.alias} volume to {level}")
        except OSError:
            pass

    def play(self, loop: bool = False) -> None:
        mci(f"play {self.alias} from 0" + (" repeat" if loop else ""))

    def pause(self) -> None:
        mci(f"pause {self.alias}")

    def resume(self) -> None:
        mci(f"resume {self.alias}")

    def playing(self) -> bool:
        try:
            return mci(f"status {self.alias} mode") == "playing"
        except OSError:
            return False

    def length(self) -> float:
        try:
            mci(f"set {self.alias} time format milliseconds")
            return int(mci(f"status {self.alias} length")) / 1000.0
        except (OSError, ValueError):
            return 0.0

    def close(self) -> None:
        try:
            mci(f"close {self.alias}")
        except OSError:
            pass


def fade(sound: Sound, start: float, end: float, seconds: float, stop=None) -> None:
    steps = max(1, int(seconds * 10))
    for n in range(steps + 1):
        if stop is not None and stop():
            return
        sound.volume(start + (end - start) * n / steps)
        time.sleep(seconds / steps)
