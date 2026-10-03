"""One break: prayers, lives of the saints and Scripture, read aloud over birdsong.

Runs on its own thread. The screen (overlay or read-along window) learns what
is being read through `post(event, value)`:
  ("title", str)     a new prayer / chapter / life begins
  ("caption", str)   the chunk now being spoken
  ("end", reason)    the session is over: "done" | "stopped"
"""

from __future__ import annotations

import queue
import threading
import time

from . import ambience, audio, speech
from .library import Library, Segment, speaking_time
from .room import TAIL_SECONDS

CLOSING_RESERVE = 75   # seconds kept for the closing prayer
OVERRUN = 90           # the closing prayer may run this far past the end
GAP_CHUNK = 0.05  # the room's own tail is the pause between sentences
GAP_SEGMENT = 1.6


class Session(threading.Thread):
    def __init__(self, kind: str, cfg: dict, library: Library, post, minutes: float | None = None,
                 civil=None) -> None:
        super().__init__(name="reader-session", daemon=True)
        self.kind = kind
        self.cfg = dict(cfg)
        self.library = library
        self.post = post
        self.seconds = (minutes if minutes is not None else cfg["break_minutes"]) * 60
        self.civil = civil  # kind "day": the calendar day to read
        self._stop = threading.Event()
        self._sound: audio.Sound | None = None
        self._ringing: list = []  # sentences whose room echo is still sounding
        self.title = ""

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------ the run

    def run(self) -> None:
        reason = "done"
        amb = None
        try:
            speech.cleanup()
            start = time.monotonic()
            self.end = start + self.seconds
            if self.cfg["ambience"]:
                try:
                    amb = audio.Sound(ambience.ensure(self.cfg["ambience_volume"]), 0)
                    amb.play(loop=True)
                    threading.Thread(target=audio.fade, args=(amb, 0, 100, 4.0, self.stopped_fn),
                                     daemon=True).start()
                except OSError:
                    amb = None
            self.post("title", "Preparing the readings…")
            segments, closing = self.library.plan(self.kind, self.cfg, self.seconds / 60, civil=self.civil)
            voice = speech.Voice(self.cfg)
            closing_files = [(c, voice.make(c)) for line in (closing.lines if closing else []) for c in speech.chunks(line)]
            self._read(segments, voice)
            if closing_files and not self.stopped:
                self._play_segment(closing, closing_files, self.end + OVERRUN)
            if self.stopped:
                reason = "stopped"
        except Exception as exc:  # never leave a break hanging on an error
            self.post("caption", f"(Something went wrong: {exc})")
            reason = "stopped"
        finally:
            if amb is not None:
                audio.fade(amb, 100, 0, 3.0)
                amb.close()
            if self._sound is not None:
                self._sound.close()
            self._close_rung(everything=True)
            self.post("end", reason)

    def stopped_fn(self) -> bool:
        return self.stopped

    def _read(self, segments: list[Segment], voice: speech.Voice) -> None:
        """Speak the segments until only the closing reserve is left. Audio is made
        two chunks ahead on a producer thread, so there are no gaps."""
        ready: queue.Queue = queue.Queue(maxsize=3)
        done = threading.Event()

        def produce() -> None:
            pending = list(segments)
            while pending and not done.is_set():
                segment = pending.pop(0)
                for text in (c for line in segment.lines for c in speech.chunks(line)):
                    while not done.is_set():
                        try:
                            path = voice.make(text)
                            break
                        except Exception:
                            time.sleep(1)
                    else:
                        return
                    while not done.is_set():
                        try:
                            ready.put((segment, text, path), timeout=0.5)
                            break
                        except queue.Full:
                            pass
                if not pending:  # time to spare: one more whole work that fits, if any
                    spare = self.end - time.monotonic() - CLOSING_RESERVE - 60
                    extra = self.library.filler(self.cfg, spare) if spare > 60 else None
                    if extra is not None:
                        pending.append(extra)
            ready.put(None)

        threading.Thread(target=produce, name="reader-voice", daemon=True).start()
        current = None
        try:
            while not self.stopped and time.monotonic() < self.end:
                try:
                    item = ready.get(timeout=1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                segment, text, path = item
                if segment is not current:
                    # A work is begun only if it can be finished before the closing prayer.
                    if time.monotonic() + speaking_time(segment) > self.end - CLOSING_RESERVE + 30:
                        break
                    if current is not None:
                        self._wait(GAP_SEGMENT)
                    current = segment
                    self.post("title", segment.title)
                self._speak(text, path, self.end)
                self._wait(GAP_CHUNK)
        finally:
            done.set()

    def _play_segment(self, segment: Segment, files: list[tuple[str, str]], limit: float) -> None:
        self._wait(GAP_SEGMENT)
        self.post("title", segment.title)
        for text, path in files:
            if self.stopped or time.monotonic() > limit:
                return
            self._speak(text, path, limit)
            self._wait(GAP_CHUNK)

    def _speak(self, text: str, path: str, limit: float) -> None:
        self.post("caption", text)
        try:
            # The room files carry their own level; a dry fallback uses the player's volume.
            sound = audio.Sound(path, 100 if path.endswith("-room.wav") else self.cfg["voice_volume"])
        except OSError:
            return
        self._sound = sound
        # A sentence in the room ends with its echo; the next sentence begins while that
        # echo is still ringing, as in a real room, instead of after silence.
        tail = TAIL_SECONDS - 0.15 if path.endswith("-room.wav") else 0.0
        try:
            sound.play()
            started = time.monotonic()
            spoken = max(0.2, sound.length() - tail)
            time.sleep(0.15)
            while sound.playing() and not self.stopped and time.monotonic() < limit:
                if time.monotonic() - started >= spoken:
                    self._ringing.append((sound, started + spoken + tail + 0.3))
                    sound = None
                    break
                time.sleep(0.05)
        finally:
            if sound is not None:
                sound.close()
            self._sound = None
            self._close_rung()

    def _close_rung(self, everything: bool = False) -> None:
        now = time.monotonic()
        keep = []
        for sound, until in self._ringing:
            if everything or now >= until or not sound.playing():
                sound.close()
            else:
                keep.append((sound, until))
        self._ringing = keep

    def _wait(self, seconds: float) -> None:
        self._stop.wait(seconds)
