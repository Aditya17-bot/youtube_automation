"""Human review gate.

Everything the pipeline produces lands here first. Nothing reaches YouTube
without an explicit approve, unless the channel sets `auto_publish: true`.

This exists because YouTube's inauthentic-content policy is enforced at the
CHANNEL level: one run of bad output can cost the whole channel its
monetisation, so unattended volume is the main risk to manage.

    python -m core.review list
    python -m core.review approve compound-interest
    python -m core.review reject  compound-interest --note "chart unreadable"
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.config import PATHS, load_channel

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PUBLISHED = "published"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def review_dir(channel: str, slug: str) -> Path:
    return PATHS.review / channel / slug


def _auto_publish(channel: str) -> bool:
    """Whether a channel skips the local queue and goes straight to upload.

    A missing config is not an error: promote() is called in tests under
    channel names that have no yaml, and the safe answer for anything unknown
    is the manual gate.
    """
    try:
        return bool(load_channel(channel).get("auto_publish"))
    except Exception:  # noqa: BLE001
        return False


def promote(job_dir: Path, channel: str, slug: str) -> Path:
    """Copy the finished artefacts out of the work dir into the review queue.

    Lands `pending` unless the channel sets `auto_publish: true`, in which case
    it lands `approved` and the next `publish run` takes it. That is not a way
    past review, only a way to move it: those uploads go out `private`, so the
    video still waits for a human, it just waits in YouTube Studio.
    """
    dest = review_dir(channel, slug)
    dest.mkdir(parents=True, exist_ok=True)

    wanted = ["final.mp4", "metadata.json", "thumbnail.png", "script.json", "captions.ass"]
    copied = []
    for name in wanted:
        src = job_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)

    if "final.mp4" not in copied:
        raise FileNotFoundError(f"no final.mp4 in {job_dir}; nothing to review")

    auto = _auto_publish(channel)
    status = {
        "channel": channel,
        "slug": slug,
        "status": STATUS_APPROVED if auto else STATUS_PENDING,
        "created": _now(),
        "files": copied,
        "source": str(job_dir),
    }
    if auto:
        status["decided"] = _now()
        status["note"] = "auto_publish: uploaded private, review in YouTube Studio"
    (dest / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return dest


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def purge_work(channel: str, slug: str) -> int:
    """Delete a job's working directory. Returns bytes freed.

    Safe because everything in it is regenerable: per-beat clips, frame dumps,
    padded audio and the pre-mix concatenations. The finished video and its
    metadata live in the review queue and are untouched.
    """
    work = PATHS.work / channel / slug
    if not work.is_dir():
        return 0
    freed = _dir_size(work)
    shutil.rmtree(work)
    return freed


def purge_published(channel: str, slug: str) -> int:
    """Drop the finished mp4 for an already-published video.

    Only ever called once the upload succeeded, so YouTube holds the copy that
    matters. Metadata and status stay, which is what topic history reads.
    """
    folder = review_dir(channel, slug)
    status = _load(folder / "status.json") if (folder / "status.json").exists() else {}
    if status.get("status") != STATUS_PUBLISHED:
        raise ValueError(f"{channel}/{slug} is {status.get('status')!r}, not published")

    freed = 0
    for name in ("final.mp4", "captions.ass"):
        target = folder / name
        if target.exists():
            freed += target.stat().st_size
            target.unlink()
    return freed


def disk_report() -> dict:
    work = _dir_size(PATHS.work) if PATHS.work.is_dir() else 0
    review = _dir_size(PATHS.review) if PATHS.review.is_dir() else 0
    generated = PATHS.root / "assets" / "generated"
    cache = _dir_size(generated) if generated.is_dir() else 0
    return {"work": work, "review": review, "image_cache": cache,
            "total": work + review + cache}


def list_pending(channel: str | None = None) -> list[dict]:
    out: list[dict] = []
    roots = [PATHS.review / channel] if channel else sorted(
        p for p in PATHS.review.glob("*") if p.is_dir()
    )
    for root in roots:
        if not root.is_dir():
            continue
        for status_file in sorted(root.glob("*/status.json")):
            data = _load(status_file)
            data["_dir"] = str(status_file.parent)
            out.append(data)
    return out


def set_status(channel: str, slug: str, status: str, note: str | None = None,
               purge: bool = False) -> dict:
    path = review_dir(channel, slug) / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"nothing queued at {path.parent}")
    data = _load(path)
    data["status"] = status
    data["decided"] = _now()
    if note:
        data["note"] = note

    if purge:
        freed = purge_work(channel, slug)
        if freed:
            data["work_purged"] = human(freed)

    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def approved(channel: str | None = None) -> list[dict]:
    return [d for d in list_pending(channel) if d.get("status") == STATUS_APPROVED]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="review queue")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show queued videos")
    p_list.add_argument("--channel")

    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        p.add_argument("--channel", default="finance")
        p.add_argument("--note")
        p.add_argument("--keep", action="store_true",
                       help="keep the working directory instead of purging it")

    sub.add_parser("disk", help="show what the pipeline is using")

    p_purge = sub.add_parser("purge", help="reclaim space")
    p_purge.add_argument("--work", action="store_true",
                         help="drop work dirs for everything already decided")
    p_purge.add_argument("--published", action="store_true",
                         help="drop final.mp4 for videos already on YouTube")
    p_purge.add_argument("--images", action="store_true",
                         help="drop the generated-image cache")

    args = ap.parse_args()

    if args.cmd == "list":
        rows = list_pending(args.channel)
        if not rows:
            print("queue empty")
        for r in rows:
            meta_path = Path(r["_dir"]) / "metadata.json"
            title = _load(meta_path)["title"] if meta_path.exists() else "(no metadata)"
            extra = f"  (work purged: {r['work_purged']})" if r.get("work_purged") else ""
            print(f"[{r['status']:<9}] {r['channel']}/{r['slug']}{extra}")
            print(f"             {title}")
            print(f"             {r['_dir']}")

    elif args.cmd == "disk":
        report = disk_report()
        for key in ("work", "review", "image_cache"):
            print(f"  {key:<12} {human(report[key])}")
        print(f"  {'total':<12} {human(report['total'])}")

    elif args.cmd == "purge":
        if not any((args.work, args.published, args.images)):
            ap.error("choose at least one of --work / --published / --images")
        freed = 0
        if args.work:
            for row in list_pending():
                if row.get("status") in (STATUS_APPROVED, STATUS_REJECTED, STATUS_PUBLISHED):
                    got = purge_work(row["channel"], row["slug"])
                    if got:
                        freed += got
                        print(f"  work    {row['channel']}/{row['slug']}  {human(got)}")
        if args.published:
            for row in list_pending():
                if row.get("status") == STATUS_PUBLISHED:
                    got = purge_published(row["channel"], row["slug"])
                    if got:
                        freed += got
                        print(f"  video   {row['channel']}/{row['slug']}  {human(got)}")
        if args.images:
            cache = PATHS.root / "assets" / "generated"
            if cache.is_dir():
                got = _dir_size(cache)
                shutil.rmtree(cache)
                freed += got
                print(f"  images  {human(got)}")
        print(f"freed {human(freed)}")

    else:
        target = STATUS_APPROVED if args.cmd == "approve" else STATUS_REJECTED
        result = set_status(args.channel, args.slug, target, args.note,
                            purge=not args.keep)
        line = f"{result['channel']}/{result['slug']} -> {result['status']}"
        if result.get("work_purged"):
            line += f"   (freed {result['work_purged']})"
        print(line)
