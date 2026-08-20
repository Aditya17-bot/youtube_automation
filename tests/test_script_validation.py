"""Lock in the script contract so a bad model reply is retried, not rendered."""
import sys, pathlib, copy, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core.config import load_channel
from core.script import load_format
from core.compliance import ComplianceError

# A real generated script, vendored. It used to be read out of out/work, but
# that directory is purged on approve and on every daily run, so the test died
# the first time the pipeline was actually used.
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "script_finance.json"
GOOD = json.loads(FIXTURE.read_text(encoding="utf-8"))
CH = load_channel("finance")
FMT = load_format("finance")

def expect_reject(data, why):
    try:
        FMT.validate_script(data, CH)
    except Exception as e:
        print(f"  rejected {why}: {type(e).__name__}")
        return
    raise AssertionError(f"should have rejected {why}")

FMT.validate_script(GOOD, CH)
print("accepted the real generated script")

d = copy.deepcopy(GOOD); d["title"] = "x" * 90
expect_reject(d, "over-long title")

d = copy.deepcopy(GOOD); d["tags"] = ["one", "two"]
expect_reject(d, "too few tags")

d = copy.deepcopy(GOOD); d["sections"] = d["sections"][:2]
expect_reject(d, "missing takeaway section")

d = copy.deepcopy(GOOD); d["sections"][1]["beats"][0]["visual"]["type"] = "explosion"
expect_reject(d, "unknown visual type")

d = copy.deepcopy(GOOD)
d["sections"][1]["beats"][0]["visual"] = {"type": "stat", "spec": {"label": "no compute"}}
expect_reject(d, "stat without a compute block")

d = copy.deepcopy(GOOD)
d["sections"][1]["beats"][0]["visual"]["spec"]["compute"] = {"fn": "os.system"}
expect_reject(d, "compute fn outside the whitelist")

d = copy.deepcopy(GOOD)
d["sections"][0]["beats"][0]["vo"] = "Buy at 1500 for a guaranteed return."
expect_reject(d, "investment advice in narration")

d = copy.deepcopy(GOOD); d["sections"][1]["beats"][0]["vo"] = ""
expect_reject(d, "empty voiceover")

print("\nALL SCRIPT VALIDATION TESTS PASS")
