"""A bank that refills itself must not refill itself with what it already has.

Three videos a day empties a hand-written bank in three weeks, so the bank is
extended by a model. That makes duplicate detection load-bearing: a repeated
premise is not just dull, it is the templated-output pattern YouTube's
inauthentic-content policy exists to catch. Also covers the other half of a
multi-video day - one run must claim a different topic for each video.
"""
import sys, pathlib, shutil, yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import daily
from core import llm, topicgen
from core.config import PATHS

BANK, CH = "_test_gen", "_test_gen_ch"
bank_path = PATHS.channels / f"topics_{BANK}.yaml"
cfg_path = PATHS.channels / f"{CH}.yaml"
history = PATHS.state / f"history_{CH}.json"
real_ask = llm.ask_json

EXISTING = [
    {"id": "night-shift-motel", "title": "The night clerk at a highway motel"},
    {"id": "last-train", "title": "The last train never reaches the last station"},
]

cfg_path.write_text(yaml.safe_dump({
    "name": "gen test", "format": "story",
    "voice": {"provider": "edge-tts", "name": "x"},
    "video": {"width": 1080, "height": 1920},
    "script": {"topics_from": BANK},
    "cadence": {"per_week": 7, "per_day": 3},
}), encoding="utf-8")

bank_path.write_text(
    "# a comment that must survive being extended\n"
    "brief: >-\n  Test bank.\n\ntopics:\n"
    + "".join(f'  - {{id: {t["id"]}, title: "{t["title"]}"}}\n' for t in EXISTING),
    encoding="utf-8",
)

try:
    # --- near-duplicate detection -----------------------------------------
    fp = topicgen.fingerprint
    assert fp("The last train never reaches the last station") == \
        fp("the LAST train, never reaches a last station!"), "casing/punctuation matter"
    assert topicgen._too_similar(fp("A last train that never reaches its last station"),
                                 [fp(t["title"]) for t in EXISTING]), \
        "a reworded existing title is a duplicate"
    assert not topicgen._too_similar(fp("A lighthouse keeper who answers no radio"),
                                     [fp(t["title"]) for t in EXISTING])
    print("near-duplicate titles are caught even under a fresh id")

    # --- extend ------------------------------------------------------------
    returned = [
        {"id": "night-shift-motel", "title": "Something else entirely at a motel"},
        {"id": "train-last", "title": "The last train that never reaches the last station"},
        {"id": "lighthouse-relief", "title": "The keeper sent to relieve a silent lighthouse"},
        {"id": "toll-booth", "title": "A toll booth where more cars leave than joined"},
        {"id": "toll-booth-two", "title": "A booth where more cars leave the road than joined"},
    ]
    llm.ask_json = lambda prompt, **kw: returned

    added = topicgen.extend(BANK, 10, attempts=1)
    ids = [a["id"] for a in added]
    assert "night-shift-motel" not in ids, "a reused id must be rejected"
    assert "train-last" not in ids, "a reworded existing title must be rejected"
    assert "toll-booth-two" not in ids, "two near-identical new titles: keep one"
    assert ids == ["lighthouse-relief", "toll-booth"], ids
    print(f"extend kept {len(ids)} of {len(returned)}: {', '.join(ids)}")

    text = bank_path.read_text(encoding="utf-8")
    assert "# a comment that must survive being extended" in text, \
        "extending the bank must not rewrite it through yaml.dump"
    assert len(topicgen.load_topics(BANK)) == 4, "additions must parse back as YAML"
    print("comments survive; the file still parses")

    # A bank with room to spare is left alone; no model call, no cost.
    calls = []
    llm.ask_json = lambda prompt, **kw: (calls.append(prompt), [])[1]
    assert topicgen.top_up(CH, days=1) == [], "4 topics at 3/day covers 1 day"
    assert not calls, "a bank with runway must not spend a model call"
    topicgen.top_up(CH, days=14)
    assert calls, "4 topics at 3/day is 1.3 days and must trigger a refill"
    assert "The night clerk at a highway motel" in calls[0], "prompt shows what exists"
    print("top_up only spends a model call when the runway is short")

    # --- one run, three videos, three different topics ---------------------
    llm.ask_json = real_ask
    if history.exists(): history.unlink()
    pairs = daily.build_plan(__import__("datetime").date.today(), [CH], False)
    assert len(pairs) == 3, f"per_day 3 should plan 3 videos, got {pairs}"
    topics = [t for _, t in pairs]
    assert len(set(topics)) == 3, f"a run must not repeat a topic: {topics}"
    print(f"one day plans 3 distinct topics: {', '.join(topics)}")

    # The bank has 4 topics and 5 were asked for: short is fine, repeat is not.
    pairs = daily.build_plan(__import__("datetime").date.today(), [CH], False, per_day=5)
    assert len(pairs) == 4, f"an exhausted bank should stop, not repeat: {pairs}"
    assert len(set(t for _, t in pairs)) == 4
    print("running out mid-day truncates the plan instead of repeating")

    print("\nALL TOPICGEN TESTS PASS")
finally:
    llm.ask_json = real_ask
    for f in (bank_path, cfg_path, history):
        if f.exists(): f.unlink()
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)
