"""Cross-checked against standard Indian SIP/EMI calculators."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import fincalc as f

def approx(a, b, tol=0.01):
    return abs(a - b) / b < tol

# SIP: 10k/month, 12% p.a., 20y -> ~Rs 99.9 lakh (annuity-due)
fv = f.sip_future_value(10_000, 12, 20)
assert approx(fv, 9_991_479), fv
print(f"SIP 10k/12%/20y      = {f.format_inr(fv)}  (expect ~Rs 99.91 L)")

# Zero rate degenerates to plain contributions
assert f.sip_future_value(1000, 0, 10) == 120_000

# Rule of 72
assert f.rule_of_72(12) == 6.0
ex = f.exact_doubling_years(12)
assert approx(ex, 6.116), ex
print(f"Rule of 72 @12%      = 6.0y approx, {ex:.2f}y exact")

# EMI: 50L, 8.5%, 20y -> ~Rs 43,391/month
sched = f.emi_schedule(5_000_000, 8.5, 20)
assert approx(sched.emi, 43_391), sched.emi
assert approx(sched.total_paid, sched.emi * 240)
assert len(sched.principal_by_year) == 20
# Interest dominates early, principal late - the whole point of the visual
assert sched.interest_by_year[0] > sched.principal_by_year[0]
assert sched.principal_by_year[-1] > sched.interest_by_year[-1]
print(f"EMI 50L/8.5%/20y     = Rs {sched.emi:,.0f}/mo, interest {f.format_inr(sched.total_interest)}")

# compound_series endpoint must equal the closed-form SIP value
ser = f.compound_series(monthly=10_000, annual_rate=12, years=20)
assert len(ser) == 241
assert approx(ser[-1][1], fv), (ser[-1][1], fv)
print(f"series endpoint      = {f.format_inr(ser[-1][1])}  (matches closed form)")

# Compound must beat simple on identical cashflows
simp = f.simple_interest_series(monthly=10_000, annual_rate=12, years=20)
assert ser[-1][1] > simp[-1][1]
print(f"simple interest same = {f.format_inr(simp[-1][1])}  (compound wins)")

# Fee drag: 1% on 12% over 20y
fd = f.fee_drag(10_000, 12, 20, 1.0)
assert fd["cost"] > 0 and 10 < fd["cost_pct"] < 25
print(f"1% fee for 20y costs = {f.format_inr(fd['cost'])} ({fd['cost_pct']:.1f}% of corpus)")

# Real return is Fisher, not naive subtraction
rr = f.real_return(12, 6)
assert approx(rr, 5.660), rr
assert rr < 6.0
print(f"real return 12%/6%   = {rr:.2f}% (naive subtraction says 6.00%)")

# Indian numbering
assert f.format_inr(15_000_000) == "Rs 1.50 Cr"
assert f.format_inr(250_000) == "Rs 2.50 L"

print("\nALL FINCALC TESTS PASS")
