"""Voiceover via edge-tts.

edge-tts streams WordBoundary events alongside the audio, which give exact
per-word timings. That removes any need for forced alignment (whisper) on the
caption path - the timings are authoritative rather than inferred.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import edge_tts

# edge-tts reports offsets in 100-nanosecond ticks.
TICKS_PER_SECOND = 10_000_000


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class VoiceResult:
    audio_path: Path
    duration: float
    words: list[Word]

    def to_dict(self) -> dict:
        return {
            "audio_path": str(self.audio_path),
            "duration": self.duration,
            "words": [asdict(w) for w in self.words],
        }


async def _synth(
    text: str, voice: str, out_path: Path, rate: str, pitch: str, boundary: str
) -> VoiceResult:
    # `boundary` must be requested explicitly: edge-tts defaults to
    # SentenceBoundary, which is too coarse to drive captions.
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary=boundary)
    words: list[Word] = []
    audio = bytearray()

    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
            start = chunk["offset"] / TICKS_PER_SECOND
            end = start + chunk["duration"] / TICKS_PER_SECOND
            words.append(Word(chunk["text"], start, end))

    if not audio:
        raise RuntimeError(f"edge-tts returned no audio for {text[:60]!r}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(audio))

    # Boundary events can be missing entirely; never return a zero duration,
    # because every downstream beat length is derived from it.
    duration = words[-1].end if words else 0.0
    probed = _probe_duration(out_path)
    if probed and probed > duration:
        duration = probed
    if duration <= 0:
        raise RuntimeError(f"could not determine audio duration for {out_path}")

    return VoiceResult(out_path, duration, words)


def _probe_duration(path: Path) -> float:
    from core.config import ffprobe_bin

    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    raw = "\x00".join([text, voice, rate, pitch])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def synthesize(
    text: str,
    out_path: Path,
    *,
    voice: str = "en-US-AndrewMultilingualNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    boundary: str = "WordBoundary",
    attempts: int = 5,
    use_cache: bool = True,
) -> VoiceResult:
    """Render `text` to `out_path` (mp3) and return audio + word timings.

    edge-tts is a free public endpoint and will intermittently refuse a request
    when called in a tight loop, so this retries with backoff. Results are
    cached by content hash: re-running a job does not re-hit the service, which
    both speeds up reruns and keeps usage low enough to avoid throttling.
    """
    out_path = Path(out_path)
    sidecar = out_path.with_suffix(".json")
    key = _cache_key(text, voice, rate, pitch)

    if use_cache and out_path.exists() and sidecar.exists():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return VoiceResult(
                    out_path,
                    cached["duration"],
                    [Word(**w) for w in cached["words"]],
                )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # unreadable cache is not an error, just regenerate

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = asyncio.run(_synth(text, voice, out_path, rate, pitch, boundary))
            payload = result.to_dict()
            payload["key"] = key
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            return result
        except Exception as exc:  # noqa: BLE001 - edge-tts raises several types
            last = exc
            if attempt == attempts:
                break
            delay = min(30.0, 1.5 * 2 ** (attempt - 1))
            print(f"    tts retry {attempt}/{attempts - 1} in {delay:.0f}s ({type(exc).__name__})")
            time.sleep(delay)

    raise RuntimeError(f"edge-tts failed after {attempts} attempts: {last}") from last


def list_voices(prefix: str = "en-") -> list[str]:
    async def _go() -> list[str]:
        found = await edge_tts.list_voices()
        return sorted(v["ShortName"] for v in found if v["ShortName"].startswith(prefix))

    return asyncio.run(_go())


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="edge-tts smoke test")
    ap.add_argument("--text", default="Options are contracts, not shares.")
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--out", default="out/work/voice_test.mp3")
    ap.add_argument("--list", action="store_true", help="list voices and exit")
    ap.add_argument("--measure", action="store_true",
                    help="re-derive words_per_minute for every channel")
    args = ap.parse_args()

    if args.list:
        for v in list_voices():
            print(v)
        raise SystemExit(0)

    if args.measure:
        import re
        import tempfile

        from core.config import load_channel
        from core.schedule import all_channels

        # A paragraph, not beats: it isolates the voice from how a given script
        # happens to be chopped up. BEAT_FACTOR then converts to the rate real
        # scripts deliver at, since a beat almost always ends on a full stop and
        # the pause for one is not in the prose figure.
        SAMPLE = (
            "The night clerk at the highway motel kept a ledger under the desk. "
            "He wrote down every guest who arrived after two in the morning, and "
            "the number in the second column was never the same twice. Room twelve "
            "had been empty since the spring, and the key still hung on its hook, "
            "and every morning it was warm. He did not mention this to anyone. "
            "The manager visited on Thursdays and asked no questions worth answering."
        )
        # Calibrated against a finished Short: 128 words of real beat-split
        # script took 46.85 s of narration, against 205.7 wpm on the paragraph.
        BEAT_FACTOR = 0.797

        n = len(re.findall(r"\b[\w']+\b", SAMPLE))
        tmp = Path(tempfile.mkdtemp())
        print(f"{'channel':16} {'rate':>5}  {'prose':>7}  {'effective':>9}  config")
        for name in all_channels():
            try:
                voice_cfg = load_channel(name)["voice"]
            except Exception:  # noqa: BLE001
                continue
            res = synthesize(SAMPLE, tmp / f"{name}.mp3", voice=voice_cfg["name"],
                             rate=voice_cfg.get("rate", "+0%"),
                             pitch=voice_cfg.get("pitch", "+0Hz"))
            prose = n / res.duration * 60
            stated = voice_cfg.get("words_per_minute")
            drift = "" if stated is None else f"  (config says {stated})"
            print(f"{name:16} {voice_cfg.get('rate', '+0%'):>5}  "
                  f"{prose:7.1f}  {prose * BEAT_FACTOR:9.0f}{drift}")
        raise SystemExit(0)

    res = synthesize(args.text, Path(args.out), voice=args.voice)
    print(f"{res.audio_path}  {res.duration:.2f}s  {len(res.words)} words")
    for w in res.words[:8]:
        print(f"  {w.start:6.2f}-{w.end:6.2f}  {w.text}")
