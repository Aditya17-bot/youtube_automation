"""Single entry point: topic -> script -> video -> review queue.

    python pipeline.py --channel finance
    python pipeline.py --channel finance --topic rule-of-72
    python pipeline.py --channel finance --script-only

Nothing here uploads. Publishing is a separate, explicit step so that the
review gate cannot be skipped by accident.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from core import assemble, ideate, package, review
from core.config import PATHS, load_channel
from core.script import generate, iter_beats


def run(channel_name: str, topic_id: str | None = None, script_only: bool = False) -> Path | None:
    channel = load_channel(channel_name)
    started = time.time()

    topic = ideate.next_topic(channel_name, topic_id)
    counts = ideate.stats(channel_name)
    print(f"topic     : {topic['title']}")
    print(f"bank      : {counts['remaining']} of {counts['total']} topics unused")

    job = PATHS.job_dir(channel_name, topic["id"])

    print("script    : generating...")
    script = generate(topic, channel)
    (job / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    beats = list(iter_beats(script))
    print(f"            {script['title']}")
    print(f"            {script['_word_count']} words, {len(beats)} beats")

    if script_only:
        print(f"\nscript at {job / 'script.json'}")
        return None

    print("render    :")
    final = assemble.build(job / "script.json", channel_name)

    timings_path = job / "timings.json"
    timings = json.loads(timings_path.read_text(encoding="utf-8")) if timings_path.exists() else []

    print("package   :")
    meta = package.build(script, channel, job, timings)
    print(f"            thumbnail + {len(meta['tags'])} tags + {len(meta['chapters'])} chapters")

    dest = review.promote(job, channel_name, topic["id"])
    ideate.record(channel_name, topic["id"], script["title"])

    mins = (time.time() - started) / 60
    print(f"\ndone in {mins:.1f} min")
    print(f"review    : {dest}")
    print(f"watch     : {dest / 'final.mp4'}")
    print(f"\napprove with:  python -m core.review approve {topic['id']} --channel {channel_name}")
    return final


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="run one video end to end")
    ap.add_argument("--channel", default="finance")
    ap.add_argument("--topic", help="topic id; default is the next unused one")
    ap.add_argument("--script-only", action="store_true", help="stop after the script")
    args = ap.parse_args()

    run(args.channel, args.topic, args.script_only)
