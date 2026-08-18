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

from core.config import PATHS

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PUBLISHED = "published"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def review_dir(channel: str, slug: str) -> Path:
    return PATHS.review / channel / slug


def promote(job_dir: Path, channel: str, slug: str) -> Path:
    """Copy the finished artefacts out of the work dir into the review queue."""
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

    status = {
        "channel": channel,
        "slug": slug,
        "status": STATUS_PENDING,
        "created": _now(),
        "files": copied,
        "source": str(job_dir),
    }
    (dest / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return dest


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def set_status(channel: str, slug: str, status: str, note: str | None = None) -> dict:
    path = review_dir(channel, slug) / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"nothing queued at {path.parent}")
    data = _load(path)
    data["status"] = status
    data["decided"] = _now()
    if note:
        data["note"] = note
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

    args = ap.parse_args()

    if args.cmd == "list":
        rows = list_pending(args.channel)
        if not rows:
            print("queue empty")
        for r in rows:
            meta_path = Path(r["_dir"]) / "metadata.json"
            title = _load(meta_path)["title"] if meta_path.exists() else "(no metadata)"
            print(f"[{r['status']:<9}] {r['channel']}/{r['slug']}")
            print(f"             {title}")
            print(f"             {r['_dir']}")
    else:
        target = STATUS_APPROVED if args.cmd == "approve" else STATUS_REJECTED
        result = set_status(args.channel, args.slug, target, args.note)
        print(f"{result['channel']}/{result['slug']} -> {result['status']}")
