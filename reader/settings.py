"""Settings and saved state, in %APPDATA%\\OrthodoxReader."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP = "OrthodoxReader"
HOME = Path(os.environ.get("APPDATA") or Path.home()) / APP
HOME.mkdir(parents=True, exist_ok=True)
SETTINGS = HOME / "settings.json"
STATE = HOME / "state.json"
LOG = HOME / "reader.log"

# Bundled data: next to the exe when frozen by PyInstaller, else the repo's data/.
DATA = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "data"

DEFAULTS = {
    "enabled": True,
    "work_minutes": 120,       # active use before a break
    "break_minutes": 15,
    "sleep_hours": 5,          # an absence this long is a night's sleep: a new day begins
    "away_minutes": 15,        # an absence this long already counts as a break
    "mode": "lock",            # "lock": take over the screen | "listen": audio while you work
    "screen": "icon",          # lock mode: "icon" | "text" | "black"
    "read_along": True,        # listen mode: a small caption window to read along
    "content": "mixed",        # "mixed" | "saints" | "scripture" | "psalter" (kathismata in order)
    "hours": True,             # open each break with the Hour of the day (needs your Horologion texts)
    "prayers": True,
    "morning": True,           # a session with the day's readings when a new day begins
    "morning_minutes": 30,     # the morning prayers and the day's readings take longer than a break
    "ambience": True,
    "ambience_volume": 30,     # 0-100: about 12 dB under the voice at the default voice volume
    "voice_volume": 70,        # 0-100
    "room": True,              # place the voice in a room (reverb, distance) instead of in your ears
    "voice": "en-GB-RyanNeural",  # an edge voice, or "sapi" for the offline Windows voice
    "rate": -10,               # speech speed, percent
    "warn_seconds": 60,
    "emergency_exit": True,    # hold Ctrl+Alt+Shift+End to end a locked break
    "autostart": True,
    "week_start": "monday",    # the calendar: "monday" | "sunday"
    "old_dates": True,         # the calendar: old-style dates under each day
    "fast_reminder": True,     # a quiet note at the start of a fast day
    "theme": "system",         # "system" | "light" | "dark"
}

VOICES = [
    ("en-GB-RyanNeural", "Ryan (British, calm)"),
    ("en-GB-SoniaNeural", "Sonia (British)"),
    ("en-US-ChristopherNeural", "Christopher (American, deep)"),
    ("en-US-AndrewNeural", "Andrew (American, warm)"),
    ("en-US-AvaNeural", "Ava (American)"),
    ("en-IE-ConnorNeural", "Connor (Irish)"),
    ("en-AU-WilliamNeural", "William (Australian)"),
    ("sapi", "Windows voice (offline)"),
]


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def load() -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in _read(SETTINGS).items() if k in DEFAULTS})
    return out


def save(cfg: dict) -> None:
    _write(SETTINGS, cfg)


def load_state() -> dict:
    return _read(STATE)


def save_state(state: dict) -> None:
    _write(STATE, state)


# ------------------------------------------------------------ start with Windows

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pyw}" "{main}" --tray'


def set_autostart(on: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass
