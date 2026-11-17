"""The small-business model: a company's snapshot and brain -> today's setup -> any idea.

Two steps, as in the FIS skeleton (`build` and `evaluate` in app/engine.js):

`build(data, brain, ...)` reads the snapshot (QuickBooks-shaped: pnl, balance, sales,
customers, ar/ap, facts, unitTerms) into the model base `M`: the facts in force, each
unit's and offering's sales a month, the fit of weekly sales, the books by month and the
tie between detail and books.

`evaluate(M, idea)` runs one idea on that base, per month: sales by unit and offering after
prices, volume and removals; direct costs (offering cost rates, which follow volume, not
price); costs that move with sales (rate facts); each unit's pay under its terms; monthly
bills; and what the business keeps. It also splits what the business takes in into the part
that swings with sales (`varMo`) and the part that does not (`steadyMo`: rent received,
wages paid), which the cash range needs.

How pay works, per unit (`terms`):
- share: the unit takes `rate`% of its own sales; the business keeps the rest of the sale,
  less direct costs and rate costs. The business's part swings with sales.
- fixedIn: the unit pays the business `rent` (per the brain's period) and keeps its sales,
  less the costs. The business's part is steady.
- wage: the business pays the unit `wage` and keeps the sales less costs. The unit's pay is
  steady; the business's part swings.
- none: the business keeps the sales less costs; nothing changes hands.

Caveats, all the skeleton's:
- A price change moves quantity by the price response (`elast`), and direct costs follow
  quantity only. The response is an assumption until the company's own history replaces it.
- A removed unit's sales are lost, except `keep`% which is spread over the remaining units
  in proportion to their sales (with their costs).
- An added unit's direct costs are the business's blended cost rate.
- Every figure is a monthly average over the window (`basis` 12: the last 52 weeks;
  3: the last 13), not a forecast for any particular month.

Nothing here formats a figure: every value is a number (NaN where it cannot be worked out).
"""

from __future__ import annotations

from .business_config import ASSUMPTION_DEFAULTS, METHOD_STATUS, METHOD_VERSION
from .company import term_types, vocab
from .facts import resolve
from .jsmath import NAN, add_days, clamp, fin, finite, month_label, n2, or_zero, total
from .levers import norm_idea
from .sales import coverage, fit, tie_to_books, units_and_offerings, weekly, window


def pnl_months(pnl: dict | None) -> list[dict]:
    """The Profit and Loss by month: income, cogs, gross, expense, other and net.

    Lines are summed by their `group`; a line with any other group is ignored. Net is
    income - cogs - expense + other (QuickBooks' "other income / expense" net).
    """
    P = pnl or {"months": [], "lines": []}
    out = []
    for i, key in enumerate(P["months"]):
        g = {"income": 0, "cogs": 0, "expense": 0, "other": 0}
        for line in P["lines"]:
            if line.get("group") in g:
                vals = line.get("values") or []
                g[line["group"]] += or_zero(vals[i] if i < len(vals) else 0)
        out.append({"key": key, "label": month_label(key), "income": g["income"], "cogs": g["cogs"],
                    "gross": g["income"] - g["cogs"], "expense": g["expense"], "other": g["other"],
                    "net": g["income"] - g["cogs"] - g["expense"] + g["other"]})
    return out


def build(data: dict, brain: dict, *, facts: dict | None = None, confirmed: dict | None = None,
          basis: int = 12) -> dict:
    """The model base for one company. See the module docstring.

    `facts` and `confirmed` are the owner's corrections (facts.latest_corrections makes them
    from stored rows); `basis` is 12 (average the last 52 weeks) or 3 (the last 13).
    """
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
        "balance": data.get("balance"), "customers": data.get("customers"), "ar": data.get("ar"), "ap": data.get("ap"),
        "sample": bool(meta.get("sample")), "uploaded": meta.get("uploaded") or None,
        "method_version": METHOD_VERSION, "method_status": METHOD_STATUS,
    }


def term_defaults(M: dict) -> dict:
    """The pay a unit gets when nothing says otherwise: the brain's default, else the first
    kind it offers, with each kind's starting figure."""
    ty = M["types"]
    d = ((M["BRAIN"].get("terms") or {}).get("default")) or {}
    first = next(iter(ty))
    return {"on": True, "type": d.get("type") if d.get("type") in ty else first,
            "rate": d["rate"] if fin(d.get("rate")) else (ty["share"]["def"] if "share" in ty else 50),
            "rent": d["rent"] if fin(d.get("rent")) else (ty["fixedIn"]["def"] if "fixedIn" in ty else 250),
            "wage": d["wage"] if fin(d.get("wage")) else (ty["wage"]["def"] if "wage" in ty else 4000)}


def term_of(M: dict, idea: dict | None, name: str) -> dict:
    """A unit's pay under an idea: defaults, then the snapshot's terms, then the idea's."""
    over = ((idea or {}).get("terms") or {}).get(name) or {}
    return {**term_defaults(M), **(M["unitTerms"].get(name) or {}), **over}


def per_month(M: dict, kind: str, amount: float) -> float:
    """A pay amount in the kind's period (week, month or year) as a monthly figure
    (a week is 52/12 of a month)."""
    per = (M["types"].get(kind) or {}).get("per")
    if per == "week":
        return amount * 52 / 12
    if per == "year":
        return amount / 12
    return amount


def _num_or0(v) -> float:
    return or_zero(n2(v))


def evaluate(M: dict, idea: dict | None = None) -> dict:
    """One idea on the model base, per month. See the module docstring for the method.

    Returns the idea (filled in), `rows` (one per unit, then the added ones) and the
    totals: sales, direct, fees, businessIn, take, fixed (fixedFirst + fixedEven), profit,
    afterDraw, margin, varMo, steadyMo, n (units on), lost and kept (a removed unit's sales),
    vr (rate costs as a share of sales), offerings (sales by offering) and looseSales
    (added units' sales, which have no offering).
    """
    idea = norm_idea(M, idea)
    F = M["F"]

    def rate_of(k):
        v = (idea.get("rate") or {}).get(k)
        return v if fin(v) else F[k]

    vr = total(rate_of(r["k"]) for r in M["rates"]) / 100
    shock = max(0, 1 + _num_or0(idea.get("shock")) / 100)
    pm, qm = [], []
    for o in M["off"]:
        p, q = 1, shock
        p1 = n2((idea.get("price") or {}).get(o["name"])) if idea.get("price") is not None else NAN
        if finite(p1) and p1 >= 0 and o["price"] > 0:
            dp = p1 / o["price"] - 1
            p = 1 + dp
            el = idea.get("elast") if fin(idea.get("elast")) else M["A"]["elasticity"]
            q *= max(0, 1 + el * dp)
        os_ = n2((idea.get("offer") or {}).get(o["name"])) if idea.get("offer") is not None else NAN
        if finite(os_):
            q *= max(0, 1 + os_ / 100)
        pm.append(p)
        qm.append(q)

    rows = []
    for u in M["units"]:
        t = term_of(M, idea, u["name"])
        sales = direct = 0
        parts = [0] * len(M["off"])
        for i, v in enumerate(u["svc"]):
            if not v:
                continue
            s = v * pm[i] * qm[i]
            parts[i] = s
            sales += s
            direct += v * qm[i] * M["off"][i]["cost"]
        rows.append({"name": u["name"], "base": u["mo"], "sales": sales, "direct": direct, "parts": parts, "t": t,
                     "on": t.get("on") is not False})
    for i, a in enumerate(idea.get("added") or []):
        mo = max(0, _num_or0(a.get("mo"))) * shock
        rows.append({"name": a.get("name") or "New " + M["V"]["unit"], "base": 0, "sales": mo, "direct": mo * M["blendCost"],
                     "parts": None, "t": {**term_defaults(M), **a, "on": True}, "on": True, "added": i})

    # when a unit leaves, some of its sales can stay with the others
    on = [r for r in rows if r["on"]]
    lost = total(r["sales"] for r in rows if not r["on"])
    kept = lost * clamp(_num_or0(idea.get("keep")), 0, 100) / 100
    tot = total(r["sales"] for r in on)
    if kept > 0 and tot > 0:
        k = 1 + kept / tot
        for r in on:
            r["sales"] *= k
            r["direct"] *= k
            if r["parts"]:
                r["parts"] = [p * k for p in r["parts"]]

    off_sales = [0] * len(M["off"])
    loose = 0
    for r in rows:
        if not r["on"]:
            r["lostSales"] = r["sales"]
            for key in ("sales", "direct", "fees", "biz", "take", "vr", "steady"):
                r[key] = 0
            continue
        r["fees"] = r["sales"] * vr
        net = r["sales"] - r["direct"] - r["fees"]
        ty = r["t"].get("type")
        if ty == "fixedIn":
            a = per_month(M, "fixedIn", max(0, _num_or0(r["t"].get("rent"))))
            r["biz"], r["take"], r["vr"], r["steady"] = a, net - a, 0, a
        elif ty == "wage":
            w = per_month(M, "wage", max(0, _num_or0(r["t"].get("wage"))))
            r["biz"], r["take"], r["vr"], r["steady"] = net - w, w, net, -w
        elif ty == "share":
            k = clamp(_num_or0(r["t"].get("rate")), 0, 100) / 100
            r["take"] = r["sales"] * k
            r["biz"] = net - r["take"]
            r["vr"], r["steady"] = r["biz"], 0
        else:
            r["biz"], r["take"], r["vr"], r["steady"] = net, 0, net, 0
        if r["parts"]:
            for i, p in enumerate(r["parts"]):
                off_sales[i] += p
        else:
            loose += r["sales"]

    lines = [{"k": f["k"], "l": f.get("l"), "mo": or_zero(F.get(f["k"])),
              "when": "even" if f.get("when") == "even" else "first"} for f in M["fixed"]]
    for x in idea.get("fixed") or []:
        if x and finite(n2(x.get("mo"))) and n2(x.get("mo")) != 0:
            lines.append({"l": str(x.get("l") or "New monthly cost"), "mo": n2(x.get("mo")), "when": "even", "added": True})
    fixed_first = total(ln["mo"] for ln in lines if ln["when"] == "first")
    fixed_even = total(ln["mo"] for ln in lines if ln["when"] == "even")
    sales = total(r["sales"] for r in rows)
    business_in = total(r["biz"] for r in rows)
    fixed_tot = fixed_first + fixed_even
    profit = business_in - fixed_tot
    return {
        "idea": idea, "rows": rows, "sales": sales, "direct": total(r["direct"] for r in rows),
        "fees": total(r["fees"] for r in rows), "businessIn": business_in, "take": total(r["take"] for r in rows),
        "fixed": fixed_tot, "fixedFirst": fixed_first, "fixedEven": fixed_even, "fixedLines": lines, "profit": profit,
        "afterDraw": profit - F["draw"], "margin": profit / sales if sales > 0 else NAN,
        "varMo": total(r["vr"] for r in rows), "steadyMo": total(r["steady"] for r in rows), "n": len(on),
        "lost": lost, "kept": kept, "vr": vr,
        "offerings": [{"name": o["name"], "sales": off_sales[i]} for i, o in enumerate(M["off"])], "looseSales": loose,
    }


def row_of(E: dict, name: str) -> dict | None:
    """The evaluated row for a unit (the skeleton's `E.by.get(name)`; the last of that name wins)."""
    found = None
    for r in E["rows"]:
        if r["name"] == name:
            found = r
    return found


def model_view(M: dict) -> dict:
    """The model base without the snapshot and brain it was built from (for output and tests)."""
    return {k: v for k, v in M.items() if k not in ("DATA", "BRAIN") and not k.startswith("_")}
