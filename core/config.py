"""Project paths and channel config loading."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    channels: Path = ROOT / "channels"
    assets: Path = ROOT / "assets"
    music: Path = ROOT / "assets" / "music"
    fonts: Path = ROOT / "assets" / "fonts"
    work: Path = ROOT / "out" / "work"
    review: Path = ROOT / "out" / "review"
    published: Path = ROOT / "out" / "published"
    state: Path = ROOT / "state"

    def job_dir(self, channel: str, slug: str) -> Path:
        d = self.work / channel / slug
        d.mkdir(parents=True, exist_ok=True)
        return d


PATHS = Paths()

# Silence padded around narration. Here rather than in assemble.py because the
# script stage has to predict the finished runtime before anything is rendered,
# and a duration estimate that disagrees with the renderer is worse than none.
TAIL_PAD = 0.45     # after every beat, so a cut never clips the last word
HEAD_PAD = 0.35     # before the first word
OUTRO_PAD = 1.2     # after the last


class ConfigError(RuntimeError):
    pass


def load_channel(name: str) -> dict:
    """Load channels/<name>.yaml and validate the keys the engine depends on."""
    path = PATHS.channels / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"no channel config at {path}")
    with path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    required = ["name", "format", "voice", "video"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ConfigError(f"{path.name} missing required keys: {', '.join(missing)}")

    cfg["_slug"] = name
    cfg.setdefault("auto_publish", False)
    cfg.setdefault("privacy", "private")
    return cfg


def _find_tool(name: str, env_var: str) -> str:
    """Resolve an ffmpeg-family binary without depending on shell PATH.

    winget installs into a versioned directory and only exposes an App Execution
    Alias, which is not always visible to non-interactive subprocesses.
    """
    override = os.environ.get(env_var)
    if override:
        return override

    found = shutil.which(name)
    if found:
        return found

    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        matches = sorted(packages.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"))
        if matches:
            return str(matches[-1])

    return name  # let the caller fail with a clear ffmpeg error


def ffmpeg_bin() -> str:
    return _find_tool("ffmpeg", "FFMPEG_BIN")


_ENCODER_CACHE: dict[str, str] = {}


def pick_encoder(preferred: str, fallback: str = "libx264") -> str:
    """Return `preferred` if it actually opens, else `fallback`.

    Listing an encoder in `-encoders` does not mean it will run: NVENC in
    particular is gated on the NVIDIA driver exposing a new enough API version,
    so a working ffmpeg build can still fail at encode time. Probing once and
    caching keeps the pipeline portable across machines.
    """
    if preferred in _ENCODER_CACHE:
        return _ENCODER_CACHE[preferred]

    if preferred == fallback:
        _ENCODER_CACHE[preferred] = preferred
        return preferred

    probe = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=0.2",
         "-c:v", preferred, "-pix_fmt", "yuv420p", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    ok = probe.returncode == 0
    chosen = preferred if ok else fallback
    if not ok:
        reason = (probe.stderr or "").strip().splitlines()
        detail = reason[0] if reason else "encoder failed to open"
        print(f"[encoder] {preferred} unavailable ({detail}); using {fallback}")
    _ENCODER_CACHE[preferred] = chosen
    return chosen


def ffprobe_bin() -> str:
    return _find_tool("ffprobe", "FFPROBE_BIN")
