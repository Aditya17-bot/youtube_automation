"""Assemble the YouTube-facing metadata and a thumbnail.

The disclaimer is appended here rather than left to the script model, so it is
present on every finance video regardless of what the model wrote.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core import affiliate, compliance
from core.script import iter_beats
from core.theme import Theme

THUMB_W, THUMB_H = 1280, 720


def build_description(script: dict, channel: dict, chapters: list[tuple[float, str]]) -> str:
    parts: list[str] = [script["description"].strip(), ""]

    if script.get("golden_rule"):
        parts.append(f"THE RULE: {script['golden_rule'].strip()}")
        parts.append("")

    if chapters:
        parts.append("CHAPTERS")
        for seconds, label in chapters:
            m, s = divmod(int(seconds), 60)
            parts.append(f"{m}:{s:02d} {label}")
        parts.append("")

    if channel.get("format") == "finance":
        parts.append("DISCLAIMER")
        parts.append(compliance.DISCLAIMER_CLOSE)
        parts.append("")

    if channel.get("format") == "product" and script.get("items"):
        slug = script.get("topic_id") or ""
        parts.append(affiliate.description_block(slug, script, channel))
        parts.append("")

    parts.append(channel.get("cta", f"Subscribe to {channel['name']} for more."))
    return "\n".join(parts).strip()


def build_chapters(script: dict, beat_timings: list[dict]) -> list[tuple[float, str]]:
    """One chapter per section. YouTube requires the first to start at 0:00."""
    labels = {
        "hook": "The question", "lesson": "How it works", "takeaway": "The rule",
        "open": "Where it starts", "build": "What happened", "turn": "The turn",
        "close": "What it means",
    }
    chapters: list[tuple[float, str]] = []
    idx = 0
    for section in script["sections"]:
        n = len(section.get("beats", []))
        if n and idx < len(beat_timings):
            start = 0.0 if not chapters else beat_timings[idx]["start"]
            chapters.append((start, labels.get(section["id"], section["id"])))
        idx += n
    return chapters


def render_thumbnail(script: dict, channel: dict, dest: Path) -> Path:
    """Big, legible, three words or so - it has to read at phone size."""
    th = Theme.from_channel(channel)
    hook = None
    for beat in iter_beats(script):
        visual = beat.get("visual")
        if not isinstance(visual, dict):
            continue
        spec = visual.get("spec", {})
        if visual.get("type") in ("text_card", "title_card") and spec.get("headline"):
            hook = spec["headline"]
            break
    hook = (hook or script.get("hook") or script["title"]).upper()

    words = hook.split()
    if len(words) > 3:
        mid = (len(words) + 1) // 2
        hook = "\n".join([" ".join(words[:mid]), " ".join(words[mid:])])

    vertical = th.height > th.width
    tw, tht = (720, 1280) if vertical else (THUMB_W, THUMB_H)
    fig = plt.figure(figsize=(tw / 100, tht / 100), dpi=100, facecolor=th.bg)
    text = fig.text(0.5, 0.56, hook, color=th.text, fontsize=104, fontfamily=th.font,
                    fontweight="bold", ha="center", va="center", linespacing=1.15)

    # Shrink to fit rather than trusting a fixed size: a three-word hook at
    # 104pt overflows 1280px, and a clipped thumbnail is a dead thumbnail.
    fig.canvas.draw()
    max_w = tw * 0.90
    for size in range(104, 39, -4):
        text.set_fontsize(size)
        fig.canvas.draw()
        if text.get_window_extent(fig.canvas.get_renderer()).width <= max_w:
            break
    fig.add_artist(plt.Line2D([0.40, 0.60], [0.25, 0.25], color=th.accent,
                              linewidth=9, solid_capstyle="round"))
    fig.text(0.5, 0.15, channel["name"].upper(), color=th.muted, fontsize=30,
             fontfamily=th.font, ha="center", va="center")

    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=th.bg, dpi=100)
    plt.close(fig)
    return dest


def build(script: dict, channel: dict, job_dir: Path, beat_timings: list[dict]) -> dict:
    # Shorts have no chapter UI, so skip them there.
    chapters = [] if channel.get("format") == "product" else build_chapters(script, beat_timings)
    meta = {
        "title": script["title"],
        "description": build_description(script, channel, chapters),
        "tags": script["tags"],
        "categoryId": "27",             # Education
        "privacyStatus": channel.get("privacy", "private"),
        "madeForKids": False,
        "channel": channel["_slug"],
        "topic_id": script.get("topic_id"),
        "golden_rule": script.get("golden_rule"),
        "chapters": [[s, label] for s, label in chapters],
    }

    thumb = render_thumbnail(script, channel, job_dir / "thumbnail.png")
    meta["thumbnail"] = thumb.name

    if channel.get("format") == "product" and script.get("items"):
        meta["items"] = script["items"]
        page = affiliate.write_video_page(script.get("topic_id") or "video", script, channel)
        meta["hub_page"] = page.name

    (job_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


if __name__ == "__main__":
    import argparse

    from core.config import load_channel

    ap = argparse.ArgumentParser(description="build metadata + thumbnail")
    ap.add_argument("--script", required=True)
    ap.add_argument("--channel", default="finance")
    args = ap.parse_args()

    job = Path(args.script).parent
    sc = json.loads(Path(args.script).read_text(encoding="utf-8"))
    ch = load_channel(args.channel)
    timings_file = job / "timings.json"
    timings = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else []

    m = build(sc, ch, job, timings)
    print(f"title: {m['title']}")
    print(f"tags : {len(m['tags'])}")
    print(f"thumb: {job / m['thumbnail']}")
    print()
    print(m["description"])
