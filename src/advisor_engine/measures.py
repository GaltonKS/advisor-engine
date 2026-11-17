"""The measure library: the numbers a company can put at the top of its Overview.

The FIS skeleton's `METRICS`, with one change of kind: the skeleton returns text
("$553.2K"); this returns numbers. A measure is

    {key, label, value, unit, decimals, signed, tone, note, vars, available}

`value` is the figure (None when it cannot be worked out on this data), `unit` says what it
is ("usd" money, "share" a fraction shown as a percent, "count", "cents" per dollar of
sales, "price" a ticket price, "months"), `decimals` and `signed` how the skeleton shows it.
`note` is the small line under it, a template whose `{name}` placeholders are figures (or
text) in `vars`. The browser formats; the engine never does, so every figure stays in the
output where the number check can find it.

Measures read today's setup evaluated (`T`) and today's cash range (`C`). A brain picks its
headline from this library and may add its own, but only as a library measure with
parameters (`{"metric": "top_n_share", "n": 4}`): a brain is config and holds no code. A
measure a company truly needs that the library lacks is new engine code here, with a test,
for everyone.

Views: an owner can choose which four measures lead (`view_keys`), from ready-made sets
(`view_presets`: the brain's own first, then the library's) or any four from the pick-list
(`view_measures`: every measure that works on this data). Tones and thresholds are DRAFT
(business_config.TONE).
"""

from __future__ import annotations

from .business_config import TONE
from .jsmath import NAN, finite, fin, mean, parse_day, total
from .tax import tax_of

GROUP = {
    "revenue_month": "Sales", "revenue_trend": "Sales", "avg_ticket": "Sales", "tickets_week": "Sales",
    "profit_month": "Profit", "profit_margin": "Profit", "breakeven_sales": "Profit",
    "cash_on_hand": "Cash", "cash_13w": "Cash", "cash_breach": "Cash", "bills_covered": "Cash",
    "ar_open": "Money owed", "ap_open": "Money owed", "gross_margin": "The books", "net_income": "The books",
    "revenue_per_unit": "Where sales come from", "top_unit_share": "Where sales come from",
    "top_customers": "Where sales come from",
    "tax_liability_year": "Taxes", "tax_next_payment": "Taxes", "tax_cash_90d": "Taxes",
}
VIEW_GROUPS = ["Sales", "Profit", "Cash", "Money owed", "The books", "Where sales come from", "Taxes"]
VIEW_PRESETS = [
    {"id": "weekly", "name": "Weekly check-in", "keys": ["revenue_month", "profit_month", "cash_on_hand", "ar_open"]},
    {"id": "cash", "name": "Cash watch", "keys": ["cash_on_hand", "cash_13w", "cash_breach", "bills_covered"]},
    {"id": "profit", "name": "Profit focus", "keys": ["profit_month", "profit_margin", "breakeven_sales", "net_income"]},
    {"id": "growth", "name": "Sales and growth", "keys": ["revenue_month", "revenue_trend", "tickets_week", "avg_ticket"]},
    {"id": "carry", "name": "Who carries the sales", "keys": ["revenue_per_unit", "top_unit_share", "top_customers", "revenue_month"]},
    {"id": "owed", "name": "Money in and out", "keys": ["ar_open", "ap_open", "cash_on_hand", "bills_covered"]},
    {"id": "books", "name": "Books at a glance", "keys": ["gross_margin", "net_income", "ar_open", "ap_open"]},
    {"id": "margins", "name": "Margins and break-even", "keys": ["gross_margin", "profit_margin", "breakeven_sales", "net_income"]},
    {"id": "bills", "name": "Can we cover the bills?", "keys": ["cash_on_hand", "bills_covered", "breakeven_sales", "cash_breach"]},
    {"id": "visits", "name": "Customers and visits", "keys": ["top_customers", "tickets_week", "avg_ticket", "revenue_trend"]},
    {"id": "taxes", "name": "Taxes", "keys": ["tax_liability_year", "tax_next_payment", "tax_cash_90d", "cash_on_hand"]},
]


# --- building a measure ---------------------------------------------------------------

def esc(text) -> str:
    """Literal text for a note template (braces doubled)."""
    return str(text).replace("{", "{{").replace("}", "}}")


def fig(value, unit: str, decimals: int = 0, signed: bool = False) -> dict:
    """A figure for a note's vars."""
    out = {"value": value if finite(value) else None, "unit": unit, "decimals": decimals}
    if signed:
        out["signed"] = True
    return out


def text(value: str) -> dict:
    return {"text": str(value)}


def _m(value, unit, note="", vars=None, tone="", decimals=0, signed=False) -> dict:
    v = value if finite(value) else None
    out = {"value": v, "unit": unit, "decimals": decimals, "tone": tone, "note": note, "vars": vars or {},
           "available": v is not None}
    if signed:
        out["signed"] = True
    return out


def _na(note="", tone="") -> dict:
    return {"value": None, "unit": "", "decimals": 0, "tone": tone, "note": note, "vars": {}, "available": False}


# --- the library -------------------------------------------------------------------------

def _revenue_month(M, T, C):
    return _m(T["sales"], "usd", "Average of the last {weeks} weeks", {"weeks": fig(M["win"]["nWin"], "count")})


def _revenue_trend(M, T, C):
    f = M["fit"]
    note = ("Last 13 weeks against the same weeks a year earlier" if f["trendKind"] == "year"
            else "Last 13 weeks against the 13 before · seasons not taken out" if f["trendKind"] == "quarter"
            else "Needs 26 weeks of sales")
    tone = ""
    if f["trendKind"] == "year":
        tone = "bad" if f["trend"] < TONE["trend_bad"] else "good" if f["trend"] > TONE["trend_good"] else ""
    return _m(f["trend"], "share", esc(note), tone=tone, decimals=1, signed=True)


def _profit_month(M, T, C):
    return _m(T["profit"], "usd", "After pay, costs and monthly bills", tone="bad" if T["profit"] < 0 else "")


def _profit_margin(M, T, C):
    return _m(T["margin"], "cents", "After pay, costs and monthly bills", tone="bad" if T["margin"] < 0 else "")


def _avg_ticket(M, T, C):
    a = M["fit"]["avgTicket"]
    return _m(a, "price", esc("Sales divided by " + M["V"]["tickets"]), decimals=2 if finite(a) and a < 100 else 0)


def _tickets_week(M, T, C):
    return _m(M["fit"]["qtyWk"], "count", "Average of the last {weeks} weeks", {"weeks": fig(M["win"]["nWin"], "count")})


def _revenue_per_unit(M, T, C):
    n = sum(1 for r in T["rows"] if r["on"] and r["sales"] > 0)
    word = M["V"]["unit"] if n == 1 else M["V"]["units"]
    return _m(T["sales"] / n if n else NAN, "usd", "{n} " + esc(word) + " with sales", {"n": fig(n, "count")})


def _on_by_sales(T):
    return sorted((r for r in T["rows"] if r["on"]), key=lambda r: -r["sales"])


def _top_unit_share(M, T, C):
    on = _on_by_sales(T)
    if not on or not T["sales"] > 0:
        return _na()
    r, n = on[0], len(on)
    share = r["sales"] / T["sales"]
    tone = "warn" if share > TONE["top_unit_over_even"] / n and n > 2 else ""
    return _m(share, "share", "{name} · an even split would be {even}",
              {"name": text(r["name"]), "even": fig(1 / n, "share")}, tone=tone)


def _top_customers(M, T, C):
    c = M["customers"]
    if c and fin(c.get("top20")):
        return _m(c["top20"], "share", "Of sales, from {count} " + esc(M["V"]["customers"]),
                  {"count": fig(c.get("count"), "count")}, tone="warn" if c["top20"] > TONE["top_customers_warn"] else "")
    return _na(esc("Needs sales by " + M["V"]["customer"]))


def _cash_on_hand(M, T, C):
    b = M["balance"]
    if b and b.get("asOf"):
        return _m(M["F"]["cash0"], "usd", "Balance sheet, {date}", {"date": {"date": parse_day(b["asOf"])}})
    return _m(M["F"]["cash0"], "usd", "")


def _cash_13w(M, T, C):
    if not C:
        return _na("Needs 12 weeks of sales")
    H = C["H"]
    return _m(C["p50"][H], "usd", "Middle outcome · 8 in 10 land between {low} and {high}",
              {"low": fig(C["p10"][H], "usd"), "high": fig(C["p90"][H], "usd")})


def _cash_breach(M, T, C):
    if not C:
        return _na("Needs 12 weeks of sales")
    p = C["pBreach"]
    tone = "bad" if p > TONE["breach_bad"] else "warn" if p > TONE["breach_warn"] else "good"
    return _m(p, "share", "Next {weeks} weeks · floor {floor}",
              {"weeks": fig(C["H"], "count"), "floor": fig(M["F"]["floor"], "usd")}, tone=tone)


def _bills_covered(M, T, C):
    out = T["fixed"] - T["steadyMo"]
    if not out > 0:
        return _na()
    v = M["F"]["cash0"] / out
    tone = "bad" if v < TONE["bills_bad"] else "warn" if v < TONE["bills_warn"] else ""
    return _m(v, "months", "Cash against monthly bills and fixed pay", tone=tone, decimals=1)


def _breakeven_sales(M, T, C):
    out = T["fixed"] - T["steadyMo"]
    cr = T["varMo"] / T["sales"] if T["sales"] > 0 else NAN
    be = out / cr if cr > 0 else NAN
    note, vars = "", {}
    if finite(be) and T["sales"] > 0:
        above = T["sales"] >= be
        note = "A month · today's sales are {gap}" + (" above" if above else " below")
        vars = {"gap": fig(T["sales"] / be - 1, "share")}
    return _m(be, "usd", note, vars, tone="bad" if T["sales"] < be else "")


def _gross_margin(M, T, C):
    m = M["pnl"]["months"][-3:]
    i = total(x["income"] for x in m)
    if i > 0:
        return _m(total(x["gross"] for x in m) / i, "share", "Profit and Loss, last 3 months")
    return _na("Needs the Profit and Loss")


def _net_income(M, T, C):
    m = M["pnl"]["months"][-3:]
    if not m:
        return _na("Needs the Profit and Loss")
    v = mean(x["net"] for x in m)
    return _m(v, "usd", "Profit and Loss, last 3 months", tone="bad" if v < 0 else "")


def _ar_open(M, T, C):
    if not M["ar"]:
        return _na("Needs A/R Aging")
    b = M["ar"]["buckets"]
    tot, late = total(x[1] for x in b), total(x[1] for x in b[2:])
    return _m(tot, "usd", "{late} is more than 30 days late", {"late": fig(late, "usd")},
              tone="warn" if tot > 0 and late / tot > TONE["ar_late_warn"] else "")


def _ap_open(M, T, C):
    if not M["ap"]:
        return _na("Needs A/P Aging")
    return _m(total(x[1] for x in M["ap"]["buckets"]), "usd", "A/P Aging")


def _tax_off(X):
    return _na(esc((X and X.get("why")) or "") if X else "Taxes are switched off in the brain")


def _tax_range(X, year):
    r = ((X.get("range") or {}).get(year) or {}).get("liability")
    if not r:
        return "", {}
    return " · 8 in 10 land between {low} and {high}", {"low": fig(r["p10"], "usd"), "high": fig(r["p90"], "usd")}


def _tax_liability_year(M, T, C):
    X = tax_of(M, T)
    if not X or not X.get("ok") or not X.get("computed"):
        return _tax_off(X)
    extra, vars = _tax_range(X, "cur")
    return _m(X["cur"]["liability"], "usd", "Federal estimate, " + esc(X["cur"]["label"]) + extra, vars)


def _tax_next_payment(M, T, C):
    X = tax_of(M, T)
    if not X or not X.get("ok") or not X.get("computed"):
        return _tax_off(X)
    p = X["nextPayment"]
    if not p:
        return _m(0, "usd", "Nothing due in the next 12 months")
    soon = (p["date"] - X["asOf"]).days <= TONE["tax_due_soon_days"]
    # the payment's label is itself a template ("installment {n} of {of}"), so its vars come along
    return _m(p["amount"], "usd", "Due {date} · " + p["l"].lower(), {**(p.get("vars") or {}), "date": {"date": p["date"]}},
              tone="warn" if soon else "")


def _tax_cash_90d(M, T, C):
    X = tax_of(M, T)
    if not X or not X.get("ok") or not X.get("computed"):
        return _tax_off(X)
    warn = X["cash90"] > M["F"]["cash0"] - M["F"]["floor"] and X["cash90"] > 0
    return _m(X["cash90"], "usd", "Federal estimated payments · {year} over the next 12 months",
              {"year": fig(X["cash12"], "usd")}, tone="warn" if warn else "")


LIBRARY = {
    "revenue_month": (lambda V: "Sales a month", _revenue_month),
    "revenue_trend": (lambda V: "Sales trend", _revenue_trend),
    "profit_month": (lambda V: "The " + V["business"] + " keeps a month", _profit_month),
    "profit_margin": (lambda V: "Kept from each $1 of sales", _profit_margin),
    "avg_ticket": (lambda V: "Average " + V["ticket"], _avg_ticket),
    "tickets_week": (lambda V: V["Tickets"] + " a week", _tickets_week),
    "revenue_per_unit": (lambda V: "Sales a month per " + V["unit"], _revenue_per_unit),
    "top_unit_share": (lambda V: "Top " + V["unit"] + "'s share of sales", _top_unit_share),
    "top_customers": (lambda V: "Top 20% of " + V["customers"], _top_customers),
    "cash_on_hand": (lambda V: "Cash in the bank", _cash_on_hand),
    "cash_13w": (lambda V: "Cash in 13 weeks", _cash_13w),
    "cash_breach": (lambda V: "Chance cash dips under the floor", _cash_breach),
    "bills_covered": (lambda V: "Months of bills in the bank", _bills_covered),
    "breakeven_sales": (lambda V: "Sales needed to break even", _breakeven_sales),
    "gross_margin": (lambda V: "Gross margin", _gross_margin),
    "net_income": (lambda V: "Net income a month", _net_income),
    "ar_open": (lambda V: "Owed by customers", _ar_open),
    "ap_open": (lambda V: "Owed to suppliers", _ap_open),
    "tax_liability_year": (lambda V: "Income tax this tax year", _tax_liability_year),
    "tax_next_payment": (lambda V: "Next tax payment", _tax_next_payment),
    "tax_cash_90d": (lambda V: "Tax leaving the bank, next 90 days", _tax_cash_90d),
}


# --- measures with parameters, for a brain's own numbers --------------------------------

def _top_n_share(M, T, C, p):
    """The top n units' share of sales; the note names them by first name.
    (Port of the barbershop brain's `top4_share` closure, with n as a parameter.)"""
    n = int(p.get("n") or 0)
    if n < 1:
        raise ValueError("top_n_share needs n of at least 1.")
    r = _on_by_sales(T)[:n]
    return _m(total(x["sales"] for x in r) / T["sales"] if T["sales"] else NAN, "share", "{names}",
              {"names": text(", ".join(x["name"].split(" ")[0] for x in r))})


PARAMETERISED = {"top_n_share": _top_n_share}


def _own(M: dict) -> dict:
    """The brain's own measures, `{}` unless the brain gives an object of them."""
    own = M["BRAIN"].get("metrics")
    return own if isinstance(own, dict) else {}


def measure(M: dict, key: str, T: dict, C: dict | None) -> dict:
    """One measure for today's setup. A brain's own entry wins over the library's.

    A measure that fails comes back unavailable with a warning note, as in the skeleton,
    so one bad number never blanks the page.
    """
    own = _own(M).get(key) if isinstance(key, str) else None
    V = M["V"]
    try:
        if own is not None:
            kind = own.get("metric")
            if kind in PARAMETERISED:
                return {"key": key, "label": own.get("l") or key, **PARAMETERISED[kind](M, T, C, own)}
            return {"key": key, "label": own.get("l") or key,
                    **_na(esc(f"“{kind}” is not a measure with parameters" if kind else "Not in the metric library"), "warn")}
        if key in LIBRARY:
            label, f = LIBRARY[key]
            return {"key": key, "label": label(V), **f(M, T, C)}
    except Exception:  # noqa: BLE001 - the skeleton shows n/a rather than failing the page
        return {"key": key, "label": key, **_na("This number could not be worked out", "warn")}
    return {"key": key, "label": key, **_na("Not in the metric library", "warn")}


def view_measures(M: dict, T: dict, C: dict | None) -> list[dict]:
    """The pick-list: every measure that works on this data, library first, then the
    brain's own, each with its group."""
    own = _own(M)
    keys = list(LIBRARY) + [k for k in own if k not in LIBRARY]
    out = []
    for k in keys:
        m = measure(M, k, T, C)
        if not m["available"]:
            continue
        entry = own.get(k)
        group = GROUP.get(k) or (entry.get("group") if isinstance(entry, dict) else None) or "Specific to this " + M["V"]["business"]
        out.append({**m, "group": group})
    return out


def view_presets(M: dict, T: dict, C: dict | None) -> list[dict]:
    """Ready-made sets of four that work on this data: the brain's own first (a brain view
    reusing a library id replaces it), then the library's."""
    have = {x["key"] for x in view_measures(M, T, C)}

    def ok(p):
        return (isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"] and p.get("name")
                and isinstance(p.get("keys"), list) and len(p["keys"]) > 0 and all(isinstance(k, str) and k in have for k in p["keys"]))

    mine = [{"id": p["id"], "name": p["name"], "keys": p["keys"][:4], "own": True}
            for p in (M["BRAIN"].get("views") or []) if ok(p)]
    taken = {p["id"] for p in mine} | {"default", "custom"}
    return mine + [{"id": p["id"], "name": p["name"], "keys": list(p["keys"]), "own": False}
                   for p in VIEW_PRESETS if p["id"] not in taken and ok(p)]


def view_keys(M: dict, sel, T: dict, C: dict | None) -> list[str]:
    """Which four measures to show for a saved choice `{preset, keys}`.

    "custom" keeps the owner's picks that work, in order, without repeats, topped up to
    four from the pick-list; a preset gives its keys; anything unusable falls back to the
    brain's headline.
    """
    dflt = list(M["BRAIN"].get("headline") or [])
    if not isinstance(sel, dict):
        return dflt
    if sel.get("preset") == "custom":
        have = [x["key"] for x in view_measures(M, T, C)]
        chosen = sel.get("keys") if isinstance(sel.get("keys"), list) else []
        ks = [k for i, k in enumerate(chosen) if isinstance(k, str) and k in have and chosen.index(k) == i][:4]
        if not ks:
            return dflt
        for k in have:
            if len(ks) >= 4:
                break
            if k not in ks:
                ks.append(k)
        return ks
    p = next((x for x in view_presets(M, T, C) if x["id"] == sel.get("preset")), None)
    return p["keys"] if p else dflt
