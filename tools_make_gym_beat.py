"""Generate a gym/hype beat procedurally, with a drop on a known beat.

Synthesised rather than sourced, for the same reason as tools_make_music.py: no
licence to track, no Content ID claim, no attribution. That matters more here
than it did for the ambient bed -- a Content ID claim on a monetised Short does
not take the video down, it redirects the revenue, which is the whole point of
the channel.

Unlike the ambient bed this is written to be CUT TO, not looped under. `--drop`
places the bar where everything enters at once, so the payoff in the footage
(the lift, the rep, the reveal) can be lined up with it.

    python tools_make_gym_beat.py --seconds 40 --drop 6.5
    python tools_make_gym_beat.py --seconds 40 --drop 6.5 --bpm 150 --out assets/music/gym_150.wav

Before the drop only the sub and a sparse kick play, so narration sits on top
cleanly. After it the snare, hats and a saw stab come in.
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

SR = 44_100


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    """A percussive envelope: near-instant attack, exponential decay."""
    a = max(1, int(attack * SR))
    out = np.ones(n)
    out[:a] = np.linspace(0.0, 1.0, a)
    tail = np.exp(-np.linspace(0.0, 1.0, max(1, n - a)) / max(decay, 1e-6))
    out[a:] = tail
    return out


def _kick(rng) -> np.ndarray:
    n = int(0.28 * SR)
    t = np.arange(n) / SR
    # Pitch sweep from 110 Hz down to 42 Hz is what reads as "kick" rather than
    # "low beep". The sweep has to finish inside the first 60 ms.
    f = 42 + 68 * np.exp(-t / 0.028)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR)
    click = rng.normal(0, 1, n) * np.exp(-t / 0.002) * 0.3
    return (body + click) * _env(n, 0.001, 0.13)


def _snare(rng) -> np.ndarray:
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    noise = rng.normal(0, 1, n)
    tone = np.sin(2 * np.pi * 185 * t) * 0.35
    return (noise * 0.8 + tone) * _env(n, 0.001, 0.09)


def _hat(rng, open_: bool = False) -> np.ndarray:
    n = int((0.16 if open_ else 0.05) * SR)
    noise = rng.normal(0, 1, n)
    # Crude high-pass: subtracting a smoothed copy leaves the top end.
    smooth = np.convolve(noise, np.ones(8) / 8, mode="same")
    return (noise - smooth) * _env(n, 0.0005, 0.05 if open_ else 0.02) * 0.5


def _sub(freq: float, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * freq * t)
            + 0.25 * np.sin(2 * np.pi * freq * 2 * t))


def _stab(freq: float, n: int) -> np.ndarray:
    """A detuned saw chord, the thing that makes the drop feel like a drop."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for mult, det in ((1.0, 0.0), (1.0, 0.6), (1.5, -0.4), (2.0, 0.3)):
        f = freq * mult + det
        out += 2.0 * (t * f - np.floor(0.5 + t * f))
    return out / 4.0 * _env(n, 0.004, 0.18)


def _place(track: np.ndarray, sample: np.ndarray, at: int, gain: float) -> None:
    """Mix `sample` into `track` at sample index `at`, clipped to the end."""
    end = min(len(track), at + len(sample))
    if at >= len(track) or end <= at:
        return
    track[at:end] += sample[:end - at] * gain


def build(seconds: float, bpm: int, drop: float, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    track = np.zeros(n)

    beat = 60.0 / bpm
    step = beat / 2                      # eighth notes
    drop_i = int(drop * SR)

    # Root note walks a short minor figure, one chord per bar.
    roots = [55.00, 55.00, 65.41, 61.74]  # A1, A1, C2, B1

    i = 0
    t = 0.0
    while t < seconds:
        pos = int(t * SR)
        after = pos >= drop_i
        bar = (i // 8) % len(roots)
        root = roots[bar]
        eighth = i % 8

        # Sub runs throughout; it is what holds the track together before the
        # drop, when nothing else is playing.
        if eighth % 2 == 0:
            seg = int(step * 2 * SR)
            _place(track, _sub(root, seg) * _env(seg, 0.005, 0.5), pos, 0.42)

        # Kick: sparse before the drop, four-on-the-floor after.
        if eighth in ((0, 6) if not after else (0, 3, 4, 6)):
            _place(track, _kick(rng), pos, 0.95)

        if after:
            if eighth in (2, 6):
                _place(track, _snare(rng), pos, 0.55)
            _place(track, _hat(rng, open_=eighth == 7), pos, 0.30)
            if eighth in (0, 5):
                _place(track, _stab(root * 4, int(step * SR)), pos, 0.16)

        i += 1
        t += step

    # One-bar riser into the drop: noise swelling up to the hit.
    riser_n = min(drop_i, int(beat * 4 * SR))
    if riser_n > 0:
        seg = rng.normal(0, 1, riser_n)
        seg *= np.linspace(0.0, 1.0, riser_n) ** 3
        _place(track, seg, drop_i - riser_n, 0.22)

    peak = float(np.max(np.abs(track))) or 1.0
    return track / peak * 0.89


def write_wav(mono: np.ndarray, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--bpm", type=int, default=146)
    ap.add_argument("--drop", type=float, default=6.0,
                    help="seconds in where everything enters at once")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="assets/music/gym_beat.wav")
    args = ap.parse_args()

    dest = write_wav(build(args.seconds, args.bpm, args.drop, args.seed),
                     Path(args.out))
    print(f"wrote {dest}  {args.seconds:.0f}s  {args.bpm} bpm  drop at {args.drop:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
