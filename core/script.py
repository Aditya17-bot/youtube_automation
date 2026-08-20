"""Script generation, dispatched to the channel's format plugin.

This module owns everything generic: loading topic banks, walking beats,
counting words, and calling the model with retry. Everything format-specific -
the prompt, the visual contract, the validation rules - lives in
`formats/<format>.py`, which must expose:

    build_prompt(topic: dict, channel: dict) -> str
    validate_script(data: object, channel: dict) -> None   # raise to reject

Adding a channel type is therefore a new plugin, not a change here.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import yaml

from core.config import PATHS
from core.llm import ask_json

WORD_RE = re.compile(r"\b[\w']+\b")


def load_format(name: str):
    try:
        return importlib.import_module(f"formats.{name}")
    except ModuleNotFoundError as exc:
        raise ValueError(f"no format plugin for {name!r}") from exc


def load_topics(channel_slug: str = "finance") -> list[dict]:
    path = PATHS.channels / f"topics_{channel_slug}.yaml"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("topics", [])


def iter_beats(script: dict):
    for section in script.get("sections", []):
        for beat in section.get("beats", []):
            yield beat


def all_vo(script: dict) -> str:
    return " ".join(beat["vo"] for beat in iter_beats(script))


def word_count(script: dict) -> int:
    return len(WORD_RE.findall(all_vo(script)))


# Fallback if a channel does not state one. Deliberately on the slow side: an
# under-estimate lets a long script through, and the whole point is a ceiling.
DEFAULT_WPM = 145


def words_per_minute(channel: dict) -> float:
    """Measured delivery rate for this channel's voice, in words per minute.

    Not the rate the voice reads flowing prose at. A script is split into short
    beats and the model puts a sentence end on most of them, so real delivery
    runs about 15% slower than a paragraph does - 164 against 194 for Ava,
    measured on a finished Short. `python -m core.voice --measure` re-derives it
    if a voice or rate changes.
    """
    return float((channel.get("voice") or {}).get("words_per_minute") or DEFAULT_WPM)


def estimate_seconds(words: int, beats: int, channel: dict) -> float:
    """Predicted runtime of a finished video, padding included.

    Beat count matters as much as word count here: every beat carries a tail of
    silence, so 28 short beats add nearly 13 seconds that no word count sees.
    """
    from core.config import HEAD_PAD, OUTRO_PAD, TAIL_PAD

    spoken = words / words_per_minute(channel) * 60.0
    return spoken + beats * TAIL_PAD + HEAD_PAD + OUTRO_PAD


def check_duration(words: int, beats: int, channel: dict, tolerance: float = 1.08) -> None:
    """Reject a script whose predicted runtime overruns the channel's ceiling.

    The estimate is good to a few percent, not exact, so this is deliberately a
    coarse filter - it is here to catch a script that is half again too long
    while there is still a retry left to fix it. `core.assemble` measures the
    real narration afterwards and enforces the ceiling properly.

    Raising is what makes it work: ask_json feeds the message back to the model
    and asks again, so an overlong first draft self-corrects rather than
    becoming an eight-minute video nobody asked for.
    """
    cap = (channel.get("script") or {}).get("duration_range")
    if not cap:
        return
    limit = float(cap[1])
    got = estimate_seconds(words, beats, channel)
    if got > limit * tolerance:
        raise ValueError(
            f"script runs about {got / 60:.1f} min ({words} words over {beats} beats); "
            f"the ceiling is {limit / 60:.1f} min. Cut roughly "
            f"{int((got - limit) / 60 * words_per_minute(channel))} words."
        )


def generate(topic: dict, channel: dict) -> dict:
    fmt = load_format(channel["format"])
    prompt = fmt.build_prompt(topic, channel)

    script = ask_json(prompt, validate=lambda d: fmt.validate_script(d, channel))
    script["_topic"] = topic
    script["_word_count"] = word_count(script)
    return script


if __name__ == "__main__":
    import argparse

    from core.config import load_channel

    ap = argparse.ArgumentParser(description="generate one script")
    ap.add_argument("--channel", default="finance")
    ap.add_argument("--topic", help="topic id (default: first in bank)")
    ap.add_argument("--out", help="write JSON here")
    args = ap.parse_args()

    ch = load_channel(args.channel)
    topics = load_topics(args.channel)
    topic = topics[0]
    if args.topic:
        topic = next((t for t in topics if t["id"] == args.topic), topics[0])

    print(f"topic: {topic['title']}")
    result = generate(topic, ch)
    beats = list(iter_beats(result))
    print(f"title: {result['title']}")
    print(f"words: {result['_word_count']}   beats: {len(beats)}")

    dest = Path(args.out) if args.out else PATHS.job_dir(args.channel, topic["id"]) / "script.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {dest}")
