"""Stress scenarios, questionnaire scoring and profile comparison, on hand-checkable cases."""

import pandas as pd
import pytest

from advisor_engine.compare import compare_profiles
from advisor_engine.holdings import validate_holdings
from advisor_engine.questionnaire import QUESTIONS, check_portfolio, score
from advisor_engine.risk import risk_profile
from advisor_engine.stress import SCENARIOS, run_scenario, scenario_list, stress_test
from advisor_engine.synthetic import synthetic_returns

pytestmark = pytest.mark.advisor


def holdings(rows):
    return validate_holdings(pd.DataFrame(rows, columns=["ticker", "shares", "price", "sector"]))


# 60% Tech, 40% Health by value (6000 and 4000).
H = holdings([("AAA", 60, 100, "Technology"), ("BBB", 40, 100, "Health Care")])


def profile(h):
    return risk_profile(h, synthetic_returns(list(h["ticker"]), list(h["sector"]))).to_dict()


# --- stress ---------------------------------------------------------------------------------

def test_sector_shocks_combine_by_weight():
    r = stress_test(H, {"Technology": -0.30}, default_shock=-0.10)
    # 0.6 * -30% + 0.4 * -10% = -22%; on 10,000 of value that is -2,200.
    assert r["portfolio_return"] == pytest.approx(-0.22)
    assert r["value_change"] == pytest.approx(-2200)
    assert r["positions"][0]["ticker"] == "AAA"  # largest loss first
    assert {s["sector"]: s["contribution"] for s in r["sectors"]} == pytest.approx(
        {"Technology": -0.18, "Health Care": -0.04})


def test_broad_selloff_moves_everything_by_the_default():
    assert run_scenario(H, "broad_selloff")["portfolio_return"] == pytest.approx(-0.20)


def test_every_preset_runs_and_says_what_it_assumes():
    assert {s["id"] for s in scenario_list()} == set(SCENARIOS)
    for sid in SCENARIOS:
        r = run_scenario(H, sid)
        assert r["basis"] and r["scenario"]["description"]


@pytest.mark.parametrize("shock", [-1.5, 2.0])
def test_shocks_outside_plus_minus_100_percent_are_rejected(shock):
    with pytest.raises(ValueError):
        stress_test(H, {"Technology": shock}, default_shock=0.0)


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        run_scenario(H, "meteor")


# --- questionnaire --------------------------------------------------------------------------

def answers(i):
    return {q["id"]: i for q in QUESTIONS}


@pytest.mark.parametrize("option, total, level", [(0, 6, 1), (4, 30, 10), (2, 18, 6)])
def test_score_maps_the_total_onto_one_to_ten(option, total, level):
    r = score(answers(option))
    assert (r["score"], r["risk_level"]) == (total, level)
    assert r["method_status"]  # the draft label travels with every score


def test_missing_or_out_of_range_answers_are_rejected():
    a = answers(2)
    del a["experience"]
    with pytest.raises(ValueError, match="experience"):
        score(a)
    with pytest.raises(ValueError):
        score({**answers(2), "experience": 7})


def test_drawdown_check_compares_like_with_like():
    p = profile(H)
    drop = abs(p["drawdown"]["historical_max_drawdown"])
    cautious = check_portfolio(score({**answers(2), "max_loss": 0}), p)[0]  # accepts 5%
    bold = check_portfolio(score({**answers(2), "max_loss": 4}), p)[0]  # accepts 35%
    assert cautious["portfolio_value"] == pytest.approx(drop)
    assert cautious["client_limit"] == 0.05 and cautious["within"] == (drop <= 0.05)
    assert bold["client_limit"] == 0.35 and bold["within"] == (drop <= 0.35)


# --- compare --------------------------------------------------------------------------------

def test_compare_reports_after_minus_before_and_membership_changes():
    more = holdings([("AAA", 60, 100, "Technology"), ("BBB", 40, 100, "Health Care"),
                     ("CCC", 50, 100, "Utilities")])
    a, b = profile(H), profile(more)
    c = compare_profiles(a, b)
    bets = next(r for r in c["headline"] if r["key"] == "effective_bets")
    assert bets["before"] == pytest.approx(a["concentration"]["effective_bets"])
    assert bets["change"] == pytest.approx(b["concentration"]["effective_bets"]
                                           - a["concentration"]["effective_bets"])
    assert c["added"] == ["CCC"] and c["removed"] == []
    util = next(s for s in c["sectors"] if s["sector"] == "Utilities")
    assert util["before"] == 0.0 and util["after"] == pytest.approx(5000 / 15000)


def test_comparing_a_profile_with_itself_changes_nothing():
    p = profile(H)
    assert all(r["change"] == 0 for r in compare_profiles(p, p)["headline"])


def test_loss_rows_compare_sizes_so_a_smaller_loss_is_a_negative_change():
    calm = holdings([("AAA", 60, 100, "Technology"), ("BBB", 40, 100, "Health Care"),
                     ("UUU", 400, 100, "Utilities")])
    c = compare_profiles(profile(H), profile(calm))
    row = next(r for r in c["headline"] if r["key"] == "stress_loss_1_in_100")
    assert row["kind"] == "loss" and row["before"] > 0 and row["after"] > 0
    assert row["change"] == pytest.approx(row["after"] - row["before"])
