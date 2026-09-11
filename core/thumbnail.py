"""Thumbnails: a generated background plus bold text.

A thumbnail competes at about 210 px wide in a feed, so it is composed for that
size first: three or four words, very large, high contrast, and a dark scrim so
the text never has to fight the image behind it.

Backgrounds come from a per-channel prompt bank rather than a model call, picked
deterministically from the topic id. That gives variety across videos, keeps a
channel visually coherent, and costs nothing extra.

The bank can be a flat `prompts` list, or three lists - `subjects`, `settings`,
`lighting` - combined one axis at a time. The combined form is what a face
channel wants: 10 x 8 x 6 is 480 distinct images that are still recognisably the
same channel, so three videos a day do not ship the same picture twice a week.
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

# Negatives for thumbnails whose whole point IS the face. This REPLACES the
# general negative rather than extending it: the two together run past CLIP's
# 77-token limit, and everything past that is discarded silently. What is kept
# is what actually ruins a horror portrait - a mangled face, a cropped head, or
# a glamour shot.
FACE_NEGATIVE_PORTRAIT = (
    "deformed face, asymmetric eyes, extra faces, two heads, cropped head, "
    "out of frame, bad anatomy, extra fingers, blurry, lowres, "
    "cartoon, anime, illustration, 3d render, doll, plastic skin, "
    "glamour, beauty retouch, makeup, smiling, text, watermark, logo"
)

# SD 1.5 puts detail where the pixels are. A 432-wide vertical frame leaves a
# face perhaps 150 px across, which is exactly where it falls apart; 512x768 is
# still inside the model's comfortable range and buys back that detail. The
# extra width is cropped off by `_cover`, which the face survives because it is
# centred.
FACE_SIZE_VERTICAL = (512, 768)
FACE_SIZE_LANDSCAPE = (768, 512)

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


def _axis(seed: str, axis: str, options: list[str]) -> str:
    """Pick one option per axis, each axis hashed separately.

    Salting with the axis name is what keeps the axes independent - hashing the
    topic id once and slicing it would lock subject 3 to setting 3 forever.
    """
    digest = hashlib.sha256(f"{seed}:{axis}".encode("utf-8")).hexdigest()[:8]
    return options[int(digest, 16) % len(options)]


def pick_parts(channel: dict, script: dict, style: str = "") -> list[str]:
    """The prompt as ordered fragments, most important first.

    Order is the whole point: CLIP stops reading at 77 tokens and discards the
    rest silently, so the subject leads, then what the light is doing to her,
    then the style that says "close up, sharp focus on the eyes", and only then
    the room she is standing in - which is the part that can afford to be lost.
    """
    cfg = channel.get("thumbnail", {})
    seed = script.get("topic_id") or script.get("title", "")
    subjects = cfg.get("subjects") or []
    if not subjects:
        return [p for p in (pick_prompt(channel, script), style) if p]

    parts = [_axis(seed, "subject", subjects)]
    lighting = cfg.get("lighting") or []
    if lighting:
        parts.append(_axis(seed, "lighting", lighting))
    if style:
        parts.append(style)
    settings = cfg.get("settings") or []
    if settings:
        parts.append(_axis(seed, "settings", settings))
    return parts


def pick_prompt(channel: dict, script: dict) -> str:
    """Choose a background prompt deterministically from the topic."""
    cfg = channel.get("thumbnail", {})
    seed = script.get("topic_id") or script.get("title", "")

    subjects = cfg.get("subjects") or []
    if subjects:
        return ", ".join(pick_parts(channel, script))

    prompts = cfg.get("prompts") or []
    if not prompts:
        # Fall back to the video's own opening shot when the channel has one.
        for section in script.get("sections", []):
            for beat in section.get("beats", []):
                if beat.get("image_prompt"):
                    return beat["image_prompt"]
        return "abstract dark textured background, cinematic lighting"

    return _axis(seed, "prompt", prompts)


def _cover(img: Image.Image, size: tuple[int, int], anchor_y: float = 0.5) -> Image.Image:
    """Scale and crop so the image fills `size` without distortion.

    `anchor_y` is where the crop keeps its weight vertically: 0.5 is centred,
    lower values bias towards the top of the source. A portrait wants that bias
    - the head is above the middle, and the bottom of the frame is under the
    headline anyway.
    """
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.LANCZOS)
    left = (resized.width - tw) // 2
    top = int(round((resized.height - th) * min(max(anchor_y, 0.0), 1.0)))
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


def _cold_shadows(img: Image.Image) -> Image.Image:
    """Blue-green shadows, faintly warm skin: the standard horror grade.

    Done per channel with a lookup curve rather than a tint overlay, so the
    shadows shift without washing grey over the face itself.
    """
    r = img.split()[0].point(lambda v: min(255, int(v * 1.04)))
    g = img.split()[1].point(lambda v: min(255, int(v * 0.99 + 3)))
    b = img.split()[2].point(lambda v: min(255, int(v * 0.97 + 12)))
    return Image.merge("RGB", (r, g, b))


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

    face = bool(cfg.get("face"))

    if face:
        gen_size = FACE_SIZE_VERTICAL if vertical else FACE_SIZE_LANDSCAPE
    else:
        gen_size = imagegen.SIZE_9_16 if vertical else imagegen.SIZE_16_9

    negative = FACE_NEGATIVE_PORTRAIT if face else imagegen.NEGATIVE + ", " + FACE_NEGATIVE

    style_text = cfg.get("style", "")
    if face:
        prompt, style_text = ", ".join(pick_parts(channel, script, style_text)), ""
    else:
        prompt = pick_prompt(channel, script)

    result = imagegen.generate(
        prompt,
        size=gen_size,
        style=style_text,
        negative=negative,
        # A face gets more of both: steps for the features, guidance so the
        # prompt's specific horror survives instead of drifting to a portrait.
        steps=38 if face else 30,
        guidance=8.5 if face else 7.0,
    )

    base = Image.open(result.path).convert("RGB")
    base = _cover(base, size, anchor_y=cfg.get("anchor_y", 0.28 if face else 0.5))

    if face:
        # Crushed, cold and hard: desaturate towards grey, then push contrast so
        # the highlights on the face stay while everything around it goes black.
        base = ImageEnhance.Color(base).enhance(0.66)
        base = ImageEnhance.Contrast(base).enhance(1.22)
        # Brighter, not darker. A horror grade that crushes everything reads as
        # a black rectangle at 210 px wide; the face has to stay legible in the
        # feed or none of the rest matters.
        base = ImageEnhance.Brightness(base).enhance(1.14)
        base = _cold_shadows(base)
    else:
        # Slightly richer than the in-video look: thumbnails are seen for a moment.
        base = ImageEnhance.Color(base).enhance(1.12)
        base = ImageEnhance.Contrast(base).enhance(1.10)

    base.putalpha(_vignette(size, 0.62 if face else 0.55))
    canvas = Image.new("RGB", size, style.get("background", "#0E1116"))
    canvas.paste(base, (0, 0), base)

    scrim = Image.new("RGB", size, "#000000")
    canvas.paste(scrim, (0, 0), _scrim(size, cfg.get("scrim", 0.82), from_bottom=True))

    draw = ImageDraw.Draw(canvas)
    font_path = _bold_font_path()
    text = headline_for(script).upper()

    margin = int(W * 0.07)
    max_w = W - margin * 2
    # A face has to stay visible, so the headline is given less of the frame
    # here than on a scene thumbnail - three lines at most, not four.
    if face:
        max_text_h = int(H * (0.34 if vertical else 0.40))
        max_lines = 3
        start_px = int(H * (0.098 if vertical else 0.135))
    else:
        max_text_h = int(H * (0.46 if vertical else 0.52))
        max_lines = 4
        start_px = int(H * (0.115 if vertical else 0.155))

    # Shrink until the block fits both the width and the space allowed for it.
    size_px = start_px
    while size_px > 18:
        font = ImageFont.truetype(font_path, size_px)
        lines = _wrap(text, font, max_w, draw)
        line_h = int(size_px * 1.06)
        if len(lines) <= max_lines and line_h * len(lines) <= max_text_h:
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
