"""Turn a validated script into a finished video.

Ordering matters: narration is synthesised first so that each beat's visual is
rendered to the exact length of its own voiceover. Timing the audio to the
video instead would force either dead air or clipped speech.

  script.json -> per-beat voice -> per-beat clip -> concat -> captions -> mix
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import captions as cap
from core import voice as tts
from core.config import PATHS, ffmpeg_bin, ffprobe_bin, load_channel, pick_encoder
from core.script import iter_beats, load_format
from core.theme import Theme

# Breathing room after each beat's narration, so cuts do not clip the last word.
TAIL_PAD = 0.45
# Lead-in before the first word, and a beat of silence at the end.
HEAD_PAD = 0.35
OUTRO_PAD = 1.2


@dataclass
class BeatTiming:
    index: int
    start: float
    duration: float
    audio: Path
    clip: Path


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:6])}...\n{proc.stderr[-1500:]}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _pad_audio(src: Path, dest: Path, duration: float, lead: float) -> None:
    """Normalise one beat's narration to exactly `duration` seconds of wav."""
    _run([
        ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src),
        "-af", f"adelay={int(lead * 1000)}|{int(lead * 1000)},"
               f"apad,atrim=0:{duration:.3f},asetpts=N/SR/TB",
        "-ar", "48000", "-ac", "2", str(dest),
    ])


def _concat(paths: list[Path], dest: Path, list_file: Path) -> None:
    # The concat demuxer resolves entries relative to the LIST FILE's directory,
    # so relative paths here get joined onto the job dir and vanish. Absolute
    # paths are the only reliable form.
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in paths) + "\n", encoding="utf-8"
    )
    _run([
        ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(dest),
    ])


def _render(fmt, beat: dict, duration: float, clip: Path, theme, index: int) -> Path:
    """Call a format's renderer, passing the beat index only if it accepts one.

    Arity is inspected rather than probed with try/except TypeError, which would
    swallow a genuine TypeError raised inside the renderer itself.
    """
    import inspect

    params = inspect.signature(fmt.render_beat).parameters
    if len(params) >= 5:
        return fmt.render_beat(beat, duration, clip, theme, index)
    return fmt.render_beat(beat, duration, clip, theme)


def beat_label(beat: dict) -> str:
    """Short tag for progress output; formats describe beats differently."""
    visual = beat.get("visual")
    if isinstance(visual, dict) and visual.get("type"):
        return visual["type"]
    return beat.get("move") or "image"


def find_music(channel: dict) -> Path | None:
    if not channel.get("music", {}).get("enabled"):
        return None
    tracks = sorted(
        p for p in PATHS.music.glob("*")
        if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".opus", ".ogg"}
    )
    return tracks[0] if tracks else None


def build(script_path: Path, channel_name: str = "finance", *, keep_work: bool = True) -> Path:
    channel = load_channel(channel_name)
    theme = Theme.from_channel(channel)
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))

    job = Path(script_path).parent
    audio_dir = job / "audio"
    clip_dir = job / "clips"
    for d in (audio_dir, clip_dir):
        d.mkdir(parents=True, exist_ok=True)

    beats = list(iter_beats(script))
    vcfg = channel["voice"]
    fmt = load_format(channel["format"])

    # --- 1. narration first: it decides every downstream duration -----------
    timings: list[BeatTiming] = []
    all_words: list[tts.Word] = []
    cue_breaks: set[int] = set()
    cursor = 0.0

    for i, beat in enumerate(beats):
        raw = audio_dir / f"beat_{i:02d}.mp3"
        result = tts.synthesize(
            beat["vo"], raw,
            voice=vcfg["name"], rate=vcfg.get("rate", "+0%"), pitch=vcfg.get("pitch", "+0Hz"),
        )

        lead = HEAD_PAD if i == 0 else 0.0
        tail = TAIL_PAD + (OUTRO_PAD if i == len(beats) - 1 else 0.0)
        duration = lead + result.duration + tail

        padded = audio_dir / f"beat_{i:02d}.wav"
        _pad_audio(raw, padded, duration, lead)

        # Sentence ends are derived from the source text, since the TTS word
        # events carry no punctuation to break on.
        cue_breaks |= cap.sentence_breaks(result.words, beat["vo"], offset=len(all_words))
        for w in result.words:
            all_words.append(tts.Word(w.text, cursor + lead + w.start, cursor + lead + w.end))

        clip = clip_dir / f"beat_{i:02d}.mp4"
        _render(fmt, beat, duration, clip, theme, i)

        timings.append(BeatTiming(i, cursor, duration, padded, clip))
        cursor += duration
        print(f"  beat {i:02d} [{beat_label(beat):<14}] {duration:5.2f}s")

    total = cursor
    print(f"  total: {total / 60:.2f} min")

    # Persisted so packaging can build chapter markers without re-synthesising.
    (job / "timings.json").write_text(
        json.dumps(
            [{"index": t.index, "start": round(t.start, 3), "duration": round(t.duration, 3)}
             for t in timings],
            indent=2,
        ),
        encoding="utf-8",
    )

    # --- 2. concat video and audio separately, then marry them -------------
    video_only = job / "video.mp4"
    voice_only = job / "voice.wav"
    _concat([t.clip for t in timings], video_only, job / "clips.txt")
    _concat([t.audio for t in timings], voice_only, job / "audio.txt")

    # --- 3. captions from the TTS word boundaries -------------------------
    ccfg = channel.get("captions", {})
    style = channel.get("style", {})
    ass_path = None
    if ccfg.get("enabled", True):
        cues = cap.group_words(
            all_words, max_chars=ccfg.get("max_chars", 42), breaks=cue_breaks
        )
        ass_path = cap.write_ass(
            cues, job / "captions.ass",
            width=theme.width, height=theme.height, font=theme.font,
            primary=style.get("text", "#E6EDF3"), outline=style.get("background", "#0E1116"),
        )
        print(f"  captions: {len(cues)} cues")

    # --- 4. final mix ------------------------------------------------------
    music = find_music(channel)
    mcfg = channel.get("music", {})
    out_path = job / "final.mp4"

    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video_only)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    cmd += ["-i", str(voice_only)]

    voice_idx = 2 if music else 1
    filters = []
    if music:
        # Music sits under the narration and ducks a little while speech plays.
        #
        # normalize=0 on amix is essential: the default divides every input by
        # the input count, quietly costing ~6dB across the whole mix.
        # Ducking is gentle (ratio 4) so the bed stays present rather than
        # disappearing; loudnorm then lifts the result to the streaming target.
        filters.append(
            f"[1:a]volume={mcfg.get('gain_db', -20)}dB,"
            f"aformat=sample_rates=48000:channel_layouts=stereo[bed];"
            f"[bed][{voice_idx}:a]sidechaincompress="
            f"threshold=0.05:ratio=4:attack=20:release=500[ducked];"
            f"[ducked][{voice_idx}:a]amix=inputs=2:duration=first:normalize=0[mixed];"
            f"[mixed]loudnorm=I={mcfg.get('target_lufs', -14)}:TP=-1.5:LRA=11[aout]"
        )
        audio_map = "[aout]"
    else:
        filters.append(
            f"[{voice_idx}:a]loudnorm=I={mcfg.get('target_lufs', -14)}:TP=-1.5:LRA=11[aout]"
        )
        audio_map = "[aout]"

    if ass_path:
        escaped = str(ass_path).replace("\\", "/").replace(":", r"\:")
        filters.append(f"[0:v]subtitles='{escaped}'[vout]")
        video_map = "[vout]"
    else:
        video_map = "0:v"

    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    cmd += [
        "-map", video_map, "-map", audio_map,
        "-c:v", pick_encoder(channel["video"].get("encoder", "libx264")),
        "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    _run(cmd)

    dur = probe_duration(out_path)
    size_mb = out_path.stat().st_size / 1e6
    print(f"  -> {out_path.name}  {dur / 60:.2f} min  {size_mb:.1f} MB"
          f"  music={'yes' if music else 'none'}")
    return out_path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="assemble a finished video from script.json")
    ap.add_argument("--script", required=True)
    ap.add_argument("--channel", default="finance")
    args = ap.parse_args()

    print(f"assembling {args.script}")
    build(Path(args.script), args.channel)
