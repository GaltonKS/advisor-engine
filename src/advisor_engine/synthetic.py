"""Synthetic return generator.

Lets the prototype run end to end without licensed market data. Returns come
from a simple market + sector factor model, so sectors are correlated within
themselves. Replace with real data once licensing is confirmed.

Each series is drawn from its own random stream, seeded from its name (the market,
a sector, a ticker), so a ticker's history is the same in every portfolio it appears
in. That matters for what-if and compare: removing one position must not change the
history of the others, or the before/after difference would be noise.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .holdings import UNCLASSIFIED


def _rng(*parts: object) -> np.random.Generator:
    """A random stream fixed by `parts`. Uses SHA-256, not hash(), which varies by process."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


# How each asset class loads on the shared factors, in the same monthly units as the
# defaults below. Illustrative choices, not estimates: they give bonds and cash a
# plausible shape (low volatility, little stock-market exposure) until real data
# replaces this module. "other" is an unrecognised label, so it is modelled as equity.
#   beta: range of stock-market loadings; rates: range of loadings on a shared
#   interest-rate factor; sector_scale: multiplier on the sector factor; idio_vol.
ASSET_CLASS_MODEL = {
    "preferred": {"beta": (0.3, 0.5), "rates": (0.5, 0.8), "sector_scale": 0.5, "idio_vol": 0.025},
    "fixed_income": {"beta": (-0.05, 0.1), "rates": (0.6, 1.2), "sector_scale": 0.3, "idio_vol": 0.008},
}
CASH_RETURN = 0.003  # per period; a money-market-like yield
CASH_NOISE = 0.0003  # tiny, so the series is not constant (covariance needs variance)
RATES_VOL = 0.012


def synthetic_market(n_periods: int = 120, seed: int = 0, market_vol: float = 0.045) -> pd.Series:
    """The stock-market factor every synthetic equity loads on, as a return series.

    In the synthetic world this is the market index: pass it to `risk_profile` as
    `market` so market sensitivity is measured against the factor that drives the
    returns. Same draws as inside `synthetic_returns`, so the two always agree.
    """
    return pd.Series(_rng("market", seed).normal(0.006, market_vol, n_periods))


def synthetic_returns(
    tickers: list[str],
    sectors: list[str],
    n_periods: int = 120,
    seed: int = 0,
    market_vol: float = 0.045,
    sector_vol: float = 0.03,
    idio_vol: float = 0.05,
    asset_classes: list[str] | None = None,
) -> pd.DataFrame:
    """Monthly-style returns from a factor model: market + sector (+ rates) + noise.

    Each row is one period; treat it as monthly, which matches the default
    `period_label` of `risk_profile`. Pass a different label there if the data differs.
    A ticker's series depends only on (ticker, sector, asset class, seed, n_periods
    and the vols), never on which other tickers are requested.

    Asset classes (default: all equity, the original model, so equity series are
    unchanged by this option):
    - equity / other: beta 0.7-1.3 to the market, the full sector factor, `idio_vol`.
    - preferred, fixed_income: see `ASSET_CLASS_MODEL`; they also load on a shared
      interest-rate factor, so bonds move together rather than with stocks.
    - cash: a near-constant `CASH_RETURN`, no market, sector or rates exposure.

    Holdings with no sector (`UNCLASSIFIED`) get no sector factor: they are not one
    sector, so giving them a shared factor would invent correlation between them.
    """
    classes = asset_classes or ["equity"] * len(tickers)
    market = synthetic_market(n_periods, seed, market_vol).to_numpy()
    rates = _rng("rates", seed).normal(0.0035, RATES_VOL, n_periods)
    sector_f = {s: _rng("sector", s, seed).normal(0.0, sector_vol, n_periods)
                for s in sorted(set(sectors)) if s != UNCLASSIFIED}
    sector_f[UNCLASSIFIED] = np.zeros(n_periods)

    cols = {}
    for t, s, cls in zip(tickers, sectors, classes):
        rng = _rng("ticker", t, seed)
        if cls == "cash":
            cols[t] = CASH_RETURN + rng.normal(0.0, CASH_NOISE, n_periods)
        elif cls in ASSET_CLASS_MODEL:
            m = ASSET_CLASS_MODEL[cls]
            beta = rng.uniform(*m["beta"])
            dur = rng.uniform(*m["rates"])
            idio = rng.normal(0.0, m["idio_vol"], n_periods)
            cols[t] = beta * market + dur * rates + m["sector_scale"] * sector_f[s] + idio
        else:
            # Same draws, in the same order, as before asset classes existed.
            beta = rng.uniform(0.7, 1.3)
            idio = rng.normal(0.0, idio_vol, n_periods)
            cols[t] = beta * market + sector_f[s] + idio
    return pd.DataFrame(cols)
