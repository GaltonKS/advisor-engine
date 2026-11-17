"""The FIS skeleton's engine tests (test/engine.test.js), ported: the engine's contract.

One test per JS test, same name (as the docstring), same assertions and tolerances. They run
on the skeleton's worked examples (tests/fixtures/skeleton/<example>/{brain,data}.json).

Two kinds of difference, both deliberate:
- The engine returns figures, not text. Where the JS reads a measure's text (`m.t`), these
  read `value` / `available`; where it greps a check message, they render the template.
- The assistant's prompt builders (`rulesPrompt`, `explainPrompt`, `plainWords`,
  `ideaFromAI`) are Phase 4 work. Their assertions are kept as comments marked "Phase 4",
  and the one test that is only about them is skipped with that reason.
"""

import copy
import json
from pathlib import Path

import pytest

from advisor_engine import brain_check, cash, charts, compare, measures, model
from advisor_engine.jsmath import mean, total
from advisor_engine.levers import FOCUS, blank_idea
from skeleton_format import render

FIX = Path(__file__).parent / "fixtures" / "skeleton"


def load(ex):
    B = json.loads((FIX / ex / "brain.json").read_text())
    D = json.loads((FIX / ex / "data.json").read_text())
    return {"BRAIN": B, "DATA": D, "M": model.build(D, B)}


HS, BS = load("home-services"), load("barbershop")


def near(a, b, tol=0.01, msg=""):
    assert abs(a - b) <= tol, f"{msg} expected {b} got {a}"


def fixes(B, D):
    return [c for c in brain_check.check(B, D) if c["level"] == "fix"]


def test_build_reads_the_snapshot():
    """build reads the snapshot"""
    M = HS["M"]
    assert len(M["weeks"]) == 52 and len(M["units"]) == 6 and len(M["offerings"]) == 6
    tot = total(r[4] for r in HS["DATA"]["sales"]["rows"])
    near(total(u["mo"] for u in M["units"]) * M["win"]["months"], tot, 1)
    assert 0.02 < M["fit"]["vol"] < 0.4, M["fit"]["vol"]
    assert M["fit"]["seasonN"] == 12
    assert M["coverage"] and abs(M["coverage"]["ratio"] - 1) < 0.02, "detail ties to the books"
    assert M["tie"]["status"] == "ties"


def test_todays_setup_adds_up():
    """today's setup adds up"""
    M = HS["M"]
    T = model.evaluate(M, blank_idea(M))
    near(T["sales"], total(u["mo"] for u in M["units"]), 0.01)
    near(T["profit"], T["sales"] - T["direct"] - T["fees"] - T["take"] - T["fixed"], 0.01, "profit identity")
    near(T["fixed"], 3200 + 3900 + 6400 + 2400 + 1300 + 850, 0.01)
    near(T["varMo"] + T["steadyMo"], T["businessIn"], 0.01, "swinging plus steady income is what the business takes in")


def test_a_price_rise_moves_sales_by_price_and_response_and_costs_by_volume_only():
    """a price rise moves sales by price and by response, and costs by volume only"""
    M = HS["M"]
    T = model.evaluate(M)
    o = next(x for x in M["off"] if x["name"] == "Service call")
    I = blank_idea(M)
    I["price"]["Service call"] = o["price"] * 1.1
    I["elast"] = -0.5
    E = model.evaluate(M, I)
    s0 = next(x for x in T["offerings"] if x["name"] == "Service call")["sales"]
    s1 = next(x for x in E["offerings"] if x["name"] == "Service call")["sales"]
    near(s1 / s0, 1.1 * 0.95, 1e-9)
    near(E["direct"] - T["direct"], s0 * o["cost"] * (0.95 - 1), 0.01, "direct cost follows volume")


def test_removing_a_unit_with_and_without_sales_staying():
    """removing a unit, with and without sales staying"""
    M = HS["M"]
    T = model.evaluate(M)
    u = M["units"][2]["name"]
    I = blank_idea(M)
    I["terms"][u] = {"on": False}
    E = model.evaluate(M, I)
    near(E["sales"], T["sales"] - model.row_of(T, u)["sales"], 0.01)
    assert E["n"] == T["n"] - 1
    I["keep"] = 50
    E2 = model.evaluate(M, I)
    near(E2["sales"], T["sales"] - model.row_of(T, u)["sales"] / 2, 0.01)
    near(E2["kept"], model.row_of(T, u)["sales"] / 2, 0.01)


def test_commission_against_booth_rent():
    """commission against booth rent"""
    M = BS["M"]
    T = model.evaluate(M)
    u = M["units"][0]["name"]
    I = blank_idea(M)
    I["terms"][u] = {"type": "fixedIn", "rent": 300}
    E = model.evaluate(M, I)
    r, t = model.row_of(E, u), model.row_of(T, u)
    near(r["biz"], 300 * 52 / 12, 0.01)
    near(r["take"], r["sales"] * (1 - E["vr"]) - 300 * 52 / 12, 0.01)
    near(t["take"], t["sales"] * 0.5, 0.01)
    near(t["biz"], t["sales"] * (0.5 - T["vr"]), 0.01)
    near(r["steady"], 1300, 0.01)
    assert r["vr"] == 0


def test_adding_a_unit_on_a_wage():
    """adding a unit on a wage"""
    M = HS["M"]
    T = model.evaluate(M)
    I = blank_idea(M)
    I["added"].append({"name": "New tech", "mo": 10000, "type": "wage", "wage": 5800})
    E = model.evaluate(M, I)
    near(E["sales"], T["sales"] + 10000, 0.01)
    near(E["profit"] - T["profit"], 10000 * (1 - M["blendCost"] - T["vr"]) - 5800, 0.01)


def test_cash_range_is_ordered_repeatable_and_moves_the_right_way():
    """cash range is ordered, repeatable, and moves the right way"""
    M = HS["M"]
    T = model.evaluate(M)
    C, C2 = cash.cash_range(M, T), cash.cash_range(M, T)
    assert C["p50"] == C2["p50"]
    assert len(C["dates"]) == 14 and C["p50"][0] == M["F"]["cash0"]
    for i in range(14):
        assert C["p10"][i] <= C["p50"][i] <= C["p90"][i]
    assert 0 <= C["pBreach"] <= 1
    assert C["seed"] == 77 and C["paths"] == 2000, "the result records its seed and path count"
    I = blank_idea(M)
    I["once"].append({"l": "Van", "amt": -38000, "wk": 4})
    Cv = cash.cash_range(M, model.evaluate(M, I))
    near(Cv["p50"][3], C["p50"][3], 0.01)
    near(Cv["p50"][4], C["p50"][4] - 38000, 0.01)
    near(Cv["p50"][13], C["p50"][13] - 38000, 0.01)
    assert Cv["pBreach"] >= C["pBreach"]
    J = blank_idea(M)
    J["fixed"].append({"l": "New lease", "mo": 2000})
    Cj = cash.cash_range(M, model.evaluate(M, J))
    near(Cj["p50"][13], C["p50"][13] - 2000 * 12 / 52 * 13, 0.5)
    # expected weekly drift ties to the monthly bottom line, give or take the seasons of the next 13 weeks
    drift = (C["p50"][13] - C["p50"][0]) / 13 * 52 / 12
    assert abs(drift - T["afterDraw"]) < abs(T["varMo"]) * 0.25 + 500


def test_the_bridge_adds_up_to_the_change_in_the_bottom_line():
    """the bridge adds up to the change in the bottom line"""
    M = BS["M"]
    T = model.evaluate(M)
    I = blank_idea(M)
    I["shock"] = -10
    I["price"]["Adult haircut"] = 50
    I["terms"][M["units"][0]["name"]] = {"type": "fixedIn", "rent": 280}
    I["terms"][M["units"][6]["name"]] = {"on": False}
    I["keep"] = 30
    I["added"].append({"name": "Weekend barber", "mo": 2500, "type": "share", "rate": 45})
    I["rate"]["supplies"] = 4
    I["fixed"].append({"l": "Rent increase", "mo": 500})
    E = model.evaluate(M, I)
    br = compare.bridge(M, T["idea"], I)
    near(br["from"], T["profit"], 0.01)
    near(br["to"], E["profit"], 0.01)
    near(br["from"] + total(s["delta"] for s in br["steps"]), E["profit"], 0.01)
    assert len(br["steps"]) >= 5, " | ".join(s["label"] for s in br["steps"])


@pytest.mark.skip(reason="Phase 4: ideaFromAI (the assistant's scenario -> an idea) is assistant work")
def test_the_assistants_scenario_becomes_a_checked_idea():
    """the assistant's scenario becomes a checked idea

    Phase 4 port of FIS.ideaFromAI: pay kinds the brain does not offer are ignored, names
    are matched case-insensitively, rates clamp to 100, salesChangePct to -90, keepPct to
    100, a one-time week to the horizon, unknown names come back in `dropped`, and levers
    switched off in the brain are not applied.
    """


def test_every_library_metric_and_every_example_brain_renders():
    """every library metric and every example brain renders"""
    for X in (HS, BS):
        M = X["M"]
        T = model.evaluate(M)
        C = cash.cash_range(M, T)
        for k in list(measures.LIBRARY) + list(X["BRAIN"].get("metrics") or {}):
            m = measures.measure(M, k, T, C)
            assert m["label"], k
            assert m["value"] is None or isinstance(m["value"], (int, float)), k
            text = m["label"] + render(m["note"], m["vars"])
            assert "undefined" not in text and "NaN" not in text and "None" not in text, (k, m)
        for k in X["BRAIN"]["headline"]:
            assert measures.measure(M, k, T, C)["available"], k
        assert fixes(X["BRAIN"], X["DATA"]) == [], "no blocking checks"
        # Phase 4: rulesPrompt(M, blankIdea) names the company and stays under 12,000 characters;
        # explainPrompt(...) under 9,000; plainWords(...) gives at least two lines; none says undefined or NaN.
        I = blank_idea(M)
        I["shock"] = -15
        E = model.evaluate(M, I)
        assert charts.chart_for(M, "cash", T, E) == "cash"
        assert charts.chart_for(M, "profit", T, E) == "bridge"


def test_the_owners_corrections_flow_through():
    """the owner's corrections flow through"""
    M = model.build(HS["DATA"], HS["BRAIN"], facts={"rent": 4200, "fees": 3.4}, confirmed={"rent": True}, basis=3)
    T, T0 = model.evaluate(M), model.evaluate(HS["M"])
    near(T["fixed"] - T0["fixed"], 1000, 0.01)
    assert next(f for f in M["FL"] if f["k"] == "rent")["confirmed"] is True
    assert M["win"]["nWin"] == 13
    near(T["vr"] - T0["vr"], 0.01, 1e-9)


# --- overview views: the owner picks four numbers ---

def test_overview_pick_list():
    """overview pick-list: only numbers that work on this data, plenty of them, each in a group"""
    for X in (HS, BS):
        T = model.evaluate(X["M"])
        C = cash.cash_range(X["M"], T)
        lst = measures.view_measures(X["M"], T, C)
        assert len(lst) >= 14, len(lst)
        assert all(x["key"] and x["label"] and x["group"] and x["available"] for x in lst)
        assert len({x["key"] for x in lst}) == len(lst)
        assert all(x["key"] not in measures.LIBRARY or x["group"] in measures.VIEW_GROUPS for x in lst)
        assert all(any(x["key"] == k for x in lst) for k in X["BRAIN"]["headline"])
    D = copy.deepcopy(HS["DATA"])
    del D["customers"], D["ar"]
    M2 = model.build(D, HS["BRAIN"])
    T2 = model.evaluate(M2)
    keys = [x["key"] for x in measures.view_measures(M2, T2, cash.cash_range(M2, T2))]
    assert "top_customers" not in keys and "ar_open" not in keys


def test_overview_presets():
    """overview presets: a decent list, exactly four numbers each, brain's own first"""
    for X in (HS, BS):
        M = X["M"]
        T = model.evaluate(M)
        C = cash.cash_range(M, T)
        ps = measures.view_presets(M, T, C)
        have = {x["key"] for x in measures.view_measures(M, T, C)}
        assert len(ps) >= (10 if X is HS else 8), len(ps)
        assert all(len(p["keys"]) == 4 and all(k in have for k in p["keys"]) and len(set(p["keys"])) == 4 for p in ps)
        assert len({p["id"] for p in ps}) == len(ps)
        assert ps[0]["own"] and ps[0]["id"] == X["BRAIN"]["views"][0]["id"]
        assert all(not p["own"] for p in ps[1:])
    D = copy.deepcopy(HS["DATA"])
    del D["ar"]
    M2 = model.build(D, HS["BRAIN"])
    T2 = model.evaluate(M2)
    ids = [p["id"] for p in measures.view_presets(M2, T2, cash.cash_range(M2, T2))]
    assert "owed" not in ids and "weekly" not in ids
    B = copy.deepcopy(HS["BRAIN"])
    B["views"] = [{"id": "cash", "name": "Our cash view", "keys": ["cash_on_hand", "bills_covered", "revenue_month", "profit_month"]}]
    M3 = model.build(HS["DATA"], B)
    T3 = model.evaluate(M3)
    cp = [p for p in measures.view_presets(M3, T3, cash.cash_range(M3, T3)) if p["id"] == "cash"]
    assert len(cp) == 1 and cp[0]["name"] == "Our cash view"


def test_which_four_numbers_show():
    """which four numbers show: default, a preset, custom, and every fallback"""
    M = HS["M"]
    T = model.evaluate(M)
    C = cash.cash_range(M, T)

    def K(s):
        return measures.view_keys(M, s, T, C)

    head = HS["BRAIN"]["headline"]
    assert K(None) == head and K({"preset": "default", "keys": []}) == head
    assert K({"preset": "cash", "keys": []}) == ["cash_on_hand", "cash_13w", "cash_breach", "bills_covered"]
    assert K({"preset": "no-such-view"}) == head
    assert K({"preset": "custom", "keys": ["profit_month", "cash_on_hand", "revenue_month", "ap_open"]}) == \
        ["profit_month", "cash_on_hand", "revenue_month", "ap_open"]
    dup = K({"preset": "custom", "keys": ["profit_month", "profit_month", "bogus", "cash_on_hand"]})
    assert len(dup) == 4 and len(set(dup)) == 4 and dup[:2] == ["profit_month", "cash_on_hand"]
    assert K({"preset": "custom", "keys": ["bogus"]}) == head
    vm = measures.view_measures(M, T, C)
    assert len(K({"preset": "custom", "keys": [vm[i]["key"] for i in range(6)]})) == 4
    assert K("garbage") == head
    for k in K({"preset": "carry"}):
        assert measures.measure(M, k, T, C)["available"]


def test_a_brains_own_number_is_offered_under_its_own_group():
    """a brain's own number is offered in the pick-list under its own group

    The JS test gives the brain two closures. A brain holds no code here, so its own numbers
    are library measures with parameters; an unknown kind is never offered."""
    B = copy.deepcopy(HS["BRAIN"])
    B["metrics"] = {"test_number": {"l": "A number only this company has", "metric": "top_n_share", "n": 2},
                    "gated": {"l": "Not workable yet", "metric": "no_such_measure"}}
    B["views"] = [{"id": "mine", "name": "Mine", "keys": ["test_number", "revenue_month", "profit_month", "cash_on_hand"]}]
    M = model.build(HS["DATA"], B)
    T = model.evaluate(M)
    C = cash.cash_range(M, T)
    lst = measures.view_measures(M, T, C)
    o = next((x for x in lst if x["key"] == "test_number"), None)
    assert o and o["group"] == "Specific to this company" and o["label"] == "A number only this company has"
    assert not any(x["key"] == "gated" for x in lst)
    assert measures.view_presets(M, T, C)[0]["id"] == "mine"


def test_the_brain_check_flags_a_bad_view():
    """the brain check flags a bad view"""
    B = copy.deepcopy(HS["BRAIN"])
    B["views"] = [{"id": "x", "name": "Three and a typo", "keys": ["revenue_month", "nope", "cash_on_hand"]},
                  {"id": "custom", "name": "Clash", "keys": ["a", "b", "c", "d"]}, {"name": "No id", "keys": []}]
    f = "\n".join(render(x["msg"], x["vars"]) for x in fixes(B, HS["DATA"]))
    for pat in ("exactly four", "“nope”", "already taken", "needs an id and a name"):
        assert pat in f, pat
    assert fixes(HS["BRAIN"], HS["DATA"]) == []


# --- overview panels ---

def test_panel_pick_list():
    """panel pick-list: only panels that can be drawn on this data, each in a known group"""
    hs, bs = charts.panel_list(HS["M"]), charts.panel_list(BS["M"])

    def keys(x):
        return [p["key"] for p in x]

    assert len(hs) >= 10 and len(bs) >= 8
    for p in hs:
        assert p["l"] and p["kind"] and p["group"] in charts.PANEL_GROUPS
    assert "ar" in keys(hs) and "ap" in keys(hs)
    assert "ar" not in keys(bs) and "ap" not in keys(bs)
    for k in charts.PANEL_DEFAULT:
        assert k in keys(hs) and k in keys(bs)
    no_books = copy.deepcopy(HS["DATA"])
    no_books["pnl"] = {"months": [], "lines": []}
    assert not set(keys(charts.panel_list(model.build(no_books, HS["BRAIN"])))) & {"books", "income", "net", "costs"}
    no_cust = copy.deepcopy(HS["DATA"])
    del no_cust["customers"]
    assert "concentration" not in keys(charts.panel_list(model.build(no_cust, HS["BRAIN"])))


def test_panel_layouts():
    """panel layouts: a decent list, exactly four distinct real panels each, brain's own first"""
    for c in (HS, BS):
        ps = charts.panel_presets(c["M"])
        have = {p["key"] for p in charts.panel_list(c["M"])}
        assert len(ps) >= (8 if c is HS else 6) and ps[0]["own"]
        for p in ps:
            assert len(p["keys"]) == 4 and len(set(p["keys"])) == 4 and all(k in have for k in p["keys"])
        assert len({p["id"] for p in ps}) == len(ps)
        assert not any(p["id"] in ("default", "custom") for p in ps)
    assert not any("ar" in p["keys"] for p in charts.panel_presets(BS["M"]))
    assert any(p["id"] == "cashfirst" for p in charts.panel_presets(HS["M"]))


def test_which_four_panels_show():
    """which four panels show: default, a layout, custom, and every fallback"""
    M, dflt = HS["M"], charts.PANEL_DEFAULT
    assert charts.panel_keys(M, None) == dflt and charts.panel_keys(M, {"preset": "default"}) == dflt
    assert charts.panel_keys(M, {"preset": "monthly"}) == next(p for p in charts.PANEL_PRESETS if p["id"] == "monthly")["keys"]
    assert charts.panel_keys(M, {"preset": "no-such-layout"}) == dflt
    assert charts.panel_keys(M, {"preset": "custom", "keys": ["net", "ar", "tickets", "books"]}) == ["net", "ar", "tickets", "books"]
    junk = charts.panel_keys(M, {"preset": "custom", "keys": ["net", "net", "nope", "<script>", "ar"]})
    assert len(junk) == 4 and len(set(junk)) == 4 and junk[:2] == ["net", "ar"]
    assert charts.panel_keys(M, {"preset": "custom", "keys": []}) == dflt
    assert charts.panel_keys(M, {"preset": "custom"}) == dflt
    bk = charts.panel_keys(BS["M"], {"preset": "custom", "keys": ["ar", "ap", "net"]})
    assert "ar" not in bk and "ap" not in bk and len(bk) == 4 and bk[0] == "net"
    assert charts.panel_keys(BS["M"], {"preset": "cashfirst"}) == charts.PANEL_DEFAULT


def test_a_brain_can_set_its_own_standard_four_and_layouts():
    """a brain can set its own standard four and its own layouts"""
    B = copy.deepcopy(HS["BRAIN"])
    B["panels"] = ["books", "income", "net", "costs"]
    M = model.build(HS["DATA"], B)
    assert charts.panel_keys(M, None) == B["panels"] and charts.panel_keys(M, {"preset": "default"}) == B["panels"]
    B["panels"] = ["books", "books", "nope", "net"]
    assert charts.panel_keys(model.build(HS["DATA"], B), None) == charts.PANEL_DEFAULT
    B["panels"] = None
    B["panelViews"] = [{"id": "mine", "name": "Mine", "keys": ["costs", "net", "ar", "weekly"]},
                       {"id": "weekly-ish", "name": "Bad", "keys": ["costs", "net", "ar", "zzz"]}]
    ps = charts.panel_presets(model.build(HS["DATA"], B))
    assert ps[0]["id"] == "mine" and ps[0]["own"] and not any(p["id"] == "weekly-ish" for p in ps)
    assert any(p["id"] == "chairs" and p["own"] for p in charts.panel_presets(BS["M"]))


def test_the_cost_list_reads_the_profit_and_loss():
    """the cost list reads the Profit and Loss: costs only, averaged, biggest first"""
    c = charts.cost_lines(HS["M"])
    assert len(c) >= 6
    assert all(c[i - 1]["v"] >= c[i]["v"] for i in range(1, len(c)))
    income = [ln["account"] for ln in HS["DATA"]["pnl"]["lines"] if ln["group"] == "income"]
    assert all(x["l"] not in income for x in c)
    L = next(ln for ln in HS["DATA"]["pnl"]["lines"] if ln["account"] == c[0]["l"])
    near(c[0]["v"], mean(L["values"][-3:]), 0.01, "three-month average")
    near(total(x["v"] for x in charts.cost_lines(HS["M"], 3)),
         mean(m["cogs"] + m["expense"] for m in HS["M"]["pnl"]["months"][-3:]), 1, "the lines add up to the books' costs")


def test_the_brain_check_flags_a_bad_standard_four_and_bad_layouts():
    """the brain check flags a bad standard four and bad layouts"""
    B = copy.deepcopy(HS["BRAIN"])
    B["panels"] = ["weekly", "weekly", "typo"]
    B["panelViews"] = [{"id": "a", "name": "Short", "keys": ["weekly", "cash"]},
                       {"id": "default", "name": "Clash", "keys": ["weekly", "cash", "books", "net"]}, {"name": "No id", "keys": []},
                       {"id": "b", "name": "Typo", "keys": ["weekly", "cash", "books", "nope"]},
                       {"id": "c", "name": "Twice", "keys": ["weekly", "weekly", "books", "net"]}]
    f = "\n".join(render(x["msg"], x["vars"]) for x in fixes(B, HS["DATA"]))
    for pat in ("BRAIN.panels is the standard four", "exactly four", "already taken", "needs an id and a name",
                "“nope”, which is not a panel", "lists “weekly” twice"):
        assert pat in f, pat
    B2 = copy.deepcopy(BS["BRAIN"])
    B2["panelViews"] = [{"id": "owed", "name": "Owed", "keys": ["ar", "ap", "weekly", "cash"]}]
    notes = "\n".join(render(x["msg"], x["vars"]) for x in brain_check.check(B2, BS["DATA"]) if x["level"] == "note")
    assert "will be hidden: “ar” cannot be drawn on this data" in notes
    for c in (HS, BS):
        assert fixes(c["BRAIN"], c["DATA"]) == []


def test_focus_list_is_the_skeletons():
    assert FOCUS == ["sales", "profit", "units", "cash"]
