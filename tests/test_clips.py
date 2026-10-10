"""The clip format's contract: the hook, the writing rules, and the cut.

Two of these guard things that are invisible until the video is finished and
wrong: a hook too long to fit its card, and narration that describes the picture
instead of the story.
"""
import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import footage
from core.config import load_channel
from core.theme import Theme
from formats import clips

CHANNEL = load_channel("clips_short")
assert CHANNEL["format"] == "clips"

SHOTS = [{"id": f"film#{i:04d}", "source": "film", "start": 100.0 + i * 8,
          "end": 106.0 + i * 8, "used_by": None} for i in range(8)]
state = {"horror": {"bank": "horror", "sources": {"film": {
    "title": "A Film (1932)", "shots": copy.deepcopy(SHOTS)}}}}
footage.load_index = lambda bank: copy.deepcopy(state[bank])
footage.save_index = lambda bank, data: state.__setitem__(bank, data)


def script(hook="I have been dead for six days", tail=None):
    beats = [{"vo": hook}] + [
        {"vo": t} for t in (tail or [
            "The house did not look like this when I lived in it. The stairs "
            "are on the wrong side now.",
            "Someone moves my things while I sleep, one at a time, and always "
            "somewhere I would have chosen myself.",
            "I counted the doors on the landing last night. There were four, "
            "the way there have been for a year.",
            "This morning there are five, and the fifth one is warm. I have "
            "not opened it.",
            "I no longer check whether the others are asleep, because the last "
            "time I looked, one of them looked back.",
        ])
    ]
    return {
        "topic_id": "t1",
        "title": "The Fifth Door",
        "hook": hook,
        "thumb": "Five Doors",
        "description": "A short piece of original horror fiction. " * 3,
        "tags": [f"tag{i}" for i in range(10)],
        "sections": [
            {"id": "open", "beats": beats[:1]},
            {"id": "build", "beats": beats[1:3]},
            {"id": "turn", "beats": beats[3:5]},
            {"id": "close", "beats": beats[5:]},
        ],
    }


def refuses(data, expect):
    clips._PLANNED["t1"] = copy.deepcopy(SHOTS)
    try:
        clips.validate_script(data, CHANNEL)
    except (ValueError, KeyError, TypeError) as exc:
        assert expect in str(exc), f"wrong reason for {expect!r}: {exc}"
        return
    raise AssertionError(f"accepted a script it should refuse: {expect}")


# --- a good script passes, and comes back with footage attached ----------
clips._PLANNED["t1"] = copy.deepcopy(SHOTS)
good = script()
clips.validate_script(good, CHANNEL)
beats = [b for s in good["sections"] for b in s["beats"]]
assert all(b["shot"]["source"] == "film" for b in beats)
assert [b["shot"]["id"] for b in beats] == [s["id"] for s in SHOTS[:len(beats)]]
# Spent, so no second video can carry the same pictures.
assert all(s["used_by"] == "t1" for s in state["horror"]["sources"]["film"]["shots"][:len(beats)])
assert "t1" not in clips._PLANNED

# --- the hook ------------------------------------------------------------
refuses(script("I woke up in the middle of the night and the light was already on somewhere"),
        "the hook card takes 12")
# Two short sentences are allowed - often the better hook. Three is a paragraph.
clips._PLANNED["t1"] = copy.deepcopy(SHOTS)
clips.validate_script(script("They pay me to sleep. I never sleep."), CHANNEL)
refuses(script("Stop. Listen. He is still there."), "one or two")
refuses(script("Why is the door open?"), "must not be a question")
bad = script()
bad["hook"] = "something else entirely"
refuses(bad, "word for word")

# --- the writing rules ---------------------------------------------------
refuses(script(tail=["You can see the man standing at the end of the hall."] * 5),
        "describes the picture")
refuses(script(tail=["The camera does not move when she does."] * 5),
        "refers to the camera")
refuses(script(tail=["Recovered footage from a house fire in 1961 shows the rest."] * 5),
        "presents the footage as evidence")
refuses(script(tail=["Based on a true story from my grandmother's village."] * 5),
        "fiction presented as fact")

# A script long enough to blow the 55s ceiling is caught while there is still a
# retry left, not after the render.
refuses(script(tail=["It is a very long night and nothing about it is over yet."] * 5
                    + ["Another sentence entirely, padding this out well past the limit."] * 20),
        "beats")

# --- the cut -------------------------------------------------------------
theme = Theme.from_channel(CHANNEL)
assert (theme.width, theme.height, theme.layout) == (1080, 1920, "tall")

for layout in ("tall", "pad", "crop"):
    chain = clips._filter_chain({}, 6.0, theme, stretch=1.0, layout=layout)
    assert "{" not in chain and "}" not in chain, chain   # every placeholder filled
    assert f"s={theme.width}x{theme.height}" in chain     # zoompan output is locked
    assert "tpad=stop_mode=clone" in chain
    assert "setpts" not in chain                          # nothing to stretch
    assert chain.endswith("format=yuv420p")

# tall and pad both composite over a blurred bed; crop fills the frame outright.
assert clips._filter_chain({}, 6.0, theme, 1.0, "tall").count("[bg]") == 2
assert clips._filter_chain({}, 6.0, theme, 1.0, "crop").count("[bg]") == 0

slowed = clips._filter_chain({}, 6.0, theme, stretch=1.45, layout="tall")
assert "setpts=PTS*1.4500" in slowed

# A beat with no footage bound is a bug upstream, and must not quietly render a
# black frame that nobody notices until the video is watched.
try:
    clips.render_beat({}, 4.0, pathlib.Path("nope.mp4"), theme, 3)
except ValueError as exc:
    assert "has no shot" in str(exc)
else:
    raise AssertionError("rendered a beat with no footage")

print("hook, writing rules and cut all hold")

# --- attribution ----------------------------------------------------------
# Public domain owes no credit and CC BY does, but the block goes on every
# video: it is the answer to "where did this picture come from", which is what
# a reused-content report asks.
state["horror"]["sources"]["film"].update(
    {"license": "public-domain", "license_proof": "https://archive.org/details/x"})
note = clips.description_note(good, CHANNEL)
assert "A Film (1932) - public domain" in note, note
assert "https://archive.org/details/x" in note
assert "original" in note
assert clips.description_note({"sections": []}, CHANNEL) == ""
print("footage is credited in the description")

# --- the grade follows the shot ------------------------------------------
# One grade for everything turned the night exteriors into a black rectangle.
assert clips.grade_for({"mean_y": 42}) == clips.GRADE_DARK
assert clips.grade_for({"mean_y": 130}) == clips.GRADE_NORMAL
assert clips.grade_for({}) == clips.GRADE_NORMAL          # unknown: assume daylight
assert "brightness" not in clips.GRADE_DARK               # never pull an already-dim shot down
print("night shots get their own grade")
