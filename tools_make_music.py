"""Generate a calm ambient bed procedurally.

Synthesised rather than sourced: no licence to track, no Content ID claim, and
no attribution obligations. It is deliberately plain - a slow pad that sits
under narration without pulling attention.

    python tools_make_music.py --minutes 6
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

SR = 48_000

# i - VI - III - VII in A minor: calm, unresolved, never triumphant.
PROGRESSION = [
    ("Am", [220.00, 261.63, 329.63]),
    ("F",  [174.61, 220.00, 261.63]),
    ("C",  [261.63, 329.63, 392.00]),
    ("G",  [196.00, 246.94, 293.66]),
]
BAR_SECONDS = 8.0


def _voice(freq: float, n: int, sr: int, detune: float, rng) -> np.ndarray:
    """One slightly-detuned, slowly-breathing sine partial."""
    t = np.arange(n) / sr
    drift = 1.0 + detune + 0.0006 * np.sin(2 * np.pi * (0.03 + rng.random() * 0.05) * t)
    phase = 2 * np.pi * freq * drift * t
    tremolo = 0.82 + 0.18 * np.sin(2 * np.pi * (0.05 + rng.random() * 0.06) * t + rng.random() * 6)
    tone = np.sin(phase) + 0.22 * np.sin(2 * phase) + 0.08 * np.sin(3 * phase)
    return tone * tremolo


def _lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """One-pole lowpass: enough to take the edge off a raw sine stack."""
    a = np.exp(-2 * np.pi * cutoff_hz / sr)
    out = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        out[i] = acc
    return out


def build(minutes: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int(minutes * 60 * SR)
    bar = int(BAR_SECONDS * SR)
    fade = int(1.8 * SR)  # long crossfade so chord changes are barely noticed

    out = np.zeros(total + bar, dtype=np.float64)
    pos = 0
    idx = 0
    while pos < total:
        _, chord = PROGRESSION[idx % len(PROGRESSION)]
        seg = np.zeros(bar + fade)
        for j, f in enumerate(chord):
            for det in (-0.0016, 0.0021):
                seg += _voice(f, bar + fade, SR, det, rng) * (0.55 ** j)
        # sub-octave for warmth
        seg += 0.35 * _voice(chord[0] / 2, bar + fade, SR, 0.0, rng)

        env = np.ones(bar + fade)
        env[:fade] = np.linspace(0, 1, fade) ** 1.5
        env[-fade:] = np.linspace(1, 0, fade) ** 1.5
        seg *= env

        end = min(len(out), pos + len(seg))
        out[pos:end] += seg[: end - pos]
        pos += bar - fade // 2
        idx += 1

    out = out[:total]
    out = _lowpass(out, 900.0, SR)

    # airy noise floor, very quiet
    noise = _lowpass(rng.normal(0, 1, total), 300.0, SR)
    out += 0.02 * noise / (np.max(np.abs(noise)) or 1)

    out /= np.max(np.abs(out)) or 1
    out *= 0.5

    edge = int(4 * SR)
    out[:edge] *= np.linspace(0, 1, edge)
    out[-edge:] *= np.linspace(1, 0, edge)
    return out


def write_wav(mono: np.ndarray, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(mono, -1, 1)
    stereo = np.stack([pcm, np.roll(pcm, 240)], axis=1)  # 5ms offset = subtle width
    data = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(data.tobytes())
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="synthesise an ambient music bed")
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="assets/music/ambient_calm.wav")
    args = ap.parse_args()

    print(f"synthesising {args.minutes:g} min ambient bed (seed {args.seed})...")
    audio = build(args.minutes, args.seed)
    path = write_wav(audio, Path(args.out))
    print(f"wrote {path}  ({path.stat().st_size / 1e6:.1f} MB)")
