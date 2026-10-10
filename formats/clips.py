"""Clip format: real footage, our story over it.

9:16, 40-55s, one licensed film shot per beat. This exists because the
generated-image format did not work - a still with a slow zoom on it is a
slideshow, the feed wants a visual change every 1.5-2 seconds, and a channel
made of slideshows settled under 100 views a video however good the writing was.

The plugin contract is unchanged: `render_beat` has to return a silent mp4 of
exactly `duration`. A cut of film satisfies that as well as a Ken Burns still
does, so everything downstream - narration, captions, music, thumbnail,
packaging, review, upload, cadence - is the same code the other channels run.

Two things make the video ours rather than a reupload, and both are enforced
here rather than hoped for:

  - the writing never describes what is on screen. It is first-person and
    interior, which is also why it survives a shot that does not match: the
    viewer resolves the mismatch as meaning. A script that narrates the picture
    is a commentary track, and the moment one line is wrong the whole thing
    reads as stock footage with a voice over it.
  - the hook is a separate, checked field. Beat 0 is one or two short
    sentences, held on screen as a card for exactly as long as it is spoken,
    and it is the only thing standing between the video and a swipe.

Footage is chosen in `build_prompt` and bound to beats in `validate_script`.
See `_PLANNED` for why it is handed over that way.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from core import emotion, footage
from core.config import ffmpeg_bin, pick_encoder
from core.script import check_duration

# Same vocabulary as formats/story.py, so core.package labels the chapters
# without needing to know which format wrote the script.
SECTION_IDS = ["open", "build", "turn", "close"]

# The hook card is held for exactly as long as beat 0 is spoken (core.assemble
# measures it), so the only limit here is how much text is readable at a glance
# on one card. Shorts are decided inside the first two seconds and nothing else
# in the pipeline gets a second chance at that.
HOOK_MAX_WORDS = 12

# Seconds skipped after each cut. Scene detection fires on the first frame of
# the new shot, which on old prints is often still half-dissolved.
HEAD_TRIM = 0.15

# A shot shorter than its beat is slowed rather than dropped - on this footage
# it reads as deliberate. Past about 1.6x the motion starts to strobe, and the
# remainder is covered by holding the last frame instead.
MAX_SLOW = 1.6

FICTION_RULES = """RULES:
  - This is ORIGINAL FICTION written to existing footage. Invent the events.
  - Never frame it as a true story, a real account, found footage, a leaked
    tape, or something that happened to a real person. No "based on true
    events", no real towns tied to invented deaths.
  - Dread comes from atmosphere, restraint and implication - not from gore.
    No graphic injury, torture, mutilation or body horror.
  - No sexual content. No depiction of self-harm or suicide method.
  - No children as victims.
  - Leave the final image unresolved rather than explaining the horror away.
"""

FOOTAGE_CONTRACT = """THE PICTURES ARE ALREADY CHOSEN. You are writing to them.

They are {count} consecutive shots from {title} - {era}. One shot per beat, in
the order given. You cannot change them and you are not describing them.

  - Write FIRST PERSON, present or recent past. The narrator is inside this,
    or remembering it badly.
  - NEVER describe what is on screen. Do not write "the man in the doorway",
    "she turns", "the camera moves". The viewer can see the picture. Your job
    is the part the picture does not show: what it meant, what came before,
    what is about to happen, what the narrator knows and the viewer does not.
  - Nothing modern. No phones, cars, brands, dates, internet, electricity as a
    subject. The footage is old and one wrong noun breaks it.
  - Do not name the film, the actors, or anything about the footage itself.
  - Sound is ours alone: never refer to music, to a voice on the tape, or to
    anything being heard on the recording.
"""

PROMPT = """You are the writer for a faceless horror Shorts channel
called "{channel_name}".

Write a {dur_lo}-{dur_hi} second first-person horror piece: {topic_title}

TONE: {tone}
AUDIENCE: {audience}

LENGTH: the combined `vo` text must total {w_lo}-{w_hi} words. Count them.

{rules}
{footage_contract}
THE HOOK. Beat 0 is the hook and nothing else. One or two short sentences,
{hook_max} words in total or fewer, and it must work with no context at all - a
threat, a contradiction, or a fact that demands the next line. It is held on
screen as a card while it is spoken, so it is also the only text a muted viewer
reads before deciding. "It was a cold night" is a swipe. "They pay me to sleep.
I have not slept once." is not. Do not open with a question.

Then {beat_lo}-{beat_hi} beats total, one to three spoken sentences each. Build,
turn, and end on an image rather than an explanation. No "subscribe", no
"welcome back", no channel branding anywhere in the `vo`.

Return ONLY this JSON:
{{
  "topic_id": "{topic_id}",
  "title": "YouTube title, <=70 chars, curiosity-driven, no emoji",
  "hook": "beat 0's line, word for word",
  "thumb": "2-4 words for the thumbnail. A complete phrase, ominous, never a truncated sentence.",
  "description": "3-5 sentences for the YouTube description, plain text",
  "tags": ["10-14 lowercase youtube tags"],
  "sections": [
    {{"id":"open","beats":[{{"vo":"..."}}]}},
    {{"id":"build","beats":[{{"vo":"..."}}]}},
    {{"id":"turn","beats":[]}},
    {{"id":"close","beats":[]}}
  ]
}}
"""

_FICTION_BANNED = [
    (re.compile(r"based on (?:a )?true (?:story|events)", re.I), "truth claim"),
    (re.compile(r"this (?:really|actually) happened", re.I), "truth claim"),
    (re.compile(r"a true story", re.I), "truth claim"),
    (re.compile(r"real(?:-| )life account", re.I), "truth claim"),
    (re.compile(r"documented case", re.I), "truth claim"),
    (re.compile(r"(?:leaked|recovered|found) (?:tape|footage|recording)", re.I),
     "presents the footage as evidence"),
]

# Words that give away that the narration is describing the screen rather than
# the story. Each one has cost a take: "you can see" turns a horror Short into
# a commentary track, and the moment the picture disagrees the video is over.
_SCREEN_TALK = [
    (re.compile(r"\byou can see\b", re.I), "describes the picture"),
    (re.compile(r"\bin th(?:is|e) (?:shot|frame|clip|footage|video)\b", re.I),
     "refers to the footage"),
    (re.compile(r"\bthe camera\b", re.I), "refers to the camera"),
    (re.compile(r"\bwatch what happens\b", re.I), "commentary voice"),
    (re.compile(r"\bthis (?:clip|footage|recording|tape)\b", re.I),
     "refers to the footage"),
]


# Footage is picked once, in build_prompt, and has to reach validate_script to
# be bound to beats. The plugin contract passes no state between the two, and
# the alternative - recomputing the choice from the topic id echoed back in the
# model's reply - trusts the model to echo an id correctly, which would silently
# swap the footage rather than fail. So the plan is parked here, keyed by topic,
# and read back on the way through. The pipeline renders one video at a time
# (daily.py loops channels sequentially), so there is never more than one or two
# entries alive.
_PLANNED: dict[str, list[dict]] = {}


def footage_bank(channel: dict) -> str:
    return (channel.get("footage") or {}).get("bank", "horror")


def _beat_target(channel: dict) -> int:
    """Beats to reserve footage for: the top of the configured range."""
    return int(channel["script"].get("beats", [6, 8])[1])


def build_prompt(topic: dict, channel: dict) -> str:
    sc = channel["script"]
    w_lo, w_hi = sc["target_words"]
    dur_lo, dur_hi = sc["duration_range"]
    beat_lo, beat_hi = sc.get("beats", [6, 8])
    bank = footage_bank(channel)

    shots = footage.plan(bank, _beat_target(channel), seed=topic["id"])
    _PLANNED[topic["id"]] = shots

    index = footage.load_index(bank)["sources"][shots[0]["source"]]
    return PROMPT.format(
        channel_name=channel["name"],
        topic_title=topic["title"],
        topic_id=topic["id"],
        tone=sc["tone"],
        audience=sc["audience"],
        dur_lo=dur_lo, dur_hi=dur_hi,
        w_lo=w_lo, w_hi=w_hi,
        beat_lo=beat_lo, beat_hi=beat_hi,
        hook_max=HOOK_MAX_WORDS,
        rules=FICTION_RULES,
        footage_contract=FOOTAGE_CONTRACT.format(
            count=len(shots),
            title=index.get("title", shots[0]["source"]),
            era=(channel.get("footage") or {}).get(
                "era", "old black and white film, no dialogue of its own"),
        ),
    )


def _planned_for(data: dict) -> tuple[str, list[dict]]:
    """The footage reserved for this script, and the slug it is claimed under."""
    topic_id = str(data.get("topic_id") or "")
    if topic_id in _PLANNED:
        return topic_id, _PLANNED[topic_id]
    if len(_PLANNED) == 1:
        # The model dropped or mangled the echoed id. The reservation is still
        # unambiguous, so use it rather than failing a whole generation over a
        # field nothing downstream reads.
        return next(iter(_PLANNED.items()))
    raise RuntimeError(
        "no footage was reserved for this script; validate_script was called "
        "without build_prompt having run first"
    )


def validate_script(data: object, channel: dict) -> None:
    if not isinstance(data, dict):
        raise TypeError("top level must be a JSON object")
    for key in ("title", "hook", "description", "tags", "sections"):
        if key not in data:
            raise KeyError(f"missing key: {key}")
    if len(data["title"]) > 70:
        raise ValueError(f"title is {len(data['title'])} chars, max 70")
    thumb = data.get("thumb")
    if thumb is not None and len(str(thumb).split()) > 6:
        raise ValueError(f"thumb is {len(str(thumb).split())} words; max 6")
    if not isinstance(data["tags"], list) or not 8 <= len(data["tags"]) <= 16:
        raise ValueError("tags must be a list of 8-16 items")

    ids = [s.get("id") for s in data["sections"]]
    if ids != SECTION_IDS:
        raise ValueError(f"sections must be exactly {SECTION_IDS}, got {ids}")

    beats = [b for s in data["sections"] for b in s.get("beats", [])]
    beat_lo, beat_hi = channel["script"].get("beats", [6, 8])
    if not beat_lo <= len(beats) <= beat_hi:
        raise ValueError(f"{len(beats)} beats; need {beat_lo}-{beat_hi}")

    for i, beat in enumerate(beats):
        if not emotion.strip(beat.get("vo", "")):
            raise ValueError(f"beat {i} has empty vo")

    _validate_hook(data, beats)

    spoken = emotion.strip(" ".join(b["vo"] for b in beats))

    # Content before length, deliberately. Both are retried the same way, but a
    # script that breaks a rule has to be rewritten whatever its word count, and
    # telling the model to trim first would send it back for the wrong reason.
    blob = f"{spoken} {data.get('description', '')} {data.get('title', '')}"
    for rx, why in _FICTION_BANNED:
        match = rx.search(blob)
        if match:
            raise ValueError(f"fiction presented as fact: {match.group(0)!r} ({why})")
    for rx, why in _SCREEN_TALK:
        match = rx.search(spoken)
        if match:
            raise ValueError(
                f"narration talks about the footage: {match.group(0)!r} ({why}). "
                "Write what the picture does not show, not what it does."
            )

    words = len(re.findall(r"\b[\w']+\b", spoken))
    w_lo, w_hi = channel["script"]["target_words"]
    if not w_lo - 20 <= words <= w_hi + 20:
        raise ValueError(f"script is {words} words; need {w_lo}-{w_hi}")
    check_duration(words, len(beats), channel)

    # Last, so a rejected draft never spends footage.
    _bind_shots(data, beats, channel)


def _validate_hook(data: dict, beats: list[dict]) -> None:
    """Beat 0 is the hook, and the hook is the video's only real decision point."""
    spoken = emotion.strip(beats[0].get("vo", "")).strip()
    n = len(re.findall(r"\b[\w']+\b", spoken))
    if n > HOOK_MAX_WORDS:
        raise ValueError(
            f"beat 0 is {n} words; the hook card takes {HOOK_MAX_WORDS} and has "
            "to be readable at a glance. Cut it to one or two short sentences."
        )
    # Two short sentences are allowed, and are often the better hook: "They pay
    # me to sleep. I have not slept once." lands harder than either half. Three
    # is a paragraph, and a paragraph on a card is not read.
    sentences = len(re.findall(r"[.!?]+", spoken.rstrip(".!?"))) + 1
    if sentences > 2:
        raise ValueError(
            f"beat 0 runs to {sentences} sentences; the hook card takes one or "
            f"two. Got {spoken!r}"
        )
    if spoken.endswith("?"):
        raise ValueError("the hook must not be a question; make a claim instead")
    # Compared on words alone: the two fields differing by a full stop is not
    # worth spending a retry on, but differing in wording means the card on
    # screen and the line being spoken are two different sentences.
    def _words(text: str) -> list[str]:
        return re.findall(r"[\w']+", emotion.strip(str(text)).lower())

    if _words(data["hook"]) != _words(spoken):
        raise ValueError("`hook` must repeat beat 0's vo word for word")


def _bind_shots(data: dict, beats: list[dict], channel: dict) -> None:
    """Attach one shot to each beat and spend them.

    Written into the script, so a re-render of the same script.json cuts exactly
    the same footage. `python -m core.footage release --slug <topic>` hands the
    shots back if the video is thrown away.
    """
    slug, shots = _planned_for(data)
    bank = footage_bank(channel)
    if len(shots) < len(beats):
        raise footage.NoFootage(
            f"reserved {len(shots)} shots but the script has {len(beats)} beats"
        )
    for beat, shot in zip(beats, shots):
        beat["shot"] = {
            "id": shot["id"], "source": shot["source"],
            "start": shot["start"], "end": shot["end"],
            # Carried into the script so the renderer can grade a night shot
            # differently without reopening the index.
            "mean_y": shot.get("mean_y"),
        }
    footage.claim(bank, shots[:len(beats)], slug)
    _PLANNED.pop(slug, None)


def description_note(script: dict, channel: dict) -> str:
    """Credit the films this video was cut from, and the licence that allows it.

    Public domain owes no attribution and CC BY does, so the block has to exist
    for the CC sources anyway. Printing it for every source is the better habit:
    it tells a viewer, and anyone reviewing the channel, exactly where the
    picture came from and why it was allowed - which is the question a reused-
    content report asks, and the one an empty description cannot answer.
    """
    used, seen = [], set()
    for section in script.get("sections", []):
        for beat in section.get("beats", []):
            source = (beat.get("shot") or {}).get("source")
            if source and source not in seen:
                seen.add(source)
                used.append(source)
    if not used:
        return ""

    index = footage.load_index(footage_bank(channel)).get("sources", {})
    lines = ["FOOTAGE"]
    for source in used:
        entry = index.get(source, {})
        title = entry.get("title", source)
        licence = LICENCE_NAMES.get(entry.get("license"), entry.get("license", "licensed"))
        proof = entry.get("license_proof")
        lines.append(f"{title} - {licence}" + (f"\n{proof}" if proof else ""))
    lines.append("Story, narration, editing and artwork are original.")
    return "\n".join(lines)


LICENCE_NAMES = {
    "public-domain": "public domain",
    "cc0": "CC0 / public domain",
    "cc-by": "CC BY",
    "cc-by-sa": "CC BY-SA",
}


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

# Where the picture sits in the frame under the `tall` layout, and how much of
# the height it takes. The band left underneath is where the captions land, so
# they sit on blur rather than over a face.
TALL_TOP = 60
TALL_RATIO = 0.75            # 3:4 - a modest crop of a 4:3 frame, not a 9:16 one


def _framing(layout: str, width: int, height: int) -> str:
    """Fit a 4:3 or 16:9 source into 9:16.

    tall  -- the default, and the one that suits this footage. A 4:3 frame
             padded to 9:16 is 42% picture and 58% blurred bar, which looks
             like a video of a video. Cropping to 3:4 instead keeps a little
             over half the width - enough to lose the empty edges of a period
             composition without losing the face - and fills three quarters of
             the height with real picture.
    crop  -- a full 9:16 cut. Fills the screen, but throws away two thirds of a
             4:3 frame's width, which usually means throwing away the subject.
    pad   -- the whole frame over a blurred bed. Nothing is ever cut off; use
             it for a source whose edges matter.
    """
    bed = (
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=luma_radius=48:luma_power=2,"
        f"eq=brightness=-0.12:saturation=0.2[bgb];"
    )
    if layout == "crop":
        return (f"crop=ih*{width}/{height}:ih:(iw-ih*{width}/{height})/2:0,"
                f"scale={width}:{height}:flags=lanczos,setsar=1")
    if layout == "pad":
        return (f"split=2[bg][fg];{bed}"
                f"[fg]scale={width}:-2:flags=lanczos[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    return (
        f"split=2[bg][fg];{bed}"
        # min() so a source narrower than 3:4 is letterboxed rather than
        # stretched: the crop must never ask for more width than exists.
        f"[fg]crop=min(iw\\,ih*{TALL_RATIO}):ih:(iw-min(iw\\,ih*{TALL_RATIO}))/2:0,"
        f"scale={width}:-2:flags=lanczos[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:{TALL_TOP},setsar=1"
    )


def _punch(fps: int) -> str:
    """A push-in that settles, then a framing step every two seconds.

    A held frame for fifty seconds is the thing that killed the last format.
    Steps are discrete on purpose: a continuously interpolated zoom shimmers on
    film grain, and this footage is nothing but film grain.
    """
    settle = max(1, int(0.8 * fps))
    step = max(1, 2 * fps)
    z = (f"if(lt(on,{settle}),1.24-0.24*on/{settle},"
         f"1.00+0.06*mod(floor((on-{settle})/{step}),2))")
    return (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={{w}}x{{h}}:fps={fps}")


# The channel look, applied after the punch-in so the grain is added at output
# scale rather than magnified with the picture.
#
# Two versions, chosen per shot from the mean luma the index already recorded.
# One grade for everything crushed the night exteriors to a black rectangle:
# they arrive at a mean of 37-50 where a daylight shot is 120+, and a fixed
# -0.035 brightness pull with a raised black point takes the rest. The dark
# variant keeps the contrast and the tint, lifts the gamma slightly instead of
# dropping the brightness, and barely touches the black point.
DARK_BELOW = 60          # mean luma under this and the shot is already night

GRADE_NORMAL = (
    "eq=saturation=0.22:contrast=1.16:brightness=-0.035,"
    "colorlevels=rimin=0.055:gimin=0.055:bimin=0.045,"
    "colorbalance=rs=-0.05:bs=0.09:bm=0.03,"
    "vignette=PI/4.2,"
    "noise=alls=9:allf=t+u"
)
GRADE_DARK = (
    "eq=saturation=0.22:contrast=1.10:gamma=1.12,"
    "colorlevels=rimin=0.012:gimin=0.012:bimin=0.008,"
    "colorbalance=rs=-0.05:bs=0.09:bm=0.03,"
    "vignette=PI/5.5,"
    "noise=alls=9:allf=t+u"
)


def grade_for(shot: dict) -> str:
    """The grade this shot can take. Unknown brightness gets the normal one."""
    mean_y = (shot or {}).get("mean_y")
    return GRADE_DARK if mean_y is not None and mean_y < DARK_BELOW else GRADE_NORMAL


def _filter_chain(beat: dict, duration: float, th, stretch: float,
                  layout: str) -> str:
    parts = []
    if stretch > 1.001:
        parts.append(f"setpts=PTS*{stretch:.4f}")
    parts.append(f"fps={th.fps}")
    parts.append(_framing(layout, th.width, th.height))
    # Hold the last frame if slowing did not cover the beat. tpad is cheaper
    # and steadier than looping, and a frozen frame under narration reads as
    # held breath rather than as a stall.
    parts.append(f"tpad=stop_mode=clone:stop_duration={duration:.3f}")
    parts.append(_punch(th.fps).format(w=th.width, h=th.height))
    parts.append(grade_for(beat.get("shot")))
    parts.append("format=yuv420p")
    return ",".join(parts)


def render_beat(beat: dict, duration: float, out_path: Path, th, index: int = 0) -> Path:
    """Cut this beat's shot, reframe it to 9:16, grade it, and time it exactly."""
    shot = beat.get("shot")
    if not shot:
        raise ValueError(
            f"beat {index} has no shot. Scripts for this format get their "
            "footage bound in validate_script; regenerate rather than "
            "hand-editing script.json."
        )

    src = footage.source_path(shot["source"])
    if not src.exists():
        raise FileNotFoundError(
            f"footage missing: {src}. "
            f"python -m core.footage fetch --source {shot['source']}"
        )

    layout = getattr(th, "layout", "pad")
    start = shot["start"] + HEAD_TRIM
    available = max(0.4, shot["end"] - start)
    stretch = 1.0 if available >= duration else min(MAX_SLOW, duration / available)
    take = min(available, duration / stretch)

    key = hashlib.sha256(json.dumps(
        {"s": shot, "d": round(duration, 3), "z": [th.width, th.height, th.fps],
         "l": layout, "v": 2}, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    sidecar = out_path.with_suffix(".key")
    if out_path.exists() and sidecar.exists() and sidecar.read_text(encoding="utf-8") == key:
        return out_path

    subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(src),
         "-an", "-sn",
         "-vf", _filter_chain(beat, duration, th, stretch, layout),
         # -t on the OUTPUT, after tpad: the clip has to be exactly the length
         # of its narration or the concat drifts out of sync with the voice.
         "-t", f"{duration:.3f}",
         "-r", str(th.fps),
         "-c:v", pick_encoder("libx264"), "-preset", "medium", "-crf", "19",
         str(out_path)],
        check=True, capture_output=True,
    )
    sidecar.write_text(key, encoding="utf-8")
    return out_path
