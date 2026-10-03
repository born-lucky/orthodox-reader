"""Put the voice in a room.

A text-to-speech voice is recorded dry, close and loud: heard on headphones it sits
inside your head. This places it a few metres away in a small stone church:

  1. distance  the air takes the edge off the highs; the closeness of the low end goes
  2. room      a few early reflections off near walls, then a dark stereo tail
               (about 1.8 s), wider than the voice, so the sound has somewhere to go
  3. level     quiet and even: normalised low, with a soft limit on peaks

The impulse response is synthesised (decaying noise that darkens as it decays), so
there is nothing to license. numpy does the work; miniaudio decodes the voice's MP3.
"""

from __future__ import annotations

import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 24000
TAIL = 1.8          # seconds for the room to fall silent (RT60)
PREDELAY = 0.022    # seconds before the first reflection
WET = 0.34          # how much of the room you hear
TARGET_RMS = 10 ** (-27 / 20)   # quiet speech level (dBFS)
CEILING = 10 ** (-6 / 20)


def _decode(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as w:
            rate, channels = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if rate != RATE:
            data = np.interp(np.arange(0, len(data), rate / RATE), np.arange(len(data)), data).astype(np.float32)
        return data
    import miniaudio

    decoded = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=RATE)
    return np.asarray(decoded.samples, dtype=np.float32)


def _tilt(x: np.ndarray, rate: int) -> np.ndarray:
    """Distance: a gentle roll-off above ~4 kHz and below ~110 Hz, done in the spectrum."""
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / rate)
    gain = 1 / np.sqrt(1 + (f / 5200) ** 4)           # air: soft high cut
    gain *= 1 / np.sqrt(1 + (110 / np.maximum(f, 1)) ** 4)  # no proximity boom
    return np.fft.irfft(spec * gain, n).astype(np.float32)


@lru_cache(maxsize=2)
def _impulse(rate: int) -> np.ndarray:
    """A stereo room: early reflections, then decaying noise that grows darker."""
    rng = np.random.default_rng(1054)
    length = int(rate * TAIL * 1.2)
    t = np.arange(length) / rate
    decay = np.exp(-6.91 * t / TAIL)  # -60 dB at TAIL
    ir = np.zeros((length, 2), dtype=np.float32)
    for ch in range(2):
        noise = rng.standard_normal(length).astype(np.float32)
        # darken with time: blend in a smoothed copy as the tail goes on
        smooth = np.convolve(noise, np.ones(9) / 9, mode="same")
        mix = np.clip(t / (TAIL * 0.6), 0, 1)
        tail = (noise * (1 - mix) + smooth * mix * 2.2) * decay
        start = int(PREDELAY * rate) + ch * int(0.0037 * rate)  # left and right differ: width
        tail[:start] = 0
        ir[:, ch] = tail
        # early reflections: near walls, a little different on each side
        for ms, g in ((11, .55), (17, .42), (23, .35), (31, .28), (43, .2), (59, .14)):
            k = int((ms + ch * 2.3) * rate / 1000)
            if k < length:
                ir[k, ch] += g * (1 if (ms + ch) % 2 else -1)
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True))  # each side carries unit energy
    return ir


def _convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, size)
    out = np.stack([np.fft.irfft(X * np.fft.rfft(ir[:, ch], size), size)[:n] for ch in range(2)], axis=1)
    return out.astype(np.float32)


def place(src: str) -> str:
    """Process one spoken chunk; returns the path of a new stereo WAV beside it."""
    path = Path(src)
    dry = _tilt(_decode(path), RATE)
    if not len(dry):
        return src
    wet = _convolve(dry, _impulse(RATE))
    out = wet * WET
    out[: len(dry)] += (dry * (1 - WET * 0.6))[:, None]
    rms = float(np.sqrt(np.mean(out[: len(dry)] ** 2))) or 1.0
    out *= TARGET_RMS / rms
    out = np.tanh(out / CEILING) * CEILING  # soft limit: no sharp peaks
    tail = int(RATE * 0.9)  # the room rings on after the words, fading, into the next sentence's gap
    out = out[: len(dry) + tail]
    fade = int(RATE * 0.45)
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    dest = path.with_name(path.stem + "-room.wav")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    return str(dest)
