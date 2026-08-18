"""Shared visual theme, derived from a channel's YAML.

Lives outside `formats/` so that format plugins do not have to import each
other just to get at the palette.
"""
from __future__ import annotations

from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager


def pick_font() -> str:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Inter", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"):
        if name in available:
            return name
    return "DejaVu Sans"


@dataclass
class Theme:
    bg: str = "#0E1116"
    surface: str = "#161B22"
    text: str = "#E6EDF3"
    muted: str = "#8B949E"
    accent: str = "#4ADE80"
    accent_alt: str = "#F87171"
    grid: str = "#232A33"
    font: str = "DejaVu Sans"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    # Appended to every image prompt so a channel's shots look like one series.
    image_style: str = ""

    @classmethod
    def from_channel(cls, channel: dict) -> "Theme":
        s = channel.get("style", {})
        v = channel.get("video", {})
        return cls(
            bg=s.get("background", cls.bg),
            surface=s.get("surface", cls.surface),
            text=s.get("text", cls.text),
            muted=s.get("muted", cls.muted),
            accent=s.get("accent", cls.accent),
            accent_alt=s.get("accent_alt", cls.accent_alt),
            grid=s.get("grid", cls.grid),
            font=pick_font(),
            width=v.get("width", cls.width),
            height=v.get("height", cls.height),
            fps=v.get("fps", cls.fps),
            image_style=s.get("image_style", ""),
        )

    def figure(self):
        return plt.figure(
            figsize=(self.width / 100, self.height / 100),
            dpi=100,
            facecolor=self.bg,
        )
