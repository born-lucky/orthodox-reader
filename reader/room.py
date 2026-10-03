"""Put the voice in a room, a few metres in front of you.

A text-to-speech voice is recorded dry and close. Played the same in both ears (as
headphones do) it is heard *inside* the head. Adding a little reverb does not fix
that: the ear still hears the dry voice first and loudest. Distance is heard in the
balance of the direct sound against the room, and in how each arrival reaches the
two ears differently.

So the voice is not mixed with reverb; it is *sent through a room* to two ears:

  the room     a small stone church, 9 x 12 x 7 m. The reader stands 4 m in front
               of you, a little to the right. Every reflection off the walls,
               floor and ceiling (up to the 9th bounce) is computed from the
               geometry, the image-source method, and arrives when and from where
               it really would.
  the ears     each arrival is heard by a spherical head (Woodworth): the far ear
               hears it later (up to 0.66 ms) and duller (the head's shadow);
               sounds from behind are duller in both ears.
  the air      every bounce and every metre takes some of the highs.
  the tail     after the first quarter-second the reflections are too many to
               count: a diffuse, decorrelated tail continues them (RT60 1.8 s),
               growing darker as it decays.

There is no separate dry voice: the direct sound is just the first arrival, from
4 m away, and the room carries about as much of the voice as it does. That balance
is what the ear reads as distance. Level: quiet and even, with a soft limit.
Everything is computed here (numpy), so there is nothing to license.
"""

from __future__ import annotations

import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 24000
C = 343.0                     # speed of sound, m/s
ROOM = (9.0, 12.0, 7.0)       # width (x), length (y, the way you face), height (z)
LISTENER = (4.5, 3.0, 1.2)    # seated
SOURCE = (4.9, 7.0, 1.6)      # standing, 4 m ahead, a little to the right
BETA = 0.86                   # how much a stone wall reflects (pressure)
ORDER = 9                     # bounces computed exactly
EARLY = 0.20                  # seconds of exact reflections; the diffuse tail after
RT60 = 1.8
HEAD = 0.0875                 # head radius, m
TARGET_RMS = 10 ** (-27 / 20)
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


def _lowpass(x: np.ndarray, cutoff: float, axis: int = 0) -> np.ndarray:
    n = x.shape[axis]
    f = np.fft.rfftfreq(n, 1 / RATE)
    gain = 1 / np.sqrt(1 + (f / cutoff) ** 4)
    shape = [1] * x.ndim
    shape[axis] = len(f)
    return np.fft.irfft(np.fft.rfft(x, axis=axis) * gain.reshape(shape), n, axis=axis)


def _images() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every image of the source up to ORDER bounces: (positions, bounce counts)."""
    rng = np.arange(-ORDER, ORDER + 1)
    pos, bounces = [], []
    for dim in range(3):
        size, s = ROOM[dim], SOURCE[dim]
        p, b = [], []
        for n in rng:
            for side in (0, 1):
                p.append((1 - 2 * side) * s + 2 * n * size)
                b.append(abs(n - side) + abs(n))
        pos.append(np.array(p))
        bounces.append(np.array(b))
    X, Y, Z = np.meshgrid(*pos, indexing="ij")
    BX, BY, BZ = np.meshgrid(*bounces, indexing="ij")
    order = (BX + BY + BZ).ravel()
    keep = order <= ORDER
    xyz = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)[keep]
    return xyz, order[keep], keep


@lru_cache(maxsize=1)
def brir() -> np.ndarray:
    """The room's response at the two ears (binaural room impulse response)."""
    length = int(RATE * RT60 * 1.25)
    xyz, order, _ = _images()
    v = xyz - np.array(LISTENER)
    dist = np.linalg.norm(v, axis=1)
    arrive = dist / C
    early = arrive < EARLY
    v, dist, arrive, order = v[early], dist[early], arrive[early], order[early]
    azimuth = np.arctan2(v[:, 0], v[:, 1])              # 0 = straight ahead, + = right
    elevation = np.arcsin(np.clip(v[:, 2] / dist, -1, 1))
    lateral = np.arcsin(np.clip(np.sin(azimuth) * np.cos(elevation), -1, 1))
    itd = HEAD / C * (np.abs(lateral) + np.abs(np.sin(lateral)))  # Woodworth
    gain = BETA ** order / dist
    shadow = np.abs(np.sin(lateral))                     # how much the far ear is shaded
    behind = np.clip(-np.cos(azimuth) * np.cos(elevation), 0, 1) * 0.45
    dull = 1 - 0.83 ** order                             # each bounce takes some highs

    # Three tap buffers per ear: plain, dulled by walls and air, shaded by the head.
    plain = np.zeros((length, 2))
    walls = np.zeros((length, 2))
    shade = np.zeros((length, 2))
    for ear, sign in ((0, -1), (1, 1)):                  # 0 = left, 1 = right
        far = (np.sign(lateral) == -sign) & (np.abs(lateral) > 1e-3)
        t = arrive + np.where(far, itd, 0.0)
        k = np.minimum((t * RATE).astype(int), length - 1)
        g_far = np.where(far, 1 - 0.55 * shadow, 1.0)    # quieter on the far side
        s_far = np.where(far, shadow, 0.0)
        base = gain * g_far
        np.add.at(plain, (k, ear), base * (1 - dull) * (1 - behind) * (1 - s_far))
        np.add.at(walls, (k, ear), base * (dull + behind * (1 - dull)) * (1 - s_far))
        np.add.at(shade, (k, ear), base * s_far)
    ir = plain + _lowpass(walls, 3400.0) + _lowpass(shade, 1600.0)

    # The diffuse tail: decorrelated noise, decaying at RT60 and darkening with time,
    # scaled so it carries on from the last exact reflections.
    noise = np.random.default_rng(1054).standard_normal((length, 2))
    t = np.arange(length) / RATE
    env = np.exp(-6.91 * t / RT60)
    bright, dark = noise, _lowpass(noise, 1800.0)
    mix = np.clip((t - EARLY) / 0.8, 0, 1)[:, None]
    tail = (bright * (1 - mix) + dark * mix * 1.6) * env[:, None]
    # Match the tail to the reflections where both are dense (40-110 ms), then hand
    # over smoothly: the tail rises from 60 to 140 ms while the counted taps fall away.
    window = (t > 0.04) & (t < 0.11)
    ref = np.sqrt(np.mean(ir[window] ** 2)) / max(1e-9, np.sqrt(np.mean(tail[window] ** 2)))
    fade_in = np.clip((t - 0.06) / 0.08, 0, 1)[:, None]
    fade_out = np.clip((EARLY - t) / (EARLY - 0.12), 0, 1)[:, None]
    ir = ir * fade_out + tail * ref * fade_in * 0.8
    return (ir / np.abs(ir).max()).astype(np.float32)


def direct_to_room_db() -> float:
    """Direct sound energy against everything else, in dB (about 0 for a reader 4 m away)."""
    ir = brir()
    first = int(np.argmax(np.abs(ir).max(axis=1)))
    span = int(0.0025 * RATE)
    direct = np.sum(ir[max(0, first - span): first + span] ** 2)
    rest = np.sum(ir ** 2) - direct
    return float(10 * np.log10(direct / rest))


def _convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, size)
    return np.stack([np.fft.irfft(X * np.fft.rfft(ir[:, ch], size), size)[:n] for ch in range(2)],
                    axis=1).astype(np.float32)


def place(src: str, volume: float = 1.0) -> str:
    """Send one spoken chunk through the room to the two ears; returns a new stereo WAV.
    `volume` (0-1) is applied here, in the file, so playback can stay at full scale."""
    path = Path(src)
    voice = _decode(path)
    if not len(voice):
        return src
    voice = voice - _lowpass(voice, 90.0)  # no close-microphone boom
    out = _convolve(voice, brir())
    rms = float(np.sqrt(np.mean(out[: len(voice)] ** 2))) or 1.0
    out *= TARGET_RMS / rms * volume
    out = np.tanh(out / CEILING) * CEILING
    out = out[: len(voice) + int(RATE * 1.2)]  # the room rings on into the pause
    fade = int(RATE * 0.6)
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    dest = path.with_name(path.stem + "-room.wav")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    return str(dest)
