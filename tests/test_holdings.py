"""Holdings CSV parsing: layouts saved by Excel or exported by brokers, and the
ambiguous cases that must be rejected rather than guessed."""

import pytest

from advisor_engine.holdings import load_holdings

EXPECTED = {"AAA": (1200.0, 185.0), "BBB": (800.0, 420.5)}


def _check(df):
    assert list(df["ticker"]) == ["AAA", "BBB"]
    for _, row in df.iterrows():
        assert (row["shares"], row["price"]) == EXPECTED[row["ticker"]]
    assert abs(df["weight"].sum() - 1) < 1e-12


@pytest.mark.parametrize("name,data", [
    ("plain", "ticker,shares,price,sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"),
    ("capitalised", "Ticker,Shares,Price,Sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"),
    ("upper + spaces", "TICKER , SHARES, PRICE ,SECTOR\naaa, 1200, 185, Tech\nbbb,800,420.5,Tech\n"),
    ("bom", "﻿ticker,shares,price,sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"),
    ("semicolon", "ticker;shares;price;sector\nAAA;1200;185;Tech\nBBB;800;420,5;Tech\n"),
    ("semicolon eu grouping", "ticker;shares;price;sector\nAAA;1.200,00;185;Tech\nBBB;800;420,50;Tech\n"),
    ("tab", "ticker\tshares\tprice\tsector\nAAA\t1200\t185\tTech\nBBB\t800\t420.5\tTech\n"),
    ("title rows", "Mock Portfolio 2\nAs of 10/31/2026,,,\n\nticker,shares,price,sector\n"
                   "AAA,1200,185,Tech\nBBB,800,420.5,Tech\n"),
    ("aliases", "Symbol,Quantity,Last Price,Sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"),
    ("currency + grouping", 'ticker,shares,price,sector\nAAA,"1,200","$185.00",Tech\n'
                            'BBB,800,$420.50,Tech\n'),
    ("trailing blank rows", "ticker,shares,price,sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n,,,\n,,,\n"),
    ("extra columns", "ticker,name,shares,price,sector,notes\nAAA,A Co,1200,185,Tech,x\n"
                      "BBB,B Co,800,420.5,Tech,\n"),
    ("crlf", "ticker,shares,price,sector\r\nAAA,1200,185,Tech\r\nBBB,800,420.5,Tech\r\n"),
])
def test_accepted_layouts(name, data):
    _check(load_holdings(data.encode("utf-8")))


def test_windows_1252_encoding():
    data = "ticker,shares,price,sector\nAAA,1200,185,Tech–Hardware\nBBB,800,420.5,Tech\n"
    df = load_holdings(data.encode("cp1252"))
    assert df["sector"][0] == "Tech–Hardware"


def test_path_and_bytes_agree(tmp_path):
    data = b"Ticker,Shares,Price,Sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"
    path = tmp_path / "h.csv"
    path.write_bytes(data)
    assert load_holdings(str(path)).equals(load_holdings(data))


@pytest.mark.parametrize("value", ["1,200", "1.200"])
def test_semicolon_ambiguous_numbers_are_rejected(value):
    data = f"ticker;shares;price;sector\nAAA;{value};185;Tech\n".encode()
    with pytest.raises(ValueError, match="ambiguous"):
        load_holdings(data)


def test_missing_header_names_what_was_found():
    data = b"Symbol,Qty,Value\nAAA,1,2\n"
    with pytest.raises(ValueError) as e:
        load_holdings(data)
    msg = str(e.value)
    assert "missing columns: ['price']" in msg and "Columns found: ['Symbol', 'Qty', 'Value']" in msg


def test_non_numeric_value_names_the_line():
    data = b"ticker,shares,price,sector\nAAA,1200,185,Tech\nCCC,,185,Tech\n"
    with pytest.raises(ValueError, match=r"Line 3: shares value '' is empty"):
        load_holdings(data)


def test_accounting_negative_is_rejected_as_short():
    data = b"ticker,shares,price,sector\nAAA,(5),185,Tech\n"
    with pytest.raises(ValueError, match="Negative shares"):
        load_holdings(data)


def test_duplicate_required_column_is_rejected():
    data = b"ticker,symbol,shares,price,sector\nAAA,AAA,1,2,Tech\n"
    with pytest.raises(ValueError, match="more than one column"):
        load_holdings(data)


def test_empty_file_is_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        load_holdings(b"")


# --- holdings with no sector ------------------------------------------------------

import numpy as np  # noqa: E402

from advisor_engine.compare import compare_profiles  # noqa: E402
from advisor_engine.holdings import UNCLASSIFIED  # noqa: E402
from advisor_engine.risk import risk_profile  # noqa: E402
from advisor_engine.synthetic import synthetic_returns  # noqa: E402

MIXED = (b"ticker,shares,price,sector\nAAA,100,50,Technology\nBBB,100,50,Technology\n"
         b"CCC,100,50,Energy\nDDD,100,100,\nEEE,100,50,unclassified\n")


def _profile(df):
    r = synthetic_returns(list(df["ticker"]), list(df["sector"]))
    return risk_profile(df, r).to_dict()


def test_no_sector_column_is_accepted_as_unclassified():
    df = load_holdings(b"Symbol,Qty,Price\nAAA,1200,185\nBBB,800,420.5\n")
    assert list(df["sector"]) == [UNCLASSIFIED, UNCLASSIFIED]


def test_blank_and_typed_unclassified_share_one_label():
    df = load_holdings(MIXED)
    assert list(df["sector"]) == ["Technology", "Technology", "Energy", UNCLASSIFIED, UNCLASSIFIED]


def test_unclassified_holdings_share_no_sector_factor():
    t = [f"U{i}" for i in range(6)]
    unclassified = synthetic_returns(t, [UNCLASSIFIED] * 6, n_periods=600)
    one_sector = synthetic_returns(t, ["Energy"] * 6, n_periods=600)
    off_diag = lambda r: r.corr().to_numpy()[~np.eye(6, dtype=bool)].mean()  # noqa: E731
    # Same market factor in both; only the shared sector factor differs.
    assert off_diag(unclassified) < off_diag(one_sector) - 0.05


def test_classified_series_do_not_depend_on_unclassified_neighbours():
    alone = synthetic_returns(["AAA"], ["Technology"])
    mixed = synthetic_returns(["AAA", "ZZZ"], ["Technology", UNCLASSIFIED])
    assert np.array_equal(alone["AAA"].to_numpy(), mixed["AAA"].to_numpy())


def test_profile_withholds_sector_bets_and_reports_coverage():
    p = _profile(load_holdings(MIXED))
    sl = p["concentration"]["sector_level"]
    assert "sector_effective_bets" not in sl and "top_component_share" not in sl
    assert sl["n_sectors"] == 2 and "no sector" in sl["withheld"]
    cov = p["sector_coverage"]
    assert cov["n_unclassified"] == 2 and cov["unclassified_tickers"] == ["DDD", "EEE"]
    assert cov["unclassified_weight"] == pytest.approx(0.5)
    # Unclassified is listed last even though it is the largest bucket.
    assert list(p["sector_exposure"]) == ["Technology", "Energy", UNCLASSIFIED]


def test_fully_classified_profile_is_unchanged():
    p = _profile(load_holdings(b"ticker,shares,price,sector\nAAA,1,2,Tech\nBBB,1,2,Energy\n"))
    assert "sector_effective_bets" in p["concentration"]["sector_level"]
    assert p["sector_coverage"] == {"unclassified_weight": 0.0, "n_unclassified": 0,
                                    "unclassified_tickers": []}


def test_compare_skips_a_withheld_figure():
    full = _profile(load_holdings(b"ticker,shares,price,sector\nAAA,1,2,Tech\nBBB,1,2,Energy\n"))
    gap = _profile(load_holdings(b"ticker,shares,price,sector\nAAA,1,2,Tech\nBBB,1,2,\n"))
    keys = [r["key"] for r in compare_profiles(full, gap)["headline"]]
    assert "sector_effective_bets" not in keys and "effective_bets" in keys


# --- full portfolio exports (many columns, totals rows) ---------------------------

EXPORT_HEADER = ("Ticker,Name,Asset Class,Sector,Shares,Avg Cost,Share Price,Dividend Yield (%),"
                 "Cost Basis,Market Value,Gain/Loss ($),Gain/Loss (%),Weight (%),"
                 "Est. Annual Income ($)\n")
EXPORT_ROWS = ('AAA,A Co,Equity,Tech,1200,"$150.00","$185.00",0.44%,"$180,000.00",'
               '"$222,000.00","$42,000.00",23.3%,69.7%,$50.00\n'
               'BBB,B Co,Equity,Tech,800,$400.00,$420.50,3.2%,"$320,000.00","$336,400.00",'
               '"$16,400.00",5.1%,30.3%,$148.80\n')


def test_portfolio_export_uses_share_price_not_cost():
    _check(load_holdings((EXPORT_HEADER + EXPORT_ROWS).encode()))


@pytest.mark.parametrize("header", ["Share Price ($)", "Price per share", "Current Price ($)"])
def test_price_header_variants(header):
    data = f"Ticker,Shares,{header},Sector\nAAA,1200,185,Tech\nBBB,800,420.5,Tech\n"
    _check(load_holdings(data.encode()))


@pytest.mark.parametrize("header", ["Avg Cost", "Cost Basis", "Market Value"])
def test_cost_or_value_columns_are_never_read_as_price(header):
    data = f"Ticker,Shares,{header},Sector\nAAA,1200,185,Tech\n".encode()
    with pytest.raises(ValueError, match=r"missing columns: \['price'\]"):
        load_holdings(data)


def test_totals_rows_are_skipped():
    totals = (',,,,,,,,"$500,000.00","$558,400.00",,,100%,\n'
              'Total:,,,,,,,,,"$558,400.00",,,,\n')
    _check(load_holdings((EXPORT_HEADER + EXPORT_ROWS + totals).encode()))


def test_a_holding_with_blank_shares_is_still_an_error():
    data = (EXPORT_HEADER + EXPORT_ROWS + 'CCC,C Co,Equity,Tech,,,,,,"$1,000.00",,,,\n').encode()
    with pytest.raises(ValueError, match=r"Line 4: shares value '' is empty"):
        load_holdings(data)
