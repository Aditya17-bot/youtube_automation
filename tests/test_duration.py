"""Videos must not overrun the ceiling their channel declares.

A story went out at 8 minutes against a config that said 4-5. Nothing was
enforcing it: `duration_range` was interpolated into the prompt as a request
and never checked, and the word-count validator's slack (+160 on a 700 word
target) allowed 860 words, which is over 6 minutes on its own.

The estimate here is words over the voice's measured delivery rate, plus the
silence padded around every beat - 28 beats carry nearly 13 seconds that no
word count can see.
"""
import sys, pathlib, json, re

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import script as sc
from core.config import HEAD_PAD, OUTRO_PAD, TAIL_PAD, load_channel
from core.schedule import all_channels

story = load_channel("story")

# --- the estimate ----------------------------------------------------------
wpm = sc.words_per_minute(story)
assert wpm == 134, f"story voice measured at 134 wpm, config says {wpm}"

bare = sc.estimate_seconds(134, 0, story)
assert abs(bare - (60 + HEAD_PAD + OUTRO_PAD)) < 0.01, bare
print(f"a minute of words at {wpm:.0f} wpm estimates one minute, plus head and outro")

# Beat count has to move the answer; the original bug was invisible to words.
padded = sc.estimate_seconds(134, 28, story)
assert abs(padded - bare - 28 * TAIL_PAD) < 0.01
assert padded - bare > 12, "28 beats should add over 12 seconds of silence"
print(f"28 beats add {padded - bare:.1f}s that a word count alone never sees")

# --- the script that actually shipped --------------------------------------
real = pathlib.Path("out/review/story/antikythera/script.json")
if real.exists():
    data = json.loads(real.read_text(encoding="utf-8"))
    beats = [b for s in data["sections"] for b in s.get("beats", [])]
    words = len(re.findall(r"\b[\w']+\b", " ".join(b["vo"] for b in beats)))
    got = sc.estimate_seconds(words, len(beats), story)
    assert got / 60 > 7, f"the 8-minute video should estimate over 7 min, got {got / 60:.1f}"
    try:
        sc.check_duration(words, len(beats), story)
        raise AssertionError(f"{words} words over {len(beats)} beats must be rejected")
    except ValueError as exc:
        assert "min" in str(exc) and "words" in str(exc), exc
    print(f"the shipped 8-minute script ({words} words) estimates "
          f"{got / 60:.1f} min and is now rejected")

# --- the message has to be actionable, since a model reads it and retries ---
try:
    sc.check_duration(1200, 30, story)
except ValueError as exc:
    msg = str(exc)
    assert "Cut roughly" in msg and re.search(r"Cut roughly \d+ words", msg), msg
    print(f"rejection tells the model what to do: \"{msg.split('. ')[-1]}\"")

# --- the top of each declared range must fit under that channel's ceiling ---
# This is the regression the whole thing exists for: a config that asks for
# more words than its own ceiling allows is the bug, not the script.
for name in all_channels():
    try:
        cfg = load_channel(name)
    except Exception:
        continue
    scr = cfg.get("script") or {}
    if not scr.get("duration_range") or not scr.get("target_words"):
        continue
    hi_words = scr["target_words"][1]
    if scr.get("beats"):
        hi_beats = scr["beats"][1]
    elif scr.get("items"):
        # The product format is one title beat, the item beats, and an outro.
        hi_beats = scr["items"][1] + 2
    else:
        hi_beats = 26
    cap = float(scr["duration_range"][1])
    got = sc.estimate_seconds(hi_words, hi_beats, cfg)
    assert got <= cap, (f"{name}: {hi_words} words over {hi_beats} beats estimates "
                        f"{got:.0f}s but the ceiling is {cap:.0f}s")
    sc.check_duration(hi_words, hi_beats, cfg)
    print(f"{name:16} {hi_words:>4} words, {hi_beats:>2} beats -> "
          f"{got:5.1f}s under its {cap:.0f}s ceiling")

# Shorts stop being Shorts at 60 seconds.
for name in all_channels():
    cfg = load_channel(name)
    if (cfg.get("video") or {}).get("height", 0) <= (cfg.get("video") or {}).get("width", 1):
        continue
    cap = float((cfg["script"])["duration_range"][1])
    assert cap < 60, f"{name} is vertical but allows {cap}s; over 60 it is not a Short"
    print(f"{name:16} vertical, ceiling {cap:.0f}s, stays a Short")

# --- a channel that declares no ceiling is not forced into one -------------
sc.check_duration(99999, 500, {"script": {}})
print("a config with no duration_range is left alone")

print("\nALL DURATION TESTS PASS")
