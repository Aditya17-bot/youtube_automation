"""Which channels are due on a given day.

`cadence.per_week` sat in every channel config without a single reader. This
module is the reader. Run days are derived arithmetically from the number
rather than stored anywhere, so a day the machine was switched off does not
shift the rest of the week: Wednesday is Wednesday whatever happened Tuesday.
"""
from __future__ import annotations

from datetime import date

from core.config import PATHS, load_channel

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


def cadence(channel: str) -> int:
    """Videos per week for a channel, 0 if it declares none.

    `product.yaml` uses `per_day` from an earlier design and has no upload
    account, so it reads as 0 and never enters a plan.
    """
    try:
        cfg = load_channel(channel)
    except Exception:  # noqa: BLE001
        return 0
    return int((cfg.get("cadence") or {}).get("per_week") or 0)


def all_channels() -> list[str]:
    """Every channel config. `topics_*.yaml` are topic banks, not channels."""
    return sorted(p.stem for p in PATHS.channels.glob("*.yaml")
                  if not p.stem.startswith("topics_"))


def parent_of(channel: str) -> str | None:
    """The long-form channel a Short funnels into, if any.

    A Short reads its parent's topic bank, which is what makes it possible to
    put both on the same subject the same day.
    """
    try:
        cfg = load_channel(channel)
    except Exception:  # noqa: BLE001
        return None
    parent = (cfg.get("script") or {}).get("topics_from")
    return parent if parent and parent != channel else None


def due(channel: str, day: date) -> bool:
    return day.weekday() in weekdays_for(cadence(channel))


def plan(day: date) -> list[str]:
    """Channels to run on `day`, long-form ahead of the Shorts that follow it.

    Ordering is not cosmetic: the runner reuses the parent's topic for a Short
    scheduled the same day, so the parent has to go first for there to be a
    topic to reuse.
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
            "days": [WEEKDAYS[d] for d in weekdays_for(n)],
            "parent": parent_of(ch),
        })
    return rows


if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    ap = argparse.ArgumentParser(description="cadence schedule")
    ap.add_argument("--weeks", type=int, default=1, help="how many weeks to print")
    args = ap.parse_args()

    for row in describe():
        days = ", ".join(row["days"]) if row["days"] else "never"
        parent = f"  (short of {row['parent']})" if row["parent"] else ""
        print(f"{row['channel']:16} {row['per_week']}/week  {days}{parent}")

    today = date.today()
    print()
    for offset in range(7 * args.weeks):
        day = today + timedelta(days=offset)
        todo = plan(day)
        mark = " <- today" if offset == 0 else ""
        print(f"{day:%a %d %b}  {', '.join(todo) if todo else '-'}{mark}")
