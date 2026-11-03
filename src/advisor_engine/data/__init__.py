"""Market data for the engine: where return history comes from.

`get_market_data()` returns the configured source behind one small interface:

    returns_source: str               label carried in every profile ("synthetic", "FMP")
    market_source: str                the market series' name (reported as market_proxy)
    returns(holdings) -> DataFrame    returns per holding; columns = holdings tickers
    market(returns) -> Series         market index returns on the same periods

Configured by environment variables (from the host, or a local `.env`; see _env.py):
    ADVISOR_MARKET_DATA   synthetic (default) | fmp | twelvedata (alias: td)
    FMP_API_KEY           required for fmp; never logged or returned
    TWELVE_DATA_API_KEY   required for twelvedata; never logged or returned
    ADVISOR_MARKET_INDEX  market index symbol for real data (default SPY)
    ADVISOR_MARKET_CACHE  where month-end prices are cached: "supabase" (the default when
                          SUPABASE_DB_URL is set, so prices survive deploys), "disk", or
                          a directory (default data/market_cache)

To add a vendor: write a `PriceSource` (see base.py and fmp.py) and add a branch below.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .._env import load_env
from ..paths import DATA_DIR
from ..synthetic import synthetic_market, synthetic_returns
from .base import DataSourceError, PriceHistoryData, PriceSource, SymbolNotFound, as_of
from .cache import PriceCache

DEFAULT_INDEX = "SPY"  # S&P 500 ETF: a broad index with dividend-adjusted history


class SyntheticData:
    """The factor model in synthetic.py: illustrative returns, no dates."""

    returns_source = "synthetic"
    market_source = "synthetic market factor"

    def returns(self, holdings: pd.DataFrame) -> pd.DataFrame:
        classes = list(holdings["asset_class"]) if "asset_class" in holdings else None
        return synthetic_returns(list(holdings["ticker"]), list(holdings["sector"]),
                                 asset_classes=classes)

    def market(self, returns: pd.DataFrame) -> pd.Series:
        return synthetic_market(len(returns)).set_axis(returns.index)


MODE_ALIASES = {"td": "twelvedata", "twelve_data": "twelvedata", "twelve-data": "twelvedata"}
KEY_VARS = {"fmp": "FMP_API_KEY", "twelvedata": "TWELVE_DATA_API_KEY"}


def _settings() -> tuple[str, str, str, str, str]:
    load_env()
    mode = os.environ.get("ADVISOR_MARKET_DATA", "synthetic").strip().lower() or "synthetic"
    mode = MODE_ALIASES.get(mode, mode)
    return (mode,
            os.environ.get(KEY_VARS.get(mode, ""), "").strip() if mode in KEY_VARS else "",
            os.environ.get("ADVISOR_MARKET_INDEX", DEFAULT_INDEX).strip().upper() or DEFAULT_INDEX,
            os.environ.get("ADVISOR_MARKET_CACHE", "").strip(),
            os.environ.get("SUPABASE_DB_URL", "").strip())


def _cache(setting: str, db_url: str):
    if setting.lower() == "supabase" or (not setting and db_url):
        if not db_url:
            raise DataSourceError("ADVISOR_MARKET_CACHE=supabase needs SUPABASE_DB_URL.")
        from .supabase_cache import SupabasePriceCache

        return SupabasePriceCache(db_url)
    if not setting or setting.lower() == "disk":
        return PriceCache(DATA_DIR / "market_cache")
    return PriceCache(Path(setting))


@lru_cache(maxsize=4)
def _build(mode: str, api_key: str, index: str, cache_setting: str, db_url: str):
    if mode == "synthetic":
        return SyntheticData()
    cache = _cache(cache_setting, db_url)
    if mode == "fmp":
        from .fmp import FMPSource

        return PriceHistoryData(FMPSource(api_key), cache, index)
    if mode == "twelvedata":
        from .twelvedata import TwelveDataSource

        return PriceHistoryData(TwelveDataSource(api_key), cache, index)
    raise DataSourceError(f"Unknown ADVISOR_MARKET_DATA {mode!r}: "
                          "use synthetic, fmp or twelvedata (td).")


def get_market_data():
    """The configured market data source (built once per configuration)."""
    return _build(*_settings())


__all__ = ["DataSourceError", "PriceHistoryData", "PriceSource", "SymbolNotFound",
           "SyntheticData", "as_of", "get_market_data"]
