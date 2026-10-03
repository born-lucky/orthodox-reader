"""What you see during a break.

Overlay (lock mode): a full-screen window on every monitor.
  icon   an icon, the title of what is read, and the words being spoken
  text   the words being spoken, large, to read along
  black  a dark screen, only a quiet countdown

ReadAlong (listen mode): a small window in the corner with the words being
spoken, so you can read along while you keep working. Drag it anywhere.

Tkinter: everything here runs on the main thread.
"""

from __future__ import annotations

import ctypes
import random
import time
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageTk

from . import settings
from . import theme as t

BG = t.NIGHT
GOLD = t.NIGHT_GOLD
INK = t.NIGHT_INK
DIM = t.NIGHT_DIM
SERIF = t.BOOK


def monitors() -> list[tuple[int, int, int, int]]:
    """(x, y, width, height) of every monitor, primary first."""
    out: list[tuple[int, int, int, int, bool]] = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def each(hmon, hdc, rect, data):
        info = MONITORINFO(ctypes.sizeof(MONITORINFO))
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top, bool(info.dwFlags & 1)))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(each), 0)
    out.sort(key=lambda m: not m[4])
    return [m[:4] for m in out] or [(0, 0, 1920, 1080)]


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def pick_icon(hint: str = "", seed: int | None = None) -> str:
    icons = sorted((settings.DATA / "icons").glob("*.jpg"))
    hint = hint.lower()
    for key, name in (("nativity of the theotokos", "nativity_theotokos"), ("nativity", "nativity_christ"),
                      ("annunciation", "annunciation"), ("dormition", "dormition"), ("transfiguration", "transfiguration"),
                      ("pentecost", "pentecost"), ("trinity", "trinity"), ("entry of the theotokos", "presentation_theotokos"),
                      ("theotokos", "theotokos_vladimir"), ("paul", "apostle_paul"), ("luke", "evangelist_luke"),
                      ("mark", "evangelist_mark")):
        if key in hint:
            for icon in icons:
                if icon.stem == name:
                    return str(icon)
    return str((random.Random(seed) if seed is not None else random).choice(icons))


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Overlay:
    def __init__(self, root: tk.Tk, style: str, icon: str, end: float, hide_cursor: bool) -> None:
        self.style = style
        self.end = end
        self.windows: list[tk.Toplevel] = []
        self.canvases: list[tk.Canvas] = []
        self._photos: list[ImageTk.PhotoImage] = []
        self.title = ""
        self.caption = ""
        for n, (x, y, w, h) in enumerate(monitors()):
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.configure(bg=BG, cursor="none" if hide_cursor else "")
            win.geometry(f"{w}x{h}+{x}+{y}")
            win.attributes("-topmost", True)
            canvas = tk.Canvas(win, bg=BG, highlightthickness=0, width=w, height=h,
                               cursor="none" if hide_cursor else "")
            canvas.pack(fill="both", expand=True)
            self.windows.append(win)
            self.canvases.append(canvas)
            self._layout(canvas, w, h, icon, primary=(n == 0))
        self._keep_on_top()

    def _layout(self, c: tk.Canvas, w: int, h: int, icon: str, primary: bool) -> None:
        tags = {}
        if self.style == "icon":
            img = Image.open(icon)
            room = int(h * (0.56 if primary else 0.7))
            img.thumbnail((int(w * 0.5), room), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._photos.append(photo)
            top = int(h * (0.08 if primary else 0.15))
            c.create_image(w // 2, top, image=photo, anchor="n")
            # a thin gold frame, like a kiot
            iw, ih = img.size
            c.create_rectangle(w // 2 - iw // 2 - 10, top - 10, w // 2 + iw // 2 + 10, top + ih + 10, outline=GOLD, width=1)
            if primary:
                tags["title"] = c.create_text(w // 2, top + ih + 50, text="", fill=t.NIGHT_RED, font=(t.RUBRIC, 22))
                tags["caption"] = c.create_text(w // 2, top + ih + 90, text="", fill=INK, font=(SERIF, 20),
                                                 width=int(w * 0.62), anchor="n", justify="center")
        elif self.style == "text" and primary:
            tags["title"] = c.create_text(w // 2, int(h * 0.16), text="", fill=t.NIGHT_RED, font=(t.RUBRIC, 26))
            c.create_text(w // 2, int(h * 0.16) + 40, text="☦", fill=GOLD, font=(t.RUBRIC, 20))
            tags["caption"] = c.create_text(w // 2, int(h * 0.46), text="", fill=INK, font=(SERIF, 34),
                                             width=int(w * 0.66), justify="center")
        if primary:
            tags["clock"] = c.create_text(w - 40, h - 30, text="", fill=DIM, font=(SERIF, 12), anchor="e")
            tags["hint"] = c.create_text(40, h - 30, text="", fill="#3b342a", font=(SERIF, 10), anchor="w")
        c.tags = tags  # type: ignore[attr-defined]

    def set_title(self, text: str) -> None:
        self.title = text
        for c in self.canvases:
            if "title" in c.tags:
                c.itemconfigure(c.tags["title"], text=text)

    def set_caption(self, text: str) -> None:
        self.caption = text
        for c in self.canvases:
            if "caption" in c.tags:
                c.itemconfigure(c.tags["caption"], text=text)

    def set_hint(self, text: str) -> None:
        for c in self.canvases:
            if "hint" in c.tags:
                c.itemconfigure(c.tags["hint"], text=text)

    def _keep_on_top(self) -> None:
        if not self.windows:
            return
        for c in self.canvases:
            if "clock" in c.tags:
                c.itemconfigure(c.tags["clock"], text=clock(self.end - time.monotonic()))
        for win in self.windows:
            win.attributes("-topmost", True)
            win.lift()
        try:
            self.windows[0].focus_force()
        except tk.TclError:
            pass
        self.windows[0].after(1000, self._keep_on_top)

    def close(self) -> None:
        for win in self.windows:
            win.destroy()
        self.windows.clear()
        self.canvases.clear()


class ReadAlong:
    """The small caption window for listen mode."""

    def __init__(self, root: tk.Tk, end: float, on_stop) -> None:
        self.end = end
        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.93)
        win.configure(bg=t.PAGE)
        w, h = 470, 190
        _, _, right, bottom = work_area()
        win.geometry(f"{w}x{h}+{right - w - 18}+{bottom - h - 18}")
        frame = tk.Frame(win, bg=t.PAGE, highlightbackground=t.GOLD, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        top = tk.Frame(frame, bg=t.PAGE)
        top.pack(fill="x", padx=14, pady=(10, 0))
        self.title = tk.Label(top, text="", fg=t.RED, bg=t.PAGE, font=(t.RUBRIC, 12), anchor="w")
        self.title.pack(side="left", fill="x", expand=True)
        self.clock = tk.Label(top, text="", fg=t.FADED, bg=t.PAGE, font=(SERIF, 9))
        self.clock.pack(side="left", padx=6)
        stop = tk.Label(top, text="✕", fg=t.FADED, bg=t.PAGE, font=(SERIF, 11), cursor="hand2")
        stop.pack(side="left")
        stop.bind("<Button-1>", lambda e: on_stop())
        self.caption = tk.Label(frame, text="", fg=t.INK, bg=t.PAGE, font=(SERIF, 12), wraplength=w - 30,
                                justify="left", anchor="nw")
        self.caption.pack(fill="both", expand=True, padx=14, pady=(6, 10))
        for widget in (frame, self.title, self.caption, top):
            widget.bind("<ButtonPress-1>", self._grab)
            widget.bind("<B1-Motion>", self._drag)
        self._tick()

    def _grab(self, event) -> None:
        self._dx, self._dy = event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y()

    def _drag(self, event) -> None:
        self.win.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    def _tick(self) -> None:
        if not self.win.winfo_exists():
            return
        self.clock.configure(text=clock(self.end - time.monotonic()))
        self.win.after(1000, self._tick)

    def set_title(self, text: str) -> None:
        self.title.configure(text=text)

    def set_caption(self, text: str) -> None:
        self.caption.configure(text=text)

    def set_hint(self, text: str) -> None:
        pass

    def close(self) -> None:
        self.win.destroy()


class Notice:
    """A quiet heads-up before a break, with Snooze."""

    def __init__(self, root: tk.Tk, seconds: int, text: str, on_snooze, on_now) -> None:
        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.95)
        win.configure(bg=t.PAGE)
        w, h = 380, 112
        _, _, right, bottom = work_area()
        win.geometry(f"{w}x{h}+{right - w - 18}+{bottom - h - 18}")
        frame = tk.Frame(win, bg=t.PAGE, highlightbackground=t.GOLD, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="☦  " + text, fg=t.RED, bg=t.PAGE, font=(t.RUBRIC, 13), anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        self.count = tk.Label(frame, text="", fg=t.FADED, bg=t.PAGE, font=(SERIF, 10), anchor="w")
        self.count.pack(fill="x", padx=14)
        row = tk.Frame(frame, bg=t.PAGE)
        row.pack(fill="x", padx=12, pady=8)
        for label, action in (("Begin now", on_now), ("Snooze 10 min", on_snooze)):
            b = tk.Label(row, text=label, fg=t.RED, bg=t.PAGE_DEEP, font=(SERIF, 10), padx=10, pady=3, cursor="hand2")
            b.pack(side="left", padx=4)
            b.bind("<Button-1>", lambda e, a=action: (self.close(), a()))
        self.left = seconds
        self._tick()

    def _tick(self) -> None:
        if not self.win.winfo_exists():
            return
        self.count.configure(text=f"Save your work: the break begins in {self.left} s.")
        self.left -= 1
        if self.left >= 0:
            self.win.after(1000, self._tick)

    def close(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()
