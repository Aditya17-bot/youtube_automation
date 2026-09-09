"""Grow a topic bank so a channel cannot run out of things to say.

At one video a day a hand-written bank of sixty lasts two months. At three a
day it lasts twenty days, and an empty bank is a silent failure: nothing
crashes, the channel simply stops posting. So the bank tops itself up.

Two rules make generated topics safe to publish unattended.

1. Duplicates are rejected here, not by the model. The prompt is shown what
   already exists, and every returned id and title is then checked against the
   bank anyway - both exactly and on a normalised word set, which catches "the
   lighthouse keeper who..." arriving twice under different ids. Repetition is
   what YouTube's inauthentic-content policy looks for, so this is a safety
   check and not a tidiness one.

2. New entries are APPENDED as text, never round-tripped through yaml.dump.
   Every bank file is mostly comments explaining what belongs in it, and
   safe_dump would silently delete all of them.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import yaml

from core import llm
from core.config import PATHS
from core.script import load_topics

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WORD_RE = re.compile(r"[a-z0-9']+")
STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "is", "it", "its", "of",
    "on", "one", "or", "that", "the", "their", "to", "with", "who", "whose",
}

MAX_BATCH = 25          # more than this in one reply and quality falls off


def bank_path(bank: str) -> Path:
    return PATHS.channels / f"topics_{bank}.yaml"


def _bank_doc(bank: str) -> dict:
    path = bank_path(bank)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def brief(bank: str) -> str:
    """The bank's own description of what belongs in it.

    Lives in the bank file as a top-level `brief:` so the rules a human wrote
    for a bank travel with it, instead of being retyped into a prompt.
    """
    return str(_bank_doc(bank).get("brief") or "").strip()


def fingerprint(title: str) -> frozenset[str]:
    """Content words of a title, for near-duplicate detection.

    Ids are easy to keep unique and prove nothing: `lighthouse-keeper` and
    `keeper-of-the-lighthouse` are two ids for one video. Comparing the
    meaningful words catches that.
    """
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return frozenset(w for w in _WORD_RE.findall(folded.lower()) if w not in STOPWORDS)


def _too_similar(fp: frozenset[str], seen: list[frozenset[str]]) -> bool:
    """True when a title shares most of its content words with an existing one."""
    if not fp:
        return True
    for other in seen:
        if not other:
            continue
        overlap = len(fp & other) / min(len(fp), len(other))
        if overlap >= 0.7:
            return True
    return False


def _prompt(bank: str, count: int, existing: list[dict], wants_visual: bool) -> str:
    titles = "\n".join(f"- {t['title']}" for t in existing)
    visual_rule = ""
    if wants_visual:
        kinds = sorted({t["visual"] for t in existing if t.get("visual")})
        visual_rule = (
            f'\n- also a "visual" field, exactly one of: {", ".join(kinds)}'
        )
    return f"""Extend a YouTube Shorts topic bank with {count} new topics.

WHAT THIS BANK IS FOR
{brief(bank) or "See the existing titles below and match their subject and register."}

TOPICS THAT ALREADY EXIST - every one of these is used up. A new topic must not
repeat one of them, rephrase one, or be the same situation somewhere else:
{titles}

RULES
- {count} topics, all genuinely distinct from each other and from the list above
- "id": lowercase kebab-case, 2-4 words, unique
- "title": one line, concrete and specific, matching the style of the list{visual_rule}
- no real people, real places, real brands, or real events
- no numbering, no "part 2", nothing that depends on another video

Return ONLY a JSON array:
[{{"id": "some-id", "title": "Some title"{', "visual": "stat"' if wants_visual else ''}}}]
"""


def _validate(data: object) -> None:
    if not isinstance(data, list) or not data:
        raise ValueError("expected a non-empty JSON array of topics")
    for row in data:
        if not isinstance(row, dict):
            raise ValueError(f"expected objects, got {type(row).__name__}")
        if not ID_RE.match(str(row.get("id", ""))):
            raise ValueError(f"id {row.get('id')!r} is not lowercase kebab-case")
        if not str(row.get("title", "")).strip():
            raise ValueError(f"topic {row.get('id')!r} has no title")


def _format_row(row: dict, wants_visual: bool) -> str:
    title = str(row["title"]).strip().replace('"', "'")
    line = f'  - {{id: {row["id"]}, title: "{title}"'
    if wants_visual and row.get("visual"):
        line += f', visual: {row["visual"]}'
    return line + "}"


def extend(bank: str, count: int, *, attempts: int = 2) -> list[dict]:
    """Add up to `count` new topics to a bank file. Returns what was added.

    Asks for extra and keeps whatever survives deduplication rather than
    looping until the exact number is reached - the bank is topped up well
    before it is empty, so coming back with eight of ten is fine and cheaper
    than arguing with the model about the other two.
    """
    path = bank_path(bank)
    if not path.exists():
        raise FileNotFoundError(f"no topic bank at {path}")

    existing = load_topics(bank)
    wants_visual = bool(existing) and all(t.get("visual") for t in existing)
    ids = {t["id"] for t in existing}
    prints = [fingerprint(t["title"]) for t in existing]

    added: list[dict] = []
    for _ in range(attempts):
        want = min(MAX_BATCH, (count - len(added)) + 4)
        rows = llm.ask_json(_prompt(bank, want, existing + added, wants_visual),
                            validate=_validate)
        for row in rows:
            if len(added) >= count:
                break
            fp = fingerprint(row["title"])
            if row["id"] in ids or _too_similar(fp, prints):
                continue
            ids.add(row["id"])
            prints.append(fp)
            added.append({k: row[k] for k in ("id", "title", "visual") if k in row})
        if len(added) >= count:
            break

    if added:
        lines = [_format_row(row, wants_visual) for row in added]
        text = path.read_text(encoding="utf-8").rstrip("\n")
        path.write_text(text + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return added


def top_up(channel: str, *, days: int = 14, batch: int = 30) -> list[dict]:
    """Extend a channel's bank if it holds less than `days` of runway.

    Called from the daily run, where the alternative to topping up early is
    finding out at 3am that the bank emptied and no video was made.
    """
    from core import ideate, schedule

    per_day = schedule.per_day(channel)
    if per_day <= 0:
        return []
    left = ideate.stats(channel)["remaining"]
    if left > per_day * days:
        return []
    return extend(ideate.topic_bank_for(channel), batch)


if __name__ == "__main__":
    from core import ideate, schedule

    ap = argparse.ArgumentParser(description="extend a topic bank with new topics")
    ap.add_argument("--channel", help="top up this channel's bank if it is low")
    ap.add_argument("--bank", help="extend this bank unconditionally, e.g. story")
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--days", type=int, default=14,
                    help="--channel only: runway below which to top up")
    args = ap.parse_args()

    if args.bank:
        new = extend(args.bank, args.count)
    elif args.channel:
        new = top_up(args.channel, days=args.days, batch=args.count)
    else:
        ap.error("give --bank or --channel")

    for row in new:
        print(f"+ {row['id']:24} {row['title']}")
    print(f"\n{len(new)} added")
    if args.channel:
        left = ideate.stats(args.channel)["remaining"]
        rate = schedule.per_day(args.channel) or 1
        print(f"{args.channel}: {left} topics left, {left / rate:.0f} days at {rate}/day")
