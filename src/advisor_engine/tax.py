"""A planning estimate of U.S. federal income tax. Never tax advice; off unless the brain
switches it on (`levers.taxes`). The FIS skeleton's tax engine, ported unchanged.

Three questions: what tax does current activity imply, when does the cash leave, and how
does an idea change it.

- The numbers come from the books (the Profit and Loss by month). What the books do not
  hold (how the business is taxed, its tax year, what has been paid) is the tax profile in
  `DATA.tax.profile`, each field `{v, src, confirmed}`. No government ID belongs there.
- Accounting profit is not taxable income. The bridge from one to the other is kept line by
  line: costs that are not deductible and the non-deductible share of meals are added back,
  book depreciation is swapped for tax depreciation (the accountant's figure when the
  profile gives it), and the profile's own adjustments are applied.
- Every cost account gets a tax category (`tax_map`): the one set when the data was
  prepared, or a suggestion from the account's name, marked as an estimate. An account
  nobody has placed stays visible as "Not reviewed" and is taken as booked; it is never
  quietly given a category.
- Rules decide the tax. Rates, thresholds and dates come from `TAX_RULES`
  (business_config), each row with the IRS page it was read from.
- The tax years: the one the latest book month falls in (`cur`) and the one after (`next`).
  Months the books have are used as booked; months before the books start are filled
  with the average month ("filled"); months after the latest are the same calendar month
  of the last year on hand plus the idea's change in monthly profit ("forecast").
- Only a C corporation's tax is worked out (21% on positive taxable income), with its four
  installments and the balance due with the return. For an S corporation, partnership or
  sole proprietor the bridge and taxable income are shown and the tax is not: it lands on
  the owners. An LLC that has not said how it is taxed gets the same, with its own reason.
- The range runs the same rules on simulated years, moving only the sales-driven part of
  income (the same swing as the cash range, seed `TAX_SEED`).

Not built: state income tax, sales tax, payroll taxes, the owners' own tax for pass-
throughs, losses and credits carried forward (recorded, not applied), depreciation
schedules and first-year write-offs, deferred tax, comparing business structures.

Labels that hold a figure are templates (`l`, `src`, `t` with `{name}`), the figure in
`vars`, as in measures.py. Years and month names are written in.
"""

from __future__ import annotations

import math
import re
from datetime import date

from .business_config import ASSET_LIFE_YEARS, METHOD_STATUS, METHOD_VERSION, TAX_PATHS, TAX_RULES, TAX_SEED
from .jsmath import (
    MONTHS, add_days, clamp, finite, get, js_join, js_max, js_min, js_or, js_round, js_string, mean, month_label, n2, parse_day, quant,
    to_number, total, truthy,
)
from .levers import levers_on
from .model import evaluate
from .rand import gauss, rng

TAX_CATS = {"deductible": "Deductible as booked", "nondeductible": "Not deductible", "meals": "Meals: partly deductible",
            "depreciation": "Depreciation (the books' figure)", "ownerPay": "Owner pay", "interest": "Interest",
            "incomeTax": "Income taxes", "other": "Other: needs a decision"}
TAX_ENTITIES = {"c_corp": "C corporation", "s_corp": "S corporation", "partnership": "Partnership",
                "sole_prop": "Sole proprietor", "llc": "LLC, tax classification not given"}
# Entities whose income tax is the owners' own. An LLC that has not said how it is taxed is not
# here: it could be any of them.
TAX_PASS = {"s_corp": "an S corporation", "partnership": "a partnership", "sole_prop": "a sole proprietor"}
TAX_STATUS = {"reviewed": "Reviewed", "estimate": "Estimate", "unsupported": "Unsupported", "unmapped": "Not reviewed"}
TAX_PROFILE = [
    ("entity", "How the business is taxed"), ("yearEndMonth", "Tax year ends"), ("state", "State"),
    ("owners", "Owners, now and expected"), ("priorYearTax", "Last year's federal income tax"),
    ("priorYearFull", "Last year was a full 12-month tax year"), ("paidThisYear", "Estimated tax already paid this tax year"),
    ("taxDepreciation", "Tax depreciation this tax year"),
    ("largeCorp", "Taxable income of $1M or more in any of the last 3 years"),
    ("nol", "Losses carried forward"), ("credits", "Credits carried forward"),
]


def _exp(x: float) -> float:
    """`Math.exp`: an infinity where Python's math.exp would raise OverflowError."""
    try:
        return math.exp(x)
    except OverflowError:
        return math.inf


def _plain_count(n) -> dict:
    """A number the skeleton writes straight into text (String(n): 2.5, 1500)."""
    return {"value": n if finite(n) else None, "unit": "count", "decimals": 0, "plain": True}


def _fig(value, unit, decimals=0):
    return {"value": value if finite(value) else None, "unit": unit, "decimals": decimals}


def tax_rule(rules: dict, kind: str, entity: str, year: int) -> dict | None:
    """The newest row of one kind that covers this entity and the year the tax year begins in."""
    rows = [r for r in (rules or {}).get("rows") or []
            if r.get("kind") == kind and r.get("entity") in ("*", entity)
            and (r.get("fromYear") is None or year >= r["fromYear"]) and (r.get("toYear") is None or year <= r["toYear"])]
    return rows[-1] if rows else None


def tax_guess(name) -> tuple[str, str] | None:
    """A first suggestion from an account's name, always an estimate for a person to review.
    It never decides the treatment."""
    n = _text(js_or(name, ""))
    if re.search(r"depreciat|amortiz", n, re.I):
        return "depreciation", "The account name says depreciation or amortization"
    if re.search(r"income tax|federal tax", n, re.I):
        return "incomeTax", "The account name says income tax"
    if re.search(r"penalt|\bfines?\b|lobby|political", n, re.I):
        return "nondeductible", "Fines, penalties, lobbying and political spending are generally not deductible"
    if re.search(r"meal", n, re.I):
        return "meals", ("Meals are partly deductible; any entertainment in this account is not, and should be split out"
                         if re.search(r"entertain", n, re.I) else "Business meals are partly deductible")
    if re.search(r"entertain", n, re.I):
        return "nondeductible", "Entertainment is generally not deductible"
    if re.search(r"officer|owner.{0,12}(salar|pay|comp|wage)|guaranteed payment", n, re.I):
        return "ownerPay", "The account name says owner or officer pay"
    if re.search(r"interest", n, re.I):
        return "interest", "The account name says interest"
    return None


def _plus(v) -> float:
    """`+v || 0`: JavaScript's number from anything, and 0 for NaN."""
    return js_or(to_number(v), 0)


def _text(v) -> str:
    """`String(v)` as JavaScript writes a value into text."""
    return v if isinstance(v, str) else js_string(v)


def _known(v, table) -> bool:
    """`table[v]` is set: a text key the table has. (JavaScript would also find names every
    object inherits, such as "toString"; those are not categories, statuses or entities.)"""
    return isinstance(v, str) and v in table


def tax_map(M: dict) -> list[dict]:
    """Every cost account in the books with its tax category, why, the rule behind it and
    whether anyone has reviewed it. "Other" lines are sign-flipped so a cost is positive."""
    out = []
    for line in M["pnl"].get("lines") or []:
        if line.get("group") == "income":
            continue
        sgn = -1 if line.get("group") == "other" else 1
        vals = [sgn * _plus(v) for v in (line.get("values") or [])]
        cat, status, reason, rule = None, "unmapped", "No one has placed this account yet. It is taken as booked.", ""
        if line.get("tax") is not None:
            if _known(line["tax"], TAX_CATS):
                cat = line["tax"]
                status = line.get("taxStatus") if _known(line.get("taxStatus"), TAX_STATUS) else "estimate"
                reason = js_or(line.get("taxNote"), "Set when the data was prepared")
                rule = js_or(line.get("taxRule"), "")
            else:
                reason = "The category “" + _text(line["tax"]) + "” is not one the system knows. It is taken as booked."
        else:
            g = tax_guess(line.get("account"))
            if g:
                cat, status, reason = g[0], "estimate", g[1] + ". Suggested from the account name; needs review."
        if cat == "meals" and not rule:
            rule = "us-fed-meals"
        out.append({**({"account": line["account"]} if "account" in line else {}), "group": line.get("group"), "cat": cat,
                    "label": TAX_CATS[cat] if cat else "Not reviewed: taken as booked", "status": status,
                    "statusLabel": TAX_STATUS[status], "reason": reason, "rule": rule, "vals": vals, "total12": total(vals[-12:])})
    return out


def tax_key(y: int, m: int) -> str:
    """Year and month (month may run past 12 or below 1) as "YYYY-MM"."""
    y += math.floor((m - 1) / 12)
    m = (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def _ym(k: str) -> tuple[int, int]:
    p = str(k).split("-")
    return int(p[0]), int(p[1])


def _biz_day(d: date) -> date:
    """A Saturday or Sunday due date moves to the Monday (holidays are not modelled)."""
    w = d.weekday()
    return add_days(d, 2) if w == 5 else add_days(d, 1) if w == 6 else d


def tax(M: dict, E: dict | None = None, *, base: dict | None = None, rules: dict | None = None,
        paths: int | None = None) -> dict:
    """The tax picture for an evaluated idea `E` (today's setup when None), against `base`
    (today's setup, for the idea's change in profit). See the module docstring."""
    rules = rules or TAX_RULES
    prof = get(M["DATA"].get("tax"), "profile")
    if not truthy(prof):
        return {"ok": False, "why": "No tax profile yet. It holds what the books do not: how the business is taxed, when its tax year ends and what has already been paid."}
    prof = prof if isinstance(prof, dict) else {}  # JavaScript reads every field of a non-object as missing
    pm = M["pnl"]["months"]
    if not pm:
        return {"ok": False, "why": "Needs the Profit and Loss by month."}
    E = E or evaluate(M, None)
    base = base or E
    delta = E["profit"] - base["profit"]
    idea = E.get("idea") or {}
    notes: list[dict] = []

    def pv(k):
        v = get(prof.get(k), "v")
        return v if v is not None and v != "" else None

    def pn(k):
        v = n2(pv(k))
        return v if finite(v) else 0

    entity = _text(js_or(pv("entity"), ""))
    ye = int(clamp(js_round(js_or(n2(pv("yearEndMonth")), 12)), 1, 12))
    tmap = tax_map(M)

    def ty_of(k):
        y, m = _ym(k)
        return y if m <= ye else y + 1

    def begin_year(Y):
        return _ym(tax_key(Y, ye - 11))[0]

    last_key = pm[-1]["key"]
    idx = {m["key"]: i for i, m in enumerate(pm)}
    meta = M["DATA"].get("meta") or {}
    if meta.get("asOf"):
        as_of = parse_day(meta["asOf"])
    else:
        ly, lm = _ym(last_key)
        ny, nm = _ym(tax_key(ly, lm + 1))
        as_of = date(ny, nm, 1)

    # the books, month by month: income before income tax, and the amounts the bridge needs
    act = []
    for i, m in enumerate(pm):
        a = {"nondeductible": 0, "meals": 0, "depreciation": 0, "incomeTax": 0}
        for x in tmap:
            if x["cat"] in a:
                a[x["cat"]] += js_or(x["vals"][i] if i < len(x["vals"]) else None, 0)
        act.append({"key": m["key"], "book": m["net"] + a["incomeTax"], "a": a})
    mean_act = {"book": mean(x["book"] for x in act),
                "a": {"nondeductible": mean(x["a"]["nondeductible"] for x in act), "meals": mean(x["a"]["meals"] for x in act),
                      "depreciation": mean(x["a"]["depreciation"] for x in act), "incomeTax": 0}}

    # an asset bought in the idea: the books spread its cost evenly over its life
    life = js_max(1, js_or(n2(get(M["BRAIN"].get("tax"), "assetLifeYears")), ASSET_LIFE_YEARS))
    bought = []
    for a in idea.get("assets") or []:
        if not truthy(a) or not n2(get(a, "cost")) > 0:
            continue
        d = add_days(as_of, 7 * (int(clamp(js_round(js_or(n2(a.get("wk")), 1)), 1, 520)) - 1))
        bought.append({"l": js_or(a.get("l"), "New equipment"), "cost": n2(a.get("cost")), "from": tax_key(d.year, d.month),
                       "mo": n2(a.get("cost")) / (life * 12)})
    salary = js_or(n2(idea.get("salary")), 0)

    def month(k):
        if k in idx:
            x = act[idx[k]]
            return {"key": k, "kind": "books", "book": x["book"], "a": x["a"], "extraDep": 0}
        src = None
        if k > last_key:
            _, km = _ym(k)
            for i in range(len(act) - 1, -1, -1):
                if _ym(act[i]["key"])[1] == km:
                    src = act[i]
                    break
        x = src or mean_act
        fc = k > last_key
        dep = total(b["mo"] for b in bought if k >= b["from"]) if fc else 0
        return {"key": k, "kind": "forecast" if fc else "filled", "book": x["book"] + (delta - salary - dep if fc else 0),
                "a": x["a"], "extraDep": dep}

    cur_y = ty_of(last_key)
    adj_list = prof.get("adjustments") if isinstance(prof.get("adjustments"), list) else []
    nd_accounts = js_join((x.get("account") for x in tmap if x["cat"] == "nondeductible"), ", ")

    def year(Y):
        keys = [tax_key(Y, ye - i) for i in range(11, -1, -1)]
        ms = [month(k) for k in keys]
        by = begin_year(Y)
        book = total(x["book"] for x in ms)
        nd = total(x["a"]["nondeductible"] for x in ms)
        meals = total(x["a"]["meals"] for x in ms)
        book_dep = total(x["a"]["depreciation"] for x in ms)
        extra = total(x["extraDep"] for x in ms)
        mr = tax_rule(rules, "mealsDeductible", entity, by)
        meals_add = meals * (1 - mr["value"]) if mr else 0
        td = prof.get("taxDepreciation")
        td = td if isinstance(td, dict) else None
        td_year = bool(td and td.get("v") is not None and td.get("v") != ""
                       and (Y == cur_y if td.get("taxYear") is None else to_number(td["taxYear"]) == Y))
        tax_dep = (js_or(n2(td["v"]), 0) if td_year else book_dep) + extra
        adjs = [a for a in adj_list if isinstance(a, dict) and finite(n2(a.get("amount")))
                and (a.get("taxYear") is None or to_number(a["taxYear"]) == Y)]
        est = sum(1 for x in ms if x["kind"] != "books")
        rows = [{"k": "book", "l": "Income in the books, before income tax", "v": book,
                 "src": "Profit and Loss" if est == 0 else "Profit and Loss, with {n} month" + ("" if est == 1 else "s") + " estimated",
                 "status": "", "vars": {} if est == 0 else {"n": _fig(est, "count")}}]
        if nd:
            rows.append({"k": "nondeductible", "l": "Costs that are not deductible, added back", "v": nd, "src": nd_accounts, "status": "estimate"})
        if meals:
            rows.append({"k": "meals", "l": "Meals: the {share} that is not deductible, added back" if mr else "Meals: no rule for this year, taken as booked",
                         "v": meals_add, "src": mr["source"] if mr else "", "rule": mr["id"] if mr else "",
                         "status": "estimate" if mr else "unmapped", "vars": {"share": _fig(1 - mr["value"], "share")} if mr else {}})
        if book_dep + extra:
            rows.append({"k": "bookDep", "l": "Depreciation in the books, added back", "v": book_dep + extra,
                         "src": "Profit and Loss" + (", plus the idea's purchase over {life} years" if extra else ""), "status": "",
                         "vars": {"life": _plain_count(life)} if extra else {}})
        if book_dep + extra or td_year:
            rows.append({"k": "taxDep", "l": "Depreciation allowed for tax", "v": -tax_dep,
                         "src": js_or(td.get("src"), "Tax profile") if td_year else "Taken as equal to the books until the accountant gives the tax figure",
                         "status": ("reviewed" if truthy(td.get("confirmed")) else "estimate") if td_year else "estimate"})
        for a in adjs:
            rows.append({"k": "adj", "l": _text(js_or(a.get("l"), "Adjustment")), "v": n2(a.get("amount")),
                         "src": js_or(a.get("src"), js_or(a.get("reason"), "Tax profile")), "rule": js_or(a.get("rule"), ""),
                         "status": a.get("status") if _known(a.get("status"), TAX_STATUS) else "estimate"})
        taxable = total(r["v"] for r in rows)
        rr = tax_rule(rules, "rate", entity, by) if entity == "c_corp" else None
        rows.append({"k": "taxable", "l": "Taxable income", "v": taxable, "total": True, "src": "", "status": ""})
        return {"Y": Y, "label": f"tax year ending {MONTHS[ye - 1]} {Y}", "beginYear": by, "months": ms, "keys": keys, "rows": rows,
                "book": book, "taxable": taxable, "rate": rr["value"] if rr else None, "rateRule": rr,
                "liability": js_max(0, taxable) * rr["value"] if rr else None,
                "filled": sum(1 for x in ms if x["kind"] == "filled"), "forecast": sum(1 for x in ms if x["kind"] == "forecast")}

    cur, nxt = year(cur_y), year(cur_y + 1)
    computed = cur["liability"] is not None
    why = ""
    if not computed:
        if not entity:
            why = "The tax profile does not say how the business is taxed."
        elif entity not in TAX_ENTITIES:
            why = ("The tax profile names an entity the system does not know: “" + entity + "”. The values are "
                   + ", ".join(TAX_ENTITIES) + ". Until it is one of those, the income tax is not worked out. Taxable income is shown.")
        elif entity == "llc":
            why = ("The tax profile says LLC but not how it is taxed (as a C corporation, S corporation, partnership or sole "
                   "proprietor). Until it does, the income tax is not worked out. Taxable income is shown.")
        elif entity in TAX_PASS:
            why = ("This business is taxed as " + TAX_PASS[entity] + ", so the income tax lands on the owners, not the business. "
                   "Working it out needs the owners' own figures, which is not built yet. Taxable income is shown.")
        else:
            why = f"The rules table has no federal rate for a tax year beginning in {cur['beginYear']}."

    # when the cash leaves: four installments and what is left with the return
    payments: list[dict] = []

    def sched(Yr, prior, prior_ok, large, paid, past):
        L = Yr["liability"]
        by = Yr["beginYear"]
        if L is None:
            return
        th, dt, sh = (tax_rule(rules, k, entity, by) for k in ("estThreshold", "estDates", "estShare"))
        sf, bd = tax_rule(rules, "safeHarbor", entity, by), tax_rule(rules, "balanceDue", entity, by)
        done, rap = paid, L
        use_prior = bool(sf and prior > 0 and prior_ok)
        big_co = bool(sf and large)
        if use_prior and not big_co:
            rap = js_min(L, prior * sf["value"])
        Yr["requiredAnnual"], Yr["large"], Yr["paid"] = rap, big_co, paid
        Yr["basis"] = "last year's tax, the smaller of the two" if use_prior and not big_co and rap < L else "this year's expected tax"
        if th and dt and sh and L >= th["value"]:
            for i, mth in enumerate(dt["value"]):
                y0, m0 = _ym(tax_key(Yr["Y"], ye - 12 + mth))
                when = _biz_day(date(y0, m0, dt.get("day") or 15))
                need = rap * sh["value"] * (i + 1)
                if when <= as_of and not past:
                    continue
                amt = js_max(0, need - done)
                if amt > 0.5:
                    payments.append({"date": when, "amount": amt, "year": Yr["Y"], "kind": "installment",
                                     "l": "Estimated tax, installment {n} of {of}",
                                     "vars": {"n": _fig(i + 1, "count"), "of": _fig(len(dt["value"]), "count")}, "rule": dt["id"]})
                    done += amt
        rest = L - done
        if rest > 0.5:
            mth = (bd["juneYearEnd"] if ye == 6 and bd.get("juneYearEnd") else bd["value"]) if bd else 4
            y0, m0 = _ym(tax_key(Yr["Y"], ye + mth))
            payments.append({"date": _biz_day(date(y0, m0, (bd and bd.get("day")) or 15)), "amount": rest, "year": Yr["Y"],
                             "kind": "balance", "l": "Balance due with the return", "vars": {}, "rule": bd["id"] if bd else ""})
        Yr["overpaid"] = -rest if rest < -0.5 else 0

    if computed:
        sf0 = tax_rule(rules, "safeHarbor", entity, cur["beginYear"])
        big = sf0.get("largeCorp") if sf0 else None
        sched(cur, pn("priorYearTax"), pv("priorYearFull") is not False, pv("largeCorp") is True, pn("paidThisYear"), False)
        sched(nxt, cur["liability"], cur["filled"] == 0, pv("largeCorp") is True or bool(big and cur["taxable"] >= big), 0, False)
    payments.sort(key=lambda p: p["date"])
    ahead = [p for p in payments if p["date"] > as_of]
    d90 = add_days(as_of, 90)

    # the range: the same rules on every simulated year; only the sales-driven part of income moves
    rng_out = None
    f = M["fit"]
    N = paths or TAX_PATHS
    if f and finite(f["vol"]) and f["vol"] > 0:
        g = gauss(rng(TAX_SEED))
        ph, sg = f["phi"] or 0, f["vol"]
        root, half = math.sqrt(1 - ph * ph), sg * sg / 2
        fk = sorted(x["key"] for x in cur["months"] + nxt["months"] if x["kind"] == "forecast")
        in_cur = set(cur["keys"])
        tc, tn = [0.0] * N, [0.0] * N
        for p in range(N):
            z = g()
            a = b = 0
            for k in fk:
                s = 0
                for _ in range(4):
                    z = ph * z + root * g()
                    s += _exp(sg * z - half)
                shk = E["varMo"] * (s / 4 - 1)
                if k in in_cur:
                    a += shk
                else:
                    b += shk
            tc[p] = cur["taxable"] + a
            tn[p] = nxt["taxable"] + b

        def q(v, Yr):
            def o(x):
                return {"p10": quant(x, 0.1), "p50": quant(x, 0.5), "p90": quant(x, 0.9)}
            lia = None if Yr["rate"] is None else [js_max(0, x) * Yr["rate"] for x in v]
            return {"taxable": o(v), "liability": o(lia) if lia else None}

        rng_out = {"cur": q(tc, cur), "next": q(tn, nxt), "paths": N, "seed": TAX_SEED}

    # the next 12 months, month by month
    months = []
    ly, lm = _ym(last_key)
    for i in range(1, 13):
        k = tax_key(ly, lm + i)
        Yr = cur if k in cur["keys"] else nxt
        x = Yr["months"][Yr["keys"].index(k)]
        share = x["book"] / Yr["book"] if Yr["book"] != 0 else 1 / 12
        months.append({"key": k, "label": month_label(k), "book": x["book"], "taxable": Yr["taxable"] * share,
                       "accrued": None if Yr["liability"] is None else Yr["liability"] * share,
                       "paid": total(p["amount"] for p in payments if tax_key(p["date"].year, p["date"].month) == k), "year": Yr["Y"]})

    # what a reader should know before trusting the figure
    for r in (cur["rateRule"], tax_rule(rules, "estDates", entity, nxt["beginYear"]) if computed else None):
        if r and r.get("checkedYear") and nxt["beginYear"] > r["checkedYear"] and not any(n["k"] == "carried" for n in notes):
            notes.append({"k": "carried", "t": f"The rules were last checked against the {r['checkedYear']} IRS forms and are carried forward to later years. Check them again when the newer forms are out.", "vars": {}})
    if cur["filled"]:
        notes.append({"k": "filled", "t": "{n} month" + ("" if cur["filled"] == 1 else "s")
                      + " of this tax year fall before the first month of the Profit and Loss on hand. They are filled with the average month.",
                      "vars": {"n": _fig(cur["filled"], "count")}})
    if pn("nol") or pn("credits"):
        notes.append({"k": "carry", "t": "Losses or credits carried forward are recorded in the tax profile but not yet applied: the rules that limit them are not in the rules table.", "vars": {}})
    if computed and (cur.get("large") or nxt.get("large")):
        notes.append({"k": "large", "t": "Taxable income of $1 million or more puts the business under the large-corporation rule: installments are based on this year's expected tax. The exception that lets the first installment use last year's tax is not applied.", "vars": {}})
    if salary:
        notes.append({"k": "salary", "t": "Owner salary is taken as deductible for the business. Payroll taxes on it and the owner's own income tax are not included.", "vars": {}})
    if bought:
        notes.append({"k": "asset", "t": "A purchase is spread evenly over {life} years, in the books and for tax. Writing more of it off in the first year is a choice to take up with the accountant; it is not modeled yet.",
                      "vars": {"life": _plain_count(life)}})
    notes.append({"k": "scope", "t": "Federal income tax only. State income tax, sales tax and payroll taxes are not included.", "vars": {}})

    unmapped = [x for x in tmap if x["status"] == "unmapped" and x["total12"] != 0]
    profile = []
    for k, label in TAX_PROFILE:
        p = prof.get(k)
        if not isinstance(p, dict) or p.get("v") is None or p.get("v") == "":
            continue
        v = p["v"]
        entry = {"k": k, "l": label, "v": v, "src": js_or(p.get("src"), ""), "confirmed": truthy(p.get("confirmed")), "vars": {}}
        if k == "entity":
            entry["text"] = TAX_ENTITIES[v] if _known(v, TAX_ENTITIES) else _text(v)
        elif k == "yearEndMonth":
            entry["text"] = MONTHS[int(clamp(js_round(js_or(n2(v), 12)), 1, 12)) - 1]
        elif isinstance(v, bool):
            entry["text"] = "Yes" if v else "No"
        elif isinstance(v, (int, float)):
            entry["text"], entry["vars"] = "{v}", {"v": _fig(v, "usd_whole")}
        else:
            entry["text"] = _text(v)
        profile.append(entry)
    return {
        "ok": True, "computed": computed, "why": why, "entity": entity,
        "entityLabel": TAX_ENTITIES[entity] if _known(entity, TAX_ENTITIES) else (entity or "Not given"), "yearEnd": ye, "asOf": as_of,
        "rulesVersion": js_or(rules.get("version"), ""), "cur": cur, "next": nxt, "bridge": cur["rows"], "map": tmap,
        "unmapped": unmapped, "payments": payments, "ahead": ahead, "nextPayment": ahead[0] if ahead else None,
        "cash90": total(p["amount"] for p in ahead if p["date"] <= d90), "cash12": total(m["paid"] for m in months),
        "range": rng_out, "months": months, "notes": notes, "profile": profile,
        "unconfirmed": [p for p in profile if not p["confirmed"]],
        "missing": [k for k in ("entity", "yearEndMonth", "priorYearTax", "paidThisYear") if pv(k) is None],
        "delta": delta, "salary": salary, "bought": bought,
        "rulesUsed": [r for r in rules.get("rows") or [] if r.get("entity") in ("*", entity)],
        "method_version": METHOD_VERSION, "method_status": METHOD_STATUS,
    }


def tax_of(M: dict, T: dict | None = None) -> dict | None:
    """The tax picture for today's setup, worked out once per evaluation, and only when the
    brain switches taxes on (None otherwise). A failure comes back as `{ok: False, why}`."""
    if not M or not truthy(levers_on(M["BRAIN"]).get("taxes")):
        return None
    if T is None:
        if "_T0" not in M:
            M["_T0"] = evaluate(M, None)
        T = M["_T0"]
    memo = M.setdefault("_tax_memo", {})
    hit = memo.get(id(T))
    if hit and hit[0] is T:
        return hit[1]
    try:
        X = tax(M, T, base=T)
    except Exception as e:  # noqa: BLE001 - the skeleton reports the reason rather than failing the page
        X = {"ok": False, "why": "The tax estimate could not be worked out: " + str(e)}
    memo[id(T)] = (T, X)
    return X
