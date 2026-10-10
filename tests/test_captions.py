"""Cues must not straddle sentence boundaries."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import captions as cap

class W:
    def __init__(self, t, i): self.text = t; self.start = i*0.3; self.end = i*0.3+0.25

TEXT = ("Cable clips. Stick them along the desk edge so chargers stop sliding "
        "behind the table. Good if you plug and unplug daily.")
words = [W(w, i) for i, w in enumerate(cap._WORD.findall(TEXT))]

assert cap.words_per_sentence(TEXT) == [2, 13, 7]
br = cap.sentence_breaks(words, TEXT)
assert sorted(br) == [1, 14, 21], br

cues = cap.group_words(words, max_chars=26, breaks=br)
assert cues[0].text == "Cable clips", cues[0].text
# No cue may contain words from two different sentences.
bounds = [1, 14, 21]
for c in cues:
    idxs = [i for i, w in enumerate(words) if c.start <= w.start <= c.end]
    for b in bounds[:-1]:
        assert not (idxs and idxs[0] <= b < idxs[-1]), f"cue straddles sentence end: {c.text}"
print(f"{len(cues)} cues, none straddle a sentence boundary")

# Mismatched counts must fail safe rather than misplace breaks.
assert cap.sentence_breaks(words[:5], TEXT) == set()
print("count mismatch falls back to no breaks")

# ASS colour conversion is BGR with leading alpha
assert cap.hex_to_ass("#E6EDF3") == "&H00F3EDE6"
print("ASS colour conversion correct")
print("\nALL CAPTION TESTS PASS")

# --- the hook card --------------------------------------------------------
# Held over the opening, with the running captions suppressed underneath: two
# blocks of text on screen at once and a scrolling viewer reads neither.
import tempfile
hook = "I have been dead for six days"
with tempfile.TemporaryDirectory() as tmp:
    path = cap.write_ass(cues, pathlib.Path(tmp) / "c.ass", width=1080, height=1920,
                         font="Arial", hook=hook, hook_hold=2.0)
    text = path.read_text(encoding="utf-8")
events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
assert events[0].startswith("Dialogue: 1,0:00:00.00,0:00:02.00,Hook"), events[0]
# WrapStyle is 2, so libass will not wrap: the card has to carry its own breaks.
assert "WrapStyle: 2" in text
assert r"\N" in events[0], events[0]
for line in events[1:]:
    start = line.split(",")[1]
    assert start >= "0:00:02.00", f"caption runs under the hook card: {line}"

wrapped, size = cap.layout_hook("The fifth door on the landing is warm to the touch", 1080, 67)
assert wrapped.count("\\N") == 2 and size <= 67
assert cap.layout_hook("", 1080, 67) == ("", 67)
print("hook card holds the open and captions wait for it")
