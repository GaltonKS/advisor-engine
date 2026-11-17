"""The Phase 2 gate, layer 2: Python gives the same numbers as the FIS skeleton.

The fixtures under tests/fixtures/skeleton/ were written by capture.js from the skeleton's
own engine (app/engine.js; its sha256 is in every expected.json and checked below) on its
three worked examples: barbershop, home-services and home-services-tax. Each example is run
five ways (as shipped; with the owner's corrections and the 13-week basis; without
customers, A/R and A/P; without a Profit and Loss; with only the last 30 weeks of sales),
and each run on about twenty ideas, one per kind of change plus all of them at once.

What is asserted, for every run:
- the model base (`build`): weeks, window, units, offerings, fit, books by month, coverage;
- every idea's evaluation (`evaluate`), its bridge from today (`bridge`), its cash range
  (`cash`), and `chartFor` for each focus;
- every measure's label, figure, note and tone, the pick-list, the ready-made views and the
  four keys shown for ten saved choices; the panel list, layouts and keys; the cost lines;
- the brain check, as shipped and on three broken brains;
- with taxes on: the tax picture for every idea and the cash range with tax payments in it.

Tolerances. Numbers worked out without random draws must agree to 1e-9 of their size (in
practice they agree to the last bit, or within one rounding of a log). Numbers from the
simulations (the cash range's percentiles, the tax range) must agree to the cent: their
uniform draws are identical (tests/test_rand.py), but the normal draws go through
log/sin/cos, which V8 and the C library may round differently in the last bit. Never loosen
these: if a figure moves, fix the port.

Text: the engine returns figures, never formatted text. Where the skeleton returned text
("$553.2K", "12 of 17 facts ..."), Python returns a template and its figures, and this test
formats them with the skeleton's own rules (tests/skeleton_format.py) and compares strings.
"""

import json
import re
from pathlib import Path

import pytest

from advisor_engine import brain_check, cash, charts, compare, measures, model, tax
from advisor_engine.jsmath import plain
from skeleton_format import figure, render

FIX = Path(__file__).parent / "fixtures" / "skeleton"
EXAMPLES = ["barbershop", "home-services", "home-services-tax"]
RUNS = ["base", "corrected", "no_customers_ar_ap", "no_books", "weeks_30"]
TEMPLATED = ("l", "src", "t", "text", "msg", "note")
SIMULATED = ("cash", "cashWithTax", "range")
SKELETON_SHA = "cd82452c5ebbdbe4bdc10454ba2ff163b1816d4d3e657da3f8c8932e68a869d5"


def _load(ex):
    return (json.loads((FIX / ex / "brain.json").read_text()), json.loads((FIX / ex / "data.json").read_text()))


_EXPECTED: dict = {}


def expected(ex):
    if ex not in _EXPECTED:
        _EXPECTED[ex] = json.loads((FIX / ex / "expected.json").read_text())
    return _EXPECTED[ex]


def variant(ex, run):
    """The model for one run, built the same way capture.js built the skeleton's."""
    B, D = _load(ex)
    opts = {}
    if run == "corrected":
        M0 = model.build(D, B)
        f0, x0 = M0["rates"][0], M0["fixed"][0]
        opts = {"facts": {x0["k"]: M0["F"][x0["k"]] + 1000, f0["k"]: M0["F"][f0["k"]] + 1.4}, "confirmed": {x0["k"]: True}, "basis": 3}
    elif run == "no_customers_ar_ap":
        for k in ("customers", "ar", "ap"):
            D.pop(k, None)
    elif run == "no_books":
        D["pnl"] = {"months": [], "lines": []}
    elif run == "weeks_30":
        n = len(D["sales"]["weeks"])
        D["sales"]["weeks"] = D["sales"]["weeks"][-30:]
        D["sales"]["rows"] = [[r[0] - (n - 30)] + r[1:] for r in D["sales"]["rows"] if r[0] >= n - 30]
    return B, D, model.build(D, B, **opts)


def same(py, js, path="", simulated=False):
    """Assert `py` matches the skeleton's `js` (see the module docstring for tolerances).
    Keys only Python has are allowed (vars, seed, method_version, ...); every key the
    skeleton has must match. Templated text is rendered with its vars first."""
    if isinstance(js, dict):
        assert isinstance(py, dict), f"{path}: expected an object, got {py!r:.80}"
        for k, v in js.items():
            assert k in py, f"{path}.{k}: missing"
            p = py[k]
            if k in TEMPLATED and isinstance(p, str) and "vars" in py:
                p = render(p, py["vars"])
            same(p, v, f"{path}.{k}", simulated or k in SIMULATED)
    elif isinstance(js, list):
        assert isinstance(py, list) and len(py) == len(js), f"{path}: lengths {len(py) if isinstance(py, list) else py!r} vs {len(js)}"
        for i, (a, b) in enumerate(zip(py, js)):
            same(a, b, f"{path}[{i}]", simulated)
    elif isinstance(js, (int, float)) and not isinstance(js, bool):
        assert isinstance(py, (int, float)) and not isinstance(py, bool), f"{path}: {py!r} vs {js}"
        tol = 0.005 if simulated else 1e-9 * max(1, abs(js))
        assert abs(py - js) <= tol, f"{path}: {py!r} vs {js!r}"
    else:
        assert py == js, f"{path}: {py!r} vs {js!r}"


def test_fixtures_come_from_the_skeleton_they_name():
    for ex in EXAMPLES:
        assert expected(ex)["engine_sha256"] == SKELETON_SHA
    assert json.loads((FIX / "check_broken.json").read_text())["engine_sha256"] == SKELETON_SHA
    assert json.loads((FIX / "rand.json").read_text())["engine_sha256"] == SKELETON_SHA


@pytest.mark.parametrize("run", RUNS)
@pytest.mark.parametrize("ex", EXAMPLES)
def test_model_ideas_cash_and_bridge(ex, run):
    R = expected(ex)["runs"][run]
    B, D, M = variant(ex, run)
    same(plain(model.model_view(M)), R["model"], "model")
    T = model.evaluate(M)
    for name, I in R["ideas"].items():
        E = model.evaluate(M, I["idea"])
        same(plain(E), I["evaluate"], f"{name}.evaluate")
        same(plain(cash.cash_range(M, E)), I["cash"], f"{name}.cash", simulated=True)
        same(plain(compare.bridge(M, None, I["idea"])), I["bridge"], f"{name}.bridge")
        for focus, want in I["chartFor"].items():
            assert charts.chart_for(M, focus, T, E) == want, f"{name}.chartFor[{focus}]"
        if "tax" in I:
            X = tax.tax(M, E, base=T)
            same(plain(X), I["tax"], f"{name}.tax")
            same(plain(cash.cash_range(M, E, tax=X)), I["cashWithTax"], f"{name}.cashWithTax", simulated=True)


def _measure_text(m):
    return "n/a" if m["value"] is None else figure(m)


@pytest.mark.parametrize("run", RUNS)
@pytest.mark.parametrize("ex", EXAMPLES)
def test_measures_views_panels_and_tax_of(ex, run):
    R = expected(ex)["runs"][run]
    B, D, M = variant(ex, run)
    T = model.evaluate(M)
    C = cash.cash_range(M, T)
    for key, want in R["metrics"].items():
        m = measures.measure(M, key, T, C)
        assert m["label"] == want["l"], key
        assert _measure_text(m) == want["t"], key
        assert render(m["note"], m["vars"]) == (want.get("s") or ""), key
        assert m["tone"] == (want.get("tone") or ""), key
    got = measures.view_measures(M, T, C)
    assert [(x["key"], x["label"], x["group"], _measure_text(x)) for x in got] == \
        [(x["key"], x["l"], x["group"], x["t"]) for x in R["viewMetrics"]]
    assert measures.view_presets(M, T, C) == R["viewPresets"]
    for case in R["viewKeys"]:
        assert measures.view_keys(M, case["sel"], T, C) == case["keys"], case["sel"]
    assert charts.panel_list(M) == R["panelList"]
    assert charts.panel_presets(M) == R["panelPresets"]
    assert charts.panel_default(M) == R["panelDefault"]
    for case in R["panelKeys"]:
        assert charts.panel_keys(M, case["sel"]) == case["keys"], case["sel"]
    same(charts.cost_lines(M), R["costLines"], "costLines")
    same(charts.cost_lines(M, 6), R["costLines6"], "costLines6")
    X = tax.tax_of(M, T)
    if R["taxOf"] is None:
        assert X is None
    else:
        same(plain(X), R["taxOf"], "taxOf")


def _check_text(items):
    return [(c["level"], render(c["msg"], c["vars"])) for c in items]


@pytest.mark.parametrize("run", ["base", "no_customers_ar_ap", "no_books", "weeks_30"])
@pytest.mark.parametrize("ex", EXAMPLES)
def test_brain_check_matches(ex, run):
    B, D, M = variant(ex, run)
    want = [(c["level"], c["msg"]) for c in expected(ex)["runs"][run]["check"]]
    assert _check_text(brain_check.check(B, D)) == want


def test_brain_check_on_broken_brains_matches():
    for case in json.loads((FIX / "check_broken.json").read_text())["cases"]:
        assert _check_text(brain_check.check(case["brain"], case["data"])) == [(c["level"], c["msg"]) for c in case["check"]]


def test_no_engine_value_is_formatted_text():
    """Every figure the engine returns is a number: no "$", "%" or K/M-suffixed figure
    appears in a value, only in labels and notes (templates whose figures are in vars)."""
    B, D, M = variant("home-services-tax", "base")
    T = model.evaluate(M)
    C = cash.cash_range(M, T)
    for key in measures.LIBRARY:
        m = measures.measure(M, key, T, C)
        assert m["value"] is None or isinstance(m["value"], float | int)
        assert "$" not in m["note"] and "%" not in m["note"], (key, m["note"])
    # Engine-written text in the tax picture. Quoted text is skipped: a fact's or rule's `src`
    # and `source` (data, IRS wording), the rules rows, the owner's own profile text, and the
    # rule threshold named in words
    # ("$1M", "$1 million", the large-corporation rule).
    found = []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                if k not in ("src", "source", "rulesUsed", "rateRule", "reason", "profile", "unconfirmed"):
                    walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str) and re.search(r"\$\d|\d%", x.replace("$1M", "").replace("$1 million", "")):
            found.append((path, x))

    X = plain(tax.tax_of(M, T))
    walk(X, "tax")
    assert found == []
    # a money value in the profile is a figure with a unit, not "$12,000"
    assert all(p["text"] == "{v}" for p in X["profile"]
               if p["k"] != "yearEndMonth" and isinstance(p["v"], (int, float)) and not isinstance(p["v"], bool))
