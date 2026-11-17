"""Small-business engine parts the parity fixtures don't reach directly: the owner's facts,
the tie to the books as a named result, the JavaScript-semantics helpers, the DRAFT config
surfacing as method_status, and the analysis entry points."""

import copy
import json
import math
from pathlib import Path

import pytest

from advisor_engine import analysis, business_config, cash, facts, measures, model, sales
from advisor_engine.jsmath import js_or, js_round, n2, plain, quant, total

FIX = Path(__file__).parent / "fixtures" / "skeleton"


def load(ex):
    return (json.loads((FIX / ex / "brain.json").read_text()), json.loads((FIX / ex / "data.json").read_text()))


# --- facts ---------------------------------------------------------------------

FACTS = [
    {"k": "cash0", "l": "Cash", "v": 1000, "group": "cash", "src": "Balance Sheet", "confirmed": False},
    {"k": "rent", "l": "Rent", "v": 500, "group": "fixed", "src": "P&L", "confirmed": False},
]


def test_resolve_applies_numeric_corrections_and_fills_required_cash_facts():
    fl, F = facts.resolve(FACTS, {"rent": 650, "cash0": "lots"}, {"rent": True})
    assert F == {"cash0": 1000, "rent": 650, "floor": 0, "draw": 0}
    assert [f["confirmed"] for f in fl] == [False, True]
    assert FACTS[1]["confirmed"] is False, "the prepared facts are never changed"


def test_latest_correction_wins_and_none_retracts():
    rows = [{"k": "rent", "v": 600, "recorded_at": "2026-11-01"},
            {"k": "rent", "v": 700, "confirmed": True, "recorded_at": "2026-11-03"},
            {"k": "rent", "v": 650, "recorded_at": "2026-11-02"},
            {"k": "draw", "v": 4000, "recorded_at": "2026-11-01"},
            {"k": "draw", "v": None, "recorded_at": "2026-11-05"}]
    corrections, confirmed = facts.latest_corrections(rows)
    assert corrections == {"rent": 700} and confirmed == {"rent": True}


def test_validate_names_every_problem():
    bad = [{"k": "rent", "l": "Rent", "group": "monthly", "src": ""}, {"k": "rent", "l": "Again", "group": "fixed", "src": "x"}, {"l": "no key"}]
    msgs = "\n".join(facts.validate(bad))
    for part in ("must be cash, rate or fixed", "no source", "listed twice", "needs a key", "“cash0” is missing"):
        assert part in msgs
    B, D = load("home-services")
    assert facts.validate(D["facts"]) == []


# --- does it tie? ------------------------------------------------------------------

def test_tie_to_books_is_a_named_result():
    assert sales.tie_to_books(None)["status"] == "cannot_check"
    ok = sales.tie_to_books({"ratio": 1.03, "detail": 103, "books": 100, "months": 12})
    assert ok["status"] == "ties" and ok["ties"] is True and ok["gap"] == 3
    off = sales.tie_to_books({"ratio": 0.9, "detail": 90, "books": 100, "months": 12})
    assert off["status"] == "does_not_tie" and off["tolerance"] == business_config.TIE_TOLERANCE


def test_a_snapshot_whose_detail_misses_a_point_of_sale_does_not_tie():
    B, D = load("home-services")
    D = copy.deepcopy(D)
    for m in D["sales"]["byMonth"]:
        m["amount"] *= 0.8
    M = model.build(D, B)
    assert M["tie"]["status"] == "does_not_tie" and M["tie"]["ratio"] == pytest.approx(0.8, abs=0.02)
    assert model.build(*reversed(load("home-services")))["tie"]["status"] == "ties"


# --- JavaScript semantics ----------------------------------------------------------

@pytest.mark.parametrize("v, want", [(None, math.nan), ("", math.nan), ("  ", 0.0), ("2600", 2600.0), (" 4.5 ", 4.5),
                                     ("0x10", 16.0), ("1_000", math.nan), ("inf", math.nan), ("abc", math.nan),
                                     (True, 1.0), (False, 0.0), (float("inf"), math.nan), (7, 7.0), ([], 0.0), (["3"], 3.0)])
def test_n2_reads_input_like_javascripts_unary_plus(v, want):
    got = n2(v)
    assert (math.isnan(got) and math.isnan(want)) or got == want


def test_math_round_and_falsy_or_follow_javascript():
    assert [js_round(x) for x in (0.5, 1.5, 2.5, -0.5, -1.5)] == [1, 2, 3, 0, -1]
    assert js_or(math.nan, 1) == 1 and js_or(0, 5) == 5 and js_or("", "d") == "d" and js_or(3, 1) == 3
    # left-to-right addition, not Python 3.12+'s compensated sum()
    xs = [0.1] * 10 + [1e16, -1e16]
    acc = 0.0
    for x in xs:
        acc += x
    assert total(xs) == acc


def test_quantile_interpolates_between_order_statistics():
    assert quant([4, 1, 3, 2], 0.5) == 2.5 and quant([1, 2, 3], 0.1) == pytest.approx(1.2)
    assert math.isnan(quant([], 0.5))


# --- DRAFT numbers surface as method_status ------------------------------------------

def test_every_result_carries_the_draft_method():
    B, D = load("home-services-tax")
    P = analysis.company_profile(D, B)
    assert P["method_status"] == business_config.METHOD_STATUS and "draft" in P["method_status"]
    assert P["method_version"] == business_config.METHOD_VERSION
    assert P["cash"]["method_status"] == P["tax"]["method_status"] == business_config.METHOD_STATUS
    text = (Path(business_config.__file__)).read_text()
    for line in text.splitlines():
        if " = " in line and line[:1].isupper() and not line.startswith(("METHOD_", "TAX_RULES")) and "{" not in line:
            assert "# DRAFT" in line, line


# --- analysis entry points -------------------------------------------------------------

def _numbers_only(x, path="out"):
    """No formatted figure anywhere a value lives: strings may be labels or notes, never '$12.3K'."""
    if isinstance(x, dict):
        for k, v in x.items():
            _numbers_only(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _numbers_only(v, f"{path}[{i}]")
    elif isinstance(x, float):
        assert math.isfinite(x), path


@pytest.mark.parametrize("ex", ["barbershop", "home-services", "home-services-tax"])
def test_company_profile_is_plain_json_and_matches_the_engine(ex):
    B, D = load(ex)
    P = analysis.company_profile(D, B)
    json.dumps(P, allow_nan=False)
    _numbers_only(P)
    M = model.build(D, B)
    T = model.evaluate(M)
    assert P["today"]["profit"] == T["profit"]
    assert [h["key"] for h in P["headline"]] == B["headline"]
    assert P["cash"]["p50"] == cash.cash_range(M, T)["p50"]
    assert (P["tax"] is not None) == bool((B.get("levers") or {}).get("taxes"))
    assert "DATA" not in P["model"] and "BRAIN" not in P["model"]


def test_apply_idea_differences_come_from_the_bridge():
    B, D = load("barbershop")
    idea = {"shock": -10, "fixed": [{"l": "Rent increase", "mo": 500}]}
    A = analysis.apply_idea(D, B, idea)
    json.dumps(A, allow_nan=False)
    assert A["bridge"]["from"] == A["today"]["profit"] and A["bridge"]["to"] == pytest.approx(A["idea"]["profit"], abs=1e-9)
    assert [s["label"] for s in A["bridge"]["steps"]] == ["Sales volume", "Monthly bills"]
    assert A["cash_idea"]["seed"] == A["cash_today"]["seed"], "both ranges run on the same draws"
    assert set(A["chart_for"]) == {"sales", "profit", "units", "cash"}
    assert "tax_idea" not in A


def test_apply_idea_with_taxes_puts_the_payments_in_the_cash_range():
    B, D = load("home-services-tax")
    A = analysis.apply_idea(D, B, {"assets": [{"l": "Van", "cost": 38000, "wk": 4}]})
    assert A["tax_idea"]["ok"] and A["cash_idea"]["taxTotal"] >= 0
    assert A["tax_idea"]["bought"][0]["cost"] == 38000


def test_a_brain_measure_with_unknown_parameters_is_never_shown():
    B, D = load("home-services")
    B = copy.deepcopy(B)
    B["metrics"] = {"top0": {"l": "Top none", "metric": "top_n_share", "n": 0}}
    M = model.build(D, B)
    T = model.evaluate(M)
    m = measures.measure(M, "top0", T, None)
    assert not m["available"] and m["tone"] == "warn"


def test_plain_turns_dates_and_nan_into_json():
    from datetime import date
    assert plain({"d": date(2026, 11, 3), "x": math.nan, "t": (1, 2)}) == {"d": "2026-11-03", "x": None, "t": [1, 2]}
