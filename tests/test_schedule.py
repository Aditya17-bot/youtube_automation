"""Cadence must spread runs across the week, and auto_publish must be honoured.

Both were dead config before: `cadence.per_week` had no reader at all, and
`auto_publish` was documented in review.py but never consulted, so setting it
silently did nothing and every video waited for a manual approve forever.
"""
import sys, pathlib, shutil, yaml
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import review, schedule
from core.config import PATHS

CH, SLUG = "_test_sched", "job1"
work = PATHS.work / CH / SLUG
rev = review.review_dir(CH, SLUG)
cfg_path = PATHS.channels / f"{CH}.yaml"


def make_work():
    if work.exists(): shutil.rmtree(work)
    if rev.exists(): shutil.rmtree(rev)
    work.mkdir(parents=True)
    (work / "final.mp4").write_bytes(b"x" * 100)


def write_cfg(auto: bool, per_week: int | None = None, per_day: int | None = None):
    cfg = {
        "name": "test", "format": "finance",
        "voice": {"provider": "edge-tts", "name": "x"},
        "video": {"width": 1920, "height": 1080},
        "auto_publish": auto,
    }
    if per_week is not None or per_day is not None:
        cad = {}
        if per_week is not None: cad["per_week"] = per_week
        if per_day is not None: cad["per_day"] = per_day
        cfg["cadence"] = cad
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


try:
    # --- weekday spreading -------------------------------------------------
    for n in range(1, 8):
        days = schedule.weekdays_for(n)
        assert len(days) == n, f"per_week={n} gave {len(days)} days: {days}"
        assert days == sorted(set(days)), f"per_week={n} has duplicates: {days}"
        assert all(0 <= d <= 6 for d in days), f"per_week={n} out of range: {days}"
    print("1..7 per week each yield that many distinct weekdays")

    assert schedule.weekdays_for(0) == [], "zero means never, not every day"
    assert schedule.weekdays_for(7) == list(range(7))
    assert schedule.weekdays_for(9) == list(range(7)), "over 7 caps at daily"
    print("0 = never, 7+ = every day")

    # Three a week must not land on three consecutive days: a burst is exactly
    # the pattern the inauthentic-content policy is tuned to spot.
    three = schedule.weekdays_for(3)
    gaps = [b - a for a, b in zip(three, three[1:])]
    assert max(gaps) >= 2 and min(gaps) >= 2, f"3/week clustered: {three}"
    print(f"3/week spreads to {[schedule.WEEKDAYS[d] for d in three]}")

    # --- real configs ------------------------------------------------------
    assert schedule.cadence("finance_short") == 0, "finance_short is paused"
    assert schedule.per_week_videos("finance_short") == 0, "paused means zero videos"
    # clips_short replaced story_short on the same YouTube channel. Exactly one
    # of the two may be live: both at once would double the day's uploads and
    # work through the same topic bank twice.
    assert schedule.cadence("clips_short") > 0, "clips_short is the live channel"
    assert schedule.per_day("clips_short") > 1, "clips_short should make several a day"
    assert schedule.cadence("story_short") == 0, "story_short was retired"
    assert sum(1 for c in ("clips_short", "story_short") if schedule.cadence(c)) == 1
    assert schedule.cadence("product") == 0, "product uses per_day and must idle"
    assert "topics_finance" not in schedule.all_channels(), "topic banks are not channels"

    # The long-form channels are gone; nothing may plan a video for them.
    live = schedule.all_channels()
    assert "finance" not in live and "story" not in live, f"long-form still live: {live}"

    # Nothing but clips_short may be planned while the others are paused.
    for offset in range(14):
        day = date.fromordinal(date.today().toordinal() + offset)
        assert schedule.plan(day) == ["clips_short"],             f"{day}: expected clips_short only, got {schedule.plan(day)}"
    print("only clips_short is live; story_short, finance_short and product are paused")

    # `topics_from` still names a topic bank, but a bank is not a partner
    # channel, so nothing should be waiting on one to run first.
    assert schedule.parent_of("finance_short") is None, "topics_finance is a bank, not a channel"
    assert schedule.parent_of("story_short") is None, "topics_story is a bank, not a channel"
    assert schedule.parent_of("clips_short") is None, "topics_story is a bank, not a channel"
    print("real configs: shorts only, product idles, banks excluded")

    # A Short must never be planned ahead of the parent whose topic it reuses.
    for offset in range(14):
        day = date.fromordinal(date.today().toordinal() + offset)
        todo = schedule.plan(day)
        for i, ch in enumerate(todo):
            parent = schedule.parent_of(ch)
            if parent in todo:
                assert todo.index(parent) < i, f"{day}: {ch} ordered before {parent}"
    print("across two weeks, every Short is planned after its parent")

    # --- per_day -----------------------------------------------------------
    # per_week is the on/off switch; per_day only says how many on a run day.
    # A config carrying per_day alone must stay dead - product.yaml is exactly
    # that, left over from an earlier design.
    write_cfg(auto=False, per_day=4)
    assert schedule.cadence(CH) == 0, "per_day alone must not schedule anything"
    assert schedule.per_day(CH) == 0, "an idle channel owes no videos"
    assert schedule.per_week_videos(CH) == 0

    write_cfg(auto=False, per_week=7, per_day=3)
    assert schedule.per_day(CH) == 3
    assert schedule.per_week_videos(CH) == 21, "7 days x 3 = 21 videos a week"

    write_cfg(auto=False, per_week=5)
    assert schedule.per_day(CH) == 1, "no per_day means one video per run day"

    write_cfg(auto=False, per_week=7, per_day=500)
    assert schedule.per_day(CH) == 10, "a typo must not queue 500 renders"
    write_cfg(auto=False, per_week=7, per_day=0)
    assert schedule.per_day(CH) == 1, "0 per_day on a running channel still means 1"
    print("per_day: gated by per_week, defaults to 1, clamped to 10")

    # --- auto_publish ------------------------------------------------------
    write_cfg(auto=False, per_week=2)
    make_work()
    review.promote(work, CH, SLUG)
    assert review._load(rev / "status.json")["status"] == review.STATUS_PENDING
    assert not review.approved(CH), "manual channel must not self-approve"
    print("auto_publish false: lands pending, stays out of the upload queue")

    write_cfg(auto=True, per_week=2)
    make_work()
    review.promote(work, CH, SLUG)
    data = review._load(rev / "status.json")
    assert data["status"] == review.STATUS_APPROVED, f"got {data['status']!r}"
    assert data.get("decided"), "an auto-approval should record when"
    assert len(review.approved(CH)) == 1, "auto channel must be queued for upload"
    print("auto_publish true: lands approved and is picked up for upload")

    # A channel with no yaml at all must fall back to the manual gate, not
    # inherit whatever the last-loaded config said.
    cfg_path.unlink()
    make_work()
    review.promote(work, CH, SLUG)
    assert review._load(rev / "status.json")["status"] == review.STATUS_PENDING
    print("unknown channel falls back to the manual gate")

    print("\nALL SCHEDULE TESTS PASS")
finally:
    if cfg_path.exists(): cfg_path.unlink()
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)

# --- release slots --------------------------------------------------------
# Three videos rendered in one run must not all appear at once: a burst is the
# shape the inauthentic-content policy looks for, and the feed will not show one
# channel three times running to the same viewer anyway.
from datetime import datetime, time as _time, timedelta   # noqa: E402

assert schedule.slot_times("clips_short") == [_time(5, 30), _time(8, 30), _time(23, 30)]
assert schedule.slot_times("finance_short") == []      # no slots: publish at once

NOW = datetime(2026, 9, 17, 17, 30).astimezone()
slots = schedule.publish_slots("clips_short", 3, now=NOW)
assert len(slots) == 3, slots
assert [s.strftime("%d %H:%M") for s in slots] == ["17 23:30", "18 05:30", "18 08:30"], slots

# Every gap honours min_gap_hours, which is enforced on top of the configured
# times rather than trusted to them - a typo must not be able to break it.
gap = timedelta(hours=3)
assert all(b - a >= gap for a, b in zip(slots, slots[1:])), slots

# Nothing is ever scheduled into the past: YouTube rejects a publishAt that has
# already gone, and losing a whole batch to that would be a silent total loss.
late = schedule.publish_slots("clips_short", 3, now=datetime(2026, 9, 17, 23, 29).astimezone())
assert all(s > datetime(2026, 9, 17, 23, 29).astimezone() for s in late), late
assert late[0].day == 18, late      # 23:30 is inside the lead time, so it rolls

# A channel with no slots opts out entirely and keeps publishing immediately.
assert schedule.publish_slots("finance_short", 3, now=NOW) == []
print("release slots spaced, never in the past, opt-out respected")

# A slot promised in an EARLIER run is spent. Without this the guarantee only
# holds inside one call, and a second publish run the same day - a retry, a
# manual top-up, a batch finished in two goes - starts again from the same slot
# and puts two videos out together.
promised = [datetime(2026, 9, 17, 23, 30).astimezone(),
            datetime(2026, 9, 18, 5, 30).astimezone()]
after = schedule.publish_slots("clips_short", 3, now=NOW, taken=promised)
assert [s.strftime("%d %H:%M") for s in after] == ["18 08:30", "18 23:30", "19 05:30"], after
assert all(abs(a - b) >= gap for a in after for b in promised), after

# A promise already in the past holds nothing open.
stale = schedule.publish_slots("clips_short", 1, now=NOW,
                               taken=[datetime(2026, 9, 17, 5, 30).astimezone()])
assert stale[0].strftime("%d %H:%M") == "17 23:30", stale
print("slots promised in an earlier run are not handed out twice")
