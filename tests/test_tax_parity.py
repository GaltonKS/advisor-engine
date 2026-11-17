"""tax.py against the FIS skeleton's own tax estimate, and the Overview with taxes on.

tests/fixtures/skeleton/home-services-tax/tax.json comes from capture_tax.js: on the tax
example, as shipped and corrected, the tax picture and the cash range with its payments for
ideas that move tax, and the Overview's tax measures, views, panels and brain check; then 27
tax-profile variants (every entity, other year ends, the large-corporation rule, prior-year
cases, an overpayment, a tax under the installment threshold, a loss, adjustments, tax
depreciation, unplaced accounts, no profile, no Profit and Loss, a government-ID lookalike,
other as-of dates, a short Profit and Loss), each stored as its overrides of data.json.

Exact, after the engine's templates are rendered with their figures (skeleton_format): every
bridge row, payment, note, profile entry and the rest of the picture. To the cent: the
simulated percentiles of the tax range and of the cash range (their normal draws go through
log/sin/cos/exp, which V8 and the C library may round differently in the last bit).
"""

import json

import pytest

from advisor_engine import brain_check, cash, measures, model, panels, tax
from advisor_engine.jsmath import plain
from parity import EXTRA, FIX, fixture, load, same
from skeleton_format import figure, render

PCT = ("p10", "p50", "p90")
TAX_EXTRA = EXTRA | {"seed", "paths"}
TEMPLATED = ("l", "src", "t", "text")
FIXTURE = fixture("home-services-tax", "tax")


def rendered(x):
    """The engine's output with every template filled in, as the skeleton writes it."""
    if isinstance(x, dict):
        out = {k: rendered(v) for k, v in x.items() if k != "vars"}
        if "vars" in x:
            for k in TEMPLATED:
                if isinstance(x.get(k), str):
                    out[k] = render(x[k], x["vars"])
        return out
    if isinstance(x, list):
        return [rendered(v) for v in x]
    return x


def check(py, js, path):
    if isinstance(js, dict) and "error" in js:
        pytest.fail(f"{path}: the skeleton threw {js['error']}")
    same(rendered(plain(py)), js, path, cents=PCT, extra=TAX_EXTRA)


def _model(run):
    brain, data = load("home-services-tax")
    o = FIXTURE["runs"][run].get("opts") or {}
    return brain, data, model.build(data, brain, facts=o.get("facts"), confirmed=o.get("confirmed"), basis=o.get("basis", 12))


def _ideas():
    for run in ("base", "corrected"):
        for name in FIXTURE["runs"][run]["ideas"]:
            yield pytest.param(run, name, id=f"{run}-{name}")


@pytest.mark.parametrize("run, name", list(_ideas()))
def test_tax_picture_and_cash_with_payments_match(run, name):
    _, _, M = _model(run)
    case = FIXTURE["runs"][run]["ideas"][name]
    T = model.evaluate(M)
    E = model.evaluate(M, case["idea"])
    X = tax.tax(M, E, base=T)
    check(X, case["tax"], f"{run}.{name}.tax")
    check(cash.cash_range(M, E, tax=X), case["cash"], f"{run}.{name}.cash")


@pytest.mark.parametrize("run", ["base", "corrected"])
def test_tax_of_a_path_override_and_the_overview_with_taxes_on_match(run):
    brain, data, M = _model(run)
    r = FIXTURE["runs"][run]
    T = model.evaluate(M)
    C = cash.cash_range(M, T)
    check(tax.tax_of(M, T), r["taxOf"], "taxOf")
    check(tax.tax(M, T, base=T, paths=100), r["rangePaths100"], "paths100")
    for key, js in r["metrics"].items():
        m = measures.measure(M, key, T, C)
        assert (m["label"], "n/a" if m["value"] is None else figure(m), render(m["note"], m["vars"]), m["tone"]) == \
            (js["l"], js["t"], js.get("s") or "", js.get("tone") or ""), key
    got = [(x["key"], x["label"], x["group"], "n/a" if x["value"] is None else figure(x)) for x in measures.view_measures(M, T, C)]
    assert got == [(x["key"], x["l"], x["group"], x["t"]) for x in r["viewMetrics"]]
    same(measures.view_presets(M, T, C), r["viewPresets"], "viewPresets")
    same(rendered(panels.panel_list(M)), r["panelList"], "panelList")
    same(panels.panel_presets(M), r["panelPresets"], "panelPresets")
    assert panels.panel_default(M) == r["panelDefault"]
    if run == "base":
        assert [(c["level"], render(c["msg"], c["vars"])) for c in brain_check.check(brain, data)] == \
            [(c["level"], c["msg"]) for c in r["check"]]


DELIBERATE = {"adjustment_status_builtin"}


def test_names_every_object_inherits_are_not_statuses_or_entities():
    """Deliberate difference. JavaScript's TAX_STATUS["toString"] and TAX_ENTITIES["toString"]
    are set (inherited), so the skeleton keeps "toString" as an adjustment's status and treats
    the entity as known. Here neither counts: the status is an estimate and the entity unknown."""
    v = FIXTURE["variants"]["adjustment_status_builtin"]
    js = v["today"]
    assert [r["status"] for r in js["cur"]["rows"] if r["k"] == "adj"] == ["toString"], "what the skeleton does"
    brain, data = load("home-services-tax")
    data.update(v["overrides"])
    M = model.build(data, brain)
    X = tax.tax(M, model.evaluate(M))
    assert [r["status"] for r in X["cur"]["rows"] if r["k"] == "adj"] == ["estimate"]
    assert not X["computed"] and "does not know" in X["why"]


@pytest.mark.parametrize("name", [n for n in FIXTURE["variants"] if n not in DELIBERATE])
def test_tax_profile_variants_match(name):
    v = FIXTURE["variants"][name]
    brain, data = load("home-services-tax")
    data.update(v["overrides"])
    M = model.build(data, brain)
    T = model.evaluate(M)
    check(tax.tax(M, T, base=T), v["today"], f"{name}.today")
    check(tax.tax(M, model.evaluate(M, v["idea"]), base=T), v["ideaTax"], f"{name}.idea")
    XT = tax.tax(M, T, base=T)
    check(cash.cash_range(M, T, tax=XT if XT.get("ok") else None), v["cash"], f"{name}.cash")
    assert [(c["level"], render(c["msg"], c["vars"])) for c in brain_check.check(brain, data)] == \
        [(c["level"], c["msg"]) for c in v["check"]], name
    C = cash.cash_range(M, T)
    for key, js in v["metrics"].items():
        m = measures.measure(M, key, T, C)
        assert ("n/a" if m["value"] is None else figure(m), render(m["note"], m["vars"]), m["tone"]) == \
            (js["t"], js.get("s") or "", js.get("tone") or ""), (name, key)


def test_the_variants_cover_what_they_say():
    v = FIXTURE["variants"]
    assert 0 < v["small_tax_under_threshold"]["today"]["cur"]["liability"] < 500
    assert not v["entity_s_corp"]["today"]["computed"] and v["no_profile"]["today"]["ok"] is False
    assert any("EIN" in c["msg"] for c in v["government_id"]["check"])


def test_every_figure_in_the_tax_picture_is_a_number():
    brain, data = load("home-services-tax")
    M = model.build(data, brain)
    X = plain(tax.tax_of(M, model.evaluate(M)))
    json.dumps(X, allow_nan=False)
    for p in X["profile"]:
        if isinstance(p["v"], (int, float)) and not isinstance(p["v"], bool) and p["k"] != "yearEndMonth":
            assert p["text"] == "{v}", p
    for pay in X["payments"]:
        assert isinstance(pay["amount"], float) and "{" in pay["l"] or "installment" not in pay["l"]
