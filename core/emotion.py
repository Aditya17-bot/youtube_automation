"""Emotional direction tags embedded in narration text.

A beat's `vo` carries inline cues that tell the voice how to read what follows:

    [fearful] The door was open. [whispering] She did not remember opening it.

The tags are direction, never speech. They exist only to become Azure
`<mstts:express-as>` spans in `core.voice`, and every other consumer of `vo` -
word counts, captions, duration estimates, format validators - has to see the
text without them. `strip()` is that single point of truth, which is why this
module has no dependencies heavier than `re`: everything on the script path can
import it.

Kept separate from core.voice deliberately. core.script and the format plugins
need the stripping and the vocabulary, and neither should have to pull in a
speech SDK to count words.
"""
from __future__ import annotations

import re

# Deliberately narrow: lowercase words and hyphens only. A caption line like
# "[static]" written as prose, or a bracketed aside the model invents, will not
# match unless it is a bare single token, and an unknown single token is
# reported by `unknown_tags` rather than silently swallowed.
TAG_RE = re.compile(r"\[([a-z][a-z\-]{1,23})\]")

# Azure styles available on the voices this project uses. Not the full catalogue
# - the full list only exists on the HD voices, which bill outside the free
# tier. Keyed by voice short name; the value is what `express-as style` accepts.
#
# Verify against a live key with `python -m core.voice --styles <voice>`, which
# reads the account's voice list rather than trusting this table.
VOICE_STYLES: dict[str, set[str]] = {
    "en-US-DavisNeural": {
        "angry", "chat", "cheerful", "excited", "friendly", "hopeful",
        "sad", "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-GuyNeural": {
        "angry", "cheerful", "excited", "friendly", "hopeful", "newscast",
        "sad", "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-JennyNeural": {
        "angry", "assistant", "chat", "cheerful", "customerservice",
        "excited", "friendly", "hopeful", "newscast", "sad", "shouting",
        "terrified", "unfriendly", "whispering",
    },
    "en-US-AriaNeural": {
        "angry", "chat", "cheerful", "customerservice", "empathetic",
        "excited", "friendly", "hopeful", "narration-professional",
        "newscast-casual", "newscast-formal", "sad", "shouting",
        "terrified", "unfriendly", "whispering",
    },
    "en-US-SaraNeural": {
        "angry", "cheerful", "excited", "friendly", "hopeful", "sad",
        "shouting", "terrified", "unfriendly", "whispering",
    },
}

# What a horror script is allowed to ask for. A subset of what the voice can do:
# the sales-y and service styles are technically available on these voices and
# would be actively wrong in a ghost story, so they are not offered to the model.
#
# Every entry must resolve to a DIFFERENT style, which is why "fearful" is not
# here despite being the obvious word. It is an alias of "terrified", and
# offering both taught the model a three-step escalation - whispering, fearful,
# terrified - that the voice delivered as two. A tag that changes nothing
# audible is worse than no tag: the script reads as directed and is not.
HORROR_STYLES = ["whispering", "terrified", "sad", "unfriendly", "shouting"]

# Some styles are documented under one name and accepted under another, and a
# couple of the obvious words a writer reaches for are not styles at all. Map
# them rather than rejecting a script over a synonym.
ALIASES = {
    "fearful": "terrified",
    "scared": "terrified",
    "afraid": "terrified",
    "whisper": "whispering",
    "quiet": "whispering",
    "calm": "chat",
    "flat": "chat",
    "neutral": "chat",
    "cold": "unfriendly",
    "angry": "angry",
    "shout": "shouting",
    "urgent": "shouting",
}


def styles_for(voice: str) -> set[str]:
    """Styles `voice` accepts, or an empty set if it supports none.

    An empty set is the honest answer for a voice like
    en-US-BrianMultilingualNeural: it renders fine, it just ignores direction.
    """
    return VOICE_STYLES.get(voice, set())


def resolve(tag: str, voice: str) -> str | None:
    """Map a written tag onto a style `voice` actually supports, else None."""
    tag = tag.strip().lower()
    allowed = styles_for(voice)
    if tag in allowed:
        return tag
    alias = ALIASES.get(tag)
    if alias and alias in allowed:
        return alias
    return None


def strip(text: str) -> str:
    """`text` with every direction tag removed and whitespace re-normalised.

    Everything that measures, counts, aligns or displays narration must go
    through here first. A stray "[whispering]" left in the text becomes a word
    in the caption word count, which silently disables sentence breaks for the
    whole beat (see core.captions.sentence_breaks).
    """
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def tags(text: str) -> list[str]:
    """Every tag in `text`, in order, as written."""
    return TAG_RE.findall(text)


def unknown_tags(text: str, voice: str) -> list[str]:
    """Tags in `text` that `voice` cannot honour.

    Used by the format validators so a script that invents "[ominous]" is
    rejected and regenerated rather than quietly read flat.
    """
    return [t for t in tags(text) if resolve(t, voice) is None]


def segments(text: str, voice: str, default: str | None = None) -> list[tuple[str | None, str]]:
    """Split `text` into (style, spoken text) runs.

    Text before the first tag takes `default`. Unresolvable tags fall back to
    `default` too - by the time synthesis runs, validation has already had its
    chance to reject the script, and refusing to speak a beat at that point
    would cost the whole video.
    """
    out: list[tuple[str | None, str]] = []
    style = default
    pos = 0

    for match in TAG_RE.finditer(text):
        chunk = text[pos:match.start()].strip()
        if chunk:
            out.append((style, chunk))
        style = resolve(match.group(1), voice) or default
        pos = match.end()

    tail = text[pos:].strip()
    if tail:
        out.append((style, tail))
    return out
