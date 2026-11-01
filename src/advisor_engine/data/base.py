"""The vendor-neutral part of the market data layer.

A vendor adapter (`PriceSource`) does one thing: return a symbol's dividend-adjusted
daily closes. Everything after that lives here and is the same for every vendor:
month-end prices, the shared date window, returns, the minimum history, cash, the
market index and the cache. Adding a vendor means writing one adapter like `fmp.py`.

Returns are monthly, matching `risk_profile`'s default `period_label`. A month counts
only once it is complete, so figures don't move with partial-month prices.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from ..synthetic import CASH_NOISE, CASH_RETURN, _rng
from .cache import PriceCache, month_index  # or SupabasePriceCache: same get_many / put_many

N_PERIODS = 120   # ten years of months, when every holding has that much history
MIN_PERIODS = 24  # risk_profile's own minimum
FETCH_WORKERS = 8  # parallel requests when filling the cache


class DataSourceError(Exception):
    """A vendor request failed (bad key, plan limits, rate limit, outage). The message is
    shown to the adviser, so it must never contain the API key or the request URL."""


class SymbolNotFound(DataSourceError):
    """The vendor has no price history for this symbol."""


class PriceSource(Protocol):
    """A market data vendor. `name` labels the figures (returns_source)."""

    name: str

    def daily_closes(self, symbol: str, start: date) -> pd.Series:
        """Dividend-adjusted daily closes from `start` on, indexed by date (any order).
        Raises SymbolNotFound when the vendor has no history for `symbol`."""
        ...


def month_end_closes(daily: pd.Series, today: date) -> pd.Series:
    """Last close of each complete calendar month, indexed by month end."""
    s = pd.Series(daily.to_numpy(dtype=float), index=month_index(daily.index)).sort_index()
    monthly = s.dropna().resample("ME").last().dropna()
    # The current month is still moving; leave it out until it ends.
    return monthly[monthly.index < pd.Timestamp(today.replace(day=1))]


class PriceHistoryData:
    """Monthly returns from a `PriceSource`, cached. Implements the engine's MarketData
    interface (see `data.get_market_data`)."""

    def __init__(self, source: PriceSource, cache: PriceCache, index_symbol: str,
                 n_periods: int = N_PERIODS, today=date.today):
        self.source, self.cache, self.index_symbol = source, cache, index_symbol
        self.n_periods = n_periods
        self.today = today
        self.returns_source = source.name
        self.market_source = index_symbol

    # --- prices --------------------------------------------------------------------

    def _fetch(self, symbol: str) -> pd.Series:
        today = self.today()
        start = today.replace(day=1) - timedelta(days=31 * (self.n_periods + 2))
        return month_end_closes(self.source.daily_closes(symbol, start), today)

    def monthly_closes(self, symbols: list[str]) -> dict[str, pd.Series]:
        """Month-end closes per symbol: from the cache when fresh, else from the vendor
        (in parallel). Raises ValueError naming every symbol the vendor doesn't have."""
        name = self.source.name
        out = self.cache.get_many(name, symbols)
        missing = [s for s in symbols if s not in out]
        if missing:
            def one(sym):
                try:
                    return sym, self._fetch(sym)
                except SymbolNotFound:
                    return sym, None
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                fetched = list(pool.map(one, missing))
            unknown = [s for s, series in fetched if series is None or series.empty]
            if unknown:
                raise ValueError(f"No {name} price history for: {', '.join(sorted(unknown))}. "
                                 "Check the tickers (for example BRK-B rather than BRK/B).")
            fresh = dict(fetched)
            self.cache.put_many(name, fresh)
            out.update(fresh)
        return out

    # --- MarketData interface --------------------------------------------------------

    def returns(self, holdings: pd.DataFrame) -> pd.DataFrame:
        """Monthly returns for every holding over the longest window they all share
        (at most `n_periods` months), on the same months as the market index.

        Cash has no price to fetch: it is modelled as a near-constant return, as in the
        synthetic model (a column with zero variance would break the correlations).
        """
        tickers = list(holdings["ticker"])
        classes = (list(holdings["asset_class"]) if "asset_class" in holdings
                   else ["equity"] * len(tickers))
        priced = [t for t, c in zip(tickers, classes) if c != "cash"]
        closes = self.monthly_closes(sorted(set(priced) | {self.index_symbol}))
        frame = pd.DataFrame(closes).dropna()  # months where every series has a price
        frame = frame.iloc[-(self.n_periods + 1):]
        rets = frame.pct_change().iloc[1:]
        if len(rets) < MIN_PERIODS:
            short = sorted(s for s in priced
                           if len(closes[s].dropna()) < MIN_PERIODS + 1)
            raise ValueError(
                f"Only {len(rets)} months of price history are shared by every holding; at "
                f"least {MIN_PERIODS} are needed. Shortest histories: "
                f"{', '.join(short) or 'the market index'}.")
        out = {}
        for t, c in zip(tickers, classes):
            if c == "cash":
                out[t] = CASH_RETURN + _rng("ticker", t, 0).normal(0.0, CASH_NOISE, len(rets))
            else:
                out[t] = rets[t].to_numpy()
        return pd.DataFrame(out, index=rets.index)

    def market(self, returns: pd.DataFrame) -> pd.Series:
        """The index's monthly returns on the same months as `returns`."""
        closes = self.monthly_closes([self.index_symbol])[self.index_symbol]
        rets = closes.pct_change()
        return rets.reindex(returns.index).astype(float)


def as_of(returns: pd.DataFrame) -> str | None:
    """The last month of history used, for dated returns; None for synthetic ones."""
    idx = returns.index
    if isinstance(idx, pd.DatetimeIndex) and len(idx):
        return idx[-1].date().isoformat()
    return None


__all__ = ["DataSourceError", "MIN_PERIODS", "N_PERIODS", "PriceHistoryData", "PriceSource",
           "SymbolNotFound", "as_of", "month_end_closes"]
