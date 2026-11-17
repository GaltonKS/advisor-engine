"""model.evaluate and bridge.bridge against the FIS skeleton.

Parity: tests/fixtures/skeleton/<example>/ideas.json is the skeleton's own evaluation and
bridge (capture_ideas.js) of about twenty ideas per example, as shipped and with the owner's
corrections on the 13-week basis, plus loose and malformed ideas on the barbershop and a bridge
from a non-blank starting point. Exactly equal: neither function goes through a log or a
random draw.

Deliberate difference: an idea of the wrong shape is refused with a plain reason
(levers.check_idea). The skeleton either throws on it or silently ignores the change; each
refused idea is checked against what the skeleton did with it.

The second half ports the skeleton's own contract tests for these functions
(test/engine.test.js), one per JS test, same assertions and tolerances.
"""

import pytest

from advisor_engine import model
from advisor_engine.bridge import bridge
from advisor_engine.jsmath import plain, total
from advisor_engine.levers import blank_idea, check_idea, levers_on, norm_idea
from parity import EXAMPLES, fixture, load, same

RUNS = ["base", "corrected"]
# wrong shapes: refused here, and what the skeleton did with each
REFUSED = {
    "price_not_an_object": "ignored",
    "terms_not_an_object": "ignored",
    "added_not_a_list": "TypeError",
    "fixed_not_a_list": "TypeError",
    "fixed_with_null": "skipped the null",
    "added_item_not_an_object": "added a unit with no sales",
    "term_entry_not_an_object": "ignored",
}


def _model(ex, run):
    want = fixture(ex, "ideas")["runs"][run]
    brain, data = load(ex)
    o = want["opts"]
    return want, model.build(data, brain, facts=o.get("facts"), confirmed=o.get("confirmed"), basis=o.get("basis", 12))


def _cases():
    for ex in EXAMPLES:
        for run in RUNS:
            for name in fixture(ex, "ideas")["runs"][run]["ideas"]:
                yield pytest.param(ex, run, name, id=f"{ex}-{run}-{name}")


@pytest.mark.parametrize("ex, run, name", list(_cases()))
def test_evaluate_and_bridge_match_the_skeleton(ex, run, name):
    want, M = _model(ex, run)
    case = want["ideas"][name]
    if name in REFUSED:
        js = case["evaluate"]
        if REFUSED[name] == "TypeError":
            assert js["error"].startswith("TypeError"), "the skeleton throws on it"
        else:
            assert "error" not in js, "the skeleton carries on"
        with pytest.raises(ValueError, match="idea"):
            model.evaluate(M, case["idea"])
        with pytest.raises(ValueError, match="idea"):
            bridge(M, None, case["idea"])
        return
    same(plain(model.evaluate(M, case["idea"])), case["evaluate"], f"{name}.evaluate")
    same(plain(bridge(M, blank_idea(M), case["idea"])), case["bridge"], f"{name}.bridge")


def test_every_refused_idea_is_in_the_fixture():
    ideas = fixture("barbershop", "ideas")["runs"]["base"]["ideas"]
    assert set(REFUSED) <= set(ideas)


def test_a_bridge_from_a_non_blank_starting_point_matches():
    want, M = _model("barbershop", "base")
    b = want["bridgeFromIdea"]
    same(plain(bridge(M, b["today"], b["idea"])), b["bridge"], "bridgeFromIdea")


# --- the idea's shape -----------------------------------------------------------------------

@pytest.mark.parametrize("idea, reason", [
    ("raise prices", "object of changes"),
    ({"price": ["Adult haircut", 50]}, "price"),
    ({"rate": 4}, "rate"),
    ({"once": {"amt": 5}}, "once"),
    ({"assets": [None]}, "assets"),
])
def test_check_idea_names_what_is_wrong(idea, reason):
    with pytest.raises(ValueError, match=reason):
        check_idea(idea)


def test_check_idea_accepts_none_a_blank_idea_and_loose_values():
    brain, data = load("barbershop")
    M = model.build(data, brain)
    check_idea(None)
    check_idea(blank_idea(M))
    check_idea({"price": {"Adult haircut": "50"}, "keep": "30", "shock": "abc", "added": [{"mo": "2600"}]})


def test_norm_idea_keeps_extra_keys_and_levers_ignore_a_bad_switch_list():
    assert norm_idea(None, {"name": "x", "extra": 1})["extra"] == 1
    assert levers_on({"levers": ["prices"]})["prices"] is True
    assert levers_on({"levers": {"prices": False}})["prices"] is False and levers_on({})["taxes"] is False


# --- the skeleton's contract tests (test/engine.test.js) ---------------------------------------

HS, BS = load("home-services"), load("barbershop")
M_HS, M_BS = model.build(HS[1], HS[0]), model.build(BS[1], BS[0])


def near(a, b, tol=0.01, msg=""):
    assert abs(a - b) <= tol, f"{msg} expected {b} got {a}"


def test_todays_setup_adds_up():
    """today's setup adds up"""
    M = M_HS
    T = model.evaluate(M, blank_idea(M))
    near(T["sales"], total(u["mo"] for u in M["units"]), 0.01)
    near(T["profit"], T["sales"] - T["direct"] - T["fees"] - T["take"] - T["fixed"], 0.01, "profit identity")
    near(T["fixed"], 3200 + 3900 + 6400 + 2400 + 1300 + 850, 0.01)
    near(T["varMo"] + T["steadyMo"], T["businessIn"], 0.01, "swinging plus steady income is what the business takes in")


def test_a_price_rise_moves_sales_by_price_and_response_and_costs_by_volume_only():
    """a price rise moves sales by price and by response, and costs by volume only"""
    M = M_HS
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
    M = M_HS
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
    M = M_BS
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
    M = M_HS
    T = model.evaluate(M)
    I = blank_idea(M)
    I["added"].append({"name": "New tech", "mo": 10000, "type": "wage", "wage": 5800})
    E = model.evaluate(M, I)
    near(E["sales"], T["sales"] + 10000, 0.01)
    near(E["profit"] - T["profit"], 10000 * (1 - M["blendCost"] - T["vr"]) - 5800, 0.01)


def test_the_bridge_adds_up_to_the_change_in_the_bottom_line():
    """the bridge adds up to the change in the bottom line"""
    M = M_BS
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
    br = bridge(M, T["idea"], I)
    near(br["from"], T["profit"], 0.01)
    near(br["to"], E["profit"], 0.01)
    near(br["from"] + total(s["delta"] for s in br["steps"]), E["profit"], 0.01)
    assert len(br["steps"]) >= 5, " | ".join(s["label"] for s in br["steps"])


def test_the_owners_corrections_flow_through():
    """the owner's corrections flow through"""
    M = model.build(HS[1], HS[0], facts={"rent": 4200, "fees": 3.4}, confirmed={"rent": True}, basis=3)
    T, T0 = model.evaluate(M), model.evaluate(M_HS)
    near(T["fixed"] - T0["fixed"], 1000, 0.01)
    assert next(f for f in M["FL"] if f["k"] == "rent")["confirmed"] is True
    assert M["win"]["nWin"] == 13
    near(T["vr"] - T0["vr"], 0.01, 1e-9)


def test_evaluate_leaves_the_idea_it_was_given_alone():
    I = blank_idea(M_BS)
    I["terms"][M_BS["units"][0]["name"]] = {"on": False}
    before = repr(I)
    model.evaluate(M_BS, I)
    bridge(M_BS, None, I)
    assert repr(I) == before
