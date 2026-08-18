import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import compliance as c

# --- lag guard ---
today = dt.date(2026, 8, 18)
try:
    c.assert_lagged(dt.date(2026, 8, 1), today=today)
    raise AssertionError("should have rejected 17-day-old data")
except c.ComplianceError as e:
    print("rejected recent data:", str(e)[:70])

c.assert_lagged(dt.date(2026, 1, 1), today=today)   # 229 days: fine
print("accepted 229-day-old data")
assert c.max_allowed_end_date(today) == dt.date(2026, 5, 10)

# --- phrase linter: must CATCH these ---
bad = [
    "You should buy at 1500 and hold.",
    "My target of 2,400 is realistic.",
    "This gives guaranteed returns every year.",
    "Keep a stop loss at 980 on this trade.",
    "It will hit 3000 by December.",
    "This is a sure shot multibagger.",
    "I recommend buying now.",
    "Sell below 200 to protect capital.",
]
for text in bad:
    r = c.lint_script(text)
    assert not r.ok, f"MISSED: {text}"
print(f"caught all {len(bad)} advice phrases")

# --- must NOT flag legitimate education ---
good = [
    "Compound interest means your interest starts earning interest too.",
    "An SIP simply invests a fixed amount every month.",
    "If it grew at 12 percent a year, the maths looks like this.",
    "Inflation quietly reduces what your savings can buy.",
    "A higher expense ratio leaves less of the return with you.",
    "The Rule of 72 tells you roughly how long money takes to double.",
    "Your EMI is split between interest and principal.",
    "Buy things you can afford, and keep an emergency fund.",
]
for text in good:
    r = c.lint_script(text)
    assert r.ok, f"FALSE POSITIVE: {text} -> {r.violations}"
print(f"passed all {len(good)} legitimate education lines")
print("\nALL COMPLIANCE TESTS PASS")
