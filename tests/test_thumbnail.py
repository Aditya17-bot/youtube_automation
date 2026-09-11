"""Thumbnail prompt bank: variety, ordering, and the CLIP token budget."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import thumbnail
from core.config import load_channel

ch = load_channel("story_short")
cfg = ch["thumbnail"]
style = cfg["style"]

# 1. Variety: a month of three-a-day must not repeat the same picture.
prompts = [thumbnail.pick_prompt(ch, {"topic_id": f"t{i:03d}"}) for i in range(90)]
unique = len(set(prompts))
assert unique > 70, f"only {unique} distinct prompts in 90 topics"

# 2. Deterministic: the same topic always renders the same thumbnail.
assert thumbnail.pick_prompt(ch, {"topic_id": "t007"}) == thumbnail.pick_prompt(ch, {"topic_id": "t007"})

# 3. Axes independent - a subject must not be welded to one setting.
by_subject: dict[str, set[str]] = {}
for i in range(200):
    parts = thumbnail.pick_parts(ch, {"topic_id": f"t{i:03d}"})
    by_subject.setdefault(parts[0], set()).add(parts[-1])
assert max(len(v) for v in by_subject.values()) > 3, "settings do not vary within a subject"

# 4. CLIP truncates at 77 tokens and drops the rest silently. ~1.4 tokens per
#    word is the usual ratio for prose like this, so 50 words is the ceiling.
for i in range(200):
    words = len(", ".join(thumbnail.pick_parts(ch, {"topic_id": f"t{i:03d}"}, style)).split())
    assert words <= 50, f"prompt is {words} words, past the CLIP budget"

assert len(thumbnail.FACE_NEGATIVE_PORTRAIT.split()) <= 50, "face negative past the CLIP budget"

# 5. Ordering: subject first, room last, so truncation costs the least.
parts = thumbnail.pick_parts(ch, {"topic_id": "t001"}, style)
assert parts[0] in cfg["subjects"]
assert parts[-1] in cfg["settings"]
assert style in parts

print(f"OK  {unique}/90 distinct prompts")
