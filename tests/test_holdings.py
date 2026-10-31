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
    data = b"Symbol,Qty,Price\nAAA,1,2\n"
    with pytest.raises(ValueError) as e:
        load_holdings(data)
    msg = str(e.value)
    assert "missing columns: ['sector']" in msg and "Columns found: ['Symbol', 'Qty', 'Price']" in msg


def test_non_numeric_value_names_the_line():
    data = b"ticker,shares,price,sector\nAAA,1200,185,Tech\nTOTAL,,,\n"
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
