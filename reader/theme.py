"""The look of a service book.

From first principles, the way the Church has always laid out a page meant to be
used while praying:
  * black ink on a warm page: the text is the thing, nothing competes with it
  * cinnabar red for rubrics, the instructions and headings ("rubric" is Latin for
    red ink), so what to *do* is told apart from what to *read* at a glance
  * one red initial to open the page; gold only as a thin ornament, never a fill
  * no boxes, no chrome: choices are written as sentences, and the chosen one is
    in ink while the others stay faded, like a rubric offering alternatives

Fonts (SIL Open Font License, bundled in data/fonts):
  Ponomar      the Slavonic Computing Initiative's church face: title, rubrics, initials
  Literata     a face made for long reading on screens: everything that is read
"""

from __future__ import annotations

import ctypes

from . import settings

PAGE = "#f3ead6"       # parchment
PAGE_DEEP = "#eadfc5"  # a shaded margin
INK = "#1c1712"
FADED = "#9a8c74"      # quiet notes
OFF = "#6b5f4c"        # an option not chosen: still easy to read, plainly not chosen
RED = "#a8261b"        # cinnabar
RED_SOFT = "#c44a3a"
GOLD = "#a9822f"

# The break screen is dark: it is for resting the eyes.
NIGHT = "#0b0907"
NIGHT_INK = "#efe6d2"
NIGHT_DIM = "#6f6553"
NIGHT_RED = "#c8553f"
NIGHT_GOLD = "#c9a24a"

RUBRIC = "Ponomar"
BOOK = "Literata 12pt"   # the name Windows gives the variable font's text instance
BOOK_BOLD = "Literata 12pt SemiBold"

_loaded = False


def load_fonts() -> None:
    """Make the bundled fonts usable by this process only (nothing is installed)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for path in (settings.DATA / "fonts").glob("*.ttf"):
        ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)  # FR_PRIVATE
