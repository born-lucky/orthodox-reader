# ☦ Reader

A quiet Orthodox companion for Windows. Every two hours of work it takes a fifteen-minute break with you: it prays, reads the lives of the saints and Holy Scripture aloud over soft birdsong, and shows an icon. When a new day begins (it notices you slept) it reads the morning prayers and the day's readings from the old calendar.

- **Two kinds of break.** *Lock screen*: a full-screen icon, the words being read, or darkness, with the mouse and keyboard held for the break. *Audio only*: the reading plays while you keep working, with an optional small window to read along.
- **The day by the old calendar.** The day's feast, fast (strict, or fish/wine/oil allowed) and saints, a month calendar of fasts and feasts, and the appointed Scripture readings, from [orthocal.info](https://orthocal.info).
- **The Hours.** With a Horologion, each break can begin with the Hour of the day: the First, Third, Sixth, Ninth Hour, or Compline.
- **A free voice.** Microsoft's neural voices through `edge-tts` when online, and the Windows voice offline. No account, no key, no cost.
- **Birdsong and a brook**, synthesised (no recordings), as a seamless loop.
- **Honest about your time.** It measures real use (mouse and keyboard). Fifteen minutes away already counts as a break, and five hours away is a night: a new day.

## Install

Download `Reader.exe` from [Releases](../../releases) and run it. It lives in the tray (the red cross); click it to open the window. Windows SmartScreen may warn about an unsigned app: *More info → Run anyway*.

Or from source (Python 3.11+ on Windows):

```powershell
pip install -r requirements.txt
python main.py            # open the window
python main.py --tray     # start quietly in the tray
powershell -ExecutionPolicy Bypass -File build.ps1   # build dist\Reader.exe
```

## Safety

A locked break can always be ended:

- **Hold Ctrl+Alt+Shift+End for 3 seconds** (the emergency exit, on by default).
- **Ctrl+Alt+Del** always works. Windows never lets a program block it.
- The lock **releases itself** a couple of minutes after the break should have ended, whatever happens, and it dies with the app.
- Breaks can be paused from the window or the tray at any time.

## What it reads

Bundled, all public domain:

- Traditional prayers in the older English of Hapgood's *Service Book* (1906) and the King James Version
- The King James Bible (1769)

Fetched from orthocal.info and kept on your computer (for offline lives of the saints):

- The day's commemorations, fasting and Scripture readings
- Lives of the saints, gathered slowly into a local library

### Your own books

If you own other translations (a prayer book, a Horologion, a Psalter), `tools/build_my_texts.py` turns them into a private pack in `%APPDATA%\OrthodoxReader\my-texts`. Reader uses that pack in place of the bundled texts. **Nothing from those books is in this repository**, and the pack never leaves your computer.

```powershell
python tools/build_my_texts.py --jordanville "Prayer Book.pdf" --horologion "Horologion.pdf" --psalter liturgy.io
```

The page ranges match the Holy Trinity Monastery *Prayer Book* (4th ed.) and *Unabbreviated Horologion* (2nd ed.). Scanned PDFs are cleaned of OCR errors, and the Hours keep only the parts for the day of the week.

## Design

The window is laid out like a page of a service book: ink on warm paper with a matte grain, **cinnabar red for rubrics** (what you *do*), black for what you *read*, and gold only as a hairline. Dark mode is the same book by lamplight, and it follows Windows unless you choose one. Type: **Ponomar** (the Slavonic Computing Initiative's church face) for the title, and **Literata**, made for long reading on screens, for everything else. Both are SIL Open Font License.

The window is a local page in a chromeless Edge window, served on `127.0.0.1` only, and every request needs a random token.

## Files

| | |
| --- | --- |
| `reader/activity.py` | measuring use, breaks, and the new day |
| `reader/session.py` | one break: planning, voice, birdsong, closing prayer |
| `reader/library.py` | prayers, Scripture, the old calendar, lives of the saints |
| `reader/speech.py` | text to speech (edge-tts, Windows voice) |
| `reader/lockdown.py` | the input lock and its safety exits |
| `reader/overlay.py` | the break screens |
| `reader/webui.py`, `data/ui/` | the window |
| `reader/ambience.py` | the synthesised birdsong |
| `tools/build_my_texts.py` | your private pack from your own books |

## License

Code: MIT. Fonts: SIL OFL 1.1 (`data/fonts`). Icons: public-domain reproductions from Wikimedia Commons (`data/icons/SOURCES.txt`). Texts in `data/`: public domain.
