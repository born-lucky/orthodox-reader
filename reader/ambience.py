"""A quiet garden outside the window: songbirds in the trees and a brook beyond them,
synthesised (no recordings, so no licence questions) and placed in real space.
Rendered once to %APPDATA% as a seamless 75-second loop.

Four birds, each with its own song, at its own place around you:
  whistle  two clear falling notes ("fee-bee")
  phrase   a short varied run of sweeping notes, like a robin
  trill    a fast run of tiny notes
  warble   a quick vibrato tumble, like a wren

How the space is made (the cues the ear really uses):
  direction  the far ear hears a bird up to 0.6 ms later (interaural time) and a
             little quieter and duller (the head's shadow)
  distance   farther birds are quieter, lose their highs to the air, and carry
             more of the garden's echo than the direct sound
  the garden a short, sparse outdoor echo (trees, a wall), wide and decorrelated
  the brook  far off and low, a slow-breathing rush
The mix is set to a fixed loudness, well under the voice.
"""

from __future__ import annotations

import math
import random
import wave

import numpy as np

from . import settings

RATE = 22050
SECONDS = 75
FADE = 3.0
FOLDER = settings.HOME
TAU = 2 * math.pi
TARGET_RMS = 10 ** (-31 / 20)  # the garden's loudness at volume 100
HEAD = 0.00066                  # seconds: the largest interaural time difference


def _note(track: np.ndarray, at: float, dur: float, f0: float, f1: float, amp: float,
          vibrato=(0.0, 0.0), harmonic: float = 0.12) -> None:
    """One sweeping note with a smooth envelope, added into a mono track."""
    n = int(dur * RATE)
    start = int(at * RATE)
    if n <= 0 or start + n >= len(track):
        return
    t = np.arange(n) / n
    f = f0 + (f1 - f0) * t
    if vibrato[1]:
        f = f + vibrato[1] * np.sin(TAU * vibrato[0] * np.arange(n) / RATE)
    phase = np.cumsum(TAU * f / RATE)
    env = np.sin(np.pi * t) ** 2
    track[start:start + n] += amp * env * (np.sin(phase) + harmonic * np.sin(2 * phase))


def _whistle(tr, rng, at, amp):
    hi = rng.uniform(3600, 4100)
    _note(tr, at, rng.uniform(0.3, 0.4), hi, hi * 0.98, amp, harmonic=0.03)
    lo = hi * rng.uniform(0.8, 0.86)
    _note(tr, at + 0.45, rng.uniform(0.32, 0.42), lo, lo * 0.98, amp, harmonic=0.03)


def _phrase(tr, rng, at, amp):
    t = at
    for _ in range(rng.randint(3, 7)):
        f0 = rng.uniform(2100, 3300)
        d = rng.uniform(0.07, 0.17)
        _note(tr, t, d, f0, f0 + rng.uniform(-700, 700), amp * rng.uniform(0.7, 1.0))
        t += d + rng.uniform(0.04, 0.12)


def _trill(tr, rng, at, amp):
    base = rng.uniform(3900, 4600)
    t = at
    for k in range(rng.randint(10, 22)):
        f = base + (220 if k % 2 else -220)
        _note(tr, t, 0.028, f, f - 300, amp * (0.6 + 0.4 * math.sin(math.pi * k / 22)))
        t += 0.043


def _warble(tr, rng, at, amp):
    f0 = rng.uniform(2600, 3200)
    _note(tr, at, rng.uniform(0.5, 0.8), f0, f0 + rng.uniform(300, 800), amp,
          vibrato=(rng.uniform(24, 36), rng.uniform(300, 520)))


SONGS = [_whistle, _phrase, _trill, _warble]


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    return np.fft.irfft(spec / np.sqrt(1 + (f / cutoff) ** 4), len(x))


def _shift(x: np.ndarray, samples: float) -> np.ndarray:
    """Delay by a fraction of a sample (interaural time is smaller than one sample)."""
    f = np.fft.rfftfreq(len(x))
    return np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * f * samples), len(x))


def _place(mono: np.ndarray, azimuth: float, distance: float) -> np.ndarray:
    """A sound at an angle (degrees, 0 = ahead, + = right) and a distance (metres)."""
    a = math.radians(azimuth)
    itd = HEAD * math.sin(a) * RATE              # samples the far ear lags
    near_gain, far_gain = 1.0, 10 ** (-abs(math.sin(a)) * 5 / 20)  # up to -5 dB shadow
    dry = _lowpass(mono, max(2500.0, 14000.0 / (1 + distance / 12))) / max(1.0, distance / 4)
    shadowed = _lowpass(dry, 3200.0 + 6000.0 * (1 - abs(math.sin(a))))
    near, far = dry * near_gain, _shift(shadowed, abs(itd)) * far_gain
    left, right = (far, near) if a > 0 else (near, far)
    return np.stack([left, right], axis=1)


def _garden(length: int, rng: np.random.Generator) -> np.ndarray:
    """The outdoor echo: sparse reflections off trees and a wall, then a short wide tail."""
    ir = np.zeros((int(0.9 * RATE), 2))
    for ch in range(2):
        for _ in range(26):
            k = int(rng.uniform(0.012, 0.35) * RATE)
            ir[k, ch] += rng.uniform(-1, 1) * math.exp(-k / RATE / 0.18)
        t = np.arange(len(ir)) / RATE
        ir[:, ch] += rng.standard_normal(len(ir)) * np.exp(-t / 0.22) * 0.06 * (t > 0.03)
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True))
    return ir


def render(seed: int = 7) -> np.ndarray:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    total = int((SECONDS + FADE) * RATE)
    birds = np.zeros((total, 2))
    # Each bird: a song, a place (angle, metres), a loudness at its place.
    places = [(-55, 9), (35, 14), (-20, 22), (70, 7)]
    rng.shuffle(places)
    for song, (azimuth, distance) in zip(SONGS, places):
        track = np.zeros(total)
        t = rng.uniform(0.5, 6.0)
        while t < SECONDS + FADE - 2:
            song(track, rng, t, rng.uniform(0.75, 1.0))
            t += rng.uniform(4.0, 12.0)
        birds += _place(track, azimuth + rng.uniform(-8, 8), distance)
    # The garden's echo: more of it for the far birds, as outdoors.
    ir = _garden(total, nrng)
    size = 1 << (total + len(ir)).bit_length()
    wet = np.stack([np.fft.irfft(np.fft.rfft(birds.mean(axis=1), size) * np.fft.rfft(ir[:, ch], size), size)[:total]
                    for ch in range(2)], axis=1)
    mix = birds * 0.8 + wet * 0.45
    # The brook: far off and low, slowly breathing, a little wider than the birds.
    noise = nrng.standard_normal((total, 2))
    brook = np.stack([_lowpass(_lowpass(noise[:, ch], 700.0), 900.0) for ch in range(2)], axis=1)
    breath = np.interp(np.arange(total), np.linspace(0, total, 60), 0.6 + 0.4 * nrng.random(60))
    brook *= breath[:, None]
    brook *= np.sqrt(np.mean(mix ** 2)) / np.sqrt(np.mean(brook ** 2)) * 0.55
    mix += brook
    # Seamless loop: fade the tail into the head.
    n, keep = int(FADE * RATE), int(SECONDS * RATE)
    w = np.linspace(0, 1, n)[:, None]
    mix[:n] = mix[:n] * w + mix[keep:keep + n] * (1 - w)
    mix = mix[:keep]
    mix *= TARGET_RMS / (np.sqrt(np.mean(mix ** 2)) or 1.0)
    return np.tanh(mix / 0.5) * 0.5  # soft ceiling: no chirp ever stabs


def ensure(volume: int = 30) -> str:
    """The ambience file at a volume (0-100), rendering it the first time (a few seconds).
    The level is in the file itself, so it plays at full scale and the balance against
    the voice is exact: at 30 it sits about 12 dB under the voice at 70."""
    volume = max(0, min(100, int(round(volume / 5) * 5)))
    path = FOLDER / f"ambience-v4-{volume:03d}.wav"
    if path.is_file():
        return str(path)
    mix = render() * (volume / 100)
    PATH = path
    tmp = PATH.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((mix * 32767).astype(np.int16).tobytes())
    tmp.replace(PATH)
    for old in FOLDER.glob("ambience-v*.wav"):
        if old != PATH:
            old.unlink(missing_ok=True)
    return str(PATH)
