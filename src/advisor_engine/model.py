"""The small-business model base: a company's snapshot and brain -> `M`.

`build(data, brain, ...)` is the FIS skeleton's `build` (app/engine.js). It reads the
snapshot (QuickBooks-shaped: pnl, balance, sales, customers, ar/ap, facts, unitTerms) into
the model base `M`: the facts in force, each unit's and offering's sales a month, the fit of
weekly sales, the books by month, and whether the sales detail ties to the books.
`evaluate(M, idea)`, which runs an idea on that base, comes in the next step.

Every figure is a monthly average over the window (`basis` 12: the last 52 weeks; 3: the
last 13), not a forecast for any particular month. Nothing here formats a figure: every
value is a number (NaN where it cannot be worked out).
"""

from __future__ import annotations

import math
import re

from .business_config import ASSUMPTION_DEFAULTS, METHOD_STATUS, METHOD_VERSION
from .company import term_types, vocab
from .facts import resolve
from .jsmath import add_days, js_or, month_label, or_zero
from .sales import coverage, fit, tie_to_books, units_and_offerings, weekly, window


def pnl_months(pnl: dict | None) -> list[dict]:
    """The Profit and Loss by month: income, cogs, gross, expense, other and net.

    Lines are summed by their `group`; a line with any other group is ignored. Net is
    income - cogs - expense + other (QuickBooks' "other income / expense" net).
    """
    P = pnl or {"months": [], "lines": []}
    out = []
    for i, key in enumerate(P["months"]):
        g = {"income": 0.0, "cogs": 0.0, "expense": 0.0, "other": 0.0}
        for line in P["lines"]:
            if line.get("group") in g:
                vals = line.get("values") or []
                g[line["group"]] += or_zero(vals[i] if i < len(vals) else 0)
        out.append({"key": key, "label": month_label(key), "income": g["income"], "cogs": g["cogs"],
                    "gross": g["income"] - g["cogs"], "expense": g["expense"], "other": g["other"],
                    "net": g["income"] - g["cogs"] - g["expense"] + g["other"]})
    return out


_NUMBER = (int, float)
_PNL_GROUPS = ("income", "cogs", "expense", "other")
_MONTH_KEY = re.compile(r"[0-9]{4}-[0-9]{2}", re.ASCII)


def _is_num(v) -> bool:
    """A finite number (Python's json loads NaN and Infinity; JavaScript's JSON.parse does not)."""
    return isinstance(v, _NUMBER) and not isinstance(v, bool) and math.isfinite(v)


def check_snapshot(data: dict) -> None:
    """Refuse a snapshot whose shape the model cannot read, with a plain reason.

    The skeleton reads whatever it is given: a sales row pointing at a week that does not
    exist is skipped, a text amount is glued onto a total as a string. A snapshot comes
    from exports, so a bad one should stop here and say why rather than give quiet garbage.
    This checks shape only (types, row length, indexes in range); whether the numbers are
    right is the brain check's and the tie's job.
    """
    S = data.get("sales")
    if S is not None:
        for key in ("weeks", "units", "offerings", "rows"):
            if not isinstance(S.get(key), list):
                raise ValueError(f"The sales detail has no {key} list.")
        n_w, n_u, n_o = len(S["weeks"]), len(S["units"]), len(S["offerings"])
        for o in S["offerings"]:
            if not isinstance(o, dict):
                raise ValueError("Every offering in the sales detail is an object with a name and a price.")
        for i, r in enumerate(S["rows"]):
            # a null quantity or amount counts as 0, as in JavaScript; indexes must be numbers
            if (not isinstance(r, list) or len(r) != 5 or not all(_is_num(x) for x in r[:3])
                    or not all(x is None or _is_num(x) for x in r[3:])):
                raise ValueError(f"Sales row {i + 1} is not [week, unit, offering, quantity, amount] as five numbers.")
            for v, n, what in ((r[0], n_w, "week"), (r[1], n_u, "unit"), (r[2], n_o, "offering")):
                if v != int(v) or not 0 <= v < n:
                    raise ValueError(f"Sales row {i + 1} points at {what} {v}, which is not in the sales detail.")
        for x in S.get("byMonth") or []:
            if not isinstance(x, dict) or not isinstance(x.get("month"), str) or not (x.get("amount") is None or _is_num(x.get("amount"))):
                raise ValueError("Each monthly total of the sales detail needs a month and an amount.")
    P = data.get("pnl")
    if P is not None:
        if not isinstance(P.get("months"), list) or not isinstance(P.get("lines"), list):
            raise ValueError("The Profit and Loss needs a months list and a lines list.")
        for key in P["months"]:
            if not isinstance(key, str) or not _MONTH_KEY.fullmatch(key) or not 1 <= int(key[5:7]) <= 12:
                raise ValueError(f"The Profit and Loss month \u201c{key}\u201d is not a month in the form YYYY-MM.")
        for line in P["lines"]:
            if not isinstance(line, dict):
                raise ValueError("Every Profit and Loss line is an object with an account, a group and values.")
            if line.get("group") not in _PNL_GROUPS:
                continue  # headers, memo and section lines: the model never reads them
            vals = line.get("values")
            if not isinstance(vals, list) or not all(v is None or _is_num(v) for v in vals):
                raise ValueError(f"The Profit and Loss line \u201c{line.get('account', '?')}\u201d has values that are not numbers.")
    for f in data.get("facts") or []:
        if not isinstance(f, dict) or not f.get("k"):
            raise ValueError("Every fact needs a key (k).")


def build(data: dict, brain: dict, *, facts: dict | None = None, confirmed: dict | None = None,
          basis: int = 12) -> dict:
    """The model base for one company. See the module docstring.

    `facts` and `confirmed` are the owner's corrections (facts.latest_corrections makes them
    from stored rows); `basis` is 12 (average the last 52 weeks) or 3 (the last 13).
    """
    check_snapshot(data)
    basis = 3 if basis == 3 else 12
    A = {**ASSUMPTION_DEFAULTS, **(brain.get("assumptions") or {})}
    FL, F = resolve(data.get("facts"), facts, confirmed)
    rates = [f for f in FL if f.get("group") == "rate"]
    fixed = [f for f in FL if f.get("group") == "fixed"]
    S = data.get("sales") or {"weeks": [], "units": [], "offerings": [], "rows": []}
    weeks = weekly(S)
    w = window(len(weeks), basis)
    uo = units_and_offerings(S, weeks, w["w0"], w["months"])
    ft = fit(weeks, w["w0"])
    win = weeks[w["w0"]:]
    pm = pnl_months(data.get("pnl"))
    cov = coverage(pm, S.get("byMonth"))
    meta = data.get("meta") or {}
    return {
        "DATA": data, "BRAIN": brain, "V": vocab(brain), "A": A, "F": F, "FL": FL, "rates": rates, "fixed": fixed,
        "basis": basis, "weeks": weeks,
        "win": {**w, "from": win[0]["dt"] if win else None, "to": add_days(win[-1]["dt"], 6) if win else None},
        **uo, "fit": ft, "pnl": {"months": pm, "lines": (data.get("pnl") or {"lines": []})["lines"]},
        "coverage": cov, "tie": tie_to_books(cov), "unitTerms": data.get("unitTerms") or {}, "types": term_types(brain),
        # `x || null` in the skeleton: an empty or zero value reads as missing
        "balance": js_or(data.get("balance"), None), "customers": js_or(data.get("customers"), None),
        "ar": js_or(data.get("ar"), None), "ap": js_or(data.get("ap"), None),
        "sample": bool(meta.get("sample")), "uploaded": js_or(meta.get("uploaded"), None),
        "method_version": METHOD_VERSION, "method_status": METHOD_STATUS,
    }


def model_view(M: dict) -> dict:
    """The model base without the snapshot and brain it was built from (for output and tests)."""
    return {k: v for k, v in M.items() if k not in ("DATA", "BRAIN") and not k.startswith("_")}
