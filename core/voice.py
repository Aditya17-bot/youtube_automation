"""Voiceover, via edge-tts or Azure Speech.

Both engines stream word-boundary events alongside the audio, which give exact
per-word timings. That removes any need for forced alignment (whisper) on the
caption path - the timings are authoritative rather than inferred.

Two providers, chosen per channel by `voice.provider`:

  edge-tts   free, no key, no emotional direction. The text is XML-escaped by
             the library, so tags and SSML are read aloud rather than obeyed.
  azure      the same voice family behind a real key. Accepts
             `<mstts:express-as style="terrified">`, which is the only way to
             get per-sentence delivery direction, and still reports word
             boundaries so nothing downstream changes.

Azure is the one that costs money in principle and nothing in practice: the F0
tier covers 500k characters a month against the ~72k three Shorts a day need.

A channel asking for azure with no key configured falls back to edge-tts with
the tags stripped. An unattended nightly run losing three videos over a missing
environment variable is worse than three flatly-read ones.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import edge_tts

from core import emotion

# Both engines report offsets in 100-nanosecond ticks.
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
    return _finish(out_path, words, text)


# --- Azure Speech --------------------------------------------------------

SSML = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
    'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang={lang}>'
    "<voice name={voice}><prosody rate={rate} pitch={pitch}>{body}</prosody></voice></speak>"
)


def azure_credentials() -> tuple[str, str] | None:
    """(key, region) from the environment or secrets/azure_speech.json, else None.

    Environment wins, so a scheduled task can carry the key without a file on
    disk. The JSON form is there because everything else this project needs is
    already a file in secrets/.
    """
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if key and region:
        return key, region

    from core.config import PATHS

    path = PATHS.root / "secrets" / "azure_speech.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("key") and data.get("region"):
                return data["key"], data["region"]
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def build_ssml(
    text: str, voice: str, rate: str, pitch: str,
    default_style: str | None = None, style_degree: float | None = None,
) -> str:
    """Turn tagged narration into one SSML document.

    Each `[tag]` in the text opens an express-as span that runs to the next tag
    or the end of the beat, which is what makes direction per-sentence rather
    than per-video.
    """
    lang = "-".join(voice.split("-")[:2]) or "en-US"
    parts: list[str] = []

    for style, chunk in emotion.segments(text, voice, default_style):
        body = escape(chunk)
        if style:
            degree = "" if style_degree is None else f" styledegree={quoteattr(f'{style_degree:g}')}"
            body = f"<mstts:express-as style={quoteattr(style)}{degree}>{body}</mstts:express-as>"
        parts.append(body)

    return SSML.format(
        lang=quoteattr(lang), voice=quoteattr(voice),
        rate=quoteattr(rate), pitch=quoteattr(pitch),
        body=" ".join(parts),
    )


def _ticks(value) -> float:
    """Seconds from a word-boundary duration, which the SDK types inconsistently."""
    import datetime as _dt

    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    try:
        return float(value) / TICKS_PER_SECOND
    except (TypeError, ValueError):
        return 0.0


def _synth_azure(
    text: str, voice: str, out_path: Path, rate: str, pitch: str,
    style: str | None, style_degree: float | None,
) -> VoiceResult:
    import azure.cognitiveservices.speech as speechsdk

    creds = azure_credentials()
    if not creds:
        raise RuntimeError("no Azure Speech credentials")
    key, region = creds

    cfg = speechsdk.SpeechConfig(subscription=key, region=region)
    # mp3 to match what edge-tts writes: _pad_audio and the concat step in
    # core.assemble both expect the same container as before.
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    # Word boundaries are not reported for SSML unless this is asked for.
    cfg.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true"
    )

    words: list[Word] = []

    def on_boundary(evt) -> None:
        # Punctuation and sentence events would not match how core.captions
        # tokenises, and a mismatched count silently disables sentence breaks.
        if evt.boundary_type != speechsdk.SpeechSynthesisBoundaryType.Word:
            return
        start = _ticks(evt.audio_offset)
        words.append(Word(evt.text, start, start + _ticks(evt.duration)))

    synth = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
    synth.synthesis_word_boundary.connect(on_boundary)

    ssml = build_ssml(text, voice, rate, pitch, style, style_degree)
    result = synth.speak_ssml_async(ssml).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = result.reason
        if result.reason == speechsdk.ResultReason.Canceled:
            cancel = result.cancellation_details
            detail = f"{cancel.reason}: {cancel.error_details}"
        raise RuntimeError(f"azure speech failed ({detail})")

    audio = bytes(result.audio_data)
    if not audio:
        raise RuntimeError(f"azure returned no audio for {text[:60]!r}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    return _finish(out_path, words, text)


def _finish(out_path: Path, words: list[Word], text: str) -> VoiceResult:
    """Settle the authoritative duration for a rendered beat.

    Boundary events can be missing entirely; never return a zero duration,
    because every downstream beat length is derived from it.
    """
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


def _cache_key(text: str, voice: str, rate: str, pitch: str, provider: str,
               style: str | None, style_degree: float | None) -> str:
    raw = "\x00".join([text, voice, rate, pitch, provider, style or "",
                       "" if style_degree is None else f"{style_degree:g}"])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


_FALLBACK_WARNED = False


def _azure_available() -> tuple[bool, str]:
    """Whether an Azure render can be attempted, and why not when it cannot."""
    try:
        import azure.cognitiveservices.speech  # noqa: F401
    except ImportError:
        return False, "azure-cognitiveservices-speech is not installed"
    if not azure_credentials():
        return False, "AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not set"
    return True, ""


def synthesize(
    text: str,
    out_path: Path,
    *,
    voice: str = "en-US-AndrewMultilingualNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    boundary: str = "WordBoundary",
    provider: str = "edge-tts",
    style: str | None = None,
    style_degree: float | None = None,
    fallback_voice: str | None = None,
    attempts: int = 5,
    use_cache: bool = True,
) -> VoiceResult:
    """Render `text` to `out_path` (mp3) and return audio + word timings.

    `text` may carry emotional direction tags (see core.emotion). On Azure they
    become express-as spans; on edge-tts they are stripped, because that engine
    would otherwise read them out as words.

    Both services will intermittently refuse a request when called in a tight
    loop, so this retries with backoff. Results are cached by content hash:
    re-running a job does not re-hit the service, which both speeds up reruns
    and keeps usage inside the free character allowance.
    """
    global _FALLBACK_WARNED

    out_path = Path(out_path)
    sidecar = out_path.with_suffix(".json")

    if provider == "azure":
        ok, why = _azure_available()
        if not ok:
            # The free endpoint carries a much smaller voice catalogue than a
            # keyed account does, so an Azure-only voice name is not merely
            # flat here - it returns no audio at all. Falling back to a voice
            # edge-tts does not have would turn a degraded run into no run.
            if fallback_voice and fallback_voice != voice:
                voice = fallback_voice
            if not _FALLBACK_WARNED:
                print(f"    [voice] azure unavailable ({why}); using edge-tts "
                      f"as {voice}, delivery will be flat")
                _FALLBACK_WARNED = True
            provider = "edge-tts"

    # edge-tts cannot act on direction, so the tags have to come out of the
    # text before it is sent - and out of the cache key with them, so a flatly
    # read beat is not reused once a key is configured.
    if provider != "azure":
        text = emotion.strip(text)
        style, style_degree = None, None

    key = _cache_key(text, voice, rate, pitch, provider, style, style_degree)

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
            if provider == "azure":
                result = _synth_azure(
                    text, voice, out_path, rate, pitch, style, style_degree
                )
            else:
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

    raise RuntimeError(f"{provider} failed after {attempts} attempts: {last}") from last


def azure_voice_styles(prefix: str = "en-US-") -> dict[str, list[str]]:
    """Styles each voice really supports, read from the account's voice list.

    The table in core.emotion is a static copy for offline validation; this is
    the authority, and disagreement between them means the table is stale.
    """
    import urllib.request

    creds = azure_credentials()
    if not creds:
        raise RuntimeError("no Azure Speech credentials")
    key, region = creds

    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
        headers={"Ocp-Apim-Subscription-Key": key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        voices = json.loads(resp.read().decode("utf-8"))

    return {
        v["ShortName"]: sorted(v.get("StyleList") or [])
        for v in voices
        if v["ShortName"].startswith(prefix) and v.get("StyleList")
    }


def list_voices(prefix: str = "en-") -> list[str]:
    async def _go() -> list[str]:
        found = await edge_tts.list_voices()
        return sorted(v["ShortName"] for v in found if v["ShortName"].startswith(prefix))

    return asyncio.run(_go())


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="tts smoke test")
    ap.add_argument("--text", default="Options are contracts, not shares.")
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--provider", default="edge-tts", choices=["edge-tts", "azure"])
    ap.add_argument("--style", help="default express-as style (azure only)")
    ap.add_argument("--style-degree", type=float,
                    help="style intensity, 0.01-2 (azure only)")
    ap.add_argument("--out", default="out/work/voice_test.mp3")
    ap.add_argument("--list", action="store_true", help="list voices and exit")
    ap.add_argument("--styles", action="store_true",
                    help="list azure voices that support styles, and exit")
    ap.add_argument("--ssml", action="store_true", help="print the SSML and exit")
    ap.add_argument("--measure", action="store_true",
                    help="re-derive words_per_minute for every channel")
    args = ap.parse_args()

    if args.list:
        for v in list_voices():
            print(v)
        raise SystemExit(0)

    if args.styles:
        for name, styles in sorted(azure_voice_styles().items()):
            print(f"{name:34} {', '.join(styles)}")
        raise SystemExit(0)

    if args.ssml:
        print(build_ssml(args.text, args.voice, "+0%", "+0Hz",
                         args.style, args.style_degree))
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
                             pitch=voice_cfg.get("pitch", "+0Hz"),
                             provider=voice_cfg.get("provider", "edge-tts"),
                             style=voice_cfg.get("default_style"),
                             style_degree=voice_cfg.get("style_degree"))
            prose = n / res.duration * 60
            stated = voice_cfg.get("words_per_minute")
            drift = "" if stated is None else f"  (config says {stated})"
            print(f"{name:16} {voice_cfg.get('rate', '+0%'):>5}  "
                  f"{prose:7.1f}  {prose * BEAT_FACTOR:9.0f}{drift}")
        raise SystemExit(0)

    res = synthesize(args.text, Path(args.out), voice=args.voice,
                     provider=args.provider, style=args.style,
                     style_degree=args.style_degree)
    print(f"{res.audio_path}  {res.duration:.2f}s  {len(res.words)} words")
    for w in res.words[:8]:
        print(f"  {w.start:6.2f}-{w.end:6.2f}  {w.text}")
