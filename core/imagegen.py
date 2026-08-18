"""Local Stable Diffusion image generation, tuned for a 4 GB GPU.

Everything here is chosen around the VRAM ceiling of an RTX 3050 Laptop:
SD 1.5 at fp16 with model CPU offload and sliced attention/VAE.

Measured on that card at 768x432, 26 steps (steady state, after warm-up):

    offload   12.3 s/image   peak 2.26 GB   <- default
    resident  10.8 s/image   peak 2.97 GB   SD_VRAM_MODE=resident

Resident is only ~14% faster and leaves barely 1.3 GB spare on a card that is
also driving the display, so anything else claiming VRAM mid-run turns into an
OOM. Offload is the default for that reason; set SD_VRAM_MODE=resident when the
GPU is otherwise idle.

Images are generated small and upscaled, because the Ken Burns renderer pans
INSIDE the image - an oversized source is what keeps a moving shot sharp.

Results are cached by prompt hash so re-running a job costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from core.config import PATHS

# SD 1.5 checkpoint. Dreamshaper is a strong general-purpose fine-tune: good at
# cinematic and illustrative scenes without needing elaborate prompting.
DEFAULT_MODEL = "Lykon/dreamshaper-8"

# SD 1.5 was trained at 512px and degrades past roughly 768 in either axis, so
# these stay conservative; the upscaler makes up the resolution afterwards.
SIZE_16_9 = (768, 432)
SIZE_9_16 = (432, 768)
SIZE_1_1 = (512, 512)

NEGATIVE = (
    "text, watermark, signature, caption, letters, words, logo, "
    "lowres, blurry, jpeg artifacts, deformed, disfigured, extra limbs, "
    "extra fingers, mutated hands, bad anatomy, ugly, duplicate, frame, border"
)

_PIPE = None
_PIPE_MODEL: str | None = None


@dataclass
class GenResult:
    path: Path
    prompt: str
    seed: int
    cached: bool


def cache_dir() -> Path:
    d = PATHS.root / "assets" / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(prompt: str, negative: str, size: tuple[int, int], seed: int, steps: int, model: str) -> str:
    raw = json.dumps([prompt, negative, list(size), seed, steps, model], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def describe_device() -> str:
    try:
        import torch
    except ImportError:
        return "torch not installed"
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory / 1e9
        return f"cuda: {name} ({total:.1f} GB)"
    return "cpu (no CUDA) - generation will be very slow"


def get_pipeline(model: str = DEFAULT_MODEL):
    """Load the diffusion pipeline once, configured for a small GPU."""
    global _PIPE, _PIPE_MODEL
    if _PIPE is not None and _PIPE_MODEL == model:
        return _PIPE

    import torch
    from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

    cuda = torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32

    pipe = StableDiffusionPipeline.from_pretrained(
        model,
        torch_dtype=dtype,
        safety_checker=None,       # a false positive silently returns a black frame
        requires_safety_checker=False,
    )
    # Fewer steps for the same quality than the default scheduler.
    #
    # algorithm_type is forced: several SD 1.5 fine-tunes ship a scheduler
    # config declaring `deis`, which is incompatible with the default
    # final_sigmas_type and raises at construction time.
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(
        pipe.scheduler.config,
        algorithm_type="dpmsolver++",
        use_karras_sigmas=True,
    )

    if cuda:
        # Offload keeps only the active submodule resident, which is what makes
        # SD 1.5 fit comfortably in 4 GB. See the module docstring for numbers.
        if os.environ.get("SD_VRAM_MODE", "offload").lower() == "resident":
            pipe = pipe.to("cuda")
        else:
            pipe.enable_model_cpu_offload()
        pipe.enable_attention_slicing()
        # pipe.enable_vae_slicing() is deprecated and removed in diffusers 0.40.
        pipe.vae.enable_slicing()
    else:
        pipe = pipe.to("cpu")

    pipe.set_progress_bar_config(disable=True)
    _PIPE, _PIPE_MODEL = pipe, model
    return pipe


def upscale(path: Path, factor: int = 3) -> Path:
    """Enlarge with Lanczos and a light sharpen.

    Deliberately dependency-free: the Real-ESRGAN Python package pulls basicsr,
    which breaks against current torchvision. Since the camera only pans across
    a fraction of the frame, Lanczos holds up well enough here.
    """
    from PIL import Image, ImageEnhance

    with Image.open(path) as img:
        big = img.resize((img.width * factor, img.height * factor), Image.LANCZOS)
        big = ImageEnhance.Sharpness(big).enhance(1.35)
        dest = path.with_name(f"{path.stem}_x{factor}{path.suffix}")
        big.save(dest, quality=95)
    return dest


# Palettes for the fallback renderer - muted and cinematic, so a placeholder
# reads as a deliberate abstract plate rather than a broken frame.
_PLACEHOLDER_PALETTES = [
    ((14, 17, 22), (38, 54, 66), (122, 141, 148)),
    ((18, 14, 12), (66, 44, 32), (168, 132, 96)),
    ((10, 16, 14), (30, 62, 52), (120, 158, 132)),
    ((16, 12, 20), (58, 38, 74), (154, 122, 176)),
    ((20, 14, 14), (78, 38, 38), (186, 118, 106)),
]

_WARNED = {"placeholder": False}


def _placeholder(full_prompt: str, size: tuple[int, int], dest: Path) -> Path:
    """Deterministic abstract plate, used when diffusion is unavailable."""
    import numpy as np
    from PIL import Image

    digest = hashlib.sha256(full_prompt.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
    lo, mid, hi = _PLACEHOLDER_PALETTES[digest[0] % len(_PLACEHOLDER_PALETTES)]

    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx /= max(w - 1, 1)
    yy /= max(h - 1, 1)

    angle = (digest[1] / 255.0) * np.pi
    ramp = np.cos(angle) * xx + np.sin(angle) * yy
    # np.ptp(), not ndarray.ptp(): the method was removed in NumPy 2.0.
    ramp = (ramp - ramp.min()) / (np.ptp(ramp) or 1)

    cx, cy = digest[2] / 255.0, digest[3] / 255.0
    glow = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / 0.12))

    out = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        base = lo[c] + (mid[c] - lo[c]) * ramp
        out[..., c] = base + (hi[c] - base) * glow * 0.65

    vignette = 1.0 - 0.55 * (((xx - 0.5) ** 2 + (yy - 0.5) ** 2) / 0.5)
    out *= np.clip(vignette, 0, 1)[..., None]
    out += rng.normal(0, 3.0, out.shape)  # grain

    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(dest)
    return dest


def generate(
    prompt: str,
    *,
    size: tuple[int, int] = SIZE_16_9,
    seed: int | None = None,
    steps: int = 26,
    guidance: float = 7.0,
    negative: str = NEGATIVE,
    model: str = DEFAULT_MODEL,
    style: str = "",
    out_dir: Path | None = None,
    allow_placeholder: bool = True,
) -> GenResult:
    """Generate one image, reusing a cached render when the inputs match.

    If diffusion is unavailable or fails, fall back to an abstract plate rather
    than aborting: losing one frame should not destroy a 36-beat render.
    """
    full_prompt = f"{prompt}, {style}".strip(", ") if style else prompt
    if seed is None:
        seed = int(hashlib.sha256(full_prompt.encode("utf-8")).hexdigest()[:8], 16) % (2**31)

    out_dir = out_dir or cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    key = _key(full_prompt, negative, size, seed, steps, model)
    dest = out_dir / f"{key}.png"

    if dest.exists():
        return GenResult(dest, full_prompt, seed, cached=True)

    try:
        import torch
    except ImportError as exc:
        if not allow_placeholder:
            raise
        if not _WARNED["placeholder"]:
            print(f"[imagegen] diffusion unavailable ({exc}); using placeholder plates")
            _WARNED["placeholder"] = True
        return GenResult(_placeholder(full_prompt, size, dest), full_prompt, seed, cached=False)

    try:
        pipe = get_pipeline(model)
    except Exception as exc:  # noqa: BLE001 - download, scheduler and load errors
        if not allow_placeholder:
            raise
        if not _WARNED["placeholder"]:
            print(f"[imagegen] pipeline unavailable ({type(exc).__name__}: {exc}); "
                  "using placeholder plates")
            _WARNED["placeholder"] = True
        return GenResult(_placeholder(full_prompt, size, dest), full_prompt, seed, cached=False)

    generator = torch.Generator(device="cpu").manual_seed(seed)
    try:
        image = pipe(
            prompt=full_prompt,
            negative_prompt=negative,
            width=size[0],
            height=size[1],
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=generator,
        ).images[0]
    except Exception as exc:  # noqa: BLE001 - OOM, CUDA faults, model errors
        if not allow_placeholder:
            raise
        print(f"[imagegen] generation failed ({type(exc).__name__}); placeholder for this beat")
        return GenResult(_placeholder(full_prompt, size, dest), full_prompt, seed, cached=False)

    image.save(dest)
    return GenResult(dest, full_prompt, seed, cached=False)


def generate_many(prompts: list[str], **kwargs) -> list[GenResult]:
    results = []
    for i, p in enumerate(prompts, 1):
        r = generate(p, **kwargs)
        flag = "cached" if r.cached else "new   "
        print(f"  [{i:02d}/{len(prompts)}] {flag} {p[:64]}")
        results.append(r)
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="local Stable Diffusion smoke test")
    ap.add_argument("--prompt", default="a lone lighthouse on a cliff at dusk, storm clouds, cinematic")
    ap.add_argument("--style", default="cinematic lighting, highly detailed, film grain, 35mm")
    ap.add_argument("--steps", type=int, default=26)
    ap.add_argument("--portrait", action="store_true")
    ap.add_argument("--upscale", type=int, default=0)
    ap.add_argument("--device", action="store_true", help="print device info and exit")
    args = ap.parse_args()

    print(describe_device())
    if args.device:
        raise SystemExit(0)

    import time

    started = time.time()
    res = generate(
        args.prompt,
        size=SIZE_9_16 if args.portrait else SIZE_16_9,
        steps=args.steps,
        style=args.style,
    )
    print(f"{'cached' if res.cached else 'generated'} in {time.time() - started:.1f}s -> {res.path}")
    if args.upscale:
        print(f"upscaled -> {upscale(res.path, args.upscale)}")
