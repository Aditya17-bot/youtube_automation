"""SEBI compliance guards for the finance channel.

Two independent gates:

1. `assert_lagged` - SEBI bars unregistered finfluencers from using live or recent
   market data even in educational content. Any price series must end at least
   LAG_DAYS in the past. This fails hard rather than warning, because a silent
   pass here is a regulatory problem, not a rendering bug.
2. `lint_script`  - rejects narration containing buy/sell calls, price targets,
   or return claims, which are investment advice regardless of framing.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

LAG_DAYS = 100

DISCLAIMER_OPEN = (
    "Educational content only. This is not investment advice and no securities "
    "are being recommended."
)
DISCLAIMER_CLOSE = (
    "This video is for education only. It is not investment advice, not a "
    "recommendation to buy or sell any security, and the creator is not a "
    "SEBI-registered analyst or adviser. Markets carry risk; consult a "
    "registered adviser before investing."
)

# Each pattern is (regex, human explanation). Word boundaries matter: "target"
# alone is fine in "targeting a strike price" but not as "target of 1500".
_BANNED: list[tuple[str, str]] = [
    (r"\bbuy\s+(?:at|above|below|near|around)\b", "buy call"),
    (r"\bsell\s+(?:at|above|below|near|around)\b", "sell call"),
    (r"\b(?:go|going)\s+long\s+(?:on|in)\s+[A-Z]{2,}", "directional call on a named security"),
    (r"\bshort\s+(?:this|the)\s+stock\b", "directional call"),
    (r"\btargets?\s+(?:of\s+)?(?:rs\.?\s*)?[\d,]+", "price target"),
    (r"\bstop\s*loss\s+(?:at|of)\s+(?:rs\.?\s*)?[\d,]+", "trade instruction"),
    (r"\bguaranteed?\s+(?:returns?|profits?|income)\b", "return guarantee"),
    (r"\bassured\s+(?:returns?|profits?)\b", "return guarantee"),
    (r"\b(?:will|going\s+to)\s+(?:hit|reach|touch|cross)\s+(?:rs\.?\s*)?[\d,]+", "price prediction"),
    (r"\bmultibagger\b", "return claim"),
    (r"\bsure\s*shot\b", "return claim"),
    (r"\b\d+\s*%\s+(?:returns?|profits?)\s+(?:in|within|guaranteed)", "return claim"),
    (r"\brisk[\s-]*free\s+(?:trade|profit|returns?)\b", "return claim"),
    (r"\bmy\s+(?:call|recommendation)\s+is\b", "recommendation"),
    (r"\b(?:recommend|suggesting)\s+(?:buying|selling)\b", "recommendation"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), why) for pat, why in _BANNED]


class ComplianceError(RuntimeError):
    """Raised when content would violate SEBI rules. Never caught silently."""


@dataclass
class LintResult:
    ok: bool
    violations: list[tuple[str, str]] = field(default_factory=list)

    def raise_if_bad(self) -> None:
        if not self.ok:
            detail = "; ".join(f"{snippet!r} ({why})" for snippet, why in self.violations)
            raise ComplianceError(f"script contains investment advice: {detail}")


def lint_script(text: str) -> LintResult:
    """Scan narration for investment-advice language."""
    found: list[tuple[str, str]] = []
    for rx, why in _COMPILED:
        for m in rx.finditer(text):
            found.append((m.group(0).strip(), why))
    return LintResult(ok=not found, violations=found)


def assert_lagged(end_date: _dt.date, *, today: _dt.date | None = None) -> None:
    """Reject any market-data window ending too close to the present."""
    today = today or _dt.date.today()
    age = (today - end_date).days
    if age < LAG_DAYS:
        raise ComplianceError(
            f"market data ends {end_date.isoformat()} ({age}d ago); "
            f"SEBI lag requires at least {LAG_DAYS}d. Use conceptual visuals "
            f"(payoff diagrams, mechanics) instead of recent price data."
        )


def max_allowed_end_date(today: _dt.date | None = None) -> _dt.date:
    """Latest date a price series is permitted to include."""
    today = today or _dt.date.today()
    return today - _dt.timedelta(days=LAG_DAYS)
