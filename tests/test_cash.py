"""cash.cash_range against the FIS skeleton's own cash ranges.

tests/fixtures/skeleton/<example>/cash.json comes from capture_cash.js: for each example, as
shipped and corrected on the 13-week basis, the range for ideas that move cash, a path-count
override and synthetic tax payments; on the barbershop, brain and data variants (11, 12, 30
and 70 weeks, a closed month, a 26-week horizon, the brain's own path count, a floor above
today's cash), each stored with its exact brain and data.

What must match: the dates, the horizon, the floor, the tax paid each week and in total and
the chance of dipping under the floor, exactly; the 10th, 50th and 90th percentiles each
week, to the cent. Their uniform draws are identical to the skeleton's (test_rand.py); the
normal draws and the weekly exp() can differ in the last bit between V8 and the C library.
If a percentile moves by more than a cent, fix the port (rand.py first), not the tolerance.
"""

import copy
import json

import pytest

from advisor_engine import cash, model
from advisor_engine.jsmath import add_days, parse_day, plain, total
from advisor_engine.levers import blank_idea
from parity import EXAMPLES, fixture, load, same

PCT = ("p10", "p50", "p90")


def _model(ex, run):
    r = fixture(ex, "cash")["runs"][run]
    brain, data = load(ex)
    o = r["opts"]
    return r, model.build(data, brain, facts=o.get("facts"), confirmed=o.get("confirmed"), basis=o.get("basis", 12))


def check(py, js, path):
    if js is None or "error" in (js or {}):
        assert py is None if js is None else False, f"{path}: the skeleton gave {js}"
        return
    same(plain(py), js, path, cents=PCT)


def _cases():
    for ex in EXAMPLES:
        for run in ("base", "corrected"):
            for name in fixture(ex, "cash")["runs"][run]["ideas"]:
                yield pytest.param(ex, run, name, id=f"{ex}-{run}-{name}")


@pytest.mark.parametrize("ex, run, name", list(_cases()))
def test_cash_range_matches_the_skeleton(ex, run, name):
    r, M = _model(ex, run)
    case = r["ideas"][name]
    check(cash.cash_range(M, model.evaluate(M, case["idea"])), case["cash"], f"{ex}.{run}.{name}")


@pytest.mark.parametrize("run", ["base", "corrected"])
@pytest.mark.parametrize("ex", EXAMPLES)
def test_path_override_and_tax_payments_match(ex, run):
    r, M = _model(ex, run)
    T = model.evaluate(M)
    check(cash.cash_range(M, T, paths=500), r["paths500"], "paths500")
    pays = [{"date": parse_day(p["date"]), "amount": p["amount"]} for p in r["tax"]["payments"]]
    check(cash.cash_range(M, T, tax={"ok": True, "payments": pays}), r["tax"]["cash"], "tax")
    check(cash.cash_range(M, T, tax={"ok": False, "payments": pays}), r["tax"]["notOk"], "tax not ok")


VARIANTS = list(fixture("barbershop", "cash")["variants"])


@pytest.mark.parametrize("name", VARIANTS)
def test_brain_and_data_variants_match(name):
    v = fixture("barbershop", "cash")["variants"][name]
    M = model.build(v["data"], v["brain"])
    check(cash.cash_range(M, model.evaluate(M)), v["today"], f"{name}.today")
    check(cash.cash_range(M, model.evaluate(M, v["idea"])), v["ideaCash"], f"{name}.idea")


def test_variants_cover_the_edges():
    v = fixture("barbershop", "cash")["variants"]
    assert v["weeks_11"]["today"] is None, "under 12 weeks there is no range"
    assert v["weeks_12"]["today"]["H"] == 13 and v["horizon_26"]["today"]["H"] == 26
    assert v["floor_above_cash"]["today"]["pBreach"] == 1


def test_a_month_with_no_sales_gives_a_range_of_nan_as_in_the_skeleton():
    """Known skeleton bug, matched for parity: a closed month in the window makes the
    de-seasoned level 0/0, and every figure of the range NaN. Fix in both after the gate."""
    v = fixture("barbershop", "cash")["variants"]["closed_february"]
    assert all(x is None for x in v["today"]["p50"][1:]), "what the skeleton does"
    M = model.build(v["data"], v["brain"])
    C = plain(cash.cash_range(M, model.evaluate(M)))
    assert all(x is None for x in C["p50"][1:]) and C["p50"][0] == v["today"]["p50"][0]


# --- what the skeleton does not have ---------------------------------------------------------

BS = load("barbershop")
M_BS = model.build(BS[1], BS[0])


@pytest.mark.parametrize("horizon", ["13", 0, -1, 13.5, True, None, 10 ** 6])
def test_a_horizon_that_is_not_a_whole_number_of_weeks_is_refused(horizon):
    brain = copy.deepcopy(BS[0])
    brain["assumptions"]["horizonWeeks"] = horizon
    M = model.build(BS[1], brain)
    with pytest.raises(ValueError, match="horizonWeeks"):
        cash.cash_range(M, model.evaluate(M))


@pytest.mark.parametrize("paths", ["500", -5, 2.5, 10 ** 9])
def test_a_path_count_that_is_not_a_whole_number_is_refused(paths):
    with pytest.raises(ValueError, match="paths"):
        cash.cash_range(M_BS, model.evaluate(M_BS), paths=paths)


def test_the_range_records_its_seed_paths_and_draft_method():
    C = cash.cash_range(M_BS, model.evaluate(M_BS))
    assert C["seed"] == 77 and C["paths"] == 2000 and "draft" in C["method_status"]
    json.dumps(plain(C), allow_nan=False)


# --- the skeleton's contract test (test/engine.test.js) ---------------------------------------

def near(a, b, tol=0.01, msg=""):
    assert abs(a - b) <= tol, f"{msg} expected {b} got {a}"


def test_cash_range_is_ordered_repeatable_and_moves_the_right_way():
    """cash range is ordered, repeatable, and moves the right way"""
    brain, data = load("home-services")
    M = model.build(data, brain)
    T = model.evaluate(M)
    C, C2 = cash.cash_range(M, T), cash.cash_range(M, T)
    assert C["p50"] == C2["p50"]
    assert len(C["dates"]) == 14 and C["p50"][0] == M["F"]["cash0"]
    for i in range(14):
        assert C["p10"][i] <= C["p50"][i] <= C["p90"][i]
    assert 0 <= C["pBreach"] <= 1
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


def test_tax_payments_land_in_their_week():
    T = model.evaluate(M_BS)
    last = M_BS["weeks"][-1]["dt"]
    C = cash.cash_range(M_BS, T, tax={"ok": True, "payments": [{"date": add_days(last, 7), "amount": 1000.0}]})
    base = cash.cash_range(M_BS, T)
    assert C["taxOut"][0] == 1000 and C["taxTotal"] == total(C["taxOut"]) == 1000
    near(C["p50"][1], base["p50"][1] - 1000, 1e-6)
