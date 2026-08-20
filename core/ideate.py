"""Topic selection with history, so the channel does not repeat itself.

Repetition is not just dull - templated output with minor substitutions is
exactly the pattern YouTube's inauthentic-content policy targets, so tracking
what has already been made is a safety measure as much as an editorial one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.config import PATHS, load_channel
from core.script import load_topics


def _history_path(channel: str) -> Path:
    PATHS.state.mkdir(parents=True, exist_ok=True)
    return PATHS.state / f"history_{channel}.json"


def load_history(channel: str) -> list[dict]:
    path = _history_path(channel)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def record(channel: str, topic_id: str, title: str) -> None:
    history = load_history(channel)
    history.append({
        "topic_id": topic_id,
        "title": title,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _history_path(channel).write_text(json.dumps(history, indent=2), encoding="utf-8")


def topic_bank_for(channel: str) -> str:
    """Which topic bank a channel reads.

    Short-form channels set `script.topics_from` to their long-form parent, so
    both cover the same subject. History stays per channel, so a topic used in
    a long video is still available for a Short.
    """
    try:
        return load_channel(channel)["script"].get("topics_from") or channel
    except Exception:  # noqa: BLE001 - a missing config is the caller's problem
        return channel


def next_topic(channel: str, explicit: str | None = None) -> dict:
    topics = load_topics(topic_bank_for(channel))
    if not topics:
        raise RuntimeError(f"no topic bank for channel {channel!r}")

    if explicit:
        found = next((t for t in topics if t["id"] == explicit), None)
        if not found:
            raise KeyError(f"unknown topic id {explicit!r}")
        return found

    used = {h["topic_id"] for h in load_history(channel)}
    remaining = [t for t in topics if t["id"] not in used]
    if not remaining:
        raise RuntimeError(
            f"every topic in the {channel} bank has been used "
            f"({len(topics)} total). Add more before generating again."
        )
    return remaining[0]


def backfill(channel: str) -> list[str]:
    """Rebuild topic history from the review queue.

    History lives in state/, the videos live in out/review, and only the second
    of those is treated as durable anywhere else in the pipeline. When state/ is
    lost or was never written, next_topic() hands back the first topic in the
    bank every single run and the channel republishes what it already posted -
    the exact repetition this module exists to stop. Idempotent, so it is safe
    to run whenever.
    """
    from core.review import list_pending

    bank = {t["id"] for t in load_topics(topic_bank_for(channel))}
    used = {h["topic_id"] for h in load_history(channel)}
    added = []
    for row in list_pending(channel):
        slug = row.get("slug")
        if slug not in bank or slug in used:
            continue
        meta = Path(row["_dir"]) / "metadata.json"
        title = slug
        if meta.exists():
            title = json.loads(meta.read_text(encoding="utf-8")).get("title", slug)
        record(channel, slug, title)
        used.add(slug)
        added.append(slug)
    return added


def stats(channel: str) -> dict:
    topics = load_topics(topic_bank_for(channel))
    used = {h["topic_id"] for h in load_history(channel)}
    return {"total": len(topics), "used": len(used), "remaining": len(topics) - len(used)}


if __name__ == "__main__":
    import argparse

    from core.config import PATHS

    ap = argparse.ArgumentParser(description="topic history")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_back = sub.add_parser("backfill", help="rebuild history from the review queue")
    p_back.add_argument("--channel", help="default: every channel config")

    p_stat = sub.add_parser("stats", help="how much of each topic bank is used")
    p_stat.add_argument("--channel")

    args = ap.parse_args()
    names = [args.channel] if args.channel else sorted(
        p.stem for p in PATHS.channels.glob("*.yaml") if not p.stem.startswith("topics_")
    )

    for name in names:
        try:
            if args.cmd == "backfill":
                added = backfill(name)
                print(f"{name:16} +{len(added)}" + (f"  {', '.join(added)}" if added else ""))
            else:
                s = stats(name)
                print(f"{name:16} {s['used']}/{s['total']} used, {s['remaining']} left")
        except Exception as exc:  # noqa: BLE001 - one bad config must not stop the rest
            print(f"{name:16} skipped: {exc}")
