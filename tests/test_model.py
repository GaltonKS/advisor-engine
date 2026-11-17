"""model.build (and facts, sales, company, business_config) against the FIS skeleton.

Parity: tests/fixtures/skeleton/<example>/build.json is the skeleton's own model base
(capture_build.js) for each worked example built five ways, plus nine edge runs on the
barbershop (a month with no sales, 70 weeks, loose inputs, six shapes of brain.terms.types),
each stored with the exact snapshot and brain it was built from. Everything must match:
weeks, window, units, offerings, the fit (seasons, swing, carry-over, trend), the books by
month and the coverage of the detail against the books. Exactly, except the swing and the
carry-over (`fit.vol`, `fit.phi`), which go through a log: 1e-12 of their size.

The rest checks what the skeleton does not have: the owner's corrections as stored rows,
the tie to the books as a named result, snapshot shapes the model refuses, and the DRAFT
config.
"""

import copy
import math
from pathlib import Path

import pytest

from advisor_engine import business_config, facts, model, sales
from advisor_engine.company import term_types, vocab
from advisor_engine.jsmath import plain
from parity import EXAMPLES, RUNS, fixture, load, same, variant


# --- parity -------------------------------------------------------------------------------

LOG_DERIVED = (".fit.vol", ".fit.phi")
EDGE_RUNS = [r for r in fixture("barbershop", "build")["runs"] if r not in RUNS]


@pytest.mark.parametrize("run", RUNS)
@pytest.mark.parametrize("ex", EXAMPLES)
def test_model_base_matches_the_skeleton(ex, run):
    want = fixture(ex, "build")["runs"][run]
    brain, data = load(ex)
    o = want["opts"]
    M = model.build(variant(data, run), brain, facts=o.get("facts"), confirmed=o.get("confirmed"), basis=o.get("basis", 12))
    same(plain(model.model_view(M)), want["model"], f"{ex}.{run}", LOG_DERIVED)


@pytest.mark.parametrize("run", EDGE_RUNS)
def test_model_base_matches_the_skeleton_on_edge_cases(run):
    want = fixture("barbershop", "build")["runs"][run]
    M = model.build(want["data"], want["brain"])
    same(plain(model.model_view(M)), want["model"], f"barbershop.{run}", LOG_DERIVED)


def test_edge_runs_cover_what_the_examples_do_not():
    runs = fixture("barbershop", "build")["runs"]
    assert len(EDGE_RUNS) == 9
    assert runs["weeks_70"]["model"]["fit"]["trendKind"] == "year" and runs["weeks_70"]["model"]["win"]["w0"] == 18
    # Skeleton bug, matched for now: a month with no sales makes the de-seasoned level NaN
    # (0/0), which will make the cash range NaN. Listed in the PR to fix in both.
    assert runs["closed_february"]["model"]["fit"]["deseason"] is None


def test_the_model_base_carries_the_draft_method():
    brain, data = load("home-services")
    M = model.build(data, brain)
    assert M["method_version"] == business_config.METHOD_VERSION
    assert M["method_status"] == business_config.METHOD_STATUS and "draft" in M["method_status"]
    assert "DATA" not in model.model_view(M) and "BRAIN" not in model.model_view(M)


# --- facts: the owner's numbers -----------------------------------------------------------

FACTS = [
    {"k": "cash0", "l": "Cash", "v": 1000, "group": "cash", "src": "Balance Sheet", "confirmed": False},
    {"k": "rent", "l": "Rent", "v": 500, "group": "fixed", "src": "P&L", "confirmed": False},
]


def test_resolve_applies_numeric_corrections_and_fills_the_required_cash_facts():
    fl, F = facts.resolve(FACTS, {"rent": 650, "cash0": "lots"}, {"rent": True})
    assert F == {"cash0": 1000, "rent": 650, "floor": 0, "draw": 0}
    assert [f["confirmed"] for f in fl] == [False, True]
    assert FACTS[1]["confirmed"] is False, "the prepared facts are never changed"


def test_the_latest_correction_wins_and_none_retracts():
    rows = [{"k": "rent", "v": 600, "recorded_at": "2026-11-01"},
            {"k": "rent", "v": 700, "confirmed": True, "recorded_at": "2026-11-03"},
            {"k": "rent", "v": 650, "recorded_at": "2026-11-02"},
            {"k": "draw", "v": 4000, "recorded_at": "2026-11-01"},
            {"k": "draw", "v": None, "recorded_at": "2026-11-05"}]
    assert facts.latest_corrections(rows) == ({"rent": 700}, {"rent": True})


def test_validate_names_every_problem_and_passes_the_examples():
    bad = [{"k": "rent", "l": "Rent", "group": "monthly", "src": ""}, {"k": "rent", "l": "Again", "group": "fixed", "src": "x"},
           {"l": "no key"}]
    msgs = "\n".join(facts.validate(bad))
    for part in ("must be cash, rate or fixed", "no source", "listed twice", "needs a key", "“cash0” is missing"):
        assert part in msgs
    for ex in EXAMPLES:
        assert facts.validate(load(ex)[1]["facts"]) == []


def test_corrections_flow_through_build():
    brain, data = load("home-services")
    M = model.build(data, brain, facts={"rent": 4200}, confirmed={"rent": True}, basis=3)
    assert M["F"]["rent"] == 4200 and next(f for f in M["FL"] if f["k"] == "rent")["confirmed"] is True
    assert M["win"]["nWin"] == 13


# --- does the detail tie to the books? ------------------------------------------------------

def test_tie_to_books_is_a_named_result():
    assert sales.tie_to_books(None)["status"] == "cannot_check"
    ok = sales.tie_to_books({"ratio": 1.03, "detail": 103.0, "books": 100.0, "months": 12})
    assert ok["status"] == "ties" and ok["ties"] is True and ok["gap"] == 3
    off = sales.tie_to_books({"ratio": 0.9, "detail": 90.0, "books": 100.0, "months": 12})
    assert off["status"] == "does_not_tie" and off["tolerance"] == business_config.TIE_TOLERANCE


def test_a_detail_that_misses_a_point_of_sale_does_not_tie():
    brain, data = load("home-services")
    assert model.build(data, brain)["tie"]["status"] == "ties"
    for m in data["sales"]["byMonth"]:
        m["amount"] *= 0.8
    tie = model.build(data, brain)["tie"]
    assert tie["status"] == "does_not_tie" and tie["ratio"] == pytest.approx(0.8, abs=0.02)
    data["pnl"] = {"months": [], "lines": []}
    assert model.build(data, brain)["tie"]["status"] == "cannot_check"


# --- snapshot shapes the model refuses ------------------------------------------------------

def _broken(fn):
    brain, data = load("barbershop")
    fn(data)
    return brain, data


@pytest.mark.parametrize("break_it, reason", [
    (lambda d: d["sales"]["rows"].append([0, 0, 0, 1]), "Sales row .* five numbers"),
    (lambda d: d["sales"]["rows"].append([0, 0, 0, 1, "45"]), "Sales row .* five numbers"),
    (lambda d: d["sales"]["rows"].append([0, 0, 0, 1, float("nan")]), "Sales row .* five numbers"),
    (lambda d: d["sales"]["rows"].append([999, 0, 0, 1, 45]), "points at week 999"),
    (lambda d: d["sales"]["rows"].append([0, 99, 0, 1, 45]), "points at unit 99"),
    (lambda d: d["sales"]["rows"].append([0, 0, -1, 1, 45]), "points at offering -1"),
    (lambda d: d["sales"]["rows"].append([0.5, 0, 0, 1, 45]), "points at week 0.5"),
    (lambda d: d["sales"].pop("rows"), "no rows list"),
    (lambda d: d["sales"]["offerings"].append("Haircut"), "is an object"),
    (lambda d: d["pnl"]["months"].__setitem__(0, "Total"), "not a month"),
    (lambda d: d["pnl"]["months"].__setitem__(0, "2026-13"), "not a month"),
    (lambda d: d["sales"]["weeks"].__setitem__(0, "2026-02-30"), "not a date"),
    (lambda d: d["pnl"]["lines"][0]["values"].__setitem__(0, "12"), "values that are not numbers"),
    (lambda d: d["pnl"].pop("lines"), "months list and a lines list"),
    (lambda d: d["facts"].append({"l": "no key"}), "needs a key"),
])
def test_a_snapshot_the_model_cannot_read_is_refused_with_a_reason(break_it, reason):
    brain, data = _broken(break_it)
    with pytest.raises(ValueError, match=reason):
        model.build(data, brain)


def test_a_float_index_that_is_whole_is_read_like_javascript():
    brain, data = load("barbershop")
    want = model.build(copy.deepcopy(data), brain)["units"]
    data["sales"]["rows"] = [[float(r[0])] + r[1:] for r in data["sales"]["rows"]]
    assert model.build(data, brain)["units"] == want


# --- the brain's words and pay kinds ----------------------------------------------------------

def test_vocab_takes_the_brains_words_and_falls_back_to_neutral_ones():
    v = vocab(load("barbershop")[0])
    assert (v["unit"], v["Units"], v["ticket"], v["people"]) == ("barber", "Barbers", "ticket", True)
    d = vocab({})
    assert (d["unit"], d["units"], d["business"], d["owner"]) == ("team member", "team members", "business", "the owner")
    assert vocab({"vocab": {"unit": "truck", "unitsArePeople": False}})["units"] == "trucks"
    assert vocab({"vocab": {"unitsArePeople": False}})["people"] is False


def test_term_types_keep_the_brains_order_and_drop_unknown_kinds():
    t = term_types(load("barbershop")[0])
    assert list(t) == ["share", "fixedIn"] and t["fixedIn"]["label"] == "Booth rent" and t["fixedIn"]["per"] == "week"
    assert list(term_types({})) == ["share", "wage"]
    assert list(term_types({"terms": {"types": {"barter": {}}}})) == ["none"]


# --- every tunable number is marked DRAFT ------------------------------------------------------

def test_every_number_in_business_config_is_marked_draft():
    lines = Path(business_config.__file__).read_text().splitlines()
    for line in lines:
        code = line.split("#")[0]
        if any(ch.isdigit() for ch in code) and ("=" in code or ":" in code) and not line.lstrip().startswith(("#", '"""')):
            if "label" in code and "def" not in code:
                continue  # a label with no starting figure
            assert "# DRAFT" in line or "# derived:" in line, line


def test_month_count_is_a_number_not_a_label():
    brain, data = load("home-services")
    M = model.build(data, brain)
    assert isinstance(M["fit"]["seasonN"], int) and math.isfinite(M["win"]["months"])
