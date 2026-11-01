"""The market data layer: vendor-neutral returns, the cache, and the FMP adapter.

Nothing here touches the network: FMP is answered by httpx.MockTransport, and the
vendor-neutral tests use an in-memory price source.
"""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from advisor_engine import data
from advisor_engine.assistant import tools
from advisor_engine.assistant.tools import engine_profile, execute_tool
from advisor_engine.data.base import (
    MIN_PERIODS,
    PriceHistoryData,
    SymbolNotFound,
    month_end_closes,
)
from advisor_engine.data.cache import PriceCache, month_index
from advisor_engine.holdings import validate_holdings

TODAY = date(2026, 11, 15)


def daily(symbol, start="2014-01-01", end="2026-11-14", drift=0.0003, vol=0.01):
    """Deterministic business-day closes for a symbol (a seeded random walk)."""
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, vol, len(days)))), index=days)


class FakeSource:
    name = "Fake"

    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    blocked: tuple = ()

    def daily_closes(self, symbol, start):
        self.calls.append(symbol)
        if symbol in self.blocked:
            from advisor_engine.data.base import NotInPlan
            raise NotInPlan(symbol)
        if symbol not in self.histories:
            raise SymbolNotFound(symbol)
        s = self.histories[symbol]
        return s[s.index >= pd.Timestamp(start)]


def holdings(rows):
    return validate_holdings(pd.DataFrame(rows))


def frame(*tickers, cash=()):
    rows = [{"ticker": t, "shares": 10, "price": 100, "sector": "Technology"} for t in tickers]
    rows += [{"ticker": t, "shares": 10, "price": 1, "sector": "", "asset_class": "cash"} for t in cash]
    return holdings(rows)


def source_for(*symbols, **custom):
    return FakeSource({**{s: daily(s) for s in symbols}, **custom})


def market_data(src, tmp_path, **kw):
    return PriceHistoryData(src, PriceCache(tmp_path / "cache"), "SPY", today=lambda: TODAY, **kw)


# --- month-end prices -----------------------------------------------------------------

def test_month_end_closes_keep_complete_months_only():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0],
                  index=pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-30",
                                        "2026-11-02", "2026-11-13"]))
    m = month_end_closes(s.iloc[::-1], TODAY)  # order doesn't matter
    assert list(m.index.strftime("%Y-%m-%d")) == ["2026-09-30", "2026-10-31"]
    assert list(m) == [2.0, 3.0]  # last close of each month; November is still open


# --- vendor-neutral returns -------------------------------------------------------------

def test_returns_cover_every_holding_on_shared_months_with_the_market(tmp_path):
    md = market_data(source_for("AAA", "BBB", "SPY"), tmp_path)
    h = frame("AAA", "BBB", cash=["CASH"])
    r = md.returns(h)
    assert list(r.columns) == ["AAA", "BBB", "CASH"]
    assert len(r) == 120 and r.index[-1] == pd.Timestamp("2026-10-31")
    assert r["CASH"].std() < 0.001 and r["AAA"].std() > 0.01  # cash is modelled, not priced
    m = md.market(r)
    assert m.index.equals(r.index) and not m.isna().any()


def test_window_is_the_longest_every_holding_shares(tmp_path):
    young = daily("NEW", start="2023-06-01")
    md = market_data(source_for("AAA", "SPY", NEW=young), tmp_path)
    r = md.returns(frame("AAA", "NEW"))
    assert r.index[0] > pd.Timestamp("2023-06-01") and len(r) >= MIN_PERIODS


def test_too_little_shared_history_names_the_short_tickers(tmp_path):
    md = market_data(source_for("AAA", "SPY", NEW=daily("NEW", start="2025-06-01")), tmp_path)
    with pytest.raises(ValueError, match=r"at least 24 are needed.*NEW"):
        md.returns(frame("AAA", "NEW"))


def test_unknown_tickers_are_all_named(tmp_path):
    md = market_data(source_for("AAA", "SPY"), tmp_path)
    with pytest.raises(ValueError, match="No Fake price history for: XXX, ZZZ"):
        md.returns(frame("AAA", "ZZZ", "XXX"))


def test_prices_are_fetched_once_then_read_from_the_cache(tmp_path):
    src = source_for("AAA", "BBB", "SPY")
    md = market_data(src, tmp_path)
    first = md.returns(frame("AAA", "BBB"))
    assert sorted(src.calls) == ["AAA", "BBB", "SPY"]
    again = market_data(src, tmp_path).returns(frame("AAA", "BBB"))  # a fresh process
    assert sorted(src.calls) == ["AAA", "BBB", "SPY"]  # nothing refetched
    pd.testing.assert_frame_equal(first, again, check_freq=False)


def test_cache_expires(tmp_path):
    clock = [1_000_000.0]
    cache = PriceCache(tmp_path, max_age=60, clock=lambda: clock[0])
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-09-30", "2026-10-31"]))
    cache.put("Fake", "AAA", s)
    import os
    os.utime(cache.path("Fake", "AAA"), (clock[0], clock[0]))
    assert cache.get("Fake", "AAA") is not None
    clock[0] += 61
    assert cache.get("Fake", "AAA") is None


def test_cache_file_names_are_safe(tmp_path):
    p = PriceCache(tmp_path).path("Fake", "../../etc/passwd")
    assert p.parent == tmp_path / "Fake" and "/" not in p.name and ".." not in p.name.split(".")[0]


# --- engine output with real data ---------------------------------------------------------

def test_profile_is_labelled_with_source_index_and_as_of_date(tmp_path, monkeypatch):
    md = market_data(source_for("AAA", "BBB", "CCC", "SPY"), tmp_path)
    monkeypatch.setattr(tools, "get_market_data", lambda: md)
    p = engine_profile(frame("AAA", "BBB", "CCC"))
    assert p["returns_source"] == "Fake" and p["returns_as_of"] == "2026-10-31"
    adjusted = p["concentration"]["market_adjusted"]
    assert p["n_periods"] == 120 and adjusted["market_proxy"] == "SPY"
    assert "portfolio_beta" in adjusted


def test_synthetic_default_is_unchanged(monkeypatch):
    for k in ("ADVISOR_MARKET_DATA", "FMP_API_KEY", "ADVISOR_MARKET_INDEX"):
        monkeypatch.delenv(k, raising=False)
    md = data.get_market_data()
    assert isinstance(md, data.SyntheticData)
    p = engine_profile(frame("AAA", "BBB"))
    assert p["returns_source"] == "synthetic" and "returns_as_of" not in p


def test_vendor_errors_come_back_as_tool_errors(tmp_path, monkeypatch):
    md = market_data(source_for("SPY"), tmp_path)  # knows none of the sample's tickers
    monkeypatch.setattr(tools, "get_market_data", lambda: md)
    from advisor_engine.assistant._paths import DEFAULT_HOLDINGS
    res = execute_tool("get_risk_profile", {"holdings_path": str(DEFAULT_HOLDINGS)})
    assert res.is_error and res.text.startswith("Error: No Fake price history for:")


def test_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("ADVISOR_MARKET_DATA", "fmp")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(data.DataSourceError, match="FMP_API_KEY is not set"):
        data.get_market_data()
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.setenv("ADVISOR_MARKET_INDEX", "voo")
    monkeypatch.setenv("ADVISOR_MARKET_CACHE", str(tmp_path))
    md = data.get_market_data()
    assert md.returns_source == "FMP" and md.market_source == "VOO" and md.cache.root == tmp_path
    monkeypatch.setenv("ADVISOR_MARKET_DATA", "bloomberg")
    with pytest.raises(data.DataSourceError, match="Unknown ADVISOR_MARKET_DATA"):
        data.get_market_data()


# --- FMP adapter --------------------------------------------------------------------------

httpx = pytest.importorskip("httpx")
from advisor_engine.data.fmp import FMPSource, fmp_symbol  # noqa: E402

KEY = "secret-key-123"


def fmp(handler):
    return FMPSource(KEY, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_fmp_request_and_stable_response():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, params=dict(request.url.params))
        return httpx.Response(200, json=[
            {"symbol": "BRK-B", "date": "2026-10-31", "adjClose": 101.5, "close": 999},
            {"symbol": "BRK-B", "date": "2026-10-30", "adjClose": 100.0, "close": 999},
        ])

    s = fmp(handler).daily_closes("brk.b", date(2016, 1, 1))
    assert seen["path"] == "/stable/historical-price-eod/dividend-adjusted"
    assert seen["params"] == {"symbol": "BRK-B", "from": "2016-01-01", "apikey": KEY}
    assert list(s) == [100.0, 101.5]  # adjusted closes, oldest first


def test_fmp_v3_response_shape_and_plain_close():
    s = fmp(lambda r: httpx.Response(200, json={"symbol": "AAA", "historical": [
        {"date": "2026-10-31", "close": 5.0}]})).daily_closes("AAA", date(2016, 1, 1))
    assert list(s) == [5.0]


def test_fmp_unknown_symbol():
    with pytest.raises(SymbolNotFound):
        fmp(lambda r: httpx.Response(200, json=[])).daily_closes("ZZZ", date(2016, 1, 1))


@pytest.mark.parametrize("response, match", [
    (httpx.Response(401, json={"Error Message": f"Invalid API KEY {KEY}"}), "check FMP_API_KEY"),
    (httpx.Response(200, json={"Error Message": f"Invalid API KEY {KEY}"}), "check FMP_API_KEY"),
    (httpx.Response(429), "rate limit"),
    (httpx.Response(500), "HTTP 500"),
    (httpx.Response(200, text="<html>"), "isn't JSON"),
])
def test_fmp_errors_never_show_the_key(response, match):
    with pytest.raises(data.DataSourceError, match=match) as e:
        fmp(lambda r: response).daily_closes("AAA", date(2016, 1, 1))
    assert KEY not in str(e.value)


def test_fmp_network_error_never_shows_the_key():
    def handler(request):
        raise httpx.ConnectError(f"failed for {request.url}")

    with pytest.raises(data.DataSourceError, match="Couldn't reach FMP") as e:
        fmp(handler).daily_closes("AAA", date(2016, 1, 1))
    assert KEY not in str(e.value) and e.value.__cause__ is None


def test_fmp_symbols():
    assert [fmp_symbol(t) for t in ("aapl", "BRK.B", "BF/B", " spy ")] == ["AAPL", "BRK-B", "BF-B", "SPY"]


def test_fmp_end_to_end_profile(tmp_path, monkeypatch):
    histories = {s: daily(s) for s in ("AAA", "BBB", "SPY")}

    def handler(request):
        sym = request.url.params["symbol"]
        rows = [{"date": d.strftime("%Y-%m-%d"), "adjClose": float(v)}
                for d, v in histories.get(sym, pd.Series(dtype=float)).items()]
        return httpx.Response(200, json=rows[::-1])

    md = PriceHistoryData(fmp(handler), PriceCache(tmp_path), "SPY", today=lambda: TODAY)
    monkeypatch.setattr(tools, "get_market_data", lambda: md)
    p = engine_profile(frame("AAA", "BBB"))
    assert p["returns_source"] == "FMP" and p["returns_as_of"] == "2026-10-31"
    json.dumps(p)  # still JSON-serialisable


def test_as_of_month_and_year_pass_the_number_check():
    # The prompt asks for "end of October 2026": years are not figures, and no day is quoted.
    from advisor_engine.assistant.numcheck import verify
    out = json.dumps({"returns_source": "FMP", "returns_as_of": "2026-10-31", "n_periods": 120})
    assert verify("120 months of FMP history to the end of October 2026.", [out])["ok"]


# --- Supabase-backed cache (real Postgres with the migrations applied) ----------------------

def closes(*values, start="2026-08-31"):
    return pd.Series(values, index=month_index(pd.date_range(start, periods=len(values), freq="ME")),
                     dtype=float)


@pytest.fixture
def db_cache(supabase_db):
    from advisor_engine.data.supabase_cache import SupabasePriceCache

    return lambda **kw: SupabasePriceCache(supabase_db, **kw)


def test_supabase_cache_round_trip_across_instances(db_cache):
    db_cache().put_many("Fake", {"AAA": closes(1.0, 2.0, 3.0), "BBB": closes(4.0, 5.0)})
    got = db_cache().get_many("Fake", ["AAA", "BBB", "CCC"])  # a fresh process
    assert sorted(got) == ["AAA", "BBB"]
    pd.testing.assert_series_equal(got["AAA"], closes(1.0, 2.0, 3.0), check_freq=False)


def test_supabase_cache_replaces_a_symbols_whole_history(db_cache, supabase_db):
    db_cache().put_many("Fake", {"AAA": closes(1.0, 2.0, 3.0)})
    db_cache().put_many("Fake", {"AAA": closes(9.0, 8.0, start="2026-09-30")})  # restated
    assert list(db_cache().get_many("Fake", ["AAA"])["AAA"]) == [9.0, 8.0]


def test_supabase_cache_entries_expire_by_the_database_clock(db_cache, supabase_db):
    import psycopg

    db_cache().put_many("Fake", {"AAA": closes(1.0)})
    with psycopg.connect(supabase_db, autocommit=True) as conn:
        conn.execute("update market.fetches set fetched_at = now() - interval '2 days'")
    assert db_cache().get_many("Fake", ["AAA"]) == {}


def test_supabase_cache_serves_repeat_reads_from_memory(db_cache):
    cache = db_cache()
    cache.put_many("Fake", {"AAA": closes(1.0)})
    cache._connect = lambda: (_ for _ in ()).throw(AssertionError("no database call"))
    assert list(cache.get_many("Fake", ["AAA"])["AAA"]) == [1.0]


def test_supabase_cache_outage_falls_back_to_the_vendor(tmp_path, caplog):
    from advisor_engine.data.supabase_cache import SupabasePriceCache

    down = SupabasePriceCache("postgresql://nobody@127.0.0.1:1/none")
    assert down.get_many("Fake", ["AAA"]) == {}
    down.put_many("Fake", {"AAA": closes(1.0)})  # skipped, not raised
    md = PriceHistoryData(source_for("AAA", "SPY"), down, "SPY", today=lambda: TODAY)
    assert len(md.returns(frame("AAA"))) == 120
    assert "Price cache read failed" in caplog.text


def test_vendor_is_asked_once_per_symbol_across_api_instances(db_cache):
    src = source_for("AAA", "BBB", "SPY")
    for _ in range(2):  # two instances, e.g. after a deploy
        PriceHistoryData(src, db_cache(), "SPY", today=lambda: TODAY).returns(frame("AAA", "BBB"))
    assert sorted(src.calls) == ["AAA", "BBB", "SPY"]


def test_cache_configuration(monkeypatch, tmp_path):
    from advisor_engine.data.supabase_cache import SupabasePriceCache

    monkeypatch.setenv("ADVISOR_MARKET_DATA", "fmp")
    monkeypatch.setenv("FMP_API_KEY", "k")
    monkeypatch.delenv("ADVISOR_MARKET_CACHE", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://x@db/postgres")
    assert isinstance(data.get_market_data().cache, SupabasePriceCache)  # default with Supabase
    monkeypatch.setenv("ADVISOR_MARKET_CACHE", "disk")
    assert isinstance(data.get_market_data().cache, PriceCache)
    monkeypatch.setenv("ADVISOR_MARKET_CACHE", "supabase")
    monkeypatch.delenv("SUPABASE_DB_URL")
    with pytest.raises(data.DataSourceError, match="needs SUPABASE_DB_URL"):
        data.get_market_data()


# --- tickers outside the vendor plan ---------------------------------------------------------

def test_tickers_outside_the_plan_are_named_and_the_rest_are_kept(tmp_path):
    src = source_for("AAA", "BBB", "SPY")
    src.blocked = ("BBB",)
    md = market_data(src, tmp_path)
    with pytest.raises(ValueError, match=r"Your Fake plan doesn't include: BBB\.$") as e:
        md.returns(frame("AAA", "BBB"))
    assert "SPY" not in str(e.value)
    src.blocked = ()
    calls = len(src.calls)
    md.returns(frame("AAA", "BBB"))
    assert src.calls[calls:] == ["BBB"]  # AAA and SPY were cached on the first try


def test_market_index_outside_the_plan_says_how_to_fix_it(tmp_path):
    src = source_for("AAA", "SPY")
    src.blocked = ("SPY",)
    with pytest.raises(ValueError, match="doesn't include SPY, the market index.*ADVISOR_MARKET_INDEX"):
        market_data(src, tmp_path).returns(frame("AAA"))


@pytest.mark.parametrize("response", [
    httpx.Response(402, json={"Error Message": "Premium endpoint"}),
    httpx.Response(200, json={"Error Message": "Special Endpoint: this symbol is not available "
                                               "under your current subscription"}),
])
def test_fmp_plan_limits_are_reported_per_symbol(response):
    from advisor_engine.data.base import NotInPlan

    with pytest.raises(NotInPlan):
        fmp(lambda r: response).daily_closes("SPY", date(2016, 1, 1))
