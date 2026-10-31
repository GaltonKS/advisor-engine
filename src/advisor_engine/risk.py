"""Risk profile: concentration, sector exposure, drawdown sensitivity.

Every function returns plain numbers or small dicts. The LLM layer will call
`risk_profile` and explain the output; it never computes figures itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from . import __version__
from .holdings import UNCLASSIFIED


def shrunk_covariance(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Covariance with Ledoit-Wolf shrinkage applied to the correlations.

    Returns (covariance, shrinkage intensity). Each series is standardised, Ledoit-Wolf
    shrinks the resulting correlation matrix toward the identity, and the result is
    rescaled by the sample volatilities: cov = D R_shrunk D.

    Shrinking stabilises the estimate when the number of periods is small relative to
    the number of holdings. Shrinking the covariance itself (target: average variance
    times the identity) would also pull every volatility toward the average, so a
    near-riskless holding such as cash would pick up a volatility it doesn't have.
    Here only correlations are shrunk; each holding keeps its own sample volatility.
    """
    x = returns.to_numpy(dtype=float)
    vol = x.std(axis=0)
    # A constant series has no volatility to standardise by; it is then uncorrelated
    # with everything and contributes no variance.
    safe = np.where(vol > 0, vol, 1.0)
    lw = LedoitWolf().fit((x - x.mean(axis=0)) / safe)
    std_cov = lw.covariance_
    d = np.sqrt(np.diag(std_cov))
    corr = std_cov / np.outer(d, d)
    return corr * np.outer(vol, vol), float(lw.shrinkage_)


def effective_number_of_bets(weights: np.ndarray, cov: np.ndarray) -> dict:
    """Effective independent bets via PCA (Meucci's entropy measure).

    Decompose the covariance into principal components. The portfolio's
    exposure to component i is e_i' w, and its variance contribution is
    (e_i' w)^2 * lambda_i. Normalise to p_i and take exp(entropy(p)).
    A value near 1 means one dominant bet; near N means N independent bets.
    """
    eigvals, eigvecs = np.linalg.eigh(cov)
    # eigh returns ascending eigenvalues; sort so component 0 is the largest.
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]

    # Project weights onto the principal components, then weight each squared
    # exposure by its eigenvalue to get that component's share of variance.
    exposures = eigvecs.T @ weights
    contrib = (exposures**2) * eigvals
    p = contrib / contrib.sum()
    # Drop ~zero shares so log(0) doesn't produce NaN in the entropy.
    p_pos = p[p > 1e-12]
    enb = float(np.exp(-np.sum(p_pos * np.log(p_pos))))

    return {
        "effective_bets": enb,
        "n_positions": int(len(weights)),
        "top_component_share": float(p[0]),
        "component_shares": [float(x) for x in p[:5]],
    }


def market_adjusted_concentration(
    returns: pd.DataFrame,
    weights: np.ndarray,
    market: pd.Series | None = None,
) -> dict:
    """Split portfolio risk into a market component and a residual component.

    Each asset is regressed (OLS) on a market factor: r_i = alpha_i + beta_i * m + e_i.
    The portfolio's systematic variance is (w . beta)^2 * var(m); its residual
    variance is w' Sigma_res w, with Sigma_res the Ledoit-Wolf shrunk covariance
    of the residuals e. `residual_effective_bets` is the same entropy measure as
    `effective_number_of_bets`, applied to Sigma_res, so it says how many
    independent bets remain once the market is taken out.

    The market factor is the equal-weighted mean return of the supplied tickers.
    That is a stand-in for when no index series is available: it contains the
    assets themselves, and the residuals then sum to zero across assets, so an
    equal-weighted portfolio has almost no residual risk by construction. With
    real data, pass a broad index series as `market` (indexed like `returns`).

    `weights` must be in the same order as `returns.columns`.
    """
    if market is None:
        m = returns.mean(axis=1)
        proxy = "equal_weight_universe"
    else:
        m = market.reindex(returns.index)
        if m.isna().any():
            raise ValueError("Market series does not cover every return period.")
        proxy = "provided"

    R = returns.to_numpy()
    mv = m.to_numpy()
    # Population variance of the market factor (divide by n, matching the betas below).
    m_dev = mv - mv.mean()
    market_var = float(m_dev @ m_dev / len(m_dev))
    if market_var <= 0:
        raise ValueError("Market series has zero variance.")

    # OLS slope per asset: cov(r_i, m) / var(m). The intercept is absorbed by
    # demeaning, so the residuals have zero mean.
    r_dev = R - R.mean(axis=0)
    betas = (r_dev.T @ m_dev) / len(m_dev) / market_var
    resid = r_dev - np.outer(m_dev, betas)

    # Systematic risk is driven by the portfolio's net market beta; residual
    # risk is what the market factor cannot explain.
    port_beta = float(weights @ betas)
    systematic = port_beta**2 * market_var
    cov_res, _ = shrunk_covariance(pd.DataFrame(resid))
    residual = float(weights @ cov_res @ weights)

    return {
        "portfolio_beta": port_beta,
        "market_share_of_risk": float(systematic / (systematic + residual)),
        "residual_effective_bets": effective_number_of_bets(weights, cov_res)["effective_bets"],
        "market_proxy": proxy,
    }


def sector_level_concentration(holdings: pd.DataFrame, returns: pd.DataFrame) -> dict:
    """Effective bets across sectors rather than individual positions.

    Each sector's return series is the within-sector-weighted average of its
    holdings' returns (a one-holding sector is just that holding). The sector
    weights are the summed position weights.

    If any holding has no sector, the figure is withheld (`sector_effective_bets` and
    `top_component_share` are removed and `withheld` says why). Unclassified holdings
    are not one sector, and leaving them out would describe a different portfolio.
    `n_sectors` counts classified sectors only.
    """
    w = holdings["weight"].to_numpy()
    sectors = sorted(s for s in holdings["sector"].unique() if s != UNCLASSIFIED)
    if (holdings["sector"] == UNCLASSIFIED).any():
        return {"n_sectors": len(sectors),
                "withheld": "Some holdings have no sector, so sector-level bets are not computed."}
    series = {}
    sector_w = []
    for s in sectors:
        mask = (holdings["sector"] == s).to_numpy()
        # Sector weight, which also renormalises the within-sector weights to sum to 1.
        sw = w[mask].sum()
        series[s] =returns[list(holdings.loc[mask, "ticker"])].to_numpy() @ (w[mask] / sw)
        sector_w.append(sw)

    sector_returns = pd.DataFrame(series, index=returns.index)
    cov, _ = shrunk_covariance(sector_returns)
    out = effective_number_of_bets(np.array(sector_w), cov)
    return {
        "sector_effective_bets": out["effective_bets"],
        "n_sectors": len(sectors),
        "top_component_share": out["top_component_share"],
    }


def position_risk(holdings: pd.DataFrame, cov: np.ndarray, corr_floor: float = 1e-12) -> list[dict]:
    """Per-position facts that back a "why is this position here / why does it matter".

    - risk_share: fraction of portfolio variance contributed by the position
      (w_i * (Cov w)_i / port_var). Sums to 1.
    - risk_to_weight: risk_share / weight. Above 1 means the position adds more
      risk than its size suggests.
    - avg_corr_to_rest: weighted average correlation with the other holdings.
    """
    w = holdings["weight"].to_numpy()
    port_var = float(w @ cov @ w)
    risk_share = w * (cov @ w) / port_var

    # Convert covariance to correlation: corr_ij = cov_ij / (vol_i * vol_j).
    vol = np.sqrt(np.diag(cov))
    corr = cov / np.outer(vol, vol)
    out = []
    for i, row in holdings.iterrows():
        # Mask out the position itself; its self-correlation of 1 would bias the average.
        others = np.arange(len(w)) != i
        wo = w[others]
        # corr_floor guards against dividing by zero when there are no other holdings.
        avg_corr = float(np.sum(wo * corr[i, others]) / max(wo.sum(), corr_floor))
        out.append(
            {
                "ticker": row["ticker"],
                "sector": row["sector"],
                "asset_class": row.get("asset_class", "equity"),
                "weight": float(w[i]),
                "risk_share": float(risk_share[i]),
                "risk_to_weight": float(risk_share[i] / w[i]) if w[i] > 0 else 0.0,
                "volatility": float(vol[i]),
                "avg_corr_to_rest": avg_corr,
            }
        )
    # Biggest risk contributors first, so callers can slice the top N.
    return sorted(out, key=lambda d: d["risk_share"], reverse=True)


def sector_exposure(holdings: pd.DataFrame) -> dict[str, float]:
    """Portfolio weight by sector, largest first; holdings with no sector come last."""
    s = holdings.groupby("sector")["weight"].sum().sort_values(ascending=False)
    out = {k: float(v) for k, v in s.items() if k != UNCLASSIFIED}
    if UNCLASSIFIED in s.index:
        out[UNCLASSIFIED] = float(s[UNCLASSIFIED])
    return out


def asset_class_exposure(holdings: pd.DataFrame) -> dict[str, float]:
    """Portfolio weight by asset class (see holdings.ASSET_CLASSES), largest first.

    A holdings frame without the column is all equity, as the return model treats it.
    """
    classes = holdings["asset_class"] if "asset_class" in holdings else pd.Series(
        "equity", index=holdings.index)
    s = holdings["weight"].groupby(classes).sum().sort_values(ascending=False)
    return {k: float(v) for k, v in s.items()}


def sector_coverage(holdings: pd.DataFrame) -> dict:
    """How much of the portfolio has no sector, so callers can say what is incomplete."""
    mask = holdings["sector"] == UNCLASSIFIED
    return {
        "unclassified_weight": float(holdings.loc[mask, "weight"].sum()),
        "n_unclassified": int(mask.sum()),
        "unclassified_tickers": list(holdings.loc[mask, "ticker"]),
    }


def max_drawdown(portfolio_returns: pd.Series) -> float:
    """Worst peak-to-trough loss of the compounded return series (negative number)."""
    # Growth of 1 unit, tracked against its running high-water mark.
    wealth = (1.0 + portfolio_returns).cumprod()
    peak = wealth.cummax()
    return float((wealth / peak - 1.0).min())


def drawdown_sensitivity(
    weights: np.ndarray,
    cov: np.ndarray,
    portfolio_returns: pd.Series,
    z: float = 2.33,
    period_label: str = "monthly",
) -> dict:
    """Two views of downside.

    - historical_max_drawdown: worst drawdown in the supplied return history.
    - stress_loss_1_in_100: parametric one-period loss at ~99% confidence
      using the shrunk covariance (z=2.33), ignoring the mean.

    `period_label` names the length of one return period ("monthly" by default).
    Every figure here is per that period, so it travels with them.
    """
    # Portfolio volatility: sqrt(w' Cov w), in the same period units as the returns.
    port_vol = float(np.sqrt(weights @ cov @ weights))
    return {
        "historical_max_drawdown": max_drawdown(portfolio_returns),
        "period_volatility": port_vol,
        "stress_loss_1_in_100": -z * port_vol,
        "period_label": period_label,
    }


@dataclass
class RiskProfile:
    """Container for everything `risk_profile` computes; `to_dict` gives the LLM-facing payload."""

    engine_version: str
    n_periods: int
    shrinkage: float
    concentration: dict
    sector_exposure: dict
    drawdown: dict
    largest_positions: list = field(default_factory=list)
    positions: list = field(default_factory=list)
    sector_coverage: dict = field(default_factory=dict)
    asset_class_exposure: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def risk_profile(
    holdings: pd.DataFrame,
    returns: pd.DataFrame,
    market: pd.Series | None = None,
    period_label: str = "monthly",
) -> RiskProfile:
    """Build the risk profile for a validated holdings frame and a returns frame.

    `returns` columns must cover every holdings ticker. `market` is an optional
    broad-index return series used for the market-adjusted concentration; when
    omitted, an equal-weighted mean of the holdings is used instead.
    `period_label` is the length of one return period, reported with the drawdown figures.
    """
    tickers = list(holdings["ticker"])
    missing = [t for t in tickers if t not in returns.columns]
    if missing:
        raise ValueError(f"No return history for: {missing}")

    # Keep only periods where every holding has a return, so the covariance
    # and the portfolio return series are built on the same sample.
    r = returns[tickers].dropna()
    # Too few observations makes the covariance estimate unreliable.
    if len(r) < 24:
        raise ValueError("Need at least 24 periods of returns.")

    weights = holdings["weight"].to_numpy()
    cov, shrink = shrunk_covariance(r)

    # Period-by-period portfolio return assuming constant weights.
    port_ret = pd.Series(r.to_numpy() @ weights, index=r.index)

    # Existing concentration keys are kept as-is; the new views nest alongside them.
    concentration = effective_number_of_bets(weights, cov)
    adjusted = market_adjusted_concentration(r, weights, market)
    if adjusted["market_proxy"] == "equal_weight_universe":
        # With the equal-weight stand-in the "market" is made of the holdings themselves,
        # so beta and market share of risk are artifacts of the construction and would
        # mislead. Only report them against a real market series.
        del adjusted["portfolio_beta"], adjusted["market_share_of_risk"]
    concentration["market_adjusted"] = adjusted
    concentration["sector_level"] = sector_level_concentration(holdings, r)

    # Five biggest positions by weight (not by risk; see `positions` for risk ranking).
    top = holdings.nlargest(5, "weight")[["ticker", "weight", "sector"]]
    return RiskProfile(
        engine_version=__version__,
        n_periods=int(len(r)),
        shrinkage=shrink,
        concentration=concentration,
        sector_exposure=sector_exposure(holdings),
        drawdown=drawdown_sensitivity(weights, cov, port_ret, period_label=period_label),
        largest_positions=top.to_dict("records"),
        positions=position_risk(holdings, cov),
        sector_coverage=sector_coverage(holdings),
        asset_class_exposure=asset_class_exposure(holdings),
    )