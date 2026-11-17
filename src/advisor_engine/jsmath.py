"""Small numeric helpers with the FIS skeleton's JavaScript semantics, so ported code adds,
rounds and reads numbers the way the skeleton does.

Why not the standard library:
- `sum()` on floats is compensated (Neumaier) from Python 3.12, so it can differ from
  JavaScript's left-to-right `reduce` in the last bits. `total()` adds left to right.
- `round()` rounds half to even; `Math.round` rounds half up (towards +infinity).
- `float("1_000")`, `float("inf")` and `float(True)` all succeed; the skeleton's `n2`
  treats input the way JavaScript's unary `+` does.

NaN stands for "cannot be worked out", as in the skeleton. The JSON layer turns it into
null.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Iterable

NAN = float("nan")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def total(values: Iterable[float]) -> float:
    """Left-to-right sum from 0, like `a.reduce((s, v) => s + v, 0)`."""
    s = 0
    for v in values:
        s += v
    return s


def mean(values) -> float:
    values = list(values)
    return total(values) / len(values) if values else NAN


def sd(values) -> float:
    """Sample standard deviation (n - 1); NaN below two values."""
    values = list(values)
    if len(values) < 2:
        return NAN
    m = mean(values)
    return math.sqrt(total((v - m) * (v - m) for v in values) / (len(values) - 1))


def quant(values, p: float) -> float:
    """The p-quantile by linear interpolation between order statistics (type 7)."""
    s = sorted(values)
    if not s:
        return NAN
    i = (len(s) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def clamp(v: float, a: float, b: float) -> float:
    """`Math.max(a, Math.min(b, v))`; NaN stays NaN, as in JavaScript."""
    if isinstance(v, float) and math.isnan(v):
        return NAN
    return max(a, min(b, v))


def fin(v) -> bool:
    """A finite number (booleans are not numbers here, as in JavaScript)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def finite(v) -> bool:
    """`isFinite(v)` on something already a number (NaN and infinities are not)."""
    return isinstance(v, (int, float)) and math.isfinite(v)


_JS_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def n2(v) -> float:
    """A number from loose input, or NaN: the skeleton's `n2`.

    null, undefined and "" are NaN. Otherwise JavaScript's unary plus: booleans are 1 and
    0, a blank string is 0, a numeric string is its number (hex too), anything else is
    NaN, and an infinite result is NaN.
    """
    if v is None or v == "":
        return NAN
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else NAN
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
            return float(int(s, 16))
        if _JS_NUMBER.fullmatch(s):
            x = float(s)
            return x if math.isfinite(x) else NAN
        return NAN
    if isinstance(v, list) and len(v) <= 1:
        return n2(v[0]) if v else 0.0
    return NAN


def js_or(v, default):
    """`v || default`: JavaScript's falsy values (NaN, 0, "", null, false) give the default.

    Python's `or` treats NaN as true, which is why this exists."""
    if v is None or v is False or v == "" or (isinstance(v, (int, float)) and (v != v or v == 0)):
        return default
    return v


def or_zero(v: float) -> float:
    """`v || 0`."""
    return js_or(v, 0)


def js_round(v: float) -> float:
    """`Math.round`: half rounds up, towards +infinity."""
    if not math.isfinite(v):
        return v
    return float(math.floor(v + 0.5))


def parse_day(s) -> date:
    """`parseDay`: "2026-11-03" (or "2026-11", the 1st) as a calendar date."""
    p = [int(x) for x in str(s).split("-")]
    return date(p[0], p[1], p[2] if len(p) > 2 and p[2] else 1)


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def month_label(key: str) -> str:
    """"2026-11" -> "Nov 2026" (a label, not a figure: years are not checked figures)."""
    y, m = str(key).split("-")[:2]
    return f"{MONTHS[int(m) - 1]} {y}"


def plain(obj):
    """A JSON-ready copy: dates as YYYY-MM-DD, NaN and infinities as None, tuples as lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items() if not callable(v)}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj
