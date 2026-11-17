"""The Overview against the FIS skeleton: measures, views, panels, cost lines, chart_for and
the brain check.

tests/fixtures/skeleton/<example>/overview.json is the skeleton's own output
(capture_overview.js) for each example built five ways; check_cases.json is its brain check
on broken and odd brains. The tax example is captured with taxes switched off (the estimate is
step 7).

The engine returns figures, never formatted text. Where the skeleton returns text ("$553.2K",
"12 of 17 facts ..."), Python returns a template and its figures in `vars`; this test formats
them with the skeleton's own rules (tests/skeleton_format.py) and compares the strings exactly.
Everything else is compared exactly too.
"""

import copy
import json

import pytest

from advisor_engine import brain_check, cash, measures, model, panels
from advisor_engine.analysis import apply_idea, company_profile
from advisor_engine.business_config import METHOD_STATUS
from advisor_engine.jsmath import plain
from parity import EXAMPLES, FIX, SKELETON_SHA, fixture, load, same
from skeleton_format import figure, render

RUNS = ["base", "no_customers_ar_ap", "no_books", "weeks_30", "weeks_11"]


def variant(ex, run):
    brain, data = load(ex)
    if ex == "home-services-tax":
        brain["levers"] = {**brain.get("levers", {}), "taxes": False}
    if run == "no_customers_ar_ap":
        for k in ("customers", "ar", "ap"):
            data.pop(k, None)
    elif run == "no_books":
        data["pnl"] = {"months": [], "lines": []}
    elif run in ("weeks_30", "weeks_11"):
        n, keep = len(data["sales"]["weeks"]), int(run[-2:])
        data["sales"]["weeks"] = data["sales"]["weeks"][-keep:]
        data["sales"]["rows"] = [[r[0] - (n - keep)] + r[1:] for r in data["sales"]["rows"] if r[0] >= n - keep]
    M = model.build(data, brain)
    T = model.evaluate(M)
    return brain, data, M, T, cash.cash_range(M, T)


def text(m):
    return "n/a" if m["value"] is None else figure(m)


CASES = [pytest.param(ex, run, id=f"{ex}-{run}") for ex in EXAMPLES for run in RUNS]


@pytest.mark.parametrize("ex, run", CASES)
def test_every_measure_says_what_the_skeleton_says(ex, run):
    want = fixture(ex, "overview")["runs"][run]["metrics"]
    _, _, M, T, C = variant(ex, run)
    for key, js in want.items():
        m = measures.measure(M, key, T, C)
        got = {"l": m["label"], "t": text(m), "s": render(m["note"], m["vars"]), "tone": m["tone"]}
        assert got == {"l": js["l"], "t": js["t"], "s": js.get("s") or "", "tone": js.get("tone") or ""}, key


@pytest.mark.parametrize("ex, run", CASES)
def test_views_match(ex, run):
    want = fixture(ex, "overview")["runs"][run]
    _, _, M, T, C = variant(ex, run)
    got = [(x["key"], x["label"], x["group"], text(x)) for x in measures.view_measures(M, T, C)]
    assert got == [(x["key"], x["l"], x["group"], x["t"]) for x in want["viewMetrics"]]
    same(measures.view_presets(M, T, C), want["viewPresets"], "viewPresets")
    for case in want["viewKeys"]:
        assert measures.view_keys(M, case["sel"], T, C) == case["keys"], case["sel"]


@pytest.mark.parametrize("ex, run", CASES)
def test_panels_and_cost_lines_match(ex, run):
    want = fixture(ex, "overview")["runs"][run]
    _, _, M, T, C = variant(ex, run)
    same(panels.panel_list(M), want["panelList"], "panelList")
    same(panels.panel_presets(M), want["panelPresets"], "panelPresets")
    assert panels.panel_default(M) == want["panelDefault"]
    for case in want["panelKeys"]:
        assert panels.panel_keys(M, case["sel"]) == case["keys"], case["sel"]
    same(panels.cost_lines(M), want["costLines"], "costLines")
    same(panels.cost_lines(M, 6), want["costLines6"], "costLines6")


@pytest.mark.parametrize("ex, run", CASES)
def test_chart_for_matches(ex, run):
    want = fixture(ex, "overview")["runs"][run]["chartFor"]
    _, _, M, T, _ = variant(ex, run)
    for name, case in want.items():
        E = model.evaluate(M, case["idea"])
        for focus, chart in case["focus"].items():
            assert panels.chart_for(M, focus, T, E) == chart, (name, focus)


def _check_text(items):
    return [(c["level"], render(c["msg"], c["vars"])) for c in items]


@pytest.mark.parametrize("ex, run", CASES)
def test_brain_check_matches(ex, run):
    want = fixture(ex, "overview")["runs"][run]["check"]
    brain, data, *_ = variant(ex, run)
    assert _check_text(brain_check.check(brain, data)) == [(c["level"], c["msg"]) for c in want]


def _check_cases():
    out = json.loads((FIX / "check_cases.json").read_text())
    assert out["engine_sha256"] == SKELETON_SHA
    return out["cases"]


@pytest.mark.parametrize("name", list(_check_cases()))
def test_brain_check_on_broken_and_odd_brains_matches(name):
    case = _check_cases()[name]
    assert _check_text(brain_check.check(case["brain"], case["data"])) == [(c["level"], c["msg"]) for c in case["check"]]


# --- deliberate differences and what the skeleton does not have -------------------------------

BS = load("barbershop")
M_BS = model.build(BS[1], BS[0])
T_BS = model.evaluate(M_BS)
C_BS = cash.cash_range(M_BS, T_BS)


def test_the_barbershops_own_measure_is_a_measure_with_parameters():
    """The skeleton's barbershop brain computes top4_share with a closure; here the brain says
    {"metric": "top_n_share", "n": 4} and the library does the work. Same text."""
    assert BS[0]["metrics"]["top4_share"] == {"l": "Top 4 barbers' share of sales", "metric": "top_n_share", "n": 4}
    m = measures.measure(M_BS, "top4_share", T_BS, C_BS)
    js = fixture("barbershop", "overview")["runs"]["base"]["metrics"]["top4_share"]
    assert (m["label"], text(m), render(m["note"], m["vars"])) == (js["l"], js["t"], js["s"])


@pytest.mark.parametrize("own, why", [({"l": "Typed in", "t": "7"}, "Not in the metric library"),
                                      ({"l": "Bad kind", "metric": "no_such"}, "is not a measure with parameters"),
                                      ({"l": "Top none", "metric": "top_n_share", "n": 0}, "could not be worked out")])
def test_a_brain_measure_that_is_not_a_library_measure_is_never_shown(own, why):
    """A brain is config: a figure typed into it, or code, is not a measure. (The skeleton
    shows a brain's static `t` text as if it were a figure.)"""
    brain = copy.deepcopy(BS[0])
    brain["metrics"] = {"mine": own}
    M = model.build(BS[1], brain)
    T = model.evaluate(M)
    m = measures.measure(M, "mine", T, None)
    assert not m["available"] and m["tone"] == "warn" and why in m["note"]
    assert "mine" not in [x["key"] for x in measures.view_measures(M, T, None)]


def test_names_every_object_inherits_are_not_panels_or_measures():
    """Deliberate difference: JavaScript's PANELS["toString"] and METRICS["constructor"] are
    set (inherited), so the skeleton would accept them as a panel or a headline number."""
    brain = copy.deepcopy(BS[0])
    brain["panels"] = ["toString", "cash", "sources", "books"]
    brain["headline"] = ["constructor", "revenue_month"]
    M = model.build(BS[1], brain)
    assert panels.panel_default(M) == panels.PANEL_DEFAULT
    msgs = "\n".join(render(c["msg"], c["vars"]) for c in brain_check.check(brain, BS[1]))
    assert "“toString”, which is not a panel" in msgs and "“constructor” is not in the metric library" in msgs


@pytest.mark.parametrize("brain_patch", [{"views": "x"}, {"panelViews": [None, 3]}, {"systems": [["quickbooks"]]},
                                         {"levers": ["taxes"]}, {"headline": "revenue_month"}, {"metrics": ["x"]},
                                         {"assistant": {"starters": "one"}}, {"panels": [["weekly"], "cash", "sources", "books"]}])
def test_odd_brain_shapes_are_reported_not_crashed_on(brain_patch):
    brain = {**copy.deepcopy(BS[0]), **brain_patch}
    out = brain_check.check(brain, BS[1])
    assert isinstance(out, list) and all(c["level"] in ("fix", "note") for c in out)


def test_tax_measures_and_panel_with_taxes_on_say_the_estimate_is_not_here_yet():
    brain, data = load("home-services-tax")
    M = model.build(data, brain)
    T = model.evaluate(M)
    m = measures.measure(M, "tax_liability_year", T, None)
    assert not m["available"] and "not in this version" in m["note"]
    assert "tax" not in [p["key"] for p in panels.panel_list(M)]
    fixes = [render(c["msg"], c["vars"]) for c in brain_check.check(brain, data) if c["level"] == "fix"]
    assert any("not in this version" in f for f in fixes)


def test_every_measure_value_is_a_number_never_text():
    for key in measures.LIBRARY:
        m = measures.measure(M_BS, key, T_BS, C_BS)
        assert m["value"] is None or isinstance(m["value"], (int, float)), key
        assert "$" not in m["note"] and "%" not in m["note"].replace("{", ""), (key, m["note"])


# --- the analysis entry points ------------------------------------------------------------

@pytest.mark.parametrize("ex", ["barbershop", "home-services"])
def test_company_profile_is_plain_json_with_the_brains_headline(ex):
    brain, data = load(ex)
    P = company_profile(data, brain)
    json.dumps(P, allow_nan=False)
    assert [h["key"] for h in P["headline"]] == brain["headline"]
    assert P["method_status"] == METHOD_STATUS and P["tax"] is None and P["tie"]["status"] == "ties"
    assert P["panels"] == panels.panel_keys(model.build(data, brain), None)


def test_apply_idea_gives_both_sides_the_bridge_and_the_chart():
    brain, data = load("barbershop")
    A = apply_idea(data, brain, {"shock": -10, "fixed": [{"l": "Rent increase", "mo": 500}]})
    json.dumps(A, allow_nan=False)
    assert A["bridge"]["from"] == A["today"]["profit"] and abs(A["bridge"]["to"] - A["idea"]["profit"]) < 1e-9
    assert [s["label"] for s in A["bridge"]["steps"]] == ["Sales volume", "Monthly bills"]
    assert A["cash_idea"]["seed"] == A["cash_today"]["seed"] and set(A["chart_for"]) == {"sales", "profit", "units", "cash"}
