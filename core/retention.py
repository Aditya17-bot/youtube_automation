"""Reclaim the disk that finished videos leave behind.

The pipeline already deletes the two big things at the right moment: the work
directory when a job is approved, and final.mp4 once the upload succeeds. What
it never had was a sweep for everything those two miss, and unattended daily
runs turn "misses" into unbounded growth:

- `assets/generated` is content-addressed on the prompt text, and prompts come
  from per-beat script lines. Two videos essentially never share a key, so the
  hit rate across videos is zero - it is a scratch directory wearing a cache's
  clothes. Measured at ~25 MB per video, which is ~19 GB a year at the current
  cadence, all of it dead the moment the render finishes.
- A purge that failed is never retried. One WinError 32 during upload stranded
  a 10 MB mp4 permanently, because nothing looks at published items again.
- Thumbnails stay after YouTube has its own copy.
- A render that dies before promote() leaves its work directory forever, since
  the purge is driven off the review queue and a crashed job never joined it.

Age is read from mtime, never atime: this volume does update atime, so merely
listing the directory - `review disk` does exactly that - would keep dead files
looking fresh forever.

Nothing here touches a pending or approved item. Those are undecided or waiting
to upload, and the whole safety model is that a human decides. Metadata, status
and script json are never deleted at any age, because topic history reads them
and losing them makes the pipeline republish what it already posted.

    python -m core.retention --dry-run
    python -m core.retention --days 7
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from core.config import PATHS
from core.review import (STATUS_PUBLISHED, _dir_size, human, list_pending,
                         purge_published, review_dir)

# Renders finish inside a day. Three covers a weekend re-run of the same topic
# still hitting a warm image cache, and nothing beyond that has any reader.
DEFAULT_DAYS = 3

# Logs are a few KB each and are the only record of an overnight failure, so
# they get kept far longer than the media does.
DEFAULT_LOG_DAYS = 30


def _age_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400.0


def _newest(root: Path) -> float:
    """Age in days of the most recently written file in a tree.

    Directory mtime is not enough: on NTFS it tracks entries added or removed
    in that directory alone, so a job writing frame dumps into a subdirectory
    can look hours stale while ffmpeg is still running.
    """
    times = [f.stat().st_mtime for f in root.rglob("*") if f.is_file()]
    if not times:
        return _age_days(root)
    return (time.time() - max(times)) / 86400.0


def _published_rows() -> list[dict]:
    return [r for r in list_pending() if r.get("status") == STATUS_PUBLISHED]


def sweep(days: int = DEFAULT_DAYS, log_days: int = DEFAULT_LOG_DAYS,
          dry_run: bool = False) -> dict:
    """Delete what nothing reads any more. Returns bytes freed per category."""
    freed = {"stranded": 0, "thumbnails": 0, "images": 0,
             "orphan_work": 0, "logs": 0}
    lines: list[str] = []

    # 1. Retry the post-upload purge. No age gate: YouTube holds the video, and
    #    the only reason a copy is still here is that a delete failed.
    for row in _published_rows():
        final = review_dir(row["channel"], row["slug"]) / "final.mp4"
        if not final.exists():
            continue
        size = final.stat().st_size
        if dry_run:
            freed["stranded"] += size
        else:
            try:
                freed["stranded"] += purge_published(row["channel"], row["slug"])
            except (OSError, ValueError) as exc:
                lines.append(f"  kept    {row['channel']}/{row['slug']}: {exc}")
                continue
        lines.append(f"  video   {row['channel']}/{row['slug']}  {human(size)}")

    # 2. Thumbnails, once the video has been up long enough that a failed
    #    thumbnails.set would have been noticed and fixed by hand.
    for row in _published_rows():
        thumb = review_dir(row["channel"], row["slug"]) / "thumbnail.png"
        if not thumb.exists() or _age_days(thumb) < days:
            continue
        size = thumb.stat().st_size
        if not dry_run:
            try:
                thumb.unlink()
            except OSError as exc:
                lines.append(f"  kept    {thumb}: {exc}")
                continue
        freed["thumbnails"] += size
        lines.append(f"  thumb   {row['channel']}/{row['slug']}  {human(size)}")

    # 3. The image scratch directory, the one that actually grows.
    cache = PATHS.root / "assets" / "generated"
    if cache.is_dir():
        stale = 0
        for f in cache.iterdir():
            if not f.is_file() or _age_days(f) < days:
                continue
            size = f.stat().st_size
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    continue
            freed["images"] += size
            stale += 1
        if stale:
            lines.append(f"  images  {stale} file(s)  {human(freed['images'])}")

    # 4. Work directories from renders that died before promote() could file
    #    them, so the queue-driven purge never sees them.
    if PATHS.work.is_dir():
        for channel_dir in PATHS.work.iterdir():
            if not channel_dir.is_dir():
                continue
            for job in channel_dir.iterdir():
                if not job.is_dir():
                    continue
                if review_dir(channel_dir.name, job.name).exists():
                    continue  # the queue owns this one; approve/publish purges it
                if _newest(job) < days:
                    continue  # possibly still rendering
                size = _dir_size(job)
                if not dry_run:
                    shutil.rmtree(job, ignore_errors=True)
                freed["orphan_work"] += size
                lines.append(f"  orphan  {channel_dir.name}/{job.name}  {human(size)}")

    # 5. Old run logs.
    logs = PATHS.state / "logs"
    if logs.is_dir():
        for f in sorted(logs.glob("daily-*.log")):
            if _age_days(f) < log_days:
                continue
            size = f.stat().st_size
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    continue
            freed["logs"] += size
            lines.append(f"  log     {f.name}  {human(size)}")

    freed["total"] = sum(freed.values())
    freed["_lines"] = lines
    return freed


if __name__ == "__main__":
    import argparse

    from core.review import disk_report

    ap = argparse.ArgumentParser(description="reclaim disk from finished videos")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"keep media this many days (default {DEFAULT_DAYS})")
    ap.add_argument("--log-days", type=int, default=DEFAULT_LOG_DAYS)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would go, delete nothing")
    args = ap.parse_args()

    before = disk_report()
    result = sweep(args.days, args.log_days, args.dry_run)
    for line in result["_lines"]:
        print(line)
    verb = "would free" if args.dry_run else "freed"
    print(f"{verb} {human(result['total'])}")
    if not args.dry_run:
        after = disk_report()
        print(f"  on disk {human(before['total'])} -> {human(after['total'])}")
