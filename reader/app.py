"""The main window, the tray icon, and the scheduler that starts the breaks."""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from . import activity, audio, overlay, settings, speech, webui
from . import library as lib
from . import theme as t
from .library import Library
from .lockdown import Lock
from .session import Session

log = logging.getLogger("reader")



def cross_image(size: int = 64) -> Image.Image:
    """The three-bar cross, parchment on a cinnabar field with a gold rim: the tray and window icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, size - 2, size - 2), fill=(168, 38, 27, 255), outline=(201, 162, 74, 255), width=max(2, size // 20))
    s = size / 64
    gold = (243, 234, 214, 255)
    d.rectangle((29 * s, 9 * s, 35 * s, 55 * s), fill=gold)          # upright
    d.rectangle((24 * s, 15 * s, 40 * s, 19 * s), fill=gold)         # title board
    d.rectangle((16 * s, 23 * s, 48 * s, 28 * s), fill=gold)         # arms
    d.polygon([(22 * s, 43 * s), (42 * s, 37 * s), (42 * s, 41 * s), (22 * s, 47 * s)], fill=gold)  # footrest
    return img


def julian(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=13)  # 1900-2099


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def spell_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes or not hours:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    return " and ".join(parts)


class App:
    def __init__(self, start_hidden: bool = False) -> None:
        self.cfg = settings.load()
        self.state = settings.load_state()
        self.tracker = activity.Tracker(self.state)
        self.library = Library()
        self.events: queue.Queue = queue.Queue()
        self.session: Session | None = None
        self.screen = None
        self.lock: Lock | None = None
        self.notice = None
        self.tray = None
        self._stop = threading.Event()

        t.load_fonts()
        # Tk only draws the break screens; the window is a local page (webui).
        root = self.root = tk.Tk()
        root.withdraw()
        self._icon = ImageTk.PhotoImage(cross_image(64))
        root.iconphoto(True, self._icon)
        self.web = webui.Server(self.web_state, self.web_call, self.today_icon, self.web_month)
        if not start_hidden:
            self.web.open_window()
        threading.Thread(target=self.library.prefetch, args=(self._stop,), name="reader-prefetch", daemon=True).start()
        threading.Thread(target=self._fetch_today, daemon=True).start()
        self._tray()
        root.after(500, self._pump)
        root.after(1000, self._tick)

    # ------------------------------------------------------------ the window (webui)

    def today_icon(self) -> str:
        today = dt.date.today()
        return overlay.pick_icon(self.library.headline(today), seed=today.toordinal())

    def web_state(self) -> dict:
        """Everything the page shows, read on the server thread."""
        today = dt.date.today()
        j = julian(today)
        data = self.library.day(today, fetch=False)
        info = lib.summary(data) if data else {}
        reading = self.session is not None
        if reading:
            headline, sub = self.session.title or "Reading", "Stop when you need to."
        elif not self.cfg["enabled"]:
            headline, sub = "Breaks are paused", "Readings are still a click away."
        else:
            headline = f"Next break in {spell_time(self.tracker.until_break(self.cfg))}"
            sub = f"{spell_time(self.tracker.state.get('active', 0))} of work since the last break"
        work = self.cfg["work_minutes"] * 60
        return {
            "cfg": self.cfg,
            "voices": settings.VOICES,
            "status": {
                "day": today.isoformat(),
                "date": f"{today:%A}, {today:%B} {today.day} · {j:%B} {j.day}, old style",
                "feast": info.get("title", ""),
                "today": info,
                "headline": headline, "sub": sub,
                "progress": 0 if reading else min(1.0, self.tracker.state.get("active", 0) / work),
                "enabled": self.cfg["enabled"], "reading": reading,
            },
        }

    def web_month(self, year: int, month: int) -> dict:
        days = self.library.civil_month(year, month)
        out = []
        for day, data in sorted(days.items()):
            item = lib.summary(data)
            civil = dt.date(year, month, day)
            j = julian(civil)
            item.update(day=day, old=f"{j:%B} {j.day}", weekday=civil.weekday(), iso=civil.isoformat())
            out.append(item)
        return {"year": year, "month": month, "name": dt.date(year, month, 1).strftime("%B %Y"),
                "first": dt.date(year, month, 1).weekday(), "days": out}

    def web_call(self, kind: str, payload: dict) -> None:
        """From the server thread: queue it for the Tk thread."""
        if kind == "set" and payload.get("key") in settings.DEFAULTS:
            self.events.put(("set", (payload["key"], payload.get("value"))))
        elif kind == "do":
            self.events.put(("do", payload.get("action")))

    def _set(self, key: str, value) -> None:
        kind = type(settings.DEFAULTS[key])
        try:
            value = kind(value) if kind is not bool else bool(value)
        except (TypeError, ValueError):
            return
        old_autostart = self.cfg["autostart"]
        self.cfg[key] = value
        settings.save(self.cfg)
        if key == "autostart" and value != old_autostart:
            try:
                settings.set_autostart(value)
            except OSError:
                log.exception("autostart")

    def _do(self, action: str) -> None:
        if action == "break":
            self.start("break", self.cfg["mode"])
        elif action == "listen":
            self.start("break", "listen")
        elif action == "today":
            self.start("morning", self.cfg["mode"])
        elif action == "stop":
            self.stop_session()
        elif action == "toggle":
            self.set_enabled(not self.cfg["enabled"])
        elif action == "hear":
            self.test_voice()

    def set_enabled(self, on: bool) -> None:
        self.cfg["enabled"] = on
        if on:
            self.tracker.took_break()  # a fresh two hours, not an instant break
        settings.save(self.cfg)
        self._tray_update()

    # ------------------------------------------------------------ tray

    def _tray(self) -> None:
        try:
            import pystray
        except ImportError:
            return
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Open", lambda: self.events.put(("show", None)), default=True),
            item("Breaks enabled", lambda: self.events.put(("toggle", None)), checked=lambda i: self.cfg["enabled"]),
            item("Take a break now", lambda: self.events.put(("start", "break"))),
            item("Listen while I work", lambda: self.events.put(("listen", None))),
            item("Stop the reading", lambda: self.events.put(("stop", None))),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: self.events.put(("quit", None))),
        )
        self.tray = pystray.Icon("OrthodoxReader", cross_image(64), "Reader", menu)
        self.tray.run_detached()

    def _tray_update(self) -> None:
        if self.tray:
            self.tray.update_menu()

    def show(self) -> None:
        self.web.open_window()

    # ------------------------------------------------------------ the loop

    def _pump(self) -> None:
        """Events from other threads (session, tray), handled on the Tk thread."""
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "title" and self.screen:
                    self.screen.set_title(value)
                elif kind == "caption" and self.screen:
                    self.screen.set_caption(value)
                elif kind == "end":
                    self._ended(value)
                elif kind == "show":
                    self.show()
                elif kind == "toggle":
                    self.set_enabled(not self.cfg["enabled"])
                elif kind == "start":
                    self.start(value, self.cfg["mode"])
                elif kind == "listen":
                    self.start("break", "listen")
                elif kind == "stop":
                    self.stop_session()
                elif kind == "emergency":
                    self.stop_session()
                elif kind == "set":
                    self._set(*value)
                elif kind == "do":
                    self._do(value)
                elif kind == "today":
                    pass
                elif kind == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _tick(self) -> None:
        now = time.time()
        idle = activity.idle_seconds()
        if self.session is None:
            for event in self.tracker.tick(now, idle, self.cfg):
                self._on(event)
        else:
            self.tracker.state["last_seen"] = now - min(idle, 60)  # the break is not an absence
        settings.save_state(self.tracker.state)
        self.root.after(activity.POLL * 1000, self._tick)

    def _on(self, event: str) -> None:
        log.info("event %s", event)
        if event == "new_day":
            threading.Thread(target=self._fetch_today, daemon=True).start()
            if self.cfg["enabled"] and self.cfg["morning"]:
                self._heads_up("The morning prayers and the day's readings", "morning")
        elif event == "warn" and self.cfg["enabled"]:
            self._heads_up("Time for a break", "break")
        elif event == "break" and self.cfg["enabled"] and self.notice is None:
            self.start("break", self.cfg["mode"])

    def _heads_up(self, text: str, kind: str) -> None:
        if self.notice is not None:
            return
        seconds = int(self.cfg["warn_seconds"])

        def now():
            self.notice = None
            self.start(kind, self.cfg["mode"])

        def snooze():
            self.notice = None
            self.tracker.snooze(time.time(), 10)

        self.notice = overlay.Notice(self.root, seconds, text, snooze, now)
        if kind == "morning":  # the morning session starts by itself after the countdown
            self.root.after(seconds * 1000, lambda: self.notice is not None and (self.notice.close(), now()))
        else:
            self.root.after(seconds * 1000, lambda: setattr(self, "notice", None) if self.notice else None)

    def _fetch_today(self) -> None:
        self.library.day(dt.date.today())
        self.events.put(("today", None))

    # ------------------------------------------------------------ sessions

    def start(self, kind: str, mode: str) -> None:
        if self.session is not None:
            return
        if self.notice is not None:
            self.notice.close()
            self.notice = None
        minutes = self.cfg["morning_minutes"] if kind == "morning" else self.cfg["break_minutes"]
        end = time.monotonic() + minutes * 60
        if mode == "lock":
            hint = "Hold Ctrl+Alt+Shift+End for 3 seconds to end early" if self.cfg["emergency_exit"] else ""
            self.screen = overlay.Overlay(self.root, self.cfg["screen"], overlay.pick_icon(self.library.headline(dt.date.today())),
                                          end, hide_cursor=True)
            self.screen.set_hint(hint)
            self.lock = Lock(minutes * 60 + 150, self.cfg["emergency_exit"], lambda: self.events.put(("emergency", None)))
            self.lock.start()
        elif self.cfg["read_along"]:
            self.screen = overlay.ReadAlong(self.root, end, self.stop_session)
        self.session = Session(kind, self.cfg, self.library, self._post, minutes)
        self.session.start()

    def _post(self, kind: str, value: str) -> None:
        if kind == "title" and self.session is not None:
            self.session.title = value
        self.events.put((kind, value))

    def stop_session(self) -> None:
        if self.session is not None:
            self.session.stop()
        else:
            self._ended("stopped")

    def _ended(self, reason: str) -> None:
        if self.lock is not None:
            self.lock.stop()
            self.lock = None
        if self.screen is not None:
            self.screen.close()
            self.screen = None
        self.session = None
        self.tracker.took_break()
        settings.save_state(self.tracker.state)

    def test_voice(self) -> None:
        cfg = dict(self.cfg)

        def run():
            try:
                path = speech.Voice(cfg).make("Glory to Thee, our God, glory to Thee.")
                sound = audio.Sound(path, cfg["voice_volume"])
                sound.play()
                time.sleep(0.2)
                while sound.playing():
                    time.sleep(0.1)
                sound.close()
            except Exception:
                log.exception("test voice")

        threading.Thread(target=run, daemon=True).start()

    def quit(self) -> None:
        self._stop.set()
        if self.session is not None:
            self.session.stop()
        if self.lock is not None:
            self.lock.stop()
        if self.tray:
            self.tray.stop()
        self.web.close()
        settings.save_state(self.tracker.state)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
