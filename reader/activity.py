"""How long you have been at the computer, and when a new day begins.

Windows keeps the time of the last mouse or keyboard input (GetLastInputInfo).
Polling it every few seconds gives two things:

  * active time: seconds of use since the last break. Two hours of it -> a break.
  * absences: when you come back after being away. The absence is measured from
    the last input before you left, so it also covers sleep/hibernate and the PC
    being off (the last-seen time is saved to disk). A long absence (5 hours by
    default) is a night's sleep, so a new day begins: the day's readings are
    fetched and the morning session runs. A shorter absence of 15 minutes or
    more already was a break, so the two-hour count starts over.
"""

from __future__ import annotations

import ctypes
import datetime as dt
from ctypes import wintypes

POLL = 5               # seconds between ticks
ACTIVE_IDLE = 300      # input within this many seconds counts as being at the computer


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


class Tracker:
    """Pure bookkeeping (no Windows calls) so it can be tested with fake clocks.

    state is the dict saved to state.json:
      last_seen  epoch seconds of the last input we saw
      active     seconds of use since the last break
      day        ISO date of the day that began after the last sleep
      warned     the "break in a minute" warning was shown for this cycle
      snooze     epoch seconds before which no break starts
    """

    def __init__(self, state: dict) -> None:
        self.state = state
        self._poll: float | None = None

    def tick(self, now: float, idle: float, cfg: dict) -> list[str]:
        st = self.state
        events: list[str] = []
        step = 0.0 if self._poll is None else now - self._poll
        self._poll = now
        if idle >= ACTIVE_IDLE:
            return events  # away right now; the absence is judged on return
        seen = now - idle
        last = st.get("last_seen")
        absence = seen - last if last else None
        if absence is None or absence >= cfg["sleep_hours"] * 3600 or not st.get("day"):
            st["day"] = dt.date.fromtimestamp(now).isoformat()
            self._restart_cycle()
            events.append("new_day")
        elif absence >= cfg["away_minutes"] * 60:
            self._restart_cycle()
            events.append("rested")
        elif 0 < step <= POLL * 6:  # a bigger step is a suspend we did not see as idle
            st["active"] = st.get("active", 0.0) + step
        st["last_seen"] = seen
        work = cfg["work_minutes"] * 60
        active = st.get("active", 0.0)
        if active >= work - cfg["warn_seconds"] and now >= st.get("snooze", 0) - cfg["warn_seconds"] and not st.get("warned"):
            st["warned"] = True
            events.append("warn")
        if active >= work and now >= st.get("snooze", 0):
            events.append("break")
        return events

    def took_break(self) -> None:
        self._restart_cycle()

    def snooze(self, now: float, minutes: float) -> None:
        self.state["snooze"] = now + minutes * 60
        self.state["warned"] = False  # warn again before the snoozed break

    def until_break(self, cfg: dict) -> float:
        return max(0.0, cfg["work_minutes"] * 60 - self.state.get("active", 0.0))

    def _restart_cycle(self) -> None:
        self.state.update(active=0.0, warned=False, snooze=0)
