"""The main window, the tray icon, and the scheduler that starts the breaks."""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

from . import activity, audio, overlay, settings, speech, webui
from . import library as lib
from . import theme as t
from .library import Library
from .lockdown import Lock
from .session import Session

log = logging.getLogger("reader")



ANGEL = settings.DATA / "icons" / "angel_golden_hair.jpg"


def angel_image(size: int = 64) -> Image.Image:
    """The face of the Angel with the Golden Hair (Novgorod, c. 1200): the tray and window icon."""
    img = Image.open(ANGEL).convert("RGB")
    w, h = img.size
    side = int(w * 0.625)
    left, top = int(w * 0.18), int(h * 0.205)
    return img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)


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
        self._icon = ImageTk.PhotoImage(angel_image(64))
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
        return str(ANGEL)  # the window's one image: the angel

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
                "first": dt.date(year, month, 1).weekday(), "days": out, "ahead": self._ahead()}

    def _ahead(self) -> dict:
        """The next great feast and the next fast season, from today, with days to go."""
        today = dt.date.today()
        feast = season = None
        y, m = today.year, today.month
        current_season = None
        for _ in range(5):
            for day, data in sorted(self.library.civil_month(y, m).items()):
                civil = dt.date(y, m, day)
                if civil < today:
                    continue
                info = lib.summary(data)
                name = (data.get("fast_level_desc") or "").strip()
                seasonal = name and name.lower() not in ("fast", "no fast", "fast free")
                if civil == today:
                    current_season = name if seasonal else None
                if feast is None and info["feast"] == "great" and civil > today:
                    feast = {"name": (info["feasts"] or [info["title"]])[0], "iso": civil.isoformat(),
                             "days": (civil - today).days}
                if season is None and seasonal and name != current_season and civil > today:
                    season = {"name": name, "iso": civil.isoformat(), "days": (civil - today).days}
            if feast and season:
                break
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return {"feast": feast, "season": season, "now": current_season}

    def web_call(self, kind: str, payload: dict) -> None:
        """From the server thread: queue it for the Tk thread."""
        if kind == "set" and payload.get("key") in settings.DEFAULTS:
            self.events.put(("set", (payload["key"], payload.get("value"))))
        elif kind == "do":
            self.events.put(("do", payload.get("action") if not payload.get("day") else ("day", payload["day"])))

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

    def _do(self, action) -> None:
        if isinstance(action, tuple) and action[0] == "day":  # read a chosen calendar day
            try:
                self.start("day", "listen", civil=dt.date.fromisoformat(action[1]))
            except ValueError:
                pass
            return
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
        self.tray = pystray.Icon("OrthodoxReader", angel_image(64), "Reader", menu)
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
        if event == "new_day" and self.cfg.get("fast_reminder", True):
            self.root.after(8000, self._fast_note)
        if event == "new_day":
            threading.Thread(target=self._fetch_today, daemon=True).start()
            if self.cfg["enabled"] and self.cfg["morning"]:
                self._heads_up("The morning prayers and the day's readings", "morning")
        elif event == "warn" and self.cfg["enabled"]:
            self._heads_up("Time for a break", "break")
        elif event == "break" and self.cfg["enabled"] and self.notice is None:
            self.start("break", self.cfg["mode"])

    def _fast_note(self) -> None:
        """At the start of the day: is today a fast day? A quiet note that leaves by itself."""
        data = self.library.day(dt.date.today(), fetch=False)
        if not data:
            return
        info = lib.summary(data)
        overlay.Toast(self.root, "Today: " + info["fast_text"], info.get("title", ""))

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

    def start(self, kind: str, mode: str, civil: dt.date | None = None) -> None:
        if self.session is not None:
            return
        if self.notice is not None:
            self.notice.close()
            self.notice = None
        minutes = self.cfg["morning_minutes"] if kind in ("morning", "day") else self.cfg["break_minutes"]
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
        self.session = Session(kind, self.cfg, self.library, self._post, minutes, civil=civil)
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
