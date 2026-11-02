import numpy as np
import pandas as pd
import pytest

import json
from pathlib import Path

from advisor_engine.holdings import load_holdings, validate_holdings
from advisor_engine.risk import (
    effective_number_of_bets,
    market_adjusted_concentration,
    max_drawdown,
    position_risk,
    risk_profile,
    sector_exposure,
    sector_level_concentration,
    shrunk_covariance,
)
from advisor_engine.synthetic import synthetic_returns


def _holdings(n=6, sectors=None):
    sectors = sectors or ["A", "A", "B", "B", "C", "C"][:n]
    return validate_holdings(
        pd.DataFrame(
            {
                "ticker": [f"T{i}" for i in range(n)],
                "shares": [100] * n,
                "price": [10.0] * n,
                "sector": sectors,
            }
        )
    )


def test_enb_uncorrelated_equal_weight_is_n():
    n = 5
    cov = np.eye(n) * 0.04
    w = np.ones(n) / n
    enb = effective_number_of_bets(w, cov)["effective_bets"]
    assert enb == pytest.approx(n, rel=1e-6)


def test_enb_perfectly_correlated_is_one():
    n = 5
    cov = np.full((n, n), 0.04)
    w = np.ones(n) / n
    enb = effective_number_of_bets(w, cov)["effective_bets"]
    assert enb == pytest.approx(1.0, abs=1e-6)


def test_enb_concentrated_below_diversified():
    cov = np.eye(4) * 0.04
    diversified = effective_number_of_bets(np.full(4, 0.25), cov)["effective_bets"]
    concentrated = effective_number_of_bets(
        np.array([0.85, 0.05, 0.05, 0.05]), cov
    )["effective_bets"]
    assert concentrated < diversified


def test_shrinkage_in_unit_interval_and_cov_is_psd():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=60)
    cov, shrink = shrunk_covariance(r)
    assert 0.0 <= shrink <= 1.0
    assert np.all(np.linalg.eigvalsh(cov) > 0)


def test_sector_exposure_sums_to_one_and_sorted():
    h = _holdings()
    exp = sector_exposure(h)
    assert sum(exp.values()) == pytest.approx(1.0)
    vals = list(exp.values())
    assert vals == sorted(vals, reverse=True)


def test_max_drawdown_known_series():
    # +10%, -50%, +20%: peak 1.1, trough 0.55 -> -50%
    r = pd.Series([0.10, -0.50, 0.20])
    assert max_drawdown(r) == pytest.approx(-0.50)


def test_max_drawdown_never_positive():
    r = pd.Series([0.01, 0.02, 0.03])
    assert max_drawdown(r) == 0.0


def test_risk_profile_end_to_end():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    p = risk_profile(h, r).to_dict()
    assert 1.0 <= p["concentration"]["effective_bets"] <= len(h)
    assert p["drawdown"]["historical_max_drawdown"] <= 0
    assert p["drawdown"]["stress_loss_1_in_100"] < 0
    assert p["engine_version"]


def test_risk_profile_missing_returns_raises():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"])[:-1], list(h["sector"])[:-1])
    with pytest.raises(ValueError, match="No return history"):
        risk_profile(h, r)


def test_risk_profile_too_short_history_raises():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=12)
    with pytest.raises(ValueError, match="at least 24"):
        risk_profile(h, r)


def test_negative_shares_rejected():
    df = pd.DataFrame(
        {"ticker": ["A", "B"], "shares": [10, -5], "price": [1.0, 1.0], "sector": ["X", "Y"]}
    )
    with pytest.raises(ValueError, match="short"):
        validate_holdings(df)


def test_position_risk_shares_sum_to_one():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    cov, _ = shrunk_covariance(r[list(h["ticker"])])
    pr = position_risk(h, cov)
    assert sum(d["risk_share"] for d in pr) == pytest.approx(1.0)
    assert [d["risk_share"] for d in pr] == sorted(
        [d["risk_share"] for d in pr], reverse=True
    )


def test_position_risk_identical_assets_share_equally():
    h = _holdings(n=3, sectors=["A", "A", "A"])
    cov = np.full((3, 3), 0.02) + np.eye(3) * 0.02
    pr = position_risk(h, cov)
    for d in pr:
        assert d["risk_share"] == pytest.approx(1 / 3)
        assert d["risk_to_weight"] == pytest.approx(1.0)


def test_position_risk_high_vol_asset_exceeds_weight():
    h = _holdings(n=2, sectors=["A", "B"])
    cov = np.array([[0.01, 0.0], [0.0, 0.09]])
    pr = {d["ticker"]: d for d in position_risk(h, cov)}
    assert pr["T1"]["risk_to_weight"] > 1 > pr["T0"]["risk_to_weight"]


SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sample_holdings.csv"


def _factor_returns(betas, resid_vol, n_periods=2000, seed=1):
    """Assets = beta * market + independent noise. Returns (returns, market)."""
    rng = np.random.default_rng(seed)
    market = pd.Series(rng.normal(0.005, 0.04, n_periods))
    cols = {
        f"T{i}": b * market + rng.normal(0.0, resid_vol, n_periods)
        for i, b in enumerate(betas)
    }
    return pd.DataFrame(cols), market


def test_market_adjusted_market_only_returns_is_nearly_all_market_risk():
    r, _ = _factor_returns([0.8, 1.0, 1.2, 1.1], resid_vol=1e-4)
    w = np.full(4, 0.25)
    out = market_adjusted_concentration(r, w)
    assert out["market_share_of_risk"] > 0.99
    assert out["market_proxy"] == "equal_weight_universe"


def test_market_adjusted_independent_residuals_give_n_bets():
    n = 5
    r, market = _factor_returns([1.0] * n, resid_vol=0.03)
    w = np.full(n, 1 / n)
    # Explicit market: the equal-weight proxy would make the residuals sum to
    # zero, which is a property of the proxy rather than of the portfolio.
    out = market_adjusted_concentration(r, w, market=market)
    assert out["residual_effective_bets"] == pytest.approx(n, rel=0.05)


def test_market_adjusted_invariant_to_ticker_order():
    r, _ = _factor_returns([0.6, 0.9, 1.1, 1.4, 1.0], resid_vol=0.03)
    w = np.array([0.4, 0.25, 0.15, 0.1, 0.1])
    assert w.sum() == pytest.approx(1.0)
    order = [3, 0, 4, 2, 1]
    a = market_adjusted_concentration(r, w)
    b = market_adjusted_concentration(r.iloc[:, order], w[order])
    for k in ("portfolio_beta", "market_share_of_risk", "residual_effective_bets"):
        assert a[k] == pytest.approx(b[k], rel=1e-9)


def test_market_adjusted_uses_provided_market():
    r, market = _factor_returns([2.0, 2.0, 2.0], resid_vol=0.01)
    out = market_adjusted_concentration(r, np.full(3, 1 / 3), market=market)
    assert out["market_proxy"] == "provided"
    assert out["portfolio_beta"] == pytest.approx(2.0, rel=0.02)


def test_market_adjusted_provided_market_must_cover_periods():
    r, market = _factor_returns([1.0, 1.0], resid_vol=0.02, n_periods=100)
    with pytest.raises(ValueError, match="cover"):
        market_adjusted_concentration(r, np.full(2, 0.5), market=market.iloc[:50])


def test_sector_level_handles_single_holding_sector():
    h = _holdings(n=3, sectors=["A", "A", "B"])
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=60)
    out = sector_level_concentration(h, r)
    assert out["n_sectors"] == 2
    assert 1.0 <= out["sector_effective_bets"] <= 2.0


def test_sector_level_single_sector_is_one_bet():
    h = _holdings(n=3, sectors=["A", "A", "A"])
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=60)
    assert sector_level_concentration(h, r)["sector_effective_bets"] == pytest.approx(1.0)


def test_risk_profile_keeps_old_keys_and_is_json_serialisable():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    c = risk_profile(h, r).to_dict()["concentration"]
    for k in ("effective_bets", "n_positions", "top_component_share", "component_shares"):
        assert k in c
    # Equal-weight proxy: beta and market share are withheld, residual bets stay.
    assert set(c["market_adjusted"]) == {"residual_effective_bets", "market_proxy"}
    assert "sector_effective_bets" in c["sector_level"]
    json.dumps(c)  # fails on numpy scalars


def test_sample_portfolio_residual_bets_exceed_raw_bets():
    h = load_holdings(str(SAMPLE))
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]))
    c = risk_profile(h, r).to_dict()["concentration"]
    assert c["market_adjusted"]["residual_effective_bets"] > c["effective_bets"]


def test_drawdown_carries_period_label():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    assert risk_profile(h, r).to_dict()["drawdown"]["period_label"] == "monthly"
    assert risk_profile(h, r, period_label="weekly").to_dict()["drawdown"]["period_label"] == "weekly"
    assert "historical_max_drawdown" in risk_profile(h, r).to_dict()["drawdown"]


def test_stress_loss_states_its_multiplier():
    # The 1-in-100 loss is z x volatility, not an observed loss; screens quote z from here.
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    dd = risk_profile(h, r).to_dict()["drawdown"]
    assert dd["stress_loss_z"] == 2.33
    assert dd["stress_loss_1_in_100"] == pytest.approx(-dd["stress_loss_z"] * dd["period_volatility"])


def test_beta_and_market_share_only_with_provided_market():
    h = _holdings()
    r = synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=120)
    market = r.mean(axis=1) + 0.001 * pd.Series(np.arange(len(r)) % 7, index=r.index)
    m = risk_profile(h, r, market=market).to_dict()["concentration"]["market_adjusted"]
    assert m["market_proxy"] == "provided"
    assert {"portfolio_beta", "market_share_of_risk", "residual_effective_bets"} <= set(m)


def test_a_tickers_synthetic_history_does_not_depend_on_the_other_holdings():
    """What-if and compare rely on this: dropping a position must not reshuffle the rest."""
    full = synthetic_returns(["AAA", "BBB", "CCC"], ["Tech", "Health", "Tech"], n_periods=36)
    fewer = synthetic_returns(["CCC", "AAA"], ["Tech", "Tech"], n_periods=36)
    pd.testing.assert_series_equal(full["AAA"], fewer["AAA"])
    pd.testing.assert_series_equal(full["CCC"], fewer["CCC"])
