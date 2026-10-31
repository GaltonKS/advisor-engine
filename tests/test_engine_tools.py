"""Engine figures behind the assistant's wider tool set: market sensitivity, risk by
group, and the get_holdings / run_what_if / run_stress_test tools."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from advisor_engine.assistant import chat
from advisor_engine.assistant.tools import TOOLS, apply_changes, execute_tool
from advisor_engine.holdings import load_holdings
from advisor_engine.risk import group_risk, market_sensitivity

REPO = Path(__file__).resolve().parents[1]
SAMPLE = str(REPO / "data" / "sample" / "sample_holdings.csv")


def call(name, **kw):
    res = execute_tool(name, {"holdings_path": SAMPLE, **kw})
    return res, (None if res.is_error else json.loads(res.text))


# --- engine -----------------------------------------------------------------------------

def test_market_sensitivity_known_answer():
    rng = np.random.default_rng(3)
    m = pd.Series(rng.normal(0, 0.04, 200))
    r = pd.DataFrame({"TWICE": 2 * m, "HALF_INV": -0.5 * m, "NOISE": rng.normal(0, 0.04, 200)})
    out = market_sensitivity(r, np.array([0.5, 0.5, 0.0]), m)
    assert out["positions"]["TWICE"]["beta"] == pytest.approx(2)
    assert out["positions"]["TWICE"]["corr_to_market"] == pytest.approx(1)
    assert out["positions"]["HALF_INV"]["corr_to_market"] == pytest.approx(-1)
    assert abs(out["positions"]["NOISE"]["corr_to_market"]) < 0.2
    assert out["portfolio_beta"] == pytest.approx(0.75)  # 0.5*2 + 0.5*-0.5


def test_group_risk_sums_shares_and_sorts():
    positions = [{"sector": "A", "weight": 0.5, "risk_share": 0.2},
                 {"sector": "B", "weight": 0.3, "risk_share": 0.7},
                 {"sector": "A", "weight": 0.2, "risk_share": 0.1}]
    out = group_risk(positions, "sector")
    assert list(out) == ["B", "A"]
    assert out["A"] == pytest.approx({"weight": 0.7, "risk_share": 0.3, "risk_to_weight": 0.3 / 0.7})


def test_profile_has_market_and_group_risk():
    _, p = call("get_risk_profile")
    ma = p["concentration"]["market_adjusted"]
    assert ma["market_proxy"] == "synthetic market factor"
    assert {"portfolio_beta", "portfolio_corr_to_market", "market_share_of_risk"} <= set(ma)
    assert all({"beta", "corr_to_market"} <= set(x) for x in p["positions"])
    assert sum(g["risk_share"] for g in p["sector_risk"].values()) == pytest.approx(1)
    assert sum(g["weight"] for g in p["asset_class_risk"].values()) == pytest.approx(1)


def test_injected_returns_leave_market_figures_out():
    from advisor_engine.synthetic import synthetic_returns
    res = execute_tool("get_risk_profile", {"holdings_path": SAMPLE},
                       returns_fn=lambda h: synthetic_returns(list(h["ticker"]), list(h["sector"]), seed=9))
    ma = json.loads(res.text)["concentration"]["market_adjusted"]
    assert ma["market_proxy"] == "equal_weight_universe" and "portfolio_beta" not in ma


# --- tools ------------------------------------------------------------------------------

def test_tool_schemas():
    assert [t["name"] for t in TOOLS] == ["get_risk_profile", "get_holdings", "run_what_if",
                                          "run_stress_test", "show_chart"]
    assert all(t["input_schema"]["required"][0] == "holdings_path" for t in TOOLS)


def test_get_holdings():
    res, d = call("get_holdings")
    df = load_holdings(SAMPLE)
    assert d["total_value"] == pytest.approx(df["value"].sum())
    assert d["n_positions"] == len(df) and res.holdings_sha256


def test_what_if_removal_matches_engine_comparison():
    df = load_holdings(SAMPLE)
    gone = list(df["ticker"][:2])
    _, d = call("run_what_if", remove=[t.lower() for t in gone])
    assert d["comparison"]["removed"] == sorted(gone)
    assert {p["ticker"] for p in d["after"]["positions"]} == set(df["ticker"]) - set(gone)
    row = next(r for r in d["comparison"]["headline"] if r["key"] == "effective_bets")
    assert row["change"] == pytest.approx(row["after"] - row["before"])
    _, before = call("get_risk_profile")
    assert row["before"] == pytest.approx(before["concentration"]["effective_bets"])


def test_what_if_set_shares_and_add():
    df = load_holdings(SAMPLE)
    t0 = df["ticker"][0]
    new = apply_changes(df, {"set_shares": {t0: 1}, "add": [
        {"ticker": "zzz", "shares": 10, "price": 50, "sector": "Utilities", "asset_class": "Bond"}]})
    assert new.loc[new["ticker"] == t0, "shares"].item() == 1
    z = new[new["ticker"] == "ZZZ"].iloc[0]
    assert (z["asset_class"], z["value"]) == ("fixed_income", 500)


@pytest.mark.parametrize("kw,msg", [
    ({"remove": ["NOPE"]}, "Not in the holdings"),
    ({"set_shares": {"NOPE": 5}}, "Not in the holdings"),
    ({"add": [{"ticker": "AAA", "shares": 1, "price": 1}]}, "Already held"),
])
def test_what_if_errors(kw, msg):
    res, _ = call("run_what_if", **kw)
    assert res.is_error and msg in res.text


def test_what_if_cannot_remove_everything():
    res, _ = call("run_what_if", remove=list(load_holdings(SAMPLE)["ticker"]))
    assert res.is_error and "no holdings" in res.text


def test_stress_tool_preset_and_custom():
    _, d = call("run_stress_test", scenario_id="broad_selloff")
    assert d["scenario"]["id"] == "broad_selloff" and d["portfolio_return"] == pytest.approx(-0.20)
    _, d = call("run_stress_test", default_shock=-0.1)
    assert d["scenario"]["id"] == "custom" and d["portfolio_return"] == pytest.approx(-0.10)
    res, _ = call("run_stress_test", scenario_id="meteor")
    assert res.is_error


def test_new_tools_keep_the_path_guard(tmp_path):
    for name in ("get_holdings", "run_what_if", "run_stress_test"):
        res = execute_tool(name, {"holdings_path": "/etc/passwd"})
        assert res.is_error and "inside" in res.text


# --- the model loop with a what-if ------------------------------------------------------

def test_model_quotes_what_if_change_and_is_verified(tmp_path):
    gone = load_holdings(SAMPLE)["ticker"][0]
    _, d = call("run_what_if", remove=[gone])
    row = next(r for r in d["comparison"]["headline"] if r["key"] == "period_volatility")
    reply = (f"Without {gone}, monthly volatility would be {row['after']:.2%} against "
             f"{row['before']:.2%} now.")
    script = [[SimpleNamespace(type="tool_use", id="tu_1", name="run_what_if",
                               input={"holdings_path": SAMPLE, "remove": [gone]})],
              [SimpleNamespace(type="text", text=reply)]]

    class Fake:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            assert [t["name"] for t in kw["tools"]] == [t["name"] for t in TOOLS]
            return SimpleNamespace(content=script.pop(0))

    r = chat.run_turn("What if we sold it?", [], Fake(), model="m", audit_path=tmp_path / "a.jsonl")
    assert not r.blocked and r.verification["checked"] >= 2
    assert r.tool_calls[0]["name"] == "run_what_if"
