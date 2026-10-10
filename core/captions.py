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
Style: Default,{font},{size},{primary},{primary},{outline},{back},{bold},0,0,0,100,100,0,0,1,{outline_w},0,2,120,120,{margin_v},1
Style: Hook,{font},{hook_size},{primary},{primary},{outline},{hook_back},-1,0,0,0,100,100,0,0,3,{hook_pad},0,5,100,100,0,1

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


# Bold Arial advances roughly half an em per character averaged over mixed-case
# English, outline included. Good enough to choose a size; libass lays it out.
_CHAR_EM = 0.5
_HOOK_LINES = 3


def layout_hook(text: str, width: int, base: int, minimum: int = 44) -> tuple[str, int]:
    """Break the hook into balanced lines and pick a size that fits them.

    The card is broken here with explicit \\N rather than left to libass,
    because the header sets `WrapStyle: 2` - no automatic wrapping - so that a
    caption line never rewraps itself mid-sentence. Under that setting a hook
    long enough to need two lines would otherwise run straight off both edges
    of the frame.

    Lines are balanced rather than greedy: a card reading "I have been dead
    for" / "six" is worse than two even lines, and the hook is the one piece of
    text on the channel that has to be read at a glance.
    """
    words = text.split()
    if not words:
        return "", base

    rows = [text]
    for n in range(1, _HOOK_LINES + 1):
        per = -(-len(words) // n)                 # ceil
        candidate = [" ".join(words[i:i + per]) for i in range(0, len(words), per)]
        rows = candidate
        if max(len(r) for r in candidate) * _CHAR_EM * base <= width - 200:
            break

    size = min(base, int((width - 200) / (max(len(r) for r in rows) * _CHAR_EM)))
    return "\\N".join(rows), max(minimum, size)


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
    bold: bool = False,
    hook: str | None = None,
    hook_hold: float = 2.0,
) -> Path:
    """Write the caption file, optionally opening on a held hook card.

    The card is the whole reason this parameter exists: a Short is decided in
    its first two seconds, and most of that audience is scrolling muted, so the
    opening line has to be READ rather than heard. While the card is up the
    running captions are suppressed - two blocks of text on screen at once and
    neither gets read.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hook_text, hook_size = layout_hook(
        (hook or "").strip(), width, base=int(size * 1.45)
    )
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
        bold=-1 if bold else 0,
        hook_size=hook_size,
        hook_back=hex_to_ass(outline, alpha="A0"),
        hook_pad=18,
    )

    lines = []
    floor = 0.0
    if hook_text:
        floor = hook_hold
        lines.append(
            f"Dialogue: 1,{_ts(0)},{_ts(hook_hold)},Hook,,0,0,0,,{hook_text}"
        )
    for c in cues:
        if c.end <= floor:
            continue
        lines.append(
            f"Dialogue: 0,{_ts(max(c.start, floor))},{_ts(c.end)},Default,,0,0,0,,{c.text}"
        )
    out_path.write_text(body + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path
