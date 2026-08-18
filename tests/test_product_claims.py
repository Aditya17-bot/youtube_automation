"""The Shorts format must reject claims it cannot support."""
import sys, pathlib, copy, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.config import load_channel
from core.script import load_format
from core import affiliate

CH = load_channel("product")
FMT = load_format("product")
GOOD = json.loads(pathlib.Path("out/work/product/desk-tidy/script.json").read_text(encoding="utf-8"))

FMT.validate_script(GOOD, CH)
print("accepted the real generated Short")

def expect_reject(mutate, why):
    d = copy.deepcopy(GOOD)
    mutate(d)
    try:
        FMT.validate_script(d, CH)
    except Exception as e:
        print(f"  rejected {why}: {type(e).__name__}")
        return
    raise AssertionError(f"should have rejected {why}")

def set_vo(text):
    return lambda d: d["sections"][0]["beats"].__setitem__(
        1, {**d["sections"][0]["beats"][1], "vo": text})

expect_reject(set_vo("This is the best cable clip you can buy."), "unsupported superlative")
expect_reject(set_vo("Costs about Rs 499 on Amazon right now."),   "invented price")
expect_reject(set_vo("It has 4.5 stars from buyers."),             "invented rating")
expect_reject(set_vo("Over 12,000 reviews say it works."),         "invented review count")
expect_reject(set_vo("I tested this for three months."),           "unverifiable personal claim")
expect_reject(set_vo("Guaranteed to fix your cable mess."),        "guarantee")
expect_reject(lambda d: d["items"].pop(),                          "items/beats count mismatch")
expect_reject(lambda d: d["items"][0].__setitem__("search_query", ""), "empty search_query")

# Links must carry the tag when configured, and never before.
ch_untagged = load_channel("product")
url = affiliate.link_for(GOOD["items"][0], ch_untagged)
assert "tag=" not in url, url
ch_tagged = dict(ch_untagged)
ch_tagged["affiliate"] = {"associate_tag": "demo-21", "marketplace": "in"}
assert "tag=demo-21" in affiliate.link_for(GOOD["items"][0], ch_tagged)
print("affiliate tag applied only when configured")

# An ASIN, once available, must produce a direct product link.
item = dict(GOOD["items"][0]); item["asin"] = "B08XYZ1234"
assert "/dp/B08XYZ1234" in affiliate.link_for(item, ch_tagged)
print("ASIN produces a direct product link")

assert affiliate.DISCLOSURE in affiliate.description_block("desk-tidy", GOOD, ch_tagged)
print("disclosure always present in description")
print("\nALL PRODUCT CLAIM TESTS PASS")
