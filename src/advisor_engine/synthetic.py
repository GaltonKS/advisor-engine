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


def synthetic_returns(
    tickers: list[str],
    sectors: list[str],
    n_periods: int = 120,
    seed: int = 0,
    market_vol: float = 0.045,
    sector_vol: float = 0.03,
    idio_vol: float = 0.05,
) -> pd.DataFrame:
    """Monthly-style returns: market factor + sector factor + idiosyncratic noise.

    Each row is one period; treat it as monthly, which matches the default
    `period_label` of `risk_profile`. Pass a different label there if the data differs.
    A ticker's series depends only on (ticker, sector, seed, n_periods and the vols),
    never on which other tickers are requested.

    Holdings with no sector (`UNCLASSIFIED`) get no sector factor: they are not one
    sector, so giving them a shared factor would invent correlation between them.
    """
    market = _rng("market", seed).normal(0.006, market_vol, n_periods)
    sector_f = {s: _rng("sector", s, seed).normal(0.0, sector_vol, n_periods)
                for s in sorted(set(sectors)) if s != UNCLASSIFIED}
    sector_f[UNCLASSIFIED] = np.zeros(n_periods)

    cols = {}
    for t, s in zip(tickers, sectors):
        rng = _rng("ticker", t, seed)
        beta = rng.uniform(0.7, 1.3)
        idio = rng.normal(0.0, idio_vol, n_periods)
        cols[t] = beta * market + sector_f[s] + idio
    return pd.DataFrame(cols)
