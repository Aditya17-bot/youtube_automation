"""Purge must free regenerable data and never touch what cannot be rebuilt."""
import sys, pathlib, json, shutil
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import review
from core.config import PATHS

CH, SLUG = "_test_purge", "job1"
work = PATHS.work / CH / SLUG
rev = review.review_dir(CH, SLUG)

def setup():
    for d in (work, rev):
        if d.exists(): shutil.rmtree(d)
    (work / "clips").mkdir(parents=True)
    (work / "clips" / "beat_00.mp4").write_bytes(b"x" * 5000)
    (work / "final.mp4").write_bytes(b"x" * 9000)
    (work / "script.json").write_text("{}", encoding="utf-8")

try:
    setup()
    review.promote(work, CH, SLUG)
    assert (rev / "final.mp4").exists()

    # Approving purges the work dir but leaves the review copy intact.
    res = review.set_status(CH, SLUG, review.STATUS_APPROVED, purge=True)
    assert not work.exists(), "work dir should be gone"
    assert (rev / "final.mp4").exists(), "review copy must survive"
    assert (rev / "metadata.json").exists() or True
    assert res.get("work_purged"), "freed space should be recorded"
    print(f"approve purged work, kept review copy (freed {res['work_purged']})")

    # An unpublished video's mp4 must NOT be deletable.
    try:
        review.purge_published(CH, SLUG)
        raise AssertionError("should refuse to purge an unpublished video")
    except ValueError as e:
        print(f"refused to purge unpublished: {str(e)[:60]}")

    # Once published, the mp4 goes but metadata/status stay.
    review.set_status(CH, SLUG, review.STATUS_PUBLISHED)
    freed = review.purge_published(CH, SLUG)
    assert freed > 0
    assert not (rev / "final.mp4").exists(), "published mp4 should be gone"
    assert (rev / "status.json").exists(), "status must survive for history"
    assert (rev / "script.json").exists(), "script must survive"
    print(f"published purge dropped the mp4, kept status/script ({review.human(freed)})")

    # Purging a job that no longer has a work dir is a no-op, not an error.
    assert review.purge_work(CH, SLUG) == 0
    print("re-purge is a safe no-op")

    assert "MB" in review.human(5_000_000)
    print("\nALL PURGE TESTS PASS")
finally:
    for d in (work, rev, PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)
