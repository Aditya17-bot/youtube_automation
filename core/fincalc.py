"""Verified financial maths.

The script model declares *what* to show; these functions produce the numbers.
Never let a language model compute compound growth - it is confidently wrong at
it, and a wrong figure on a finance channel is unrecoverable.

Conventions:
  - `annual_rate` is a percentage (12 means 12% p.a.), not a fraction.
  - SIP future value uses annuity-due (contribution at period start), matching
    how Indian SIP calculators quote it.
  - All series are monthly and returned as (month_index, value) pairs.
"""
from __future__ import annotations

from dataclasses import dataclass


def _monthly_rate(annual_rate: float) -> float:
    return annual_rate / 100.0 / 12.0


def sip_future_value(monthly: float, annual_rate: float, years: float) -> float:
    """FV of a monthly SIP, contributions at the start of each month."""
    i = _monthly_rate(annual_rate)
    n = round(years * 12)
    if n <= 0:
        return 0.0
    if i == 0:
        return monthly * n
    return monthly * (((1 + i) ** n - 1) / i) * (1 + i)


def lumpsum_future_value(principal: float, annual_rate: float, years: float) -> float:
    return principal * (1 + annual_rate / 100.0) ** years


def compound_series(
    principal: float = 0.0,
    monthly: float = 0.0,
    annual_rate: float = 12.0,
    years: float = 20.0,
) -> list[tuple[int, float]]:
    """Monthly (month, balance) series for a lump sum plus monthly SIP."""
    i = _monthly_rate(annual_rate)
    n = round(years * 12)
    out: list[tuple[int, float]] = []
    balance = principal
    for m in range(n + 1):
        if m > 0:
            balance = (balance + monthly) * (1 + i)  # annuity-due
        out.append((m, balance))
    return out


def simple_interest_series(
    principal: float = 0.0,
    monthly: float = 0.0,
    annual_rate: float = 12.0,
    years: float = 20.0,
) -> list[tuple[int, float]]:
    """Same cashflows, but interest never earns interest. The contrast video."""
    i = _monthly_rate(annual_rate)
    n = round(years * 12)
    out: list[tuple[int, float]] = []
    contributed = principal
    interest = 0.0
    for m in range(n + 1):
        if m > 0:
            interest += contributed * i  # interest on principal only
            contributed += monthly
        out.append((m, contributed + interest))
    return out


def invested_series(
    principal: float = 0.0, monthly: float = 0.0, years: float = 20.0
) -> list[tuple[int, float]]:
    """What you actually put in - the baseline that makes growth legible."""
    n = round(years * 12)
    return [(m, principal + monthly * m) for m in range(n + 1)]


def rule_of_72(annual_rate: float) -> float:
    """Approximate years to double."""
    if annual_rate <= 0:
        raise ValueError("annual_rate must be positive")
    return 72.0 / annual_rate


def exact_doubling_years(annual_rate: float) -> float:
    from math import log

    return log(2) / log(1 + annual_rate / 100.0)


@dataclass
class EMISchedule:
    emi: float
    total_paid: float
    total_interest: float
    principal_by_year: list[float]
    interest_by_year: list[float]


def emi_schedule(principal: float, annual_rate: float, years: float) -> EMISchedule:
    """Equated monthly instalment plus the yearly principal/interest split."""
    i = _monthly_rate(annual_rate)
    n = round(years * 12)
    if n <= 0:
        raise ValueError("years must be positive")
    emi = principal * n if i == 0 else principal * i * (1 + i) ** n / ((1 + i) ** n - 1)
    if i == 0:
        emi = principal / n

    balance = principal
    p_year: list[float] = []
    i_year: list[float] = []
    p_acc = i_acc = 0.0
    for m in range(1, n + 1):
        interest = balance * i
        princ = emi - interest
        balance -= princ
        p_acc += princ
        i_acc += interest
        if m % 12 == 0 or m == n:
            p_year.append(p_acc)
            i_year.append(i_acc)
            p_acc = i_acc = 0.0

    total = emi * n
    return EMISchedule(emi, total, total - principal, p_year, i_year)


def fee_drag(monthly: float, annual_rate: float, years: float, fee_pct: float) -> dict:
    """What an expense ratio costs, as a corpus difference."""
    gross = sip_future_value(monthly, annual_rate, years)
    net = sip_future_value(monthly, annual_rate - fee_pct, years)
    return {"gross": gross, "net": net, "cost": gross - net,
            "cost_pct": (gross - net) / gross * 100 if gross else 0.0}


def real_return(nominal_pct: float, inflation_pct: float) -> float:
    """Fisher equation - not the naive subtraction most people use."""
    return ((1 + nominal_pct / 100) / (1 + inflation_pct / 100) - 1) * 100


def purchasing_power(amount: float, inflation_pct: float, years: float) -> float:
    return amount / (1 + inflation_pct / 100) ** years


def cost_of_waiting(monthly: float, annual_rate: float, years: float, delay_years: float) -> dict:
    on_time = sip_future_value(monthly, annual_rate, years)
    delayed = sip_future_value(monthly, annual_rate, years - delay_years)
    return {"on_time": on_time, "delayed": delayed, "gap": on_time - delayed}


def format_inr(value: float, *, short: bool = True) -> str:
    """Indian numbering: lakh / crore, which is what the audience reads in."""
    if not short:
        s = f"{value:,.0f}"
        return f"Rs {s}"
    a = abs(value)
    if a >= 1e7:
        return f"Rs {value / 1e7:.2f} Cr".replace(".00", "")
    if a >= 1e5:
        return f"Rs {value / 1e5:.2f} L".replace(".00", "")
    if a >= 1e3:
        return f"Rs {value / 1e3:.1f}K".replace(".0", "")
    return f"Rs {value:.0f}"
