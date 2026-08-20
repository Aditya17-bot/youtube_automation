"""Narrated story format: AI images with camera moves.

16:9, 6-10 minutes, one generated image per beat with a slow Ken Burns move.
Hard cuts between beats - with continuous motion inside each shot that reads as
deliberate, and it avoids re-encoding the whole timeline for crossfades.

The camera pans INSIDE an oversized image, which is why core.imagegen upscales
before this module ever sees the file.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from core import imagegen
from core.config import ffmpeg_bin, pick_encoder
from core.script import check_duration

# Cycled so consecutive shots never repeat a move.
MOVES = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "pan_down"]

ZOOM_MAX = 1.18
PAN_ZOOM = 1.12

VISUAL_CONTRACT = """
Each beat needs an "image_prompt": a single vivid sentence describing ONE shot.

  - Describe a scene, not a story. No dialogue, no captions, no text in frame.
  - Name the subject, the setting, the light, and the mood.
  - Prefer concrete nouns over abstractions ("a rusted trawler in fog", not
    "the feeling of being lost").
  - No real public figures, no brand names, no copyrighted characters.
  - Vary the framing across beats: wide establishing, mid, close detail.

Also give each beat a "move", one of: zoom_in, zoom_out, pan_left, pan_right,
pan_up, pan_down. Vary it between consecutive beats.
"""

FACTUAL_RULES = """RULES:
  - Everything stated as fact must be genuinely true. Where something is
    disputed or unknown, say so plainly - that honesty is the format's appeal.
  - No real living private individuals as subjects.
  - Do not describe graphic violence or gore.
"""

FICTION_RULES = """RULES:
  - This is ORIGINAL FICTION. Invent the events, places and people.
  - Never frame it as a true story, a real account, or something that happened
    to a real person. No "based on true events", no fake locations presented as
    real, no real named towns tied to invented deaths.
  - Dread comes from atmosphere, restraint and implication - not from gore.
    No graphic injury, torture, mutilation or body horror.
  - No sexual content. No depiction of self-harm or suicide method.
  - No children as victims.
  - Leave the final image unresolved rather than explaining the horror away.
"""

PROMPT = """You are a scriptwriter for a faceless YouTube channel
called "{channel_name}".

Write a {dur_lo}-{dur_hi} second narrated story: {topic_title}

TONE: {tone}
AUDIENCE: {audience}

Write it as a story, not a list. Build tension, then turn it. Use short
sentences and concrete sensory detail. Open cold - the first sentence must make
someone stop scrolling. No "welcome back to the channel", no intro branding.

LENGTH: the combined `vo` text must total {w_lo}-{w_hi} words. Count them.

{rules}
{visual_contract}

Break the story into {beat_lo}-{beat_hi} beats. Each beat is 2-4 spoken
sentences plus one image.

Return ONLY this JSON:
{{
  "topic_id": "{topic_id}",
  "title": "YouTube title, <=70 chars, curiosity-driven, honest, no emoji",
  "hook": "the opening line, repeated from the first beat",
  "thumb": "2-4 words for the thumbnail. A complete phrase, ominous, never a truncated sentence.",
  "description": "4-6 sentences for the YouTube description, plain text",
  "tags": ["10-14 lowercase youtube tags"],
  "sections": [
    {{"id":"open","beats":[{{"vo":"...","image_prompt":"...","move":"zoom_in"}}]}},
    {{"id":"build","beats":[]}},
    {{"id":"turn","beats":[]}},
    {{"id":"close","beats":[]}}
  ]
}}
"""

SECTION_IDS = ["open", "build", "turn", "close"]

# Invented horror must never claim to be real: it is deceptive, and it is the
# fabricated-content pattern that costs channels their monetisation.
_FICTION_BANNED = [
    (re.compile(r"based on (?:a )?true (?:story|events)", re.I), "truth claim"),
    (re.compile(r"this (?:really|actually) happened", re.I), "truth claim"),
    (re.compile(r"a true story", re.I), "truth claim"),
    (re.compile(r"real(?:-| )life account", re.I), "truth claim"),
    (re.compile(r"true scary stor(?:y|ies)", re.I), "truth claim"),
    (re.compile(r"documented case", re.I), "truth claim"),
]
ALLOWED_MOVES = {"zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"}


def build_prompt(topic: dict, channel: dict) -> str:
    sc = channel["script"]
    w_lo, w_hi = sc["target_words"]
    dur_lo, dur_hi = sc["duration_range"]
    beat_lo, beat_hi = sc.get("beats", [28, 45])
    mode = sc.get("mode", "factual")
    return PROMPT.format(
        rules=FICTION_RULES if mode == "fiction" else FACTUAL_RULES,
        channel_name=channel["name"],
        topic_title=topic["title"],
        topic_id=topic["id"],
        tone=sc["tone"],
        audience=sc["audience"],
        dur_lo=dur_lo, dur_hi=dur_hi,
        w_lo=w_lo, w_hi=w_hi,
        beat_lo=beat_lo, beat_hi=beat_hi,
        visual_contract=VISUAL_CONTRACT,
    )


def validate_script(data: object, channel: dict) -> None:
    if not isinstance(data, dict):
        raise TypeError("top level must be a JSON object")
    for key in ("title", "description", "tags", "sections"):
        if key not in data:
            raise KeyError(f"missing key: {key}")
    thumb = data.get("thumb")
    if thumb is not None and len(str(thumb).split()) > 6:
        raise ValueError(f"thumb is {len(str(thumb).split())} words; max 6")
    if len(data["title"]) > 70:
        raise ValueError(f"title is {len(data['title'])} chars, max 70")
    if not isinstance(data["tags"], list) or not 8 <= len(data["tags"]) <= 16:
        raise ValueError("tags must be a list of 8-16 items")

    ids = [s.get("id") for s in data["sections"]]
    if ids != SECTION_IDS:
        raise ValueError(f"sections must be exactly {SECTION_IDS}, got {ids}")

    beats = [b for s in data["sections"] for b in s.get("beats", [])]
    beat_lo, beat_hi = channel["script"].get("beats", [28, 45])
    if not beat_lo - 6 <= len(beats) <= beat_hi + 6:
        raise ValueError(f"{len(beats)} beats; need {beat_lo}-{beat_hi}")

    for i, beat in enumerate(beats):
        if not beat.get("vo", "").strip():
            raise ValueError(f"beat {i} has empty vo")
        if not beat.get("image_prompt", "").strip():
            raise ValueError(f"beat {i} has no image_prompt")
        move = beat.get("move")
        if move and move not in ALLOWED_MOVES:
            raise ValueError(f"beat {i}: unknown move {move!r}")

    w_lo, w_hi = channel["script"]["target_words"]
    spoken = " ".join(b["vo"] for b in beats)
    words = len(re.findall(r"\b[\w']+\b", spoken))
    if not w_lo - 120 <= words <= w_hi + 160:
        raise ValueError(f"script is {words} words; need {w_lo}-{w_hi}")
    check_duration(words, len(beats), channel)

    # Enforced rather than merely requested: a model told "this is fiction"
    # will still reach for "based on true events" because it reads as a stronger
    # hook.
    if channel["script"].get("mode") == "fiction":
        blob = f"{spoken} {data.get('description', '')} {data.get('title', '')}"
        for rx, why in _FICTION_BANNED:
            match = rx.search(blob)
            if match:
                raise ValueError(f"fiction presented as fact: {match.group(0)!r} ({why})")


def _zoompan(move: str, total_frames: int, width: int, height: int, fps: int) -> str:
    """Build the zoompan expression for one camera move.

    zoompan works on the already-upscaled source, so `iw`/`ih` here are large
    and the crop window stays well inside real pixel detail.
    """
    n = max(total_frames, 2)
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if move == "zoom_in":
        z = f"min(1.0+({ZOOM_MAX - 1.0:.4f}/{n})*on,{ZOOM_MAX})"
        x, y = centre_x, centre_y
    elif move == "zoom_out":
        z = f"max({ZOOM_MAX}-({ZOOM_MAX - 1.0:.4f}/{n})*on,1.0)"
        x, y = centre_x, centre_y
    else:
        z = f"{PAN_ZOOM}"
        span_x = f"(iw-iw/zoom)"
        span_y = f"(ih-ih/zoom)"
        if move == "pan_right":
            x, y = f"{span_x}*on/{n}", centre_y
        elif move == "pan_left":
            x, y = f"{span_x}*(1-on/{n})", centre_y
        elif move == "pan_down":
            x, y = centre_x, f"{span_y}*on/{n}"
        elif move == "pan_up":
            x, y = centre_x, f"{span_y}*(1-on/{n})"
        else:
            x, y = centre_x, centre_y

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"
    )


def render_beat(beat: dict, duration: float, out_path: Path, th, index: int = 0) -> Path:
    """Generate the image for a beat and render its camera move to mp4."""
    key_src = json.dumps(
        {"p": beat.get("image_prompt"), "m": beat.get("move"),
         "d": round(duration, 3), "s": [th.width, th.height]}, sort_keys=True
    )
    import hashlib

    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:16]
    sidecar = out_path.with_suffix(".key")
    if out_path.exists() and sidecar.exists() and sidecar.read_text(encoding="utf-8") == key:
        return out_path

    style = getattr(th, "image_style", "") or ""
    result = imagegen.generate(
        beat["image_prompt"],
        size=imagegen.SIZE_16_9 if th.width >= th.height else imagegen.SIZE_9_16,
        style=style,
    )
    big = imagegen.upscale(result.path, factor=3)

    move = beat.get("move") or MOVES[index % len(MOVES)]
    frames = int(duration * th.fps)
    vf = _zoompan(move, frames, th.width, th.height, th.fps) + ",format=yuv420p"

    subprocess.run(
        [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(big),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(th.fps),
            "-c:v", pick_encoder("libx264"), "-preset", "medium", "-crf", "19",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    sidecar.write_text(key, encoding="utf-8")
    return out_path
