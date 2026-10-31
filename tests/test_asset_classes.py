"""Asset classes: parsing, the synthetic return model, the covariance method, stress
scenarios and the profile fields that report the mix."""

import numpy as np
import pandas as pd
import pytest

from advisor_engine.assistant.tools import get_returns
from advisor_engine.holdings import classify_asset_class, load_holdings, validate_holdings
from advisor_engine.risk import risk_profile, shrunk_covariance
from advisor_engine.stress import run_scenario, stress_test
from advisor_engine.synthetic import synthetic_returns

# Trimmed from an advisor's mock portfolio export: same columns, a few rows, and the
# totals line the export ends with.
EXPORT = (
    "Ticker,Name,Asset Class,Sector,Shares,Avg Cost,Share Price,Dividend Yield (%),Cost Basis,"
    "Market Value,Gain/Loss ($),Gain/Loss (%),Weight (%),Est. Annual Income ($)\n"
    "KO,Coca-Cola Co.,US Equity,Consumer Staples,90,54.20,71.30,2.90,4878.00,6417.00,1539.00,31.55,3.68,186.09\n"
    "VZ,Verizon Communications,US Equity,Communication,120,41.90,43.75,6.20,5028.00,5250.00,222.00,4.42,3.01,325.50\n"
    "SCHD,Schwab US Dividend Equity ETF,US Equity ETF,Dividend Growth,400,62.75,81.20,3.50,25100.00,32480.00,7380.00,29.40,18.62,1136.80\n"
    "PFF,iShares Preferred & Income Securities ETF,Preferred ETF,Preferred Stock,200,34.10,31.85,6.40,6820.00,6370.00,-450.00,-6.60,3.65,407.68\n"
    "LQD,iShares iBoxx IG Corporate Bond ETF,Fixed Income ETF,Corporate Bonds,120,114.50,108.90,4.30,13740.00,13068.00,-672.00,-4.89,7.49,561.92\n"
    "SHY,iShares 1-3 Year Treasury Bond ETF,Fixed Income ETF,Short Treasuries,150,83.20,82.35,4.10,12480.00,12352.50,-127.50,-1.02,7.08,506.45\n"
    "CASH,Cash & Money Market,Cash,Cash,12000,1.00,1.00,4.50,12000.00,12000.00,0.00,0.00,6.88,540.00\n"
    "TOTAL,Portfolio Total,,,,,,3.64,150841.50,174455.75,23614.25,15.66,100.00,6344.41\n"
).encode()


def _profile(df):
    return risk_profile(df, get_returns(df)).to_dict()


# --- parsing ----------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected", [
    ("US Equity", "equity"), ("Intl Equity ETF", "equity"), ("Stock", "equity"),
    ("Fixed Income ETF", "fixed_income"), ("Municipal Bonds", "fixed_income"),
    ("Treasury", "fixed_income"), ("fixed_income", "fixed_income"),
    ("Preferred ETF", "preferred"), ("Preferred Stock", "preferred"),
    ("Cash", "cash"), ("Money Market Fund", "cash"),
    ("", "equity"), ("Commodities", "other"), ("Balanced", "other"),
])
def test_classify_asset_class(label, expected):
    assert classify_asset_class(label) == expected


def test_export_reads_asset_classes_and_standard_sectors():
    df = load_holdings(EXPORT)
    assert list(df["ticker"]) == ["KO", "VZ", "SCHD", "PFF", "LQD", "SHY", "CASH"]
    assert dict(zip(df["ticker"], df["asset_class"])) == {
        "KO": "equity", "VZ": "equity", "SCHD": "equity", "PFF": "preferred",
        "LQD": "fixed_income", "SHY": "fixed_income", "CASH": "cash"}
    # "Communication" is the standard sector under another name.
    assert df.loc[df["ticker"] == "VZ", "sector"].item() == "Communication Services"


@pytest.mark.parametrize("raw,standard", [
    ("Healthcare", "Health Care"), ("health care", "Health Care"),
    ("Consumer Cyclical", "Consumer Discretionary"), ("Financial Services", "Financials"),
    ("Information Technology", "Technology"), ("Dividend Growth", "Dividend Growth"),
])
def test_sector_aliases(raw, standard):
    df = load_holdings(f"ticker,shares,price,sector\nAAA,1,2,{raw}\n".encode())
    assert df["sector"][0] == standard


def test_file_without_asset_class_is_all_equity():
    df = load_holdings(b"ticker,shares,price,sector\nAAA,1,2,Energy\n")
    assert list(df["asset_class"]) == ["equity"]


# --- synthetic returns ------------------------------------------------------------------

def test_equity_series_unchanged_by_asset_class_option():
    t, s = ["AAA", "BBB"], ["Energy", "Technology"]
    a = synthetic_returns(t, s)
    b = synthetic_returns(t, s, asset_classes=["equity", "equity"])
    c = synthetic_returns([*t, "LQD"], [*s, "Bonds"], asset_classes=["equity", "equity", "fixed_income"])
    assert a.equals(b)
    assert a["AAA"].equals(c["AAA"]) and a["BBB"].equals(c["BBB"])


def test_bonds_and_cash_behave_unlike_stocks():
    t = ["S1", "S2", "B1", "B2", "C"]
    r = synthetic_returns(t, ["Energy", "Technology", "Bonds", "Bonds", "Cash"], n_periods=600,
                          asset_classes=["equity", "equity", "fixed_income", "fixed_income", "cash"])
    vol, corr = r.std(), r.corr()
    assert vol["C"] < 0.001 < vol["B1"] < 0.03 < vol["S1"]
    assert corr.loc["B1", "B2"] > 0.5            # bonds share the rates factor
    assert abs(corr.loc["S1", "B1"]) < 0.3       # and barely move with stocks


# --- covariance -------------------------------------------------------------------------

def test_shrinkage_keeps_each_volatility():
    rng = np.random.default_rng(1)
    r = pd.DataFrame(rng.normal(0, [0.05, 0.02, 0.0003], size=(60, 3)))
    cov, shrink = shrunk_covariance(r)
    assert 0 <= shrink <= 1
    assert np.allclose(np.sqrt(np.diag(cov)), r.to_numpy().std(axis=0))
    assert np.all(np.linalg.eigvalsh(cov) > -1e-12)


def test_constant_series_contributes_no_variance():
    r = pd.DataFrame({"A": np.linspace(-0.05, 0.05, 30), "K": 0.003})
    cov, _ = shrunk_covariance(r)
    assert cov[1, 1] == pytest.approx(0, abs=1e-20) and cov[0, 1] == pytest.approx(0, abs=1e-20)


# --- profile ----------------------------------------------------------------------------

def test_profile_reports_mix_and_cash_is_near_riskless():
    p = _profile(load_holdings(EXPORT))
    mix = p["asset_class_exposure"]
    assert set(mix) == {"equity", "fixed_income", "preferred", "cash"}
    assert sum(mix.values()) == pytest.approx(1)
    assert list(mix) == sorted(mix, key=mix.get, reverse=True)
    cash = next(x for x in p["positions"] if x["ticker"] == "CASH")
    assert cash["asset_class"] == "cash" and cash["volatility"] < 0.001


def test_bonds_and_cash_lower_portfolio_risk():
    df = load_holdings(EXPORT)
    as_stocks = validate_holdings(df.assign(asset_class="equity"))
    assert (_profile(df)["drawdown"]["period_volatility"]
            < 0.8 * _profile(as_stocks)["drawdown"]["period_volatility"])


# --- stress -----------------------------------------------------------------------------

def test_broad_selloff_by_asset_class():
    r = run_scenario(load_holdings(EXPORT), "broad_selloff")
    shock = {p["ticker"]: p["shock"] for p in r["positions"]}
    assert shock["KO"] == pytest.approx(-0.20) and shock["PFF"] == pytest.approx(-0.10)
    assert shock["LQD"] == pytest.approx(0.02) and shock["CASH"] == 0.0
    classes = {a["asset_class"]: a for a in r["asset_classes"]}
    assert classes["cash"]["contribution"] == 0.0
    assert r["portfolio_return"] == pytest.approx(sum(p["contribution"] for p in r["positions"]))


def test_custom_stress_cash_holds_unless_shocked():
    df = load_holdings(EXPORT)
    r = stress_test(df, {}, default_shock=-0.10)
    shock = {p["ticker"]: p["shock"] for p in r["positions"]}
    assert shock["CASH"] == 0.0 and shock["LQD"] == pytest.approx(-0.10)
    r = stress_test(df, {}, -0.10, {"fixed_income": -0.03, "cash": -0.01})
    shock = {p["ticker"]: p["shock"] for p in r["positions"]}
    assert shock["LQD"] == pytest.approx(-0.03) and shock["CASH"] == pytest.approx(-0.01)


def test_equity_cannot_be_shocked_as_an_asset_class():
    with pytest.raises(ValueError, match="Asset class shocks apply to"):
        stress_test(load_holdings(EXPORT), {}, 0.0, {"equity": -0.2})


def test_sector_shock_is_weighted_average_when_classes_mix():
    df = load_holdings(b"ticker,shares,price,sector,asset class\n"
                       b"AAA,100,10,Energy,Equity\nBBB,100,30,Energy,Bond\n")
    r = stress_test(df, {"Energy": -0.20}, 0.0, {"fixed_income": 0.0})
    (energy,) = r["sectors"]
    assert energy["shock"] == pytest.approx(0.25 * -0.20)
