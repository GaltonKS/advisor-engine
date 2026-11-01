"""Market data for the engine: where return history comes from.

`get_market_data()` returns the configured source behind one small interface:

    returns_source: str               label carried in every profile ("synthetic", "FMP")
    market_source: str                the market series' name (reported as market_proxy)
    returns(holdings) -> DataFrame    returns per holding; columns = holdings tickers
    market(returns) -> Series         market index returns on the same periods

Configured by environment variables:
    ADVISOR_MARKET_DATA   synthetic (default) | fmp
    FMP_API_KEY           required for fmp; never logged or returned
    ADVISOR_MARKET_INDEX  market index symbol for real data (default SPY)
    ADVISOR_MARKET_CACHE  cache directory (default data/market_cache)

To add a vendor: write a `PriceSource` (see base.py and fmp.py) and add a branch below.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..synthetic import synthetic_market, synthetic_returns
from .base import DataSourceError, PriceHistoryData, PriceSource, SymbolNotFound, as_of
from .cache import PriceCache

# Same place as assistant._paths.DATA_DIR (not imported, to keep this package standalone).
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
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


def _settings() -> tuple[str, str, str, str]:
    return (os.environ.get("ADVISOR_MARKET_DATA", "synthetic").strip().lower() or "synthetic",
            os.environ.get("FMP_API_KEY", "").strip(),
            os.environ.get("ADVISOR_MARKET_INDEX", DEFAULT_INDEX).strip().upper() or DEFAULT_INDEX,
            os.environ.get("ADVISOR_MARKET_CACHE", "").strip())


@lru_cache(maxsize=4)
def _build(mode: str, fmp_key: str, index: str, cache_dir: str):
    if mode == "synthetic":
        return SyntheticData()
    cache = PriceCache(Path(cache_dir) if cache_dir else DATA_DIR / "market_cache")
    if mode == "fmp":
        from .fmp import FMPSource

        return PriceHistoryData(FMPSource(fmp_key), cache, index)
    raise DataSourceError(f"Unknown ADVISOR_MARKET_DATA {mode!r}: use synthetic or fmp.")


def get_market_data():
    """The configured market data source (built once per configuration)."""
    return _build(*_settings())


__all__ = ["DataSourceError", "PriceHistoryData", "PriceSource", "SymbolNotFound",
           "SyntheticData", "as_of", "get_market_data"]
