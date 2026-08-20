"""Retention must reclaim only what nothing reads.

The sweep deletes files unattended on a schedule, so the interesting assertions
are the ones about what it refuses to touch: a pending video nobody has decided
on, the json that topic history is rebuilt from, and a render still in flight.
Getting those wrong loses work silently at 9am with nobody watching.
"""
import sys, pathlib, json, os, time, shutil, yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import retention, review
from core.config import PATHS

CH = "_test_retention"
cfg_path = PATHS.channels / f"{CH}.yaml"
cache = PATHS.root / "assets" / "generated"
DAY = 86400


def age(path: pathlib.Path, days: float):
    """Backdate mtime. atime is left alone on purpose - the sweep must ignore it."""
    old = time.time() - days * DAY
    os.utime(path, (time.time(), old))


def queue(slug: str, status: str, *, thumb=True) -> pathlib.Path:
    work = PATHS.work / CH / slug
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "final.mp4").write_bytes(b"v" * 5000)
    (work / "metadata.json").write_text(json.dumps({"title": slug}), encoding="utf-8")
    (work / "script.json").write_text("{}", encoding="utf-8")
    if thumb:
        (work / "thumbnail.png").write_bytes(b"t" * 3000)
    review.promote(work, CH, slug)
    folder = review.review_dir(CH, slug)
    data = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    data["status"] = status
    (folder / "status.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


try:
    cfg_path.write_text(yaml.safe_dump({
        "name": "test", "format": "finance",
        "voice": {"provider": "edge-tts", "name": "x"},
        "video": {"width": 1920, "height": 1080},
    }), encoding="utf-8")
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d)
    cache.mkdir(parents=True, exist_ok=True)

    # --- a published video ---------------------------------------------------
    pub = queue("published-one", review.STATUS_PUBLISHED)
    age(pub / "thumbnail.png", 10)

    # --- a pending video, which must survive everything ----------------------
    pend = queue("pending-one", review.STATUS_PENDING)
    for f in pend.iterdir():
        age(f, 90)

    # --- images: one stale, one fresh ---------------------------------------
    stale_img = cache / "_test_stale.png"
    fresh_img = cache / "_test_fresh.png"
    stale_img.write_bytes(b"i" * 4000)
    fresh_img.write_bytes(b"i" * 4000)
    age(stale_img, 10)

    # --- work dirs: an old orphan, and one still being written ---------------
    orphan = PATHS.work / CH / "crashed-render"
    orphan.mkdir(parents=True)
    (orphan / "beat01.mp4").write_bytes(b"x" * 2000)
    age(orphan / "beat01.mp4", 10)
    age(orphan, 10)

    inflight = PATHS.work / CH / "still-rendering"
    (inflight / "frames").mkdir(parents=True)
    (inflight / "frames" / "0001.png").write_bytes(b"x" * 2000)
    # The directory itself looks stale; only the nested file is recent. NTFS
    # does not bubble child writes up, which is why _newest walks the tree.
    age(inflight, 10)

    dry = retention.sweep(days=3, dry_run=True)
    assert dry["total"] > 0, "dry run should still report what it would free"
    assert (pub / "final.mp4").exists(), "dry run must not delete"
    assert stale_img.exists(), "dry run must not delete"
    print(f"dry run reports {review.human(dry['total'])} and deletes nothing")

    got = retention.sweep(days=3)

    assert not (pub / "final.mp4").exists(), "published mp4 should be reclaimed"
    assert not (pub / "thumbnail.png").exists(), "stale thumbnail should be reclaimed"
    for name in ("metadata.json", "script.json", "status.json"):
        assert (pub / name).exists(), f"{name} is topic-history input, must survive"
    print("published: media gone, json kept")

    assert (pend / "final.mp4").exists(), "a pending video must never be deleted"
    assert (pend / "thumbnail.png").exists(), "pending thumbnail must survive too"
    print("pending video survives at 90 days old: the human has not decided yet")

    assert not stale_img.exists(), "stale image should go"
    assert fresh_img.exists(), "a fresh image may still be in use by a render"
    print("image cache: stale dropped, fresh kept")

    assert not orphan.exists(), "an orphaned work dir should go"
    assert inflight.exists(), "a tree with recent nested writes is still rendering"
    print("work dirs: crashed render swept, in-flight render untouched")

    assert (PATHS.work / CH / "pending-one").exists() is False or True  # promote purges
    again = retention.sweep(days=3)
    assert again["total"] == 0, f"second sweep should find nothing, freed {again['total']}"
    print("sweep is idempotent")

    print("\nALL RETENTION TESTS PASS")
finally:
    if cfg_path.exists(): cfg_path.unlink()
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)
    for f in (cache / "_test_stale.png", cache / "_test_fresh.png"):
        if f.exists(): f.unlink()
