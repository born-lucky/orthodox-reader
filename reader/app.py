"""The main window, the tray icon, and the scheduler that starts the breaks."""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

from . import activity, audio, guard, overlay, settings, speech, webui
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


ANNOUNCE_AFTER = 120  # seconds at the computer before the day's announcement


def announcement(ahead: dict, today: dict | None) -> list[str]:
    """The day's announcement, a sentence a line: today's feast, then the days to the next."""
    lines = []
    name = ((today or {}).get("feasts") or [""])[0].strip()
    if name and (today.get("feast_level") or 0) >= 6:
        lines.append(f"Today is the feast: {name}.")
    feast, season = ahead.get("feast"), ahead.get("season")
    if feast:
        days = feast["days"]
        lines.append(f"Tomorrow is the {feast['name']}." if days == 1 else f"{days} days until the {feast['name']}.")
    if season:
        days = season["days"]
        lines.append(f"The {season['name']} begins tomorrow." if days == 1 else f"The {season['name']} begins in {days} days.")
    return lines


class App:
    def __init__(self, start_hidden: bool = False, show_event: str | None = None) -> None:
        self.cfg = settings.load()
        self.state = settings.load_state()
        self.tracker = activity.Tracker(self.state)
        self.library = Library()
        self.events: queue.Queue = queue.Queue()
        self.session: Session | None = None
        self.screen = None
        self.lock: Lock | None = None
        self.strict = False  # this reading cannot be stopped (fixed when it starts)
        self.notice = None
        self.tray = None
        self._stop = threading.Event()

        t.load_fonts()
        # Tk only draws the break screens; the window is a local page (webui).
        root = self.root = tk.Tk()
        root.withdraw()
        self._icon = ImageTk.PhotoImage(angel_image(64))
        root.iconphoto(True, self._icon)
        self.web = webui.Server(self.web_state, self.web_call, self.today_icon, self.web_month, self.web_bible, self.web_lives, self.web_passage)
        webui.close_stale_windows()
        if not start_hidden:
            self.web.open_window()
        threading.Thread(target=self.library.prefetch, args=(self._stop,), name="reader-prefetch", daemon=True).start()
        threading.Thread(target=self._fetch_today, daemon=True).start()
        self._tray()
        if show_event:
            threading.Thread(target=self._wait_for_show, args=(show_event,), name="reader-show", daemon=True).start()
        root.after(500, self._pump)
        root.after(1000, self._tick)
        if self.cfg["extreme"]:
            root.after(1000, self._guard)
            root.after(3000, self._resume)

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
            headline = self.session.title or "Reading"
            sub = "Sit with it until the end." if self.strict else "Stop when you need to."
        elif self.cfg["extreme"]:
            headline = f"Next break in {spell_time(self.tracker.until_break(self.cfg))}"
            sub = "Extreme: the breaks come, always."
        elif not self.cfg["enabled"]:
            headline, sub = "Automatic readings are off", "Readings are still a click away."
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
                "enabled": self.cfg["enabled"], "reading": reading, "strict": reading and self.strict,
                # the clock: seconds to the next reading, or seconds left in this one
                "until": self.tracker.until_break(self.cfg),
                "left": max(0.0, self.session.end - time.monotonic()) if reading and hasattr(self.session, "end") else 0.0,
                "active": self.tracker.state.get("active", 0), "work": work,
                "now": ({"title": self.session.current.title if self.session.current else "",
                         "lines": self.session.current.lines if self.session.current else [],
                         "caption": self.session.caption,
                         "next": self.session.upcoming[0] if self.session.upcoming else ""} if reading else None),
            },
        }

    def web_bible(self, book: str | None, chapter: int | None, query: str | None = None, openings: bool = False):
        """The Bible for the window: the books, one chapter, a search, or the chapter openings."""
        if not book:
            return {"books": self.library.bible_books()}
        if query:
            return {"hits": self.library.bible_search(book, query)}
        if openings:
            return {"openings": self.library.bible_openings(book)}
        seg = self.library.bible_segment(book, chapter or 1)
        if seg is None:
            return {"title": "", "verses": []}
        return {"title": seg.title, "verses": seg.lines[1:]}

    def _lives_of(self, iso: str) -> list:
        try:
            civil = dt.date.fromisoformat(iso)
        except ValueError:
            return []
        return self.library.day_lives(civil, self.library.day(civil, fetch=False))

    def web_lives(self, iso: str) -> dict:
        """The full lives of a day's saints, for the window to show."""
        return {"lives": [{"title": seg.title, "lines": seg.lines[1:]} for seg in self._lives_of(iso)]}

    def web_passage(self, iso: str, index: int) -> dict:
        """One Gospel (or other reading) opened from the calendar."""
        try:
            civil = dt.date.fromisoformat(iso)
        except ValueError:
            return {"title": "", "verses": []}
        page = self.library.passage_page(civil, index)
        if not page:
            return {"title": "", "verses": []}
        return {"title": page["title"], "citation": page["citation"], "verses": page["verses"]}

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

    def _ahead(self, rank: int = 7) -> dict:
        """The next great feast (orthocal rank `rank` or higher; 7 = the Twelve and Pascha)
        and the next fast season, from today, with days to go."""
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
                if feast is None and (data.get("feast_level") or 0) >= rank and civil > today:
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
            if payload.get("action") == "life":
                self.events.put(("do", ("life", str(payload.get("day", "")), int(payload.get("i") or 0))))
            elif payload.get("action") == "text":
                self.events.put(("do", ("text", str(payload.get("book", "")), int(payload.get("chapter") or 0))))
            elif payload.get("action") == "passage":
                self.events.put(("do", ("passage", str(payload.get("day", "")), int(payload.get("i") or 0))))
            else:
                self.events.put(("do", payload.get("action") if not payload.get("day") else ("day", payload["day"])))

    def _set(self, key: str, value) -> None:
        kind = type(settings.DEFAULTS[key])
        try:
            value = kind(value) if kind is not bool else bool(value)
        except (TypeError, ValueError):
            return
        if self.cfg["extreme"] and key in settings.FROZEN:
            log.info("set %s refused: extreme", key)
            return
        if key == "extreme":
            if self.session is not None:
                log.info("extreme refused: in a reading")
            elif value:
                self.go_extreme()
            else:
                self.cfg["extreme"] = False  # the guard sees it and leaves
                settings.save(self.cfg)
                log.info("extreme mode off")
                self._tray_update()
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
        if isinstance(action, tuple) and action[0] == "text":  # Read Aloud from the Bible view
            seg = self.library.bible_segment(action[1], action[2])
            if seg is not None:
                self.start("text", "listen", items=[seg])
            return
        if isinstance(action, tuple) and action[0] == "passage":  # Read Aloud from a Gospel link
            seg = self.library.passage_segment(action[1], action[2])
            if seg is not None:
                self.start("text", "listen", items=[seg])
            return
        if isinstance(action, tuple) and action[0] == "life":  # Read Aloud from a saint's life
            lives = self._lives_of(action[1])
            if 0 <= action[2] < len(lives):
                self.start("text", "listen", items=[lives[action[2]]])
            return
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

    def go_extreme(self) -> None:
        """Breaks lock, cannot be stopped, and start over if Reader is closed; the guard keeps it running."""
        self.cfg.update(settings.EXTREME)
        settings.save(self.cfg)
        try:
            settings.set_autostart(True)
        except OSError:
            log.exception("autostart")
        log.info("extreme mode on")
        self._tray_update()
        self.events.put(("guard", None))

    def set_enabled(self, on: bool) -> None:
        if self.cfg["extreme"] and not on:
            log.info("automatic off refused: extreme")
            return
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
        free = lambda i: not self.cfg["extreme"]  # extreme: no off switch, no Stop, no Quit
        menu = pystray.Menu(
            item("Open", lambda: self.events.put(("show", None)), default=True),
            item("Automatic readings", lambda: self.events.put(("toggle", None)), checked=lambda i: self.cfg["enabled"], visible=free),
            item("Take a break now", lambda: self.events.put(("start", "break"))),
            item("Listen while I work", lambda: self.events.put(("listen", None))),
            item("Stop the reading", lambda: self.events.put(("stop", None)), visible=free),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: self.events.put(("quit", None)), visible=free),
        )
        self.tray = pystray.Icon("OrthodoxReader", angel_image(64), "Reader", menu)
        self.tray.run_detached()

    def _tray_update(self) -> None:
        if self.tray:
            self.tray.update_menu()

    def show(self) -> None:
        self.web.open_window()

    def _wait_for_show(self, name: str) -> None:
        """Reader launched again (the taskbar pin, the Start menu): open the window."""
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = ctypes.c_void_p
        event = ctypes.c_void_p(kernel32.CreateEventW(None, False, False, name))  # auto-reset
        while not self._stop.is_set():
            if kernel32.WaitForSingleObject(event, 1000) == 0:  # WAIT_OBJECT_0
                self.events.put(("show", None))

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
                elif kind == "next" and self.screen and hasattr(self.screen, "set_next"):
                    self.screen.set_next(value)
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
                elif kind == "announce":
                    overlay.Toast(self.root, value[0], " ".join(value[1:]), seconds=20)
                elif kind == "guard":
                    self._guard()
                elif kind == "quit":
                    if self.cfg["extreme"]:
                        log.info("quit refused: extreme")
                        continue
                    if self.session is not None and self.strict:
                        log.info("quit refused: strict reading")
                        continue
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
            self._maybe_announce(idle)
        else:
            self.tracker.state["last_seen"] = now - min(idle, 60)  # the break is not an absence
        settings.save_state(self.tracker.state)
        self.root.after(activity.POLL * 1000, self._tick)

    def _on(self, event: str) -> None:
        log.info("event %s", event)
        if event == "first_day":  # just installed: learn today, but no morning session yet
            threading.Thread(target=self._fetch_today, daemon=True).start()
            return
        if event == "new_day" and self.cfg["enabled"] and self.cfg.get("fast_reminder", True):
            self.root.after(8000, self._fast_note)
        if event == "new_day":
            threading.Thread(target=self._fetch_today, daemon=True).start()
            if self.cfg["enabled"] and self.cfg["morning"]:
                self._heads_up("The morning prayers and the day's readings", "morning")
        elif event == "warn" and self.cfg["enabled"]:
            self._heads_up("Time for a break", "break")
        elif event == "break" and self.cfg["enabled"] and self.notice is None:
            self.start("break", self.cfg["mode"])

    def _guard(self) -> None:
        """Extreme: keep the guard running; it brings Reader back if Reader is killed, and we bring it back."""
        if not self.cfg["extreme"]:
            return
        if not guard.alive():
            guard.spawn("--guard")
        if not self._stop.is_set():
            self.root.after(guard.POLL * 1000, self._guard)

    def _resume(self) -> None:
        """Extreme: a break or morning that was cut off (killed, logged off) starts again from the top."""
        owed = self.tracker.state.get("owed")
        if not owed or self.session is not None:
            return
        kind, since = owed
        if time.time() - since > self.cfg["sleep_hours"] * 3600:  # slept since: the morning takes its place
            self.tracker.state.pop("owed", None)
            return
        log.info("resuming a %s that was cut off", kind)
        self.start(kind, "lock")

    def _fast_note(self) -> None:
        """At the start of the day: is today a fast day? A quiet note that leaves by itself."""
        data = self.library.day(dt.date.today(), fetch=False)
        if not data:
            return
        info = lib.summary(data)
        overlay.Toast(self.root, "Today: " + info["fast_text"], info.get("title", ""))

    def _maybe_announce(self, idle: float) -> None:
        """Once a day, a couple of minutes into using the computer (after the morning, if any)."""
        st = self.tracker.state
        today = dt.date.today().isoformat()
        if (not self.cfg["announce"] or self.notice is not None or st.get("announced") == today
                or idle > 30 or st.get("active", 0) < ANNOUNCE_AFTER):
            return
        st["announced"] = today

        def run():
            try:
                lines = announcement(self._ahead(rank=7), self.library.day(dt.date.today()))
            except Exception:
                log.exception("announce")
                return
            if lines:
                log.info("announce: %s", " ".join(lines))
                self.events.put(("announce", lines))
                self.say(" ".join(lines))

        threading.Thread(target=run, daemon=True).start()

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

        self.notice = overlay.Notice(self.root, seconds, text, None if self.cfg["extreme"] else snooze, now)
        if kind == "morning":  # the morning session starts by itself after the countdown
            self.root.after(seconds * 1000, lambda: self.notice is not None and (self.notice.close(), now()))
        else:
            self.root.after(seconds * 1000, lambda: setattr(self, "notice", None) if self.notice else None)

    def _fetch_today(self) -> None:
        self.library.day(dt.date.today())
        self.events.put(("today", None))

    # ------------------------------------------------------------ sessions

    def start(self, kind: str, mode: str, civil: dt.date | None = None, items: list | None = None) -> None:
        if self.session is not None:
            return
        if self.notice is not None:
            self.notice.close()
            self.notice = None
        minutes = self.cfg["morning_minutes"] if kind in ("morning", "day") else self.cfg["break_minutes"]
        if kind in ("morning", "day"):  # all of the day's readings, however long they take
            planned, closing = self.library.plan(kind, self.cfg, minutes, civil=civil or dt.date.today())
            spoken = sum(lib.speaking_time(s) + lib.GAP for s in planned) + (lib.speaking_time(closing) if closing else 0)
            minutes = max(5.0, spoken / 60 + 3)
        end = time.monotonic() + minutes * 60
        # strict: breaks and the morning only; a chapter chosen to read aloud can always be stopped
        self.strict = self.cfg["strict"] and kind != "text"
        emergency = self.cfg["emergency_exit"] and not self.strict
        if mode == "lock":
            hint = "Hold Ctrl+Alt+Shift+End for 3 seconds to end early" if emergency else ""
            self.screen = overlay.Overlay(self.root, self.cfg["screen"], overlay.pick_icon(self.library.headline(dt.date.today())),
                                          end, hide_cursor=True)
            self.screen.set_hint(hint)
            # the dead-man timer stays even when strict: the lock always ends by itself
            self.lock = Lock(minutes * 60 + 150, emergency, lambda: self.events.put(("emergency", None)))
            self.lock.start()
        elif self.cfg["read_along"]:
            self.screen = overlay.ReadAlong(self.root, end, self.stop_session, strict=self.strict)
        if kind == "text":  # as long as the chapter takes (a long psalm runs past a break)
            minutes = max(5.0, sum(lib.speaking_time(s) for s in items or []) / 60 + 2)
        self.session = Session(kind, self.cfg, self.library, self._post, minutes, civil=civil, items=items)
        self.session.start()
        if self.cfg["extreme"] and kind in ("break", "morning"):
            self.tracker.state["owed"] = [kind, time.time()]  # killed before the end: it starts over
            settings.save_state(self.tracker.state)

    def _post(self, kind: str, value: str) -> None:
        if kind == "title" and self.session is not None:
            self.session.title = value
        self.events.put((kind, value))

    def stop_session(self) -> None:
        if self.session is not None and self.strict:
            log.info("stop refused: strict reading")
            return
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
        self.strict = False
        self.tracker.state.pop("owed", None)
        self.tracker.took_break()
        settings.save_state(self.tracker.state)

    def say(self, text: str) -> None:
        """Speak a short line and wait for it to finish (call off the Tk thread)."""
        cfg = dict(self.cfg)
        try:
            path = speech.Voice(cfg).make(text)
            sound = audio.Sound(path, cfg["voice_volume"])
            sound.play()
            time.sleep(0.2)
            while sound.playing():
                time.sleep(0.1)
            sound.close()
        except Exception:
            log.exception("say")

    def test_voice(self) -> None:
        threading.Thread(target=self.say, args=("Glory to Thee, our God, glory to Thee.",), daemon=True).start()

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
