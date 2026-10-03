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

PAGE = "#dcdcdc"       # NeXT grey
PAGE_DEEP = "#cfcfcf"
INK = "#000000"
FADED = "#6e6e6e"      # quiet notes
OFF = "#6e6e6e"        # an option not chosen: still easy to read, plainly not chosen
RED = "#000000"        # the accent is ink itself
RED_SOFT = "#3c3c3c"
GOLD = "#8a8a8a"

# The break screen is dark: it is for resting the eyes.
NIGHT = "#000000"
NIGHT_INK = "#ffffff"
NIGHT_DIM = "#6e6e6e"
NIGHT_RED = "#c8c8c8"
NIGHT_GOLD = "#555555"

RUBRIC = "Arial"        # Helvetica's stand-in on Windows
BOOK = "Arial"
BOOK_BOLD = "Arial"

_loaded = False


def load_fonts() -> None:
    """Make the bundled fonts usable by this process only (nothing is installed)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for path in (settings.DATA / "fonts").glob("*.ttf"):
        ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)  # FR_PRIVATE
