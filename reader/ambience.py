"""A quiet garden: songbirds over a soft brook, synthesised (no recordings, so no
licence questions). Rendered once to %APPDATA% as a seamless 75-second loop.

Four birds sit at fixed places in the stereo field, each with its own song:
  whistle  two clear falling notes ("fee-bee")
  phrase   a short varied run of sweeping notes, like a robin
  trill    a fast run of tiny notes
  warble   a quick vibrato tumble, like a wren
"""

from __future__ import annotations

import array
import math
import random
import wave

from . import settings

RATE = 22050
SECONDS = 75
FADE = 3.0
PATH = settings.HOME / "ambience-v2.wav"
TAU = 2 * math.pi


def _note(left, right, rate, at, dur, f0, f1, amp, pan, vibrato=(0.0, 0.0), harmonic=0.12):
    """One sweeping note with a smooth envelope, added into the two channels."""
    n = int(dur * rate)
    start = int(at * rate)
    if start + n >= len(left):
        return
    gl, gr = amp * math.cos(pan * math.pi / 2), amp * math.sin(pan * math.pi / 2)
    vib_rate, vib_depth = vibrato
    phase = 0.0
    for i in range(n):
        t = i / n
        f = f0 + (f1 - f0) * t + (vib_depth * math.sin(TAU * vib_rate * i / rate) if vib_depth else 0.0)
        phase += TAU * f / rate
        env = math.sin(math.pi * t) ** 2
        s = env * (math.sin(phase) + harmonic * math.sin(2 * phase))
        left[start + i] += s * gl
        right[start + i] += s * gr


def _whistle(L, R, rng, at, amp, pan):
    hi = rng.uniform(3600, 4100)
    _note(L, R, RATE, at, rng.uniform(0.3, 0.4), hi, hi * 0.98, amp, pan, harmonic=0.03)
    lo = hi * rng.uniform(0.8, 0.86)
    _note(L, R, RATE, at + 0.45, rng.uniform(0.32, 0.42), lo, lo * 0.98, amp, pan, harmonic=0.03)


def _phrase(L, R, rng, at, amp, pan):
    t = at
    for _ in range(rng.randint(3, 7)):
        f0 = rng.uniform(2100, 3300)
        f1 = f0 + rng.uniform(-700, 700)
        d = rng.uniform(0.07, 0.17)
        _note(L, R, RATE, t, d, f0, f1, amp * rng.uniform(0.7, 1.0), pan)
        t += d + rng.uniform(0.04, 0.12)


def _trill(L, R, rng, at, amp, pan):
    base = rng.uniform(3900, 4600)
    t = at
    for k in range(rng.randint(10, 22)):
        f = base + (220 if k % 2 else -220)
        _note(L, R, RATE, t, 0.028, f, f - 300, amp * (0.6 + 0.4 * math.sin(math.pi * k / 22)), pan)
        t += 0.043


def _warble(L, R, rng, at, amp, pan):
    f0 = rng.uniform(2600, 3200)
    _note(L, R, RATE, at, rng.uniform(0.5, 0.8), f0, f0 + rng.uniform(300, 800), amp, pan,
          vibrato=(rng.uniform(24, 36), rng.uniform(300, 520)))


SONGS = [_whistle, _phrase, _trill, _warble]


def render(seed: int = 7) -> tuple[array.array, array.array]:
    rng = random.Random(seed)
    total = int((SECONDS + FADE) * RATE)
    L = array.array("d", bytes(8 * total))
    R = array.array("d", bytes(8 * total))
    # The brook: two low-passed noises (left/right), slowly swelling.
    # Three one-pole stages roll off the hiss, leaving a soft low rush of water.
    a = b = c = d = e = f = 0.0
    swell = 0.5
    for i in range(total):
        if i % 2205 == 0:
            swell = min(1.0, max(0.35, swell + rng.uniform(-0.06, 0.06)))
        a += 0.08 * (rng.uniform(-1, 1) - a)
        b += 0.08 * (rng.uniform(-1, 1) - b)
        c += 0.08 * (a - c)
        d += 0.08 * (b - d)
        e += 0.15 * (c - e)
        f += 0.15 * (d - f)
        L[i] += 2.2 * e * swell
        R[i] += 2.2 * f * swell
    # The birds, each at its own distance and side, singing every few seconds.
    birds = [(song, rng.uniform(0.1, 0.9), rng.uniform(0.06, 0.13)) for song in SONGS]
    for song, pan, amp in birds:
        t = rng.uniform(0.5, 6.0)
        while t < SECONDS + FADE - 2:
            song(L, R, rng, t, amp * rng.uniform(0.75, 1.0), pan)
            t += rng.uniform(3.5, 11.0)
    # Seamless loop: fade the tail into the head.
    n = int(FADE * RATE)
    keep = int(SECONDS * RATE)
    for i in range(n):
        w = i / n
        L[i] = L[i] * w + L[keep + i] * (1 - w)
        R[i] = R[i] * w + R[keep + i] * (1 - w)
    return L[:keep], R[:keep]


def ensure() -> str:
    """The ambience file, rendering it the first time (a few seconds)."""
    if PATH.is_file():
        return str(PATH)
    L, R = render()
    peak = max(max(abs(x) for x in L), max(abs(x) for x in R)) or 1.0
    scale = 0.8 * 32767 / peak
    pcm = array.array("h", bytes(4 * len(L)))
    for i in range(len(L)):
        pcm[2 * i] = int(L[i] * scale)
        pcm[2 * i + 1] = int(R[i] * scale)
    tmp = PATH.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    tmp.replace(PATH)
    return str(PATH)
