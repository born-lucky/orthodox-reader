"""Screenshots of the window for the README: python tools/screenshots.py

Runs the real window (webui) on a fresh, empty profile in a temporary folder, so
nothing private (your texts, your settings) can show up, and photographs it with
Edge through Playwright (pip install playwright). The break screen and the
heads-up are drawn with Tk and grabbed from the screen: they appear for a moment
in the top-left corner. Nothing is locked.

Writes docs/screenshots/*.png.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="reader-shots-")  # before reader.settings reads it
sys.path.insert(0, str(ROOT))

from reader import activity, settings, webui  # noqa: E402
from reader.app import App  # noqa: E402
from reader.library import Library  # noqa: E402

W, H = 560, 900


def fake_app() -> App:
    """The parts of App the window needs, without Tk, the tray or the clock."""
    app = App.__new__(App)
    app.cfg = dict(settings.DEFAULTS)
    app.state = {"active": 47 * 60, "day": time.strftime("%Y-%m-%d")}
    app.tracker = activity.Tracker(app.state)
    app.library = Library()
    app.session = None
    app.strict = False
    app.notice = None
    app.web_call = lambda kind, payload: None  # read-only: clicks change nothing
    return app


def window_shots(app: App) -> None:
    from playwright.sync_api import sync_playwright

    server = webui.Server(app.web_state, app.web_call, app.today_icon, app.web_month, app.web_bible,
                          app.web_lives, app.web_passage)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        for scheme in ("light", "dark"):
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2, color_scheme=scheme)
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#grid') && document.querySelector('#grid').children.length > 7", timeout=90000)
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, 0)")  # the calendar scrolls today into view
            page.wait_for_timeout(300)
            page.screenshot(path=OUT / f"home-{scheme}.png")
            if scheme == "dark":
                break
            page.evaluate("show('settings')")
            page.wait_for_timeout(400)
            page.screenshot(path=OUT / "settings-break.png")
            # show the break hours in use: 9 to 5, weekdays
            app.cfg.update(break_from=9, break_to=17, break_days=[True] * 5 + [False] * 2, snooze_minutes=5)
            page.evaluate("refresh()")
            page.wait_for_timeout(800)
            page.evaluate("[...document.querySelectorAll('legend')].find(l => l.textContent === 'Time')"
                          ".scrollIntoView({block: 'start'})")
            page.wait_for_timeout(400)
            page.screenshot(path=OUT / "settings-time.png")
            page.evaluate("show('bible')")
            page.wait_for_timeout(2500)
            page.screenshot(path=OUT / "bible.png")
            page.evaluate("show(null)")
            page.wait_for_timeout(400)
            page.evaluate("document.querySelector('#detail').scrollIntoView({block: 'end'})")
            page.wait_for_timeout(400)
            page.screenshot(path=OUT / "day.png")
            page.close()
        browser.close()
    server.close()


def tk_shots() -> None:
    """The heads-up and the break screen, drawn small in the corner and grabbed."""
    import tkinter as tk

    from PIL import ImageGrab

    from reader import overlay
    from reader import theme as t

    t.load_fonts()
    root = tk.Tk()
    root.withdraw()

    def grab(win, name: str) -> None:
        win.update()
        time.sleep(0.6)
        win.update()
        x, y = win.winfo_rootx(), win.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        ImageGrab.grab((x, y, x + w, y + h), all_screens=True).save(OUT / name)

    notice = overlay.Notice(root, 60, "Time for a break", lambda: None, lambda: None, snooze_minutes=10)
    grab(notice.win, "heads-up.png")
    notice.close()

    overlay.monitors = lambda: [(0, 0, 1280, 720)]
    from reader.app import ANGEL
    screen = overlay.Overlay(root, "icon", str(ANGEL), time.monotonic() + 14 * 60 + 32, hide_cursor=False)
    screen.set_title("The Life of Saint John of Kronstadt")
    screen.set_caption("He rose before dawn every day, and began the day with prayer.")
    grab(screen.windows[0], "break-screen.png")
    root.destroy()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = fake_app()
    threading.Thread(target=lambda: app.library.civil_month(time.localtime().tm_year, time.localtime().tm_mon),
                     daemon=True).start()
    window_shots(app)
    if "--no-tk" not in sys.argv:
        tk_shots()
    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
