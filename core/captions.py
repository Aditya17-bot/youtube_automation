"""Caption generation from edge-tts word timings.

Timings come from the TTS engine itself (WordBoundary events), so they are
exact rather than inferred - no forced alignment, no Whisper, no GPU.

Output is ASS rather than SRT because ASS carries the styling, which keeps the
look consistent instead of depending on a player's defaults.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{primary},{outline},{back},0,0,0,0,100,100,0,0,1,{outline_w},0,2,120,120,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass
class Cue:
    start: float
    end: float
    text: str


def hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """ASS colours are &HAABBGGRR - alpha first, then reversed RGB."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:;])\s+")
_WORD = re.compile(r"\b[\w']+\b")


def words_per_sentence(text: str) -> list[int]:
    """Word counts for each sentence in `text`.

    Needed because edge-tts WordBoundary events carry the bare word with no
    punctuation, so a cue splitter working only on the events cannot see where
    sentences end. Counting against the source text recovers that.
    """
    counts = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        n = len(_WORD.findall(sentence))
        if n:
            counts.append(n)
    return counts


def sentence_breaks(words: list, text: str, offset: int = 0) -> set[int]:
    """Indices of words that end a sentence, or empty if the counts disagree.

    A mismatch means the engine tokenised differently than we did (numbers and
    abbreviations are the usual causes); silently emitting misplaced breaks
    would be worse than emitting none.
    """
    counts = words_per_sentence(text)
    if sum(counts) != len(words):
        return set()
    breaks: set[int] = set()
    idx = offset
    for n in counts:
        idx += n
        breaks.add(idx - 1)
    return breaks


def group_words(
    words: list, max_chars: int = 42, max_gap: float = 0.65, breaks: set[int] | None = None
) -> list[Cue]:
    """Pack words into short lines.

    Breaks on length, on a natural pause, and at any index in `breaks` - which
    is how sentence endings get respected. Without them a cue happily straddles
    a full stop, producing fragments like "behind the table Good if".
    """
    breaks = breaks or set()
    cues: list[Cue] = []
    buf: list = []

    def flush() -> None:
        if buf:
            cues.append(Cue(buf[0].start, buf[-1].end, " ".join(w.text for w in buf)))
            buf.clear()

    for i, w in enumerate(words):
        candidate = len(" ".join(x.text for x in buf)) + 1 + len(w.text)
        gap = w.start - buf[-1].end if buf else 0.0
        if buf and (candidate > max_chars or gap > max_gap):
            flush()
        buf.append(w)
        if i in breaks:
            flush()
    flush()
    return cues


def write_ass(
    cues: list[Cue],
    out_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    font: str = "Segoe UI",
    size: int = 46,
    primary: str = "#E6EDF3",
    outline: str = "#0E1116",
    margin_v: int = 90,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    body = ASS_HEADER.format(
        width=width,
        height=height,
        font=font,
        size=size,
        primary=hex_to_ass(primary),
        outline=hex_to_ass(outline),
        back=hex_to_ass(outline, alpha="80"),
        outline_w=3,
        margin_v=margin_v,
    )
    lines = [
        f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Default,,0,0,0,,{c.text}"
        for c in cues
    ]
    out_path.write_text(body + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path
