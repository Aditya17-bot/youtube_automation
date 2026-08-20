"""Topic history must be recoverable from the review queue.

state/ held nothing at all while three videos existed on disk and one was
already public, so next_topic() kept handing back the first topic in the bank.
Unattended, that republishes the same video forever - which is the repetition
pattern the inauthentic-content policy targets.
"""
import sys, pathlib, json, shutil, yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import ideate, review
from core.config import PATHS

CH = "_test_backfill"
cfg_path = PATHS.channels / f"{CH}.yaml"
bank_path = PATHS.channels / f"topics_{CH}.yaml"
hist = PATHS.state / f"history_{CH}.json"


def queue(slug: str, title: str):
    """Put a finished-looking video in the review queue."""
    work = PATHS.work / CH / slug
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "final.mp4").write_bytes(b"x" * 10)
    (work / "metadata.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    review.promote(work, CH, slug)


try:
    cfg_path.write_text(yaml.safe_dump({
        "name": "test", "format": "finance",
        "voice": {"provider": "edge-tts", "name": "x"},
        "video": {"width": 1920, "height": 1080},
        "script": {"topics_from": CH},
    }), encoding="utf-8")
    bank_path.write_text(yaml.safe_dump({"topics": [
        {"id": "alpha", "title": "Alpha"},
        {"id": "beta", "title": "Beta"},
        {"id": "gamma", "title": "Gamma"},
    ]}), encoding="utf-8")
    if hist.exists(): hist.unlink()
    if (PATHS.review / CH).exists(): shutil.rmtree(PATHS.review / CH)

    # With no history, the bank hands back its first topic - the bug.
    assert ideate.next_topic(CH)["id"] == "alpha"
    queue("alpha", "Alpha the video")
    assert ideate.next_topic(CH)["id"] == "alpha", "queue alone does not update history"
    print("reproduced: a video on disk does not stop next_topic reoffering it")

    added = ideate.backfill(CH)
    assert added == ["alpha"], f"expected ['alpha'], got {added}"
    assert ideate.next_topic(CH)["id"] == "beta", "backfill must advance the bank"
    assert ideate.load_history(CH)[0]["title"] == "Alpha the video", "title comes from metadata"
    print("backfill records the queued topic and the bank advances")

    assert ideate.backfill(CH) == [], "second run must add nothing"
    assert len(ideate.load_history(CH)) == 1, "backfill must not duplicate"
    print("backfill is idempotent")

    # A slug that is not in the bank cannot be regenerated, so it is not history.
    queue("legacy-topic", "Made before the bank changed")
    assert ideate.backfill(CH) == [], "off-bank slugs must be ignored"
    assert len(ideate.load_history(CH)) == 1
    print("a slug missing from the bank is skipped, not invented")

    queue("gamma", "Gamma the video")
    assert ideate.backfill(CH) == ["gamma"]
    assert ideate.next_topic(CH)["id"] == "beta", "beta is still the earliest unused"
    assert ideate.stats(CH) == {"total": 3, "used": 2, "remaining": 1}
    print("out-of-order topics backfill correctly and stats agree")

    print("\nALL BACKFILL TESTS PASS")
finally:
    for p in (cfg_path, bank_path, hist):
        if p.exists(): p.unlink()
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)
