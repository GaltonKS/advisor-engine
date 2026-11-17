"""Small numeric helpers with the FIS skeleton's JavaScript semantics, so ported code adds,
rounds and reads numbers the way the skeleton does.

Why not the standard library:
- `sum()` on floats is compensated (Neumaier) from Python 3.12, so it can differ from
  JavaScript's left-to-right `reduce` in the last bits. `total()` adds left to right.
- JavaScript numbers are all 64-bit floats. JSON integers load in Python as exact ints, so
  any running total of snapshot amounts must start from 0.0, not 0.
- `round()` rounds half to even; `Math.round` rounds half up (towards +infinity).
- `float("1_000")`, `float("inf")` and `float(True)` all succeed; the skeleton's `n2`
  treats input the way JavaScript's unary `+` does.

NaN stands for "cannot be worked out", as in the skeleton. The JSON layer turns it into
null.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from datetime import date, timedelta
from typing import Iterable

NAN = float("nan")
_DAY = re.compile(r"([0-9]{4})-([0-9]{1,2})(?:-([0-9]{1,2}))?", re.ASCII)
_MONTH = re.compile(r"([0-9]{4})-([0-9]{1,2})", re.ASCII)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def total(values: Iterable[float]) -> float:
    """Left-to-right sum from 0, like `a.reduce((s, v) => s + v, 0)`, in 64-bit floats.

    Floats from the start, because JavaScript has no integers: a JSON integer loads in
    Python as an exact `int`, and adding ints keeps digits a double drops (1e16 + 1)."""
    s = 0.0
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
    if not s or _nan(p):
        return NAN
    i = (len(s) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    if lo < 0 or hi > len(s) - 1:  # JavaScript reads undefined there, and the result is NaN
        return NAN
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def _nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def clamp(v: float, a: float, b: float) -> float:
    """`Math.max(a, Math.min(b, v))`: NaN anywhere gives NaN, as in JavaScript (Python's
    min and max would quietly return one of the other arguments)."""
    inner = NAN if _nan(b) or _nan(v) else min(b, v)
    return NAN if _nan(a) or _nan(inner) else max(a, inner)


def as_float(v: int | float) -> float:
    """A number as JavaScript holds it. An integer too big for a double is what JSON.parse
    makes of it, an infinity (Python's float() would raise OverflowError instead)."""
    try:
        return float(v)
    except OverflowError:
        return math.inf if v > 0 else -math.inf


def fin(v) -> bool:
    """A finite number (booleans are not numbers here, as in JavaScript)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(as_float(v))


def finite(v) -> bool:
    """`isFinite(v)` on something already a number (NaN and infinities are not)."""
    return isinstance(v, (int, float)) and math.isfinite(as_float(v))


# JavaScript's StringToNumber, exactly: the whitespace it trims (WhiteSpace and
# LineTerminator, which Python's str.strip() does not match), ASCII digits only (Python's
# \d and float() accept any Unicode digit), 0x / 0o / 0b literals without a sign, and
# "Infinity" (whose result is then refused as not finite).
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_JS_DECIMAL = re.compile(r"[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)", re.ASCII)
_JS_RADIX = re.compile(r"0(?:([xX])([0-9a-fA-F]+)|([oO])([0-7]+)|([bB])([01]+))", re.ASCII)


def _string_to_number(text: str) -> float:
    s = text.strip(_JS_SPACE)
    if not s:
        return 0.0
    m = _JS_RADIX.fullmatch(s)
    if m:
        digits = next(g for g in (m.group(2), m.group(4), m.group(6)) if g)
        return float(int(digits, 16 if m.group(1) else 8 if m.group(3) else 2))
    if _JS_DECIMAL.fullmatch(s):
        return float(s.replace("Infinity", "inf"))
    return NAN


def _number_string(x: float) -> str:
    """JavaScript's Number::toString: the shortest digits that read back as `x` (as Python's
    repr), written plainly from 1e-6 up to (not including) 1e21 and with an exponent outside
    that ("1e+21", "1.5e-7"); NaN, Infinity, and 0 for -0."""
    if _nan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    if 1e-6 <= abs(x) < 1e21:
        text = format(Decimal(repr(x)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    mantissa, exp = repr(x).split("e")
    return f"{mantissa.removesuffix('.0')}e{'+' if int(exp) > 0 else '-'}{abs(int(exp))}"


def _js_string(v) -> str:
    """`String(v)` for what a JSON value can hold, as `+v` sees it inside an array."""
    if v is None:
        return ""  # null and undefined become "" when an array is joined
    if v is True or v is False:
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _number_string(as_float(v))
    if isinstance(v, list):
        return ",".join(_js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def n2(v) -> float:
    """A number from loose input, or NaN: the skeleton's `n2`.

    null, undefined and "" are NaN. Anything else is JavaScript's unary plus: a boolean is 1
    or 0, a string goes through JavaScript's string-to-number rules (a blank string is 0), an
    array is first turned into a string the way JavaScript does ([] is "", ["3"] is "3",
    [1, 2] is "1,2"), an object is NaN. An infinite result is NaN.
    """
    if v is None or v == "":
        return NAN
    if v is True or v is False:
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        x = as_float(v)
    elif isinstance(v, str):
        x = _string_to_number(v)
    elif isinstance(v, list):
        x = _string_to_number(_js_string(v))
    else:
        return NAN
    return x if math.isfinite(x) else NAN


def to_number(v) -> float:
    """JavaScript's ToNumber, as arithmetic coerces an operand (`"40" / 100` is 0.4).

    Unlike `n2`, null is 0 (null / 100 is 0) and "" is 0; undefined has no Python
    counterpart here, since a missing key arrives as None. Infinities stay infinite."""
    if v is None or v == "":
        return 0.0
    if v is True or v is False:
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return as_float(v)
    if isinstance(v, str):
        return _string_to_number(v)
    if isinstance(v, list):
        return _string_to_number(_js_string(v))
    return NAN


def js_div(a: float, b: float) -> float:
    """`a / b` as JavaScript does it: dividing by zero gives NaN (0/0, NaN/0) or an infinity
    with the sign of a * b, never an exception."""
    if b == 0:
        if a == 0 or _nan(a):
            return NAN
        return math.copysign(math.inf, a) * math.copysign(1, b)
    return a / b


def js_max(*xs: float) -> float:
    """`Math.max`: NaN if any argument is NaN (Python's max can quietly return 0 instead),
    and +0 beats -0."""
    if any(_nan(x) for x in xs):
        return NAN
    m = max(xs)
    return 0.0 if m == 0 and any(x == 0 and math.copysign(1, x) > 0 for x in xs) else m


def js_min(*xs: float) -> float:
    """`Math.min`: NaN if any argument is NaN, and -0 beats +0."""
    if any(_nan(x) for x in xs):
        return NAN
    m = min(xs)
    return -0.0 if m == 0 and any(x == 0 and math.copysign(1, float(x)) < 0 for x in xs) else m


def js_string(v) -> str:
    """`String(v)` for a JSON value: 5.0 is "5", null is "null", true is "true"."""
    if v is None:
        return "null"
    return _js_string(v)


def get(obj, key):
    """`obj && obj[key]` for config and ideas: anything that is not an object reads as empty."""
    return obj.get(key) if isinstance(obj, dict) else None


def truthy(v) -> bool:
    """JavaScript's `!!v`: only null, false, 0, NaN and "" are false. An empty object or list
    is true, unlike Python's bool()."""
    return not (v is None or v is False or v == "" or (isinstance(v, (int, float)) and (v != v or v == 0)))


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
    """`Math.round`: the nearest integer, a half rounding up (towards +infinity).

    Not `floor(v + 0.5)`: for 0.49999999999999994 the addition itself rounds to 1.0, so
    that gives 1 where Math.round gives 0."""
    if not math.isfinite(v):
        return v
    r = math.floor(v)
    out = float(r + 1 if v - r >= 0.5 else r)
    return -0.0 if out == 0 and v < 0 else out  # Math.round(-0.2) is -0


def parse_day(s) -> date:
    """`parseDay`: "2026-11-03" (or "2026-11", the 1st) as a calendar date.

    One deliberate difference: JavaScript's Date rolls an impossible day over ("2026-02-30"
    becomes March 2). A snapshot's dates come from exports, so an impossible one is a bad
    file, and this refuses it rather than moving the week. So does anything else that is not
    YYYY-MM or YYYY-MM-DD in ASCII digits with a four-digit year (JavaScript reads "0099" as
    1999 and "2026-11-" as the 1st)."""
    m = _DAY.fullmatch(str(s))
    try:
        if not m or int(m.group(1)) < 1000:
            raise ValueError
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        raise ValueError(f"\u201c{s}\u201d is not a date in the form YYYY-MM-DD.") from None


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def month_label(key: str) -> str:
    """"2026-11" -> "Nov 2026" (a label, not a figure: years are not checked figures).

    A key that is not a real month is refused (JavaScript writes "undefined 2026")."""
    m = _MONTH.fullmatch(str(key))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"\u201c{key}\u201d is not a month in the form YYYY-MM.")
    return f"{MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


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
