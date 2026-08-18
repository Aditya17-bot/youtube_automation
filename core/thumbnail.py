"""Thumbnails: a generated background plus bold text.

A thumbnail competes at about 210 px wide in a feed, so it is composed for that
size first: three or four words, very large, high contrast, and a dark scrim so
the text never has to fight the image behind it.

Backgrounds come from a per-channel prompt bank rather than a model call, picked
deterministically from the topic id. That gives variety across videos, keeps a
channel visually coherent, and costs nothing extra.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from core import imagegen

SIZE_LANDSCAPE = (1280, 720)
SIZE_VERTICAL = (720, 1280)

# SD 1.5 renders faces poorly at small sizes, and a mangled face is worse than
# no face. These push hard against the usual failure modes.
FACE_NEGATIVE = (
    "deformed face, distorted face, asymmetric eyes, extra fingers, "
    "malformed hands, blurry face, uncanny, disfigured, mutated, "
    "text, watermark, logo, caption, letters"
)

_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
]


def _bold_font_path() -> str:
    for candidate in _BOLD_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # matplotlib always ships DejaVu, so this is a guaranteed fallback.
    import matplotlib

    return str(Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans-Bold.ttf")


def pick_prompt(channel: dict, script: dict) -> str:
    """Choose a background prompt deterministically from the topic."""
    cfg = channel.get("thumbnail", {})
    prompts = cfg.get("prompts") or []
    if not prompts:
        # Fall back to the video's own opening shot when the channel has one.
        for section in script.get("sections", []):
            for beat in section.get("beats", []):
                if beat.get("image_prompt"):
                    return beat["image_prompt"]
        return "abstract dark textured background, cinematic lighting"

    seed = script.get("topic_id") or script.get("title", "")
    idx = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(prompts)
    return prompts[idx]


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale and centre-crop so the image fills `size` without distortion."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.LANCZOS)
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _scrim(size: tuple[int, int], strength: float, from_bottom: bool) -> Image.Image:
    """Vertical gradient used to guarantee text contrast."""
    w, h = size
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = (y / max(1, h - 1)) if from_bottom else (1 - y / max(1, h - 1))
        grad.putpixel((0, y), int(255 * strength * (t ** 1.6)))
    return grad.resize(size)


def _vignette(size: tuple[int, int], strength: float = 0.55) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse([-w * 0.25, -h * 0.25, w * 1.25, h * 1.25], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=min(w, h) * 0.16))
    return Image.eval(mask, lambda v: int(255 - v * strength))


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def headline_for(script: dict) -> str:
    """Short, punchy text for the thumbnail - not the full title.

    Prefers the script's own `thumb` line. The fallbacks break at a clause
    boundary rather than a word count, because slicing the first N words of a
    sentence produces things like "THE GUEST IN ROOM 12 CHECKED".
    """
    thumb = (script.get("thumb") or "").strip()
    if thumb:
        return thumb

    for section in script.get("sections", []):
        for beat in section.get("beats", []):
            visual = beat.get("visual")
            if isinstance(visual, dict) and visual.get("type") in ("text_card", "title_card"):
                head = (visual.get("spec") or {}).get("headline")
                if head:
                    return head

    source = script.get("hook") or script.get("title", "")
    clause = re.split(r"[,.;:!?]", source)[0].strip()
    if 0 < len(clause.split()) <= 7:
        return clause
    return script.get("title", "").strip()


def render(script: dict, channel: dict, dest: Path) -> Path:
    style = channel.get("style", {})
    cfg = channel.get("thumbnail", {})
    video = channel.get("video", {})
    vertical = video.get("height", 1080) > video.get("width", 1920)
    size = SIZE_VERTICAL if vertical else SIZE_LANDSCAPE
    W, H = size

    prompt = pick_prompt(channel, script)
    gen_size = imagegen.SIZE_9_16 if vertical else imagegen.SIZE_16_9
    result = imagegen.generate(
        prompt,
        size=gen_size,
        style=cfg.get("style", ""),
        negative=imagegen.NEGATIVE + ", " + FACE_NEGATIVE,
        steps=30,
    )

    base = Image.open(result.path).convert("RGB")
    base = _cover(base, size)

    # Slightly richer than the in-video look: thumbnails are seen for a moment.
    base = ImageEnhance.Color(base).enhance(1.12)
    base = ImageEnhance.Contrast(base).enhance(1.10)

    base.putalpha(_vignette(size))
    canvas = Image.new("RGB", size, style.get("background", "#0E1116"))
    canvas.paste(base, (0, 0), base)

    scrim = Image.new("RGB", size, "#000000")
    canvas.paste(scrim, (0, 0), _scrim(size, cfg.get("scrim", 0.82), from_bottom=True))

    draw = ImageDraw.Draw(canvas)
    font_path = _bold_font_path()
    text = headline_for(script).upper()

    margin = int(W * 0.07)
    max_w = W - margin * 2
    max_text_h = int(H * (0.46 if vertical else 0.52))

    # Shrink until the block fits both the width and the space allowed for it.
    size_px = int(H * (0.115 if vertical else 0.155))
    while size_px > 18:
        font = ImageFont.truetype(font_path, size_px)
        lines = _wrap(text, font, max_w, draw)
        line_h = int(size_px * 1.06)
        if len(lines) <= 4 and line_h * len(lines) <= max_text_h:
            break
        size_px -= 4

    accent = style.get("accent", "#4ADE80")
    text_col = style.get("text", "#FFFFFF")
    block_h = line_h * len(lines)
    y = H - int(H * 0.085) - block_h

    bar_y = y - int(H * 0.035)
    draw.rounded_rectangle(
        [margin, bar_y, margin + int(W * 0.13), bar_y + max(5, int(H * 0.011))],
        radius=6, fill=accent,
    )

    for line in lines:
        # Heavy offset shadow: cheaper than a stroke and reads better when the
        # thumbnail is scaled down to feed size.
        draw.text((margin + 4, y + 5), line, font=font, fill="#000000")
        draw.text((margin, y), line, font=font, fill=text_col)
        y += line_h

    name_font = ImageFont.truetype(font_path, max(14, int(H * 0.028)))
    draw.text((margin, int(H * 0.055)), channel["name"].upper(),
              font=name_font, fill=style.get("muted", "#8B949E"))

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=92)

    # YouTube rejects thumbnails over 2 MB.
    if dest.stat().st_size > 2_000_000:
        canvas.save(dest, quality=80, optimize=True)
    return dest


if __name__ == "__main__":
    import argparse
    import json

    from core.config import load_channel

    ap = argparse.ArgumentParser(description="render a thumbnail from a script")
    ap.add_argument("--script", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    sc = json.loads(Path(args.script).read_text(encoding="utf-8"))
    ch = load_channel(args.channel)
    out = Path(args.out) if args.out else Path(args.script).parent / "thumbnail.png"
    print(f"prompt: {pick_prompt(ch, sc)[:90]}")
    print(f"text  : {headline_for(sc)}")
    print(f"-> {render(sc, ch, out)}")
