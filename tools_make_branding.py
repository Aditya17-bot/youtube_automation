"""Generate a channel avatar and banner locally, with the wordmark drawn on top.

Uses core.imagegen (local SD 1.5) for the imagery and Pillow for the text. The
split is deliberate: SD 1.5 cannot spell, so asking it for "GymPeak" returns
confident gibberish. The model makes the picture, PIL makes the letters.

    python tools_make_branding.py --name GYMPEAK --tagline "daily lifts, daily gains"
    python tools_make_branding.py --name GYMPEAK --seed 42 --avatar-only

Writes to assets/generated/branding/. Upload them by hand in YouTube Studio ->
Customisation -> Branding; nothing here touches the account.

Banner geometry is YouTube's: the file is 2048x1152, but only the middle
1546x423 is guaranteed visible on every device, so the wordmark stays inside it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from core import imagegen  # noqa: E402
from core.config import PATHS  # noqa: E402

OUT = PATHS.assets / "generated" / "branding"
BANNER = (2048, 1152)
SAFE = (1546, 423)
AVATAR = 800

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]

AVATAR_PROMPT = (
    "dramatic low-angle photo of a loaded olympic barbell on a gym floor, "
    "heavy bumper plates, chalk dust in the air, dark gym, single hard rim light"
)
BANNER_PROMPT = (
    "wide dark gym interior, rows of dumbbells and a squat rack in shadow, "
    "volumetric light from high windows, dust in the beams, moody, cinematic"
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Cover-crop to `size` without distorting."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _text(draw: ImageDraw.ImageDraw, xy, s: str, font, fill="#FFFFFF",
          outline="#000000", w: int = 6, anchor: str = "mm") -> None:
    x, y = xy
    for dx in range(-w, w + 1):
        for dy in range(-w, w + 1):
            if dx * dx + dy * dy <= w * w:
                draw.text((x + dx, y + dy), s, font=font, fill=outline, anchor=anchor)
    draw.text((x, y), s, font=font, fill=fill, anchor=anchor)


def make_avatar(name: str, seed: int) -> Path:
    res = imagegen.generate(AVATAR_PROMPT, size=(512, 512), seed=seed, steps=28)
    img = _fit(Image.open(res.path).convert("RGB"), (AVATAR, AVATAR))

    # Darken so white letters hold at 32px, which is how big this actually
    # renders next to a Short in the feed.
    img = Image.blend(img, Image.new("RGB", img.size, "#000000"), 0.42)

    draw = ImageDraw.Draw(img)
    short = name[:3].upper()
    _text(draw, (AVATAR // 2, AVATAR // 2), short, _font(360), w=10)

    dest = OUT / "avatar.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def make_banner(name: str, tagline: str, seed: int) -> Path:
    res = imagegen.generate(BANNER_PROMPT, size=(768, 432), seed=seed, steps=28)
    img = _fit(Image.open(res.path).convert("RGB"), BANNER)
    img = img.filter(ImageFilter.GaussianBlur(2))
    img = Image.blend(img, Image.new("RGB", img.size, "#000000"), 0.5)

    draw = ImageDraw.Draw(img)
    cx, cy = BANNER[0] // 2, BANNER[1] // 2

    # Everything below stays inside the 1546x423 safe box, centred.
    _text(draw, (cx, cy - 40), name.upper(), _font(190), w=8)
    if tagline:
        _text(draw, (cx, cy + 110), tagline.lower(), _font(56),
              fill="#C9C4BC", w=4)

    dest = OUT / "banner.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="GYMPEAK")
    ap.add_argument("--tagline", default="daily lifts, daily gains")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--avatar-only", action="store_true")
    ap.add_argument("--banner-only", action="store_true")
    args = ap.parse_args()

    print(f"device: {imagegen.describe_device()}")
    if not args.banner_only:
        print(f"avatar: {make_avatar(args.name, args.seed)}")
    if not args.avatar_only:
        print(f"banner: {make_banner(args.name, args.tagline, args.seed + 1)}")
    print("\nupload by hand: Studio -> Customisation -> Branding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
