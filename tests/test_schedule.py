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


def write_cfg(auto: bool, per_week: int | None = None):
    cfg = {
        "name": "test", "format": "finance",
        "voice": {"provider": "edge-tts", "name": "x"},
        "video": {"width": 1920, "height": 1080},
        "auto_publish": auto,
    }
    if per_week is not None:
        cfg["cadence"] = {"per_week": per_week}
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
    assert schedule.cadence("finance") > 0, "finance should declare a cadence"
    assert schedule.parent_of("finance_short") == "finance"
    assert schedule.parent_of("finance") is None, "a long channel has no parent"
    assert schedule.cadence("product") == 0, "product uses per_day and must idle"
    assert "topics_finance" not in schedule.all_channels(), "topic banks are not channels"
    print("real configs: shorts map to parents, product idles, banks excluded")

    # A Short must never be planned ahead of the parent whose topic it reuses.
    for offset in range(14):
        day = date.fromordinal(date.today().toordinal() + offset)
        todo = schedule.plan(day)
        for i, ch in enumerate(todo):
            parent = schedule.parent_of(ch)
            if parent in todo:
                assert todo.index(parent) < i, f"{day}: {ch} ordered before {parent}"
    print("across two weeks, every Short is planned after its parent")

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
