"""Product roundup Shorts: 9:16, 30-60 seconds, built for affiliate clicks.

Two things drive the design.

Most Shorts are watched muted, so every frame carries large on-screen text and
the captions are burned in rather than optional.

And without PA-API access there is no legitimate product data, so items are
described as CATEGORIES with an honest illustrative visual - never a fabricated
photo of a specific product with an invented price. `core.affiliate` links each
item to a category search until real ASINs are available.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core import imagegen
from core.config import ffmpeg_bin, pick_encoder

REVEAL_FPS = 15

VISUAL_CONTRACT = """
Every beat needs a "visual" object, one of:

1. {"type":"title_card","spec":{"headline":"<=6 words","sub":"<=8 words or null"}}
2. {"type":"item","spec":{"index":1,"name":"<=5 words","tagline":"<=8 words",
                          "image_prompt":"one clean product-category still"}}
3. {"type":"outro","spec":{"headline":"<=5 words","sub":"<=8 words"}}

image_prompt rules:
  - Describe a GENERIC product category on a clean studio background.
  - Never name or imply a specific brand, model or logo.
  - No people, no hands, no text in frame.
"""

PROMPT = """You are writing a {dur_lo}-{dur_hi} second vertical YouTube Short for a
channel called "{channel_name}".

Topic: {topic_title}

TONE: {tone}
AUDIENCE: {audience}

Fast, concrete, useful. First sentence must stop the scroll. No filler, no
"hey guys", no sign-off beyond the outro beat.

LENGTH: combined `vo` across all beats must total {w_lo}-{w_hi} words. Count them.

HONESTY RULES (the script is rejected if broken):
  - Recommend CATEGORIES of product, never a specific brand or model.
  - Never invent prices, ratings, review counts, or specifications.
  - Never claim you tested, owned or measured anything.
  - No superlatives you cannot support ("the best", "#1", "guaranteed").
  - Say what a category is genuinely useful FOR, and who it suits.

Structure: one title beat, then {n_lo}-{n_hi} item beats, then one outro beat.

{visual_contract}

Also return a top-level "items" array, one entry per item beat, in the same
order. `search_query` is what a shopper would type into Amazon to find that
category - generic words only, no brand names.

Return ONLY this JSON:
{{
  "topic_id": "{topic_id}",
  "title": "YouTube title, <=70 chars, no emoji, no fake urgency",
  "description": "2-3 sentences for the description, plain text",
  "tags": ["10-14 lowercase youtube tags"],
  "items": [{{"name":"<=5 words","why":"<=18 words","search_query":"generic search words"}}],
  "sections": [
    {{"id":"body","beats":[{{"vo":"...","visual":{{"type":"...","spec":{{}}}}}}]}}
  ]
}}
"""

ALLOWED_VISUALS = {"title_card", "item", "outro"}
SECTION_IDS = ["body"]

BANNED = [
    (r"\b(?:the\s+)?best\b", "unsupported superlative"),
    (r"\b#\s*1\b", "unsupported superlative"),
    (r"\bguarantee(?:d|s)?\b", "guarantee"),
    (r"\bI (?:tested|tried|used|own|bought)\b", "unverifiable personal claim"),
    (r"\bwe (?:tested|tried|reviewed)\b", "unverifiable personal claim"),
    (r"\b\d+(?:\.\d+)?\s*(?:stars?|/5)\b", "invented rating"),
    (r"\b(?:rs\.?|₹|\$)\s*[\d,]+", "invented price"),
    (r"\b\d[\d,]*\s+reviews\b", "invented review count"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in BANNED]


def build_prompt(topic: dict, channel: dict) -> str:
    sc = channel["script"]
    w_lo, w_hi = sc["target_words"]
    dur_lo, dur_hi = sc["duration_range"]
    n_lo, n_hi = sc.get("items", [3, 5])
    return PROMPT.format(
        channel_name=channel["name"],
        topic_title=topic["title"],
        topic_id=topic["id"],
        tone=sc["tone"],
        audience=sc["audience"],
        dur_lo=dur_lo, dur_hi=dur_hi,
        w_lo=w_lo, w_hi=w_hi,
        n_lo=n_lo, n_hi=n_hi,
        visual_contract=VISUAL_CONTRACT,
    )


def validate_script(data: object, channel: dict) -> None:
    if not isinstance(data, dict):
        raise TypeError("top level must be a JSON object")
    for key in ("title", "description", "tags", "items", "sections"):
        if key not in data:
            raise KeyError(f"missing key: {key}")
    if len(data["title"]) > 70:
        raise ValueError(f"title is {len(data['title'])} chars, max 70")
    if not isinstance(data["tags"], list) or not 8 <= len(data["tags"]) <= 16:
        raise ValueError("tags must be a list of 8-16 items")

    ids = [s.get("id") for s in data["sections"]]
    if ids != SECTION_IDS:
        raise ValueError(f"sections must be exactly {SECTION_IDS}, got {ids}")

    beats = [b for s in data["sections"] for b in s.get("beats", [])]
    item_beats = [b for b in beats if b.get("visual", {}).get("type") == "item"]
    n_lo, n_hi = channel["script"].get("items", [3, 5])
    if not n_lo <= len(item_beats) <= n_hi:
        raise ValueError(f"{len(item_beats)} item beats; need {n_lo}-{n_hi}")

    if len(data["items"]) != len(item_beats):
        raise ValueError(
            f"items array has {len(data['items'])} entries but there are "
            f"{len(item_beats)} item beats; they must correspond"
        )

    for i, entry in enumerate(data["items"]):
        for field in ("name", "why", "search_query"):
            if not str(entry.get(field, "")).strip():
                raise ValueError(f"items[{i}] missing {field}")

    for i, beat in enumerate(beats):
        if not beat.get("vo", "").strip():
            raise ValueError(f"beat {i} has empty vo")
        visual = beat.get("visual") or {}
        if visual.get("type") not in ALLOWED_VISUALS:
            raise ValueError(f"beat {i}: visual type {visual.get('type')!r} not allowed")
        if not isinstance(visual.get("spec"), dict):
            raise ValueError(f"beat {i}: visual.spec must be an object")
        if visual["type"] == "item" and not visual["spec"].get("image_prompt"):
            raise ValueError(f"beat {i}: item visual needs an image_prompt")

    spoken = " ".join(b["vo"] for b in beats)
    words = len(re.findall(r"\b[\w']+\b", spoken))
    w_lo, w_hi = channel["script"]["target_words"]
    if not w_lo - 25 <= words <= w_hi + 35:
        raise ValueError(f"script is {words} words; need {w_lo}-{w_hi}")

    found = [(m.group(0), why) for rx, why in _COMPILED for m in rx.finditer(spoken)]
    if found:
        detail = "; ".join(f"{t!r} ({w})" for t, w in found)
        raise ValueError(f"script makes unsupportable claims: {detail}")


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _text_frames(th, headline: str, sub: str | None, n_frames: int, accent_first: bool = True):
    figs = []
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        fig = th.figure()
        fig.text(0.5, 0.56, headline, color=th.text, fontsize=104, fontfamily=th.font,
                 fontweight="bold", ha="center", va="center", wrap=True,
                 alpha=min(1.0, p * 1.8))
        if sub:
            fig.text(0.5, 0.44, sub, color=th.muted, fontsize=48, fontfamily=th.font,
                     ha="center", va="center", alpha=max(0.0, min(1.0, (p - 0.2) * 2.2)))
        w = 0.10 * min(1.0, p * 1.4)
        fig.add_artist(plt.Line2D([0.5 - w, 0.5 + w], [0.37, 0.37],
                                  color=th.accent if accent_first else th.accent_alt,
                                  linewidth=8, solid_capstyle="round"))
        figs.append(fig)
    return figs


def _item_clip(beat: dict, duration: float, out_path: Path, th) -> Path:
    """Generated image on top, big label beneath - readable with sound off."""
    spec = beat["visual"]["spec"]
    style = getattr(th, "image_style", "") or ""
    result = imagegen.generate(spec["image_prompt"], size=imagegen.SIZE_1_1, style=style)

    overlay = out_path.parent / f"{out_path.stem}_overlay.png"
    fig = th.figure()
    fig.text(0.5, 0.30, spec.get("name", ""), color=th.text, fontsize=96,
             fontfamily=th.font, fontweight="bold", ha="center", va="center")
    fig.text(0.5, 0.225, spec.get("tagline", ""), color=th.muted, fontsize=46,
             fontfamily=th.font, ha="center", va="center")
    if spec.get("index"):
        # Above the image band: the overlay covers roughly 0.48-0.90 of the
        # frame height, so anything inside that range is hidden.
        fig.text(0.5, 0.945, f"#{spec['index']}", color=th.accent, fontsize=104,
                 fontfamily=th.font, fontweight="bold", ha="center", va="center")
    fig.savefig(overlay, facecolor=th.bg, dpi=100, transparent=False)
    plt.close(fig)

    # Slow push-in on the still keeps a static product shot from feeling dead.
    img_h = int(th.height * 0.42)
    vf = (
        f"[1:v]scale={th.width}:{th.width},"
        f"zoompan=z='min(1.0+0.0009*on,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={th.width}x{img_h}:fps={th.fps}[img];"
        f"[0:v][img]overlay=0:{int(th.height * 0.10)}:shortest=1,format=yuv420p[v]"
    )
    subprocess.run(
        [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(overlay),
            "-loop", "1", "-i", str(result.path),
            "-t", f"{duration:.3f}",
            "-filter_complex", vf, "-map", "[v]",
            "-r", str(th.fps),
            "-c:v", pick_encoder("libx264"), "-preset", "medium", "-crf", "19",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    return out_path


def render_beat(beat: dict, duration: float, out_path: Path, th) -> Path:
    visual = beat["visual"]
    key_src = json.dumps({"v": visual, "d": round(duration, 3),
                          "s": [th.width, th.height]}, sort_keys=True)
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:16]
    sidecar = out_path.with_suffix(".key")
    if out_path.exists() and sidecar.exists() and sidecar.read_text(encoding="utf-8") == key:
        return out_path

    if visual["type"] == "item":
        _item_clip(beat, duration, out_path, th)
        sidecar.write_text(key, encoding="utf-8")
        return out_path

    spec = visual["spec"]
    reveal = min(1.1, max(0.4, duration * 0.6))
    n_frames = max(2, int(reveal * REVEAL_FPS))
    figs = _text_frames(th, spec.get("headline", ""), spec.get("sub"), n_frames,
                        accent_first=visual["type"] == "title_card")

    frame_dir = out_path.parent / f"{out_path.stem}_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.png"):
        old.unlink()
    for idx, fig in enumerate(figs):
        fig.savefig(frame_dir / f"f{idx:05d}.png", facecolor=th.bg, dpi=100)
        plt.close(fig)

    hold = max(0.0, duration - n_frames / REVEAL_FPS)
    last = frame_dir / f"f{n_frames - 1:05d}.png"
    subprocess.run(
        [
            ffmpeg_bin(), "-y", "-loglevel", "error",
            "-framerate", str(REVEAL_FPS), "-i", str(frame_dir / "f%05d.png"),
            "-loop", "1", "-t", f"{hold:.3f}", "-i", str(last),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-r", str(th.fps),
            "-c:v", pick_encoder("libx264"), "-preset", "medium", "-crf", "19",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    sidecar.write_text(key, encoding="utf-8")
    return out_path
