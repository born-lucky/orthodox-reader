"""Text to speech, free: Microsoft Edge's neural voices (online, via edge-tts),
falling back to the Windows voice (SAPI, offline) when there is no internet.

Text is spoken a few sentences at a time. Each chunk becomes an audio file,
made a little ahead of playback, and the caption on screen follows the chunk
being played, so you can read along.
"""

from __future__ import annotations

import asyncio
import itertools
import re
import tempfile
from pathlib import Path

from . import settings

CHUNK = 320  # characters per spoken chunk: about two sentences
TMP = Path(tempfile.gettempdir()) / "reader-speech"
TMP.mkdir(exist_ok=True)
_files = itertools.count(1)


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """Sentences grouped into chunks of about `size` characters; a long sentence is
    split at a comma or a space."""
    sentences = re.split(r"(?<=[.!?;:])\s+(?=[\"'“‘(\[A-Z0-9])", text.strip())
    out: list[str] = []
    for sentence in sentences:
        while len(sentence) > size * 1.5:
            cut = sentence.rfind(", ", 0, size)
            cut = cut + 1 if cut > size // 3 else sentence.rfind(" ", 0, size)
            if cut <= 0:
                cut = size
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if out and len(out[-1]) + len(sentence) < size:
            out[-1] = f"{out[-1]} {sentence}"
        elif sentence:
            out.append(sentence)
    return out


def speakable(text: str) -> str:
    """Small fixes so the voice reads Scripture references and abbreviations well."""
    text = re.sub(r"\b(\d+)\.(\d+)(?:-(\d+))?\b",
                  lambda m: f"{m[1]}, verses {m[2]} to {m[3]}" if m[3] else f"{m[1]}, verse {m[2]}", text)
    text = re.sub(r"\bSt\.? ", "Saint ", text)
    text = re.sub(r"\bSts\.? ", "Saints ", text)
    text = re.sub(r"\((\d+)(?:st|nd|rd|th) c\.\)", r"(\1th century)", text)
    return text


class Voice:
    """Makes one audio file per chunk of text."""

    def __init__(self, cfg: dict) -> None:
        self.voice = cfg["voice"]
        self.rate = int(cfg["rate"])
        self.online = self.voice != "sapi"

    def make(self, text: str) -> str:
        n = next(_files)
        text = speakable(text)
        if self.online:
            path = TMP / f"c{n}.mp3"
            try:
                _edge(text, self.voice, self.rate, path)
                if path.stat().st_size > 0:
                    return str(path)
            except Exception:
                self.online = False  # no internet: the Windows voice for the rest of the session
        path = TMP / f"c{n}.wav"
        _sapi(text, self.rate, path)
        return str(path)


def _edge(text: str, voice: str, rate: int, path: Path) -> None:
    import edge_tts

    async def run() -> None:
        await edge_tts.Communicate(text, voice, rate=f"{rate:+d}%").save(str(path))

    asyncio.run(asyncio.wait_for(run(), timeout=30))


def _sapi(text: str, rate: int, path: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 34  # SAFT44kHz16BitStereo
        stream.Format = fmt
        stream.Open(str(path), 3)  # SSFMCreateForWrite
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.AudioOutputStream = stream
        voice.Rate = max(-10, min(10, round(rate / 10)))
        voice.Speak(text)
        stream.Close()
    finally:
        pythoncom.CoUninitialize()


def cleanup() -> None:
    for path in TMP.glob("c*.*"):
        try:
            path.unlink()
        except OSError:
            pass


def voice_names() -> list[tuple[str, str]]:
    return settings.VOICES
