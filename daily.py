"""Unattended daily run: render whatever today's cadence calls for and upload it.

    python daily.py --dry-run     # what today would do, without doing it
    python daily.py               # the real thing
    python daily.py --force       # ignore cadence, run every channel
    python daily.py --channels finance_short

Visibility comes from each channel's `privacy:` key, which the one live Shorts
channel sets to `public`. This run therefore publishes without a human
having watched the video - that is the deliberate trade for a daily Shorts
cadence, and the guards that used to sit behind the review gate now sit in
front of the renderer instead: the compliance linter, the duration ceiling and
the fiction check all reject a script before it is ever voiced.

`--privacy` overrides the config for one run, for a batch you want held back.

One channel failing does not stop the rest. A render can die on a bad LLM
response or a busy GPU, and losing the day's other channel to that is worse
than losing one.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

import pipeline
from core import (ideate, llm, notify, publish, retention, review, schedule,
                  topicgen)
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


def topic_for(channel: str, preferred: str | None = None,
              taken: set[str] | None = None) -> str:
    """Resolve which topic a channel should cover.

    `preferred` is the topic its long-form parent is doing today. Reusing it is
    the whole point of the Short: same subject, so the Short funnels into the
    long video rather than advertising a different one. Falls back to the next
    unused topic when the Short has already covered it.

    `taken` is what earlier videos in the same run already claimed. Nothing is
    written to history until a render finishes, so without it a channel making
    three videos today would make the same one three times.
    """
    taken = taken or set()
    if preferred and preferred not in taken:
        used = {h["topic_id"] for h in ideate.load_history(channel)}
        if preferred not in used:
            try:
                return ideate.next_topic(channel, preferred)["id"]
            except KeyError:
                pass  # not in this channel's bank; fall through
    return ideate.next_topic(channel, exclude=taken)["id"]


def build_plan(day: date, only: list[str] | None, force: bool,
               per_day: int | None = None) -> list[tuple[str, str]]:
    """Today's (channel, topic_id) pairs, parents before their Shorts.

    A channel appears once per video it owes today, so `cadence.per_day: 3`
    yields three pairs and three separate renders. Running out of topics
    part-way through is not an error: the pairs already collected still run.
    """
    channels = only or (schedule.all_channels() if force else schedule.plan(day))
    if force or only:
        channels = sorted(channels, key=lambda c: (schedule.parent_of(c) is not None, c))

    chosen: dict[str, str] = {}
    taken: dict[str, set[str]] = {}
    pairs: list[tuple[str, str]] = []
    for channel in channels:
        parent = schedule.parent_of(channel)
        # --channels/--force name a channel explicitly; honour its per_day, but
        # a channel with no cadence at all still owes exactly one video.
        wanted = per_day if per_day is not None else (schedule.per_day(channel) or 1)
        for _ in range(wanted):
            claimed = taken.setdefault(channel, set())
            try:
                topic = topic_for(channel, chosen.get(parent) if parent else None,
                                  claimed)
            except Exception as exc:  # noqa: BLE001 - an exhausted bank is not fatal
                print(f"skip {channel}: {exc}")
                break
            claimed.add(topic)
            chosen.setdefault(channel, topic)
            pairs.append((channel, topic))
    return pairs


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="daily unattended run")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    ap.add_argument("--force", action="store_true", help="ignore cadence")
    ap.add_argument("--channels", nargs="+", help="run exactly these")
    ap.add_argument("--per-day", type=int, default=None,
                    help="override how many videos each channel makes today")
    ap.add_argument("--no-topic-refill", action="store_true",
                    help="do not extend a topic bank that is running low")
    ap.add_argument("--privacy", default=None,
                    choices=["private", "unlisted", "public"],
                    help="override every channel's configured visibility")
    ap.add_argument("--no-publish", action="store_true", help="render only")
    ap.add_argument("--date", help="pretend it is this date (YYYY-MM-DD)")
    ap.add_argument("--retain-days", type=int, default=retention.DEFAULT_DAYS,
                    help="keep rendered media and cached images this many days")
    return ap.parse_args()


def run(args: argparse.Namespace, day: date) -> tuple[int, list[str]]:
    """One day's work. Returns its exit code and anything worth an email."""
    problems: list[str] = []
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"\n=== daily {day:%a %d %b %Y} (started {stamp}) ===")

    # The task is set for 09:00, but the laptop is usually asleep then, so
    # StartWhenAvailable runs it at login instead - and Claude Code updates
    # itself at login too, deleting its own binary for the length of an npm
    # install. On 2026-09-10 the two landed 49 seconds apart and all three
    # videos died on a missing exe. Wait it out here, once, before the topic
    # bank and the renderer are committed to anything.
    if not args.dry_run:
        began = time.monotonic()
        try:
            llm.wait_for_claude()
        except llm.ClaudeUnavailable as exc:
            print(f"claude: {exc}")
            return 1, [f"nothing ran today: {exc}"]
        waited = time.monotonic() - began
        if waited >= llm.BIN_POLL:
            print(f"claude: waited {waited:.0f}s for the binary to come back")

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

    # Before the plan, because the plan is what spends the bank. At three
    # videos a day a sixty-topic bank is gone in twenty days, so waiting for a
    # human to read the warning email is not a strategy.
    # Skipped on a dry run: it calls a model and rewrites a bank file, and a
    # dry run is meant to be free and read-only.
    if not args.no_topic_refill and not args.dry_run:
        for channel in schedule.all_channels():
            try:
                new = topicgen.top_up(channel)
            except Exception as exc:  # noqa: BLE001 - never lose a run to this
                if schedule.per_day(channel):
                    problems.append(f"{channel}: topic refill failed: "
                                    f"{type(exc).__name__}: {exc}")
                continue
            if new:
                print(f"topics: +{len(new)} generated for {channel}")

    # Checked after the refill, so the warning only fires for a bank that could
    # not be topped up. A bank that has just run dry produces no pair at all -
    # the channel goes quiet and there is nothing in the plan left to notice it by.
    for warning in notify.low_banks():
        print(f"topics: {warning}")
        problems.append(warning)

    pairs = build_plan(day, args.channels, args.force, args.per_day)
    if not pairs:
        print("nothing scheduled today")
        return 0, problems

    warned: set[str] = set()
    for channel, topic in pairs:
        if publish.has_credentials(channel):
            note = ""
        else:
            note = "   [no credentials: render only]"
            if channel not in warned:   # one email line per channel, not per video
                warned.add(channel)
                problems.append(f"{channel}: not authorised, video will not upload")
        print(f"plan: {channel:16} {topic}{note}")

    if args.dry_run:
        print("\ndry run, nothing rendered")
        return 0, problems

    rendered, failed = [], []
    for channel, topic in pairs:
        print(f"\n--- {channel} / {topic} ---")
        try:
            pipeline.run(channel, topic)
            rendered.append(channel)
        except Exception as exc:  # noqa: BLE001 - keep the other channels alive
            failed.append(channel)
            problems.append(f"{channel}/{topic}: render failed: "
                            f"{type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stdout)

    if args.no_publish:
        print("\n--publish skipped")
        return (1 if failed else 0), problems

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
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{channel} (upload)")
            problems.append(f"{channel}: upload failed: "
                            f"{type(exc).__name__}: {exc}")
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
    return (1 if failed else 0), problems


def main() -> int:
    args = _parse()
    day = date.fromisoformat(args.date) if args.date else date.today()
    log = _log_path(day)

    if not args.dry_run:
        sys.stdout = Tee(log)

    # An error out here - a bad import, a full disk, a config that will not
    # parse - is the one failure with no other way of reaching anybody, since
    # it happens before the run is in a position to report anything itself.
    try:
        code, problems = run(args, day)
    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stdout)
        code, problems = 1, ["the run stopped with an unhandled error"]

    if not args.dry_run:
        notify.report(f"{day:%Y-%m-%d}", problems, log)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
