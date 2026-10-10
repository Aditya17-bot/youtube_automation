"""The licence gate, and what counts as a usable shot.

The gate is the only thing standing between this channel and a strike, so every
way it can be talked into a download is pinned here.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import footage

cl = footage.classify_license_url

# --- what a licence URL grants -------------------------------------------
assert cl("http://creativecommons.org/licenses/publicdomain/") == "public-domain"
assert cl("https://creativecommons.org/publicdomain/mark/1.0/") == "public-domain"
assert cl("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"
assert cl("https://creativecommons.org/licenses/by/4.0/") == "cc-by"
assert cl("https://creativecommons.org/licenses/by-sa/4.0/") == "cc-by-sa"
assert cl("") is None

# Non-commercial and no-derivatives grant us nothing: a monetised Short is a
# commercial derivative of whatever it was cut from. by-nc-sa in particular
# contains "by-sa" as a substring, which a naive check would wave through.
for refused in ("https://creativecommons.org/licenses/by-nc/4.0/",
                "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                "https://creativecommons.org/licenses/by-nd/4.0/",
                "https://creativecommons.org/licenses/by-nc-nd/4.0/"):
    assert cl(refused) is None, refused


def rejects(entry, meta, expect):
    try:
        footage.verify_license(entry, meta)
    except footage.UnlicensedSource as exc:
        assert expect in str(exc), f"wrong reason: {exc}"
        return
    raise AssertionError(f"accepted what it should refuse: {entry}")


PD_META = {"metadata": {"licenseurl": "http://creativecommons.org/licenses/publicdomain/"}}

# --- the bank's own claim -------------------------------------------------
rejects({"id": "x", "kind": "archive", "ref": "r", "license": "cc-by-nc",
         "license_proof": "https://example.org/x"}, PD_META, "not one of")
rejects({"id": "x", "kind": "archive", "ref": "r", "license": "public-domain",
         "license_proof": "ask me"}, PD_META, "must be a URL")

# --- the host overrules the bank -----------------------------------------
# This is the real case that motivated the second check: archive.org serves
# `TheLastManOnEarth` under CC BY-NC, so a bank written from memory - everyone
# calls that film public domain - would otherwise have downloaded it.
NC_META = {"metadata": {"licenseurl": "https://creativecommons.org/licenses/by-nc/3.0/"}}
GOOD_CLAIM = {"id": "last_man", "kind": "archive", "ref": "TheLastManOnEarth",
              "license": "public-domain",
              "license_proof": "https://archive.org/details/TheLastManOnEarth"}
rejects(GOOD_CLAIM, NC_META, "does not permit a monetised derivative")
rejects(GOOD_CLAIM, {}, "cannot be checked")
rejects({**GOOD_CLAIM, "license": "cc-by"},
        PD_META, "bank claims 'cc-by'")

# CC0 and public domain are the same grant, so the two must not read as a
# conflict when a host labels an item one way and the bank the other.
assert footage.verify_license(
    {**GOOD_CLAIM, "license": "public-domain"},
    {"metadata": {"licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"}},
) == "cc0"

# A source that is simply not in the bank has no licence on record at all.
try:
    footage.source_entry("horror", "some_youtube_rip")
except footage.UnlicensedSource as exc:
    assert "not in" in str(exc)
else:
    raise AssertionError("an unlisted source was accepted")

# --- the shipped bank passes its own rules -------------------------------
for entry in footage.sources("horror"):
    assert entry["license"] in footage.ALLOWED_LICENSES, entry["id"]
    assert str(entry["license_proof"]).startswith("http"), entry["id"]

# --- shot filtering -------------------------------------------------------
ENTRY = {"id": "film", "skip_head": 60, "skip_tail": 60}


def cut(t, y=90, s=30):
    return {"t": t, "mean_y": y, "stdev_y": s}


shots = footage.build_shots(
    [cut(10), cut(20),        # inside skip_head: titles and studio logos
     cut(100),                # -> 4s, kept
     cut(104),                # -> 1.5s, too short to narrate over
     cut(105.5, y=3),         # -> opens on a fade to black
     cut(110, s=2),           # -> flat frame: a card, or an empty sky
     cut(115, y=250),         # -> blown out, a fade to white
     cut(120),                # -> 4s, kept
     cut(124),                # -> a held master, trimmed to MAX_SHOT
     cut(500),                # -> 60s, trimmed
     cut(560),                # -> runs past skip_tail
     cut(900)],               # -> starts inside skip_tail: credits
    duration=940, entry=ENTRY,
)
assert [s["start"] for s in shots] == [100.0, 120.0, 124.0, 500.0], shots
assert [s["end"] for s in shots] == [104.0, 124.0, 164.0, 540.0], shots
assert [s["id"] for s in shots] == [f"film#{i:04d}" for i in range(4)]
assert all(s["used_by"] is None for s in shots)

# --- planning, claiming, releasing ---------------------------------------
INDEX = {"bank": "t", "sources": {"film": {"title": "Film", "shots": [
    {"id": f"film#{i:04d}", "source": "film", "start": 100.0 + i * 5,
     "end": 104.0 + i * 5, "used_by": None} for i in range(10)
]}}}

state = {}
footage.load_index = lambda bank: json.loads(json.dumps(state.setdefault(bank, INDEX)))
footage.save_index = lambda bank, data: state.__setitem__(bank, data)

run = footage.plan("t", 4, seed="the-hallway-light")
assert len(run) == 4
# From one neighbourhood of one film, which is what makes the cuts read as
# coverage of one scene rather than a compilation.
assert run[-1]["end"] - run[0]["start"] <= footage.MAX_SPAN

# A blacklisted shot in the middle must not cost the whole run: dropping the
# classroom shots out of a nine-minute film left no four in a row anywhere.
footage.claim("t", [state["t"]["sources"]["film"]["shots"][2]], "dropped")
gapped = footage.plan("t", 4, seed="x")
assert len(gapped) == 4 and all(s["used_by"] is None for s in gapped)
assert footage.release("t", "dropped") == 1
# Deterministic: the script stage and the validation stage must agree without
# sharing any state.
assert footage.plan("t", 4, seed="the-hallway-light") == run

footage.claim("t", run, "the-hallway-light")
after = footage.plan("t", 4, seed="the-hallway-light")
assert not ({s["id"] for s in after} & {s["id"] for s in run}), "spent footage came back"
assert footage.release("t", "the-hallway-light") == 4
assert footage.plan("t", 4, seed="the-hallway-light") == run

# Nothing left is an error, not an empty list: a video with no pictures is not
# a smaller video, it is a broken one.
footage.claim("t", state["t"]["sources"]["film"]["shots"], "everything")
try:
    footage.plan("t", 4, seed="x")
except footage.NoFootage as exc:
    assert "no run of 4 unused shots" in str(exc)
else:
    raise AssertionError("planned a video out of no footage")

print("licence gate holds; shot filtering and reservation behave")
