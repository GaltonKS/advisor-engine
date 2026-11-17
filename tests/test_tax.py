"""The tax module (advisor_engine.tax), with the FIS skeleton's own tests (test/tax.test.js)
ported one for one: same examples, same numbers, same tolerances.

The tax estimate is off unless BRAIN.levers.taxes is true. Rates and dates come from a
rules table, never from the engine's code.

The engine returns figures, never formatted text: where a JS assertion reads a measure,
a note or a check message, the test formats the figures with the skeleton's own rules
(tests/skeleton_format.py) and asserts on that.

Assertions about the assistant (`rulesPrompt`, `explainPrompt`, `plainWords`,
`ideaFromAI`, `resultSummary`) wait for the Phase 4 port; each is left in place as a
`# Phase 4:` comment holding the original assertion.
"""

import copy
import json
import math
import re
from datetime import date
from pathlib import Path

from advisor_engine import brain_check, business_config, cash, charts, levers, measures, model, tax
from skeleton_format import figure, render

FIX = Path(__file__).parent / "fixtures" / "skeleton"


def _load(example):
    brain = json.loads((FIX / example / "brain.json").read_text())
    data = json.loads((FIX / example / "data.json").read_text())
    return {"BRAIN": brain, "DATA": data, "M": model.build(data, brain)}


HS, BS, TX = _load("home-services"), _load("barbershop"), _load("home-services-tax")
clone = copy.deepcopy


def near(a, b, tol=0.01, msg=""):
    assert abs(a - b) <= tol, f"{msg} expected {b} got {a}"


def iso(d: date) -> str:
    return d.isoformat()


def tax_brain(extra=None):
    return {**TX["BRAIN"], **(extra or {})}


def metric_t(M, k, T, C):
    """The JS measure's `t`: the formatted figure, or "n/a"."""
    m = measures.measure(M, k, T, C)
    return "n/a" if m["value"] is None else figure(m)


def checks(brain, data):
    """FIS.check with each message rendered as the skeleton writes it."""
    return [{"level": c["level"], "msg": render(c["msg"], c["vars"])} for c in brain_check.check(brain, data)]


def RULES(rate):
    """A rules table made for the tests: the engine must take every rate and date from here."""
    return {"version": "test", "rows": [
        {"id": "t-rate", "jurisdiction": "Test", "entity": "c_corp", "kind": "rate", "value": rate, "fromYear": 2000, "effective": "test", "source": "test fixture"},
        {"id": "t-th", "jurisdiction": "Test", "entity": "c_corp", "kind": "estThreshold", "value": 500, "fromYear": 2000, "source": "test fixture"},
        {"id": "t-dates", "jurisdiction": "Test", "entity": "c_corp", "kind": "estDates", "value": [4, 6, 9, 12], "day": 15, "fromYear": 2000, "source": "test fixture"},
        {"id": "t-share", "jurisdiction": "Test", "entity": "c_corp", "kind": "estShare", "value": 0.25, "fromYear": 2000, "source": "test fixture"},
        {"id": "t-safe", "jurisdiction": "Test", "entity": "c_corp", "kind": "safeHarbor", "value": 1, "largeCorp": 1000000, "fromYear": 2000, "source": "test fixture"},
        {"id": "t-bal", "jurisdiction": "Test", "entity": "c_corp", "kind": "balanceDue", "value": 4, "juneYearEnd": 3, "day": 15, "fromYear": 2000, "source": "test fixture"},
        {"id": "t-meals", "jurisdiction": "Test", "entity": "*", "kind": "mealsDeductible", "value": 0.5, "fromYear": 2000, "source": "test fixture"}]}


def anchor(profile=None):
    """The blueprint's anchor case as a snapshot: a closed 2026 with $1,000,000 of book income."""
    D = clone(HS["DATA"])
    months = [f"2026-{m:02d}" for m in range(1, 13)]

    def spread(tot, per):
        return [per if i < 11 else tot - per * 11 for i in range(len(months))]

    D["meta"]["asOf"] = "2027-01-01"
    D["pnl"] = {"months": months, "lines": [
        {"account": "Sales", "group": "income", "values": spread(2400000, 200000)},
        {"account": "Operating costs", "group": "expense", "values": spread(1270000, 105833), "tax": "deductible", "taxStatus": "reviewed"},
        {"account": "Penalties and fines", "group": "expense", "values": spread(30000, 2500), "tax": "nondeductible"},
        {"account": "Depreciation expense", "group": "expense", "values": spread(100000, 8333), "tax": "depreciation"}]}

    def f(v):
        return {"v": v, "src": "Test fixture", "confirmed": True}

    D["tax"] = {"profile": {
        "entity": f("c_corp"), "yearEndMonth": f(12), "priorYearTax": f(0), "priorYearFull": f(True), "paidThisYear": f(0),
        "taxDepreciation": f(220000),
        "adjustments": [{"l": "Illustrative deduction", "amount": -40000, "reason": "From the blueprint's example", "status": "estimate"}],
        **(profile or {})}, "assets": []}
    M = model.build(D, tax_brain())
    T = model.evaluate(M, None)
    return D, M, T


def test_the_blueprints_anchor():
    """the blueprint's anchor: $1,000,000 of book income becomes $870,000 taxable and $182,700 of tax at the fixture's 21%"""
    _, M, T = anchor()
    X = tax.tax(M, T, base=T, rules=RULES(0.21))
    assert X["ok"] and X["computed"]

    def row(k):
        return next(r for r in X["bridge"] if r["k"] == k)["v"]

    near(row("book"), 1000000, 0.01, "book income")
    near(row("nondeductible"), 30000)
    near(row("bookDep"), 100000)
    near(row("taxDep"), -220000)
    near(row("adj"), -40000)
    near(X["cur"]["taxable"], 870000, 0.01, "taxable income")
    near(X["cur"]["liability"], 182700, 0.01, "tax")
    near(sum(r["v"] for r in X["bridge"] if not r.get("total")), X["cur"]["taxable"], 0.01, "the bridge adds up to taxable income")
    assert X["cur"]["label"] == "tax year ending Dec 2026"
    assert X["cur"]["forecast"] == 0, "a closed year uses the books only"


def test_the_rate_comes_from_the_rules_table_not_from_the_engine():
    """the rate comes from the rules table, not from the engine"""
    _, M, T = anchor()
    near(tax.tax(M, T, base=T, rules=RULES(0.30))["cur"]["liability"], 261000, 0.01)
    none = tax.tax(M, T, base=T, rules={"version": "empty", "rows": []})
    assert none["ok"] and not none["computed"] and none["cur"]["liability"] is None and re.search("no federal rate", none["why"]), \
        "no rule, no tax figure: " + none["why"]
    assert len(none["payments"]) == 0
    near(none["cur"]["taxable"], 870000 - 0, 0.01)


def test_every_shipped_rule_names_its_jurisdiction_entity_effective_date_and_source():
    """every shipped rule names its jurisdiction, entity, effective date and a source that was looked up"""
    R = business_config.TAX_RULES
    assert R["version"] and len(R["rows"]) >= 6
    for r in R["rows"]:
        for k in ("id", "jurisdiction", "entity", "kind", "effective", "source", "url", "checked"):
            assert r.get(k), f"{r.get('id')} has {k}"
        assert r.get("fromYear") and r.get("checkedYear"), f"{r['id']} says which tax years it was checked for"
        assert re.match(r"^https://www\.irs\.gov/", r["url"])
    kinds = {r["kind"] for r in R["rows"]}
    for k in ("rate", "estThreshold", "estDates", "estShare", "safeHarbor", "balanceDue", "mealsDeductible"):
        assert k in kinds, k
    assert tax.tax_rule(R, "rate", "c_corp", 2017) is None, "no rate before the rule's first year"
    assert tax.tax_rule(R, "rate", "s_corp", 2026) is None, "no entity rate for a pass-through"


def test_installments_plus_what_is_already_paid_equal_the_years_tax_on_the_right_dates():
    """installments plus what is already paid equal the year's tax, on the right dates"""
    M = TX["M"]
    T = model.evaluate(M, None)
    X = tax.tax(M, T, base=T)
    assert X["ok"] and X["computed"] and X["cur"]["liability"] > 500, "the example owes tax"

    def of(Y):
        return [p for p in X["payments"] if p["year"] == Y]

    near(sum(p["amount"] for p in of(X["cur"]["Y"])) + X["cur"]["paid"], X["cur"]["liability"], 0.5, "this tax year")
    near(sum(p["amount"] for p in of(X["next"]["Y"])), X["next"]["liability"], 0.5, "next tax year")
    assert [iso(p["date"]) for p in of(2026)] == ["2026-12-15"], "three installments are already behind the snapshot date"
    assert [iso(p["date"]) for p in of(2027)] == ["2027-04-15", "2027-06-15", "2027-09-15", "2027-12-15", "2028-04-17"], \
        "15th of the 4th, 6th, 9th and 12th months, then the balance on the next business day after Sat Apr 15, 2028"
    # next year's installments rest on the smaller of the two years' tax
    assert X["next"]["liability"] > X["cur"]["liability"]
    near(X["next"]["requiredAnnual"], X["cur"]["liability"], 0.01, "last year's tax is the base")
    for p in of(2027)[:4]:
        near(p["amount"], X["cur"]["liability"] / 4, 0.5)
    near(of(2027)[4]["amount"], X["next"]["liability"] - X["cur"]["liability"], 0.5, "the rest goes with the return")
    assert of(2027)[4]["kind"] == "balance"
    assert all(not i or X["payments"][i - 1]["date"] <= p["date"] for i, p in enumerate(X["payments"])), "in date order"
    near(X["cash12"], sum(p["amount"] for p in X["payments"] if X["asOf"] < p["date"] < date(2027, 11, 1)), 0.5,
         "the 12-month total is the payments in those months")
    cur, nxt = X["cur"], X["next"]
    near(sum(m["accrued"] for m in X["months"]),
         cur["liability"] * sum(m["book"] for m in cur["months"] if m["kind"] == "forecast") / cur["book"]
         + nxt["liability"] * sum(m["book"] for m in nxt["months"][:10]) / nxt["book"], 1, "monthly rows share out each year's tax")
    assert len(X["months"]) == 12
    assert X["months"][0]["key"] == "2026-11"


def test_too_little_tax_means_no_installments_a_large_corporation_cannot_lean_on_last_years_tax():
    """too little tax means no installments; a large corporation cannot lean on last year's tax"""
    _, Mt, Tt = anchor({"taxDepreciation": {"v": 1088000, "src": "t", "confirmed": True}})
    Xt = tax.tax(Mt, Tt, base=Tt, rules=RULES(0.21))
    near(Xt["cur"]["liability"], 420, 0.01)
    assert [p["kind"] for p in Xt["payments"] if p["year"] == 2026] == ["balance"], "under the threshold the whole amount goes with the return"
    _, Ml, Tl = anchor({"taxDepreciation": {"v": 2000000, "src": "t", "confirmed": True}})
    Xl = tax.tax(Ml, Tl, base=Tl, rules=RULES(0.21))
    assert Xl["cur"]["taxable"] < 0
    assert Xl["cur"]["liability"] == 0, "a loss year owes nothing"
    assert len([p for p in Xl["payments"] if p["year"] == 2026]) == 0
    _, Mn, Tn = anchor({"priorYearTax": {"v": 1000, "src": "t", "confirmed": True}})
    Xn = tax.tax(Mn, Tn, base=Tn, rules=RULES(0.21))
    near(Xn["cur"]["requiredAnnual"], 1000, 0.01, "under $1M, installments may rest on last year's smaller tax")
    assert Xn["cur"]["large"] is False
    _, Mc, Tc = anchor({"largeCorp": {"v": True, "src": "t", "confirmed": True}, "priorYearTax": {"v": 1000, "src": "t", "confirmed": True}})
    Xc = tax.tax(Mc, Tc, base=Tc, rules=RULES(0.21))
    near(Xc["cur"]["requiredAnnual"], Xc["cur"]["liability"], 0.01, "flagged large: this year's tax is the base")
    assert any(n["k"] == "large" for n in Xc["notes"])


def test_a_fiscal_year_end_moves_every_date():
    """a fiscal year end moves every date"""
    D = clone(TX["DATA"])
    D["tax"]["profile"]["yearEndMonth"]["v"] = 6
    D["tax"]["profile"]["paidThisYear"]["v"] = 0
    M = model.build(D, tax_brain())
    T = model.evaluate(M, None)
    X = tax.tax(M, T, base=T)
    assert X["cur"]["label"] == "tax year ending Jun 2027"
    assert X["cur"]["Y"] == 2027
    assert [iso(p["date"]) for p in X["payments"] if p["year"] == 2027] == ["2026-12-15", "2027-03-15", "2027-06-15"], \
        "Oct 15 is behind the snapshot; then the 15th of the 6th, 9th and 12th months of a July-to-June year"
    near(sum(p["amount"] for p in X["payments"] if p["year"] == 2027), X["cur"]["liability"], 0.5)
    D["tax"]["profile"]["priorYearTax"]["v"] = 1000
    M2 = model.build(D, tax_brain())
    T2 = model.evaluate(M2, None)
    X2 = tax.tax(M2, T2, base=T2)
    last = [p for p in X2["payments"] if p["year"] == 2027][-1]
    assert last["kind"] == "balance"
    assert iso(last["date"]) == "2027-09-15", "the balance falls in the 3rd month after a June year end"


def test_an_ideas_change_flows_through_to_tax():
    """an idea's change flows through to tax"""
    M = TX["M"]
    T = model.evaluate(M, None)
    X = tax.tax(M, T, base=T)
    rate = X["next"]["rate"]
    up = levers.blank_idea(M)
    up["fixed"] = [{"l": "Saving", "mo": -1000}]
    Eu = model.evaluate(M, up)
    Xu = tax.tax(M, Eu, base=T)
    near(Eu["profit"] - T["profit"], 1000)
    near(Xu["next"]["taxable"] - X["next"]["taxable"], 12000, 0.01, "a full year of the change")
    near(Xu["next"]["liability"] - X["next"]["liability"], 12000 * rate, 0.01)
    near(Xu["cur"]["taxable"] - X["cur"]["taxable"], 2000, 0.01, "only the two months left in this tax year move")
    pay = levers.blank_idea(M)
    pay["salary"] = 3000
    Xp = tax.tax(M, model.evaluate(M, pay), base=T)
    near(X["next"]["taxable"] - Xp["next"]["taxable"], 36000, 0.01, "owner salary is a cost to the business")
    assert any(n["k"] == "salary" for n in Xp["notes"]), "and the reader is told payroll and personal tax are left out"
    buy = levers.blank_idea(M)
    buy["assets"] = [{"l": "Van", "cost": 40000, "wk": 4}]
    Eb = model.evaluate(M, buy)
    Xb = tax.tax(M, Eb, base=T)
    near(X["next"]["taxable"] - Xb["next"]["taxable"], 8000, 0.01, "one year of a five-year spread")
    near(X["cur"]["taxable"] - Xb["cur"]["taxable"], 40000 / 60 * 2, 0.01, "two months this year")
    assert any(n["k"] == "asset" for n in Xb["notes"])
    c0, cb = cash.cash_range(M, T), cash.cash_range(M, Eb)
    near(c0["p50"][13] - cb["p50"][13], 40000, 1, "and the purchase leaves the bank")
    near(cash.cash_range(M, Eb)["p50"][3], c0["p50"][3], 0.01, "not before its week")


def test_the_range_runs_the_same_rules_on_every_simulated_year_and_stays_ordered():
    """the range runs the same rules on every simulated year and stays ordered"""
    M = TX["M"]
    T = model.evaluate(M, None)
    X = tax.tax(M, T, base=T)
    assert X["range"] and X["range"]["paths"] >= 200
    for k in ("cur", "next"):
        r, t = X["range"][k]["liability"], X["range"][k]["taxable"]
        assert r["p10"] <= r["p50"] <= r["p90"], k + " tax ordered"
        assert t["p10"] <= t["p50"] <= t["p90"], k + " taxable ordered"
        assert r["p10"] >= 0
        assert r["p10"] <= X[k]["liability"] <= r["p90"], k + ": the expected figure sits inside the range"

    def w(k):
        return X["range"][k]["liability"]["p90"] - X["range"][k]["liability"]["p10"]

    assert w("next") > w("cur"), "a year with twelve months still to come is less certain than one with two"
    again = tax.tax(M, T, base=T)
    assert again["range"] == X["range"], "same draws every time"


def test_tax_payments_leave_the_bank_in_the_week_they_fall_due():
    """tax payments leave the bank in the week they fall due"""
    M = TX["M"]
    T = model.evaluate(M, None)
    X = tax.tax(M, T, base=T)
    c0, c1 = cash.cash_range(M, T), cash.cash_range(M, T, tax=X)
    dec = next((p for p in X["payments"] if iso(p["date"]) == "2026-12-15"), None)
    assert dec and dec["amount"] > 100
    near(c1["taxTotal"], dec["amount"], 0.01, "only the December installment falls inside 13 weeks")
    near(c0["p50"][13] - c1["p50"][13], dec["amount"], 0.5)
    wk = next(i for i, v in enumerate(c1["taxOut"]) if v > 0)
    # JS: assert.ok(c1.dates[wk] <= dec.date || true) -- always true, so not ported as an assertion
    near(c0["p50"][wk] - c1["p50"][wk], 0, 0.01, "nothing before that week")
    near(c0["p50"][wk + 1] - c1["p50"][wk + 1], dec["amount"], 0.5)
    assert c1["pBreach"] >= c0["pBreach"]
    assert c0["taxTotal"] == 0


def test_a_pass_through_shows_taxable_income_and_no_tax_figure():
    """a pass-through shows taxable income and no tax figure"""
    D = clone(TX["DATA"])
    D["tax"]["profile"]["entity"]["v"] = "s_corp"
    M = model.build(D, tax_brain())
    T = model.evaluate(M, None)
    X = tax.tax_of(M, T)
    assert X["ok"] and not X["computed"] and X["cur"]["liability"] is None
    assert re.search("lands on the owners", X["why"])
    assert math.isfinite(X["cur"]["taxable"]) and len(X["bridge"]) >= 3
    assert len(X["payments"]) == 0
    for k in ("tax_liability_year", "tax_next_payment", "tax_cash_90d"):
        assert metric_t(M, k, T, None) == "n/a"
    assert any(p["key"] == "tax" for p in charts.panel_list(M)), "the panel still shows the bridge"
    assert cash.cash_range(M, T, tax=X)["taxTotal"] == 0
    assert any(c["level"] == "note" and re.search("lands on the owners", c["msg"]) for c in checks(tax_brain(), D))
    # Phase 4: assert.match(FIS.plainWords(M, T, FIS.evaluate(M, Object.assign(FIS.blankIdea(M), { fixed: [{ l: "x", mo: 500 }] })), null, null).join(" "), /not worked out here/);


def test_every_cost_account_is_placed_in_the_open():
    """every cost account is placed in the open: reviewed, estimated from its name, or left as not reviewed"""
    M = TX["M"]
    tmap = tax.tax_map(M)

    def by(n):
        return next(x for x in tmap if x["account"] == n)

    assert len(tmap) == len([ln for ln in M["pnl"]["lines"] if ln["group"] != "income"]), "no account is dropped"
    assert by("Insurance")["status"] == "unmapped"
    assert by("Insurance")["cat"] is None
    assert re.search("taken as booked", by("Insurance")["label"])
    assert by("Meals")["cat"] == "meals"
    assert by("Meals")["rule"] == "us-fed-meals"
    assert by("Depreciation expense")["cat"] == "depreciation"
    assert by("Penalties and fines")["cat"] == "nondeductible"
    X = tax.tax_of(M)
    assert [u["account"] for u in X["unmapped"]] == ["Insurance"]
    meals = by("Meals")["vals"]
    near(next(r for r in X["bridge"] if r["k"] == "meals")["v"], sum(meals[2:]) * 0.5 + sum(meals[:2]) * 0.5, 0.01,
         "half of the meals in the tax year")
    # names only suggest, and say so
    D = clone(HS["DATA"])
    D["pnl"]["lines"] += [
        {"account": "Meals and Entertainment", "group": "expense", "values": [100 for _ in D["pnl"]["months"]]},
        {"account": "Amortization", "group": "expense", "values": [50 for _ in D["pnl"]["months"]]},
        {"account": "Odd one", "group": "expense", "values": [10 for _ in D["pnl"]["months"]], "tax": "made-up"}]
    m2 = tax.tax_map(model.build(D, tax_brain()))

    def b2(n):
        return next(x for x in m2 if x["account"] == n)

    assert b2("Meals and Entertainment")["cat"] == "meals"
    assert b2("Meals and Entertainment")["status"] == "estimate"
    assert re.search("needs review", b2("Meals and Entertainment")["reason"])
    assert b2("Amortization")["cat"] == "depreciation"
    assert b2("Odd one")["status"] == "unmapped"
    assert re.search("not one the system knows", b2("Odd one")["reason"])
    assert b2("Rent")["status"] == "unmapped"
    assert tax.tax_guess("Rent") is None
    assert tax.tax_guess("Parking fines")[0] == "nondeductible"
    assert tax.tax_guess("Federal income tax")[0] == "incomeTax"


def test_the_check_flags_a_missing_profile_unconfirmed_fields_unplaced_accounts_and_government_ids():
    """the check flags a missing profile, unconfirmed fields, unplaced accounts and anything that looks like a government ID"""
    def has(c, lvl, rx):
        return any(x["level"] == lvl and re.search(rx, x["msg"]) for x in c)

    ok = checks(TX["BRAIN"], TX["DATA"])
    assert not any(c["level"] == "fix" for c in ok), "the example passes: " + " | ".join(c["msg"] for c in ok if c["level"] == "fix")
    assert has(ok, "note", "11 of 11 tax profile fields are not confirmed")
    assert has(ok, "note", "1 cost account has no tax category.*Insurance")
    assert has(ok, "note", "estimates? for the accountant")
    assert has(checks(tax_brain(), HS["DATA"]), "fix", "Taxes are switched on but.*No tax profile"), "taxes on, no profile"
    D = clone(TX["DATA"])
    del D["tax"]["profile"]["entity"]
    del D["tax"]["profile"]["paidThisYear"]
    assert has(checks(tax_brain(), D), "fix", "tax profile is missing: How the business is taxed, Estimated tax already paid")
    E = clone(TX["DATA"])
    E["tax"]["profile"]["owners"]["v"] = "EIN 12-3456789"
    assert has(checks(tax_brain(), E), "fix", "EIN or a Social Security number")
    assert has(checks(tax_brain({"levers": {**TX["BRAIN"]["levers"], "taxes": "yes"}}), TX["DATA"]), "fix", r"levers\.taxes is true or false")
    assert not re.search(r"\b\d{2}-\d{7}\b|\b\d{3}-\d{2}-\d{4}\b", json.dumps(TX["DATA"]["tax"], ensure_ascii=False)), "no government ID in the example"
    C = clone(TX["DATA"])
    for v in C["tax"]["profile"].values():
        # JS also sets .confirmed on the adjustments array (typeof "object"); a list has no
        # such property in Python, and nothing reads it there, so only the fields are set.
        if isinstance(v, dict):
            v["confirmed"] = True
    assert not any(re.search("tax profile fields are not confirmed", c["msg"]) for c in checks(tax_brain(), C))


def test_with_taxes_off_nothing_changes():
    """with taxes off nothing changes: no numbers, no panel, no prompt, no scenario parts"""
    assert levers.levers_on({})["taxes"] is False, "off unless a brain turns it on"
    third = {"BRAIN": HS["BRAIN"], "DATA": TX["DATA"], "M": model.build(TX["DATA"], HS["BRAIN"])}
    for P in (HS, BS, third):
        M = P["M"]
        T = model.evaluate(M, None)
        C = cash.cash_range(M, T)
        assert tax.tax_of(M, T) is None
        for k in ("tax_liability_year", "tax_next_payment", "tax_cash_90d"):
            assert metric_t(M, k, T, C) == "n/a"
        assert not any(re.match("^tax_", x["key"]) for x in measures.view_measures(M, T, C))
        assert not any(p["id"] == "taxes" for p in measures.view_presets(M, T, C))
        assert not any(p["key"] == "tax" for p in charts.panel_list(M))
        assert not any(p["id"] == "taxes" for p in charts.panel_presets(M))
        # Phase 4: const pr = FIS.rulesPrompt(M, null); assert.ok(!/ownerSalaryChange|assetPurchase|"tax"|Taxes:/.test(pr), "nothing about tax reaches the assistant");
        # Phase 4: const r = FIS.ideaFromAI(M, null, { name: "x", ownerSalaryChange: 5000, assetPurchase: [{ name: "Van", cost: 30000, week: 2 }] }); assert.equal(r.idea.salary, 0); assert.equal(r.idea.assets.length, 0);
        # Phase 4: assert.equal(FIS.resultSummary(M, T, T, C, C).tax, undefined);
        # Phase 4: assert.ok(!/income tax/i.test(FIS.plainWords(M, T, FIS.evaluate(M, Object.assign(FIS.blankIdea(M), { shock: 5 })), C, C).join(" ")));
        assert not any(re.search("tax", c["msg"], re.I) for c in checks(P["BRAIN"], P["DATA"])), "the check says nothing about tax"
        assert C["taxTotal"] == 0


def test_with_taxes_on_the_assistant_is_told_the_estimate_and_can_move_owner_pay_or_buy_equipment():
    """with taxes on the assistant is told the estimate, its limits and the sector's notes, and can move owner pay or buy equipment"""
    M = TX["M"]
    # Phase 4: const pr = FIS.rulesPrompt(M, null);
    # Phase 4: assert.match(pr, /"ownerSalaryChange"/); assert.match(pr, /"assetPurchase"/); assert.match(pr, /not tax advice/); assert.match(pr, /never decide how the law treats/); assert.match(pr, /Fines and penalties, such as parking tickets/);
    # Phase 4: assert.match(pr, /"taxedAs":"C corporation"/); assert.match(pr, /"accountsNotReviewed":\["Insurance"\]/);
    # Phase 4: const r = FIS.ideaFromAI(M, null, { name: "Pay and a van", ownerSalaryChange: 6000, assetPurchase: [{ name: "Van", cost: 38000, week: 4 }, { name: "Bad", cost: -5 }] });
    # Phase 4: assert.equal(r.idea.salary, 6000); assert.deepEqual(r.idea.assets, [{ l: "Van", cost: 38000, wk: 4 }]);
    # Phase 4: const E = FIS.evaluate(M, r.idea), res = FIS.resultSummary(M, T, E, null, null);
    # Phase 4: assert.ok(res.tax && res.tax.planningEstimateNotAdvice && res.tax.federalIncomeTaxIdea < res.tax.federalIncomeTaxToday && res.tax.caveats.length >= 2);
    # Phase 4: assert.match(FIS.plainWords(M, T, E, null, null).join(" "), /Federal income tax, as a planning estimate/);
    T = model.evaluate(M, None)
    for k in ("tax_liability_year", "tax_next_payment", "tax_cash_90d"):
        assert metric_t(M, k, T, None) != "n/a"
    assert any(p["id"] == "taxes" for p in measures.view_presets(M, T, cash.cash_range(M, T)))
    assert any(p["id"] == "taxes" for p in charts.panel_presets(M))
    assert charts.panel_default(M) == ["weekly", "cash", "tax", "books"]


def test_an_unknown_entity_or_an_llc_without_a_classification_is_not_a_pass_through():
    """an entity the system does not know, or an LLC that has not said how it is taxed, is not described as a pass-through"""
    def run(e):
        D = clone(TX["DATA"])
        D["tax"]["profile"]["entity"]["v"] = e
        M = model.build(D, tax_brain())
        T = model.evaluate(M, None)
        X = tax.tax_of(M, T)
        # Phase 4: const words = FIS.plainWords(M, T, FIS.evaluate(M, Object.assign(FIS.blankIdea(M), { fixed: [{ l: "x", mo: 500 }] })), null, null).join(" ");
        return {"D": D, "M": M, "T": T, "X": X, "check": checks(tax_brain(), D)}

    u = run("something_else")
    X = u["X"]
    assert X["ok"] and not X["computed"] and X["cur"]["liability"] is None and math.isfinite(X["cur"]["taxable"]) \
        and len(X["payments"]) == 0, "taxable income is still shown, no tax figure"
    assert re.search("names an entity the system does not know: “something_else”", X["why"])
    assert re.search("c_corp, s_corp, partnership, sole_prop, llc", X["why"])
    assert not re.search("lands on the owners", X["why"])
    assert any(c["level"] == "fix" and re.search("Tax: .*does not know", c["msg"]) for c in u["check"]), \
        "a value the system does not know blocks the pilot"
    assert len([c for c in u["check"] if re.search("Tax: ", c["msg"])]) == 1, "said once"
    # Phase 4: assert.match(u.words, /The tax on it is not worked out here\./); assert.doesNotMatch(u.words, /lands on the owners/);
    llc = run("llc")
    assert re.search("says LLC but not how it is taxed", llc["X"]["why"])
    assert not re.search("lands on the owners", llc["X"]["why"])
    assert any(c["level"] == "note" and re.search("says LLC but not how", c["msg"]) for c in llc["check"]) \
        and not any(c["level"] == "fix" for c in llc["check"]), "an LLC is a note, not a block"
    # Phase 4: assert.doesNotMatch(l.words, /lands on the owners/); assert.match(l.words, /not worked out here/);
    for e, rx in (("s_corp", "taxed as an S corporation, so the income tax lands on the owners"),
                  ("partnership", "taxed as a partnership, so"), ("sole_prop", "taxed as a sole proprietor, so")):
        r = run(e)
        assert re.search(rx, r["X"]["why"]), e
        # Phase 4: assert.match(r.words, /lands on the owners/, e);
        assert not any(c["level"] == "fix" for c in r["check"]), e + " is not a block"
    assert run("c_corp")["X"]["computed"] is True, "a C corporation is unchanged"


def test_check_stops_a_brain_that_has_taxes_off_but_names_a_tax_number_or_a_tax_panel():
    """check() stops a brain that has taxes off but names a tax number or a tax panel"""
    def off(extra=None):
        return tax_brain({"levers": {**TX["BRAIN"]["levers"], "taxes": False}, **(extra or {})})

    def fixes(c):
        return [x["msg"] for x in c if x["level"] == "fix"]

    # the example's headline names two tax numbers and its panels name the tax panel
    a = fixes(checks(off(), TX["DATA"]))
    for k in ("tax_liability_year", "tax_next_payment"):
        assert any(("Headline metric “" + k + "” is a tax number") in m and re.search("taxes are switched off", m) for m in a), \
            k + " in the headline"
    assert any(re.search(r"BRAIN\.panels uses “tax”, a tax panel, but taxes are switched off", m) for m in a), "the tax panel in the standard four"
    v = fixes(checks(off({"headline": ["revenue_month"], "panels": ["weekly", "cash", "net", "books"],
                          "views": [{"id": "v1", "name": "Money out", "keys": ["tax_cash_90d", "revenue_month", "profit_month", "cash_on_hand"]}]}),
                     TX["DATA"]))
    assert [re.sub(r"\s+", " ", m) for m in v] == [
        "View “Money out” uses “tax_cash_90d”, a tax number, but taxes are switched off in this brain. Set levers.taxes to true, or choose another number."]
    pv = checks(off({"headline": ["revenue_month"], "panels": ["weekly", "cash", "net", "books"],
                     "panelViews": [{"id": "p1", "name": "Money out", "keys": ["tax", "cash", "net", "books"]}]}), TX["DATA"])
    assert any(c["level"] == "note" and re.search(r"Panel layout “Money out” will be hidden: “tax” needs levers\.taxes to be true\.", c["msg"])
               for c in pv), "a layout naming the tax panel says why it is hidden"
    # the same brain with taxes on, a brain that names none, and a brain's own metric of that name: none of this fires
    assert fixes(checks(tax_brain(), TX["DATA"])) == [], "taxes on: nothing to fix"
    assert fixes(checks(HS["BRAIN"], HS["DATA"])) == [], "a brain that never names a tax item is untouched"
    own = fixes(checks(off({"headline": ["tax_liability_year", "revenue_month"], "panels": ["weekly", "cash", "net", "books"],
                            "metrics": {"tax_liability_year": {"l": "Our own", "t": "ok", "s": ""}}}), TX["DATA"]))
    assert not any(re.search("Headline metric", m) for m in own), "a brain's own number of that name is not a tax number"
