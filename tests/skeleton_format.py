"""The FIS skeleton's display formatters (app/engine.js `usd`, `usdk`, `pct`, `num`, `fmtD`,
`signed`), ported for the parity tests only.

The engine returns numbers; the skeleton returned text. To check that a Python measure,
note or check message says exactly what the skeleton's did, the tests format the engine's
figures with these and compare strings. This file is not part of the engine: the browser
formats (web/src/lib/format.ts), and these rules are what it would port if the skeleton's
look is wanted.

`toLocaleString("en-US")` rounds the exact value of the double half away from zero; so does
Decimal(value) with ROUND_HALF_UP on the absolute value.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MINUS = "−"


def _ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def grp(v: float, d: int = 0) -> str:
    q = Decimal(abs(v)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return f"{q:,.{d}f}"


def usd(v) -> str:
    return ((MINUS + "$" if v < 0 else "$") + grp(v, 0)) if _ok(v) else "n/a"


def usdk(v) -> str:
    if not _ok(v):
        return "n/a"
    sign = MINUS + "$" if v < 0 else "$"
    if abs(v) >= 1e6:
        return sign + grp(v / 1e6, 2) + "M"
    if abs(v) >= 10000:
        return sign + grp(v / 1000, 1) + "K"
    return usd(v)


def pct(v, d: int = 0) -> str:
    return ((MINUS if v < 0 else "") + grp(v * 100, d) + "%") if _ok(v) else "n/a"


def num(v, d: int = 0) -> str:
    return ((MINUS if v < 0 else "") + grp(v, d)) if _ok(v) else "n/a"


def signed(v, f) -> str:
    return ("+" if v > 0 else MINUS if v < 0 else "") + f(abs(v))


def fmt_date(d) -> str:
    if isinstance(d, str):
        y, m, dd = (int(x) for x in d.split("-"))
        d = date(y, m, dd)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def figure(f: dict) -> str:
    """One figure `{value, unit, decimals, signed}` as the skeleton writes it."""
    v, unit, d = f.get("value"), f.get("unit"), f.get("decimals") or 0
    if v is None:
        return "n/a"
    if unit == "usd":
        return usdk(v)
    if unit == "usd_whole":
        return usd(v)
    if unit == "share":
        return signed(v, lambda x: pct(x, d)) if f.get("signed") else pct(v, d)
    if unit in ("count", "months"):
        return num(v, d)
    if unit == "cents":
        return str(int(math.floor(v * 100 + 0.5))) + "¢"
    if unit == "price":
        return "$" + grp(v, d)
    raise ValueError(f"unknown unit {unit!r}")


def var(x) -> str:
    if "text" in x:
        return x["text"]
    if "date" in x:
        return fmt_date(x["date"])
    return figure(x)


def render(template: str, vars: dict | None) -> str:
    return template.format(**{k: var(v) for k, v in (vars or {}).items()})
