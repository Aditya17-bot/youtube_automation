"""Unattended daily run: render whatever today's cadence calls for, upload it private.

    python daily.py --dry-run     # what today would do, without doing it
    python daily.py               # the real thing
    python daily.py --force       # ignore cadence, run every channel
    python daily.py --channels finance_short

Uploads are forced `private` here regardless of channel config. The review gate
does not disappear when `auto_publish` is on, it moves: the video waits in
YouTube Studio instead of `out/review`, and a human still decides what goes
public. Passing --privacy is possible but is the one flag that makes this
pipeline capable of publishing something nobody has watched.

One channel failing does not stop the rest. A render can die on a bad LLM
response or a busy GPU, and losing the other three channels to that is worse
than losing one.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

import pipeline
from core import ideate, publish, retention, review, schedule
from core.config import PATHS


def _log_path(day: date) -> Path:
    logs = PATHS.state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"daily-{day:%Y-%m-%d}.log"


class Tee:
    """Write to the console and the day's log at once.

    Task Scheduler discards stdout, so without a file on disk a failed 3am run
    leaves nothing to read in the morning.
    """

    def __init__(self, path: Path):
        self.stream = path.open("a", encoding="utf-8")

    def write(self, text: str) -> int:
        sys.__stdout__.write(text)
        self.stream.write(text)
        # Flushed per write, not per buffer. A run killed by the four-hour task
        # limit or a reboot must still leave behind everything it got through,
        # otherwise the log is empty exactly when it is needed.
        self.stream.flush()
        return len(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self.stream.flush()


def topic_for(channel: str, preferred: str | None = None) -> str:
    """Resolve which topic a channel should cover.

    `preferred` is the topic its long-form parent is doing today. Reusing it is
    the whole point of the Short: same subject, so the Short funnels into the
    long video rather than advertising a different one. Falls back to the next
    unused topic when the Short has already covered it.
    """
    if preferred:
        used = {h["topic_id"] for h in ideate.load_history(channel)}
        if preferred not in used:
            try:
                return ideate.next_topic(channel, preferred)["id"]
            except KeyError:
                pass  # not in this channel's bank; fall through
    return ideate.next_topic(channel)["id"]


def build_plan(day: date, only: list[str] | None, force: bool) -> list[tuple[str, str]]:
    """Today's (channel, topic_id) pairs, parents before their Shorts."""
    channels = only or (schedule.all_channels() if force else schedule.plan(day))
    if force or only:
        channels = sorted(channels, key=lambda c: (schedule.parent_of(c) is not None, c))

    chosen: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for channel in channels:
        parent = schedule.parent_of(channel)
        try:
            topic = topic_for(channel, chosen.get(parent) if parent else None)
        except Exception as exc:  # noqa: BLE001 - an exhausted bank is not fatal
            print(f"skip {channel}: {exc}")
            continue
        chosen[channel] = topic
        pairs.append((channel, topic))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="daily unattended run")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    ap.add_argument("--force", action="store_true", help="ignore cadence")
    ap.add_argument("--channels", nargs="+", help="run exactly these")
    ap.add_argument("--privacy", default="private",
                    choices=["private", "unlisted", "public"])
    ap.add_argument("--no-publish", action="store_true", help="render only")
    ap.add_argument("--date", help="pretend it is this date (YYYY-MM-DD)")
    ap.add_argument("--retain-days", type=int, default=retention.DEFAULT_DAYS,
                    help="keep rendered media and cached images this many days")
    args = ap.parse_args()

    day = date.fromisoformat(args.date) if args.date else date.today()

    if not args.dry_run:
        sys.stdout = Tee(_log_path(day))

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"\n=== daily {day:%a %d %b %Y} (started {stamp}) ===")

    # Cheap, idempotent, and it prevents the worst unattended failure there is:
    # an empty state/ makes next_topic() reoffer the first topic in the bank, so
    # the run republishes a video the channel already has.
    for channel in schedule.all_channels():
        try:
            recovered = ideate.backfill(channel)
        except Exception:  # noqa: BLE001
            continue
        if recovered:
            print(f"history: recovered {len(recovered)} topic(s) for {channel}")

    pairs = build_plan(day, args.channels, args.force)
    if not pairs:
        print("nothing scheduled today")
        return 0

    for channel, topic in pairs:
        note = "" if publish.has_credentials(channel) else "   [no credentials: render only]"
        print(f"plan: {channel:16} {topic}{note}")

    if args.dry_run:
        print("\ndry run, nothing rendered")
        return 0

    rendered, failed = [], []
    for channel, topic in pairs:
        print(f"\n--- {channel} / {topic} ---")
        try:
            pipeline.run(channel, topic)
            rendered.append(channel)
        except Exception:  # noqa: BLE001 - keep the other channels alive
            failed.append(channel)
            traceback.print_exc(file=sys.stdout)

    if args.no_publish:
        print("\n--publish skipped")
        return 1 if failed else 0

    # Every channel with credentials, not only today's: a video approved
    # yesterday whose upload died on a network blip should go out now.
    print("\n--- upload ---")
    uploaded = 0
    for channel in schedule.all_channels():
        if not publish.has_credentials(channel) or not review.approved(channel):
            continue
        try:
            uploaded += len(publish.publish_approved(
                channel, limit=10, privacy=args.privacy))
        except Exception:  # noqa: BLE001
            failed.append(f"{channel} (upload)")
            traceback.print_exc(file=sys.stdout)

    # Last, so a sweep never removes something this run still needed. Failing
    # to reclaim disk must not fail the run: the videos are already on YouTube.
    try:
        swept = retention.sweep(args.retain_days)
        for line in swept["_lines"]:
            print(line)
        if swept["total"]:
            print(f"reclaimed {review.human(swept['total'])}")
    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stdout)

    print(f"\nrendered {len(rendered)}, uploaded {uploaded}, failed {len(failed)}")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
