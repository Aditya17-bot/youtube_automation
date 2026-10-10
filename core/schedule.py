"""Which channels are due on a given day, and how many videos each one owes.

`cadence.per_week` sat in every channel config without a single reader. This
module is the reader. Run days are derived arithmetically from the number
rather than stored anywhere, so a day the machine was switched off does not
shift the rest of the week: Wednesday is Wednesday whatever happened Tuesday.

The two cadence keys answer different questions and neither implies the other:
`per_week` is WHICH days run, `per_day` is HOW MANY videos on one of those
days. per_week stays the sole on/off switch, so a config carrying only
`per_day` - product.yaml still does, from an earlier design - never runs.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from core.config import PATHS, ConfigError, load_channel

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekdays_for(per_week: int) -> list[int]:
    """Spread `per_week` runs across the week. 0 is Monday.

    Even spacing matters more than which particular days: three videos on
    Mon/Wed/Sat reads as a schedule, three on Mon/Tue/Wed reads as a burst,
    and bursts are what the inauthentic-content policy looks for.
    """
    if per_week <= 0:
        return []
    if per_week >= 7:
        return list(range(7))
    return sorted({round(i * 7 / per_week) for i in range(per_week)})


def _cadence_cfg(channel: str) -> dict:
    try:
        return load_channel(channel).get("cadence") or {}
    except Exception:  # noqa: BLE001
        return {}


def cadence(channel: str) -> int:
    """Run days per week for a channel, 0 if it declares none.

    `product.yaml` uses `per_day` from an earlier design and has no upload
    account, so it reads as 0 and never enters a plan.
    """
    return int(_cadence_cfg(channel).get("per_week") or 0)


def per_day(channel: str) -> int:
    """How many videos this channel makes on a day it runs. Default 1.

    Only consulted for a channel that already passed `due()`, so a stray
    `per_day` on an idle config cannot bring it back to life. Clamped at 1
    below and 10 above: ten uploads is roughly where an unverified YouTube
    account starts refusing them, and a config typo should not spend a night
    rendering fifty videos that cannot be posted.
    """
    if cadence(channel) <= 0:
        return 0
    return max(1, min(10, int(_cadence_cfg(channel).get("per_day") or 1)))


def per_week_videos(channel: str) -> int:
    """Videos per week, not run days per week - what a topic bank is spent at."""
    return cadence(channel) * per_day(channel)


# Prefixes that live in channels/ without being channels. Anything here is data
# a channel reads, not a channel: `topics_*` are topic banks, `footage_*` are
# licensed source lists. A file that reaches load_channel without being one
# fails on the missing `format` key, which reads as a broken config rather than
# as "that was never a channel".
NOT_A_CHANNEL = ("topics_", "footage_")


def all_channels() -> list[str]:
    """Every channel config; the data files in channels/ are skipped."""
    return sorted(p.stem for p in PATHS.channels.glob("*.yaml")
                  if not p.stem.startswith(NOT_A_CHANNEL))


def parent_of(channel: str) -> str | None:
    """The channel whose topic a Short reuses on the same day, if any.

    `script.topics_from` does double duty: it names a topic BANK, and where a
    channel of that name also exists it names a same-day partner whose topic
    gets reused. Since the long-form channels were retired it only ever names a
    bank, so this returns None - the existence check is what keeps it honest
    rather than pointing the runner at a config that is not there.
    """
    try:
        cfg = load_channel(channel)
    except Exception:  # noqa: BLE001
        return None
    parent = (cfg.get("script") or {}).get("topics_from")
    if not parent or parent == channel:
        return None
    return parent if (PATHS.channels / f"{parent}.yaml").exists() else None


def due(channel: str, day: date) -> bool:
    return day.weekday() in weekdays_for(cadence(channel))


def plan(day: date) -> list[str]:
    """Channels to run on `day`, any parent ahead of the Shorts that follow it.

    Ordering is not cosmetic: the runner reuses a parent's topic for a Short
    scheduled the same day, so the parent has to go first for there to be a
    topic to reuse. With only Shorts left there are no parents, and this is a
    plain alphabetical sort.
    """
    todo = [c for c in all_channels() if due(c, day)]
    return sorted(todo, key=lambda c: (parent_of(c) is not None, c))


def describe() -> list[dict]:
    """Per-channel cadence summary, for `python -m core.schedule`."""
    rows = []
    for ch in all_channels():
        n = cadence(ch)
        rows.append({
            "channel": ch,
            "per_week": n,
            "per_day": per_day(ch),
            "videos_per_week": per_week_videos(ch),
            "days": [WEEKDAYS[d] for d in weekdays_for(n)],
            "parent": parent_of(ch),
        })
    return rows


# --- release times --------------------------------------------------------
#
# A day's videos are rendered in one run but must not all appear at once. Three
# uploads landing in the same minute is a burst, and a burst is the shape the
# inauthentic-content policy looks for; it also wastes two of the three, since
# the feed will not show one channel three times in a row to the same viewer.
#
# The spacing is done by YouTube rather than by this machine. `videos.insert`
# accepts `status.publishAt`, so a video can be uploaded now and released hours
# later whether or not the PC is still on. That also keeps it inside the
# existing upload scope: `videos.update` would need a write scope the shared
# token does not have.

# Minimum minutes between "now" and the first slot. An upload takes a couple of
# minutes and YouTube needs a moment to finish processing; scheduling a release
# for thirty seconds' time simply fails.
SLOT_LEAD_MINUTES = 15


def _publish_cfg(channel: str) -> dict:
    try:
        return load_channel(channel).get("publish") or {}
    except Exception:  # noqa: BLE001
        return {}


def slot_times(channel: str) -> list[time]:
    """The times of day this channel releases at, in local time, sorted."""
    raw = _publish_cfg(channel).get("slots") or []
    out = []
    for entry in raw:
        try:
            hh, _, mm = str(entry).partition(":")
            out.append(time(int(hh), int(mm or 0)))
        except ValueError:
            raise ConfigError(
                f"{channel}: publish.slots entry {entry!r} is not HH:MM"
            ) from None
    return sorted(out)


def publish_slots(channel: str, count: int, now: datetime | None = None,
                  taken: list[datetime] | None = None) -> list[datetime]:
    """`count` release times for one day's videos, earliest first.

    Returns an empty list when the channel declares no slots, which is how a
    channel opts out and keeps publishing immediately.

    Slots already past today roll to tomorrow, so a run that starts at 23:00
    does not silently schedule three releases into the past - YouTube rejects a
    `publishAt` in the past, and losing the whole batch to that would be a
    quiet, total failure.

    `min_gap_hours` is enforced on top of the configured times rather than
    trusted to them: it is the guarantee the config is trying to express, and a
    typo that puts two slots ten minutes apart should not be able to break it.

    `taken` are release times already promised to videos that are up but not
    out yet, and they hold the gap open just as this run's own picks do.
    Without it the guarantee only holds INSIDE one call: a second publish run
    the same day - a retry, a manual top-up, a batch finished in two goes -
    would start again from the same slot and put two videos out together, which
    is the burst the spacing exists to prevent.
    """
    times = slot_times(channel)
    if not times:
        return []

    now = now or datetime.now().astimezone()
    floor = now + timedelta(minutes=SLOT_LEAD_MINUTES)
    gap = timedelta(hours=float(_publish_cfg(channel).get("min_gap_hours", 0) or 0))

    out: list[datetime] = []
    claimed = sorted(t.astimezone(now.tzinfo) for t in (taken or []) if t >= floor)
    day = floor.date()
    # Bounded rather than `while True`: a min_gap wider than the slots can
    # satisfy would otherwise spin forever. Two weeks is far past any sane
    # backlog, and running out is reported rather than silently truncated.
    for _ in range(14):
        for t in times:
            candidate = datetime.combine(day, t).astimezone(now.tzinfo)
            if candidate < floor:
                continue
            if any(abs(candidate - t) < gap for t in out + claimed):
                continue
            out.append(candidate)
            if len(out) == count:
                return out
        day += timedelta(days=1)
    return out


if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    ap = argparse.ArgumentParser(description="cadence schedule")
    ap.add_argument("--weeks", type=int, default=1, help="how many weeks to print")
    args = ap.parse_args()

    for row in describe():
        days = ", ".join(row["days"]) if row["days"] else "never"
        parent = f"  (short of {row['parent']})" if row["parent"] else ""
        each = f" x{row['per_day']}" if row["per_day"] > 1 else ""
        print(f"{row['channel']:16} {row['videos_per_week']} videos/week  "
              f"{days}{each}{parent}")

    today = date.today()
    print()
    for offset in range(7 * args.weeks):
        day = today + timedelta(days=offset)
        todo = [f"{c} x{per_day(c)}" if per_day(c) > 1 else c for c in plan(day)]
        mark = " <- today" if offset == 0 else ""
        print(f"{day:%a %d %b}  {', '.join(todo) if todo else '-'}{mark}")
