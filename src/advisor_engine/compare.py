"""Before/after comparison of two risk profiles (for what-if and portfolio compare).

Every figure is read from the two engine outputs; the only arithmetic is the
difference after - before, done here so no caller (UI or model) has to compute it.

Loss figures (kind "loss") are compared as sizes: the profile stores them as negative
returns, which would make a smaller loss read as a positive change. Here a loss of 9.3%
after 9.6% is a change of -0.3%: the loss shrank.
"""

from __future__ import annotations

# (key, label, how to read it) for the headline figures; paths into RiskProfile.to_dict().
HEADLINE = [
    (("concentration", "effective_bets"), "Effective independent bets", "number"),
    (("concentration", "top_component_share"), "Largest single source of risk", "fraction"),
    (("concentration", "market_adjusted", "residual_effective_bets"),
     "Independent bets after removing the market", "number"),
    (("concentration", "sector_level", "sector_effective_bets"), "Independent sector bets", "number"),
    (("drawdown", "period_volatility"), "Volatility per period", "fraction"),
    (("drawdown", "stress_loss_1_in_100"), "1-in-100 stress loss", "loss"),
    (("drawdown", "historical_max_drawdown"), "Worst historical drop", "loss"),
    (("concentration", "n_positions"), "Positions", "count"),
]


def _get(d: dict, path: tuple):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
        if d is None:
            return None
    return d


def compare_profiles(before: dict, after: dict) -> dict:
    """Headline, sector and position differences between two `RiskProfile.to_dict()`s."""
    headline = []
    for path, label, kind in HEADLINE:
        b, a = _get(before, path), _get(after, path)
        if b is None or a is None:
            # The engine withheld this figure for one side (e.g. sector-level bets when
            # some holdings have no sector), so there is no difference to report.
            continue
        b, a = float(b), float(a)
        if kind == "loss":
            b, a = abs(b), abs(a)
        headline.append({"key": path[-1], "label": label, "kind": kind,
                         "before": b, "after": a, "change": a - b})

    sectors = []
    for s in sorted(set(before["sector_exposure"]) | set(after["sector_exposure"])):
        b = float(before["sector_exposure"].get(s, 0.0))
        a = float(after["sector_exposure"].get(s, 0.0))
        sectors.append({"sector": s, "before": b, "after": a, "change": a - b})
    sectors.sort(key=lambda r: -max(r["before"], r["after"]))

    tickers_b = {p["ticker"] for p in before["positions"]}
    tickers_a = {p["ticker"] for p in after["positions"]}
    return {
        "headline": headline,
        "sectors": sectors,
        "added": sorted(tickers_a - tickers_b),
        "removed": sorted(tickers_b - tickers_a),
        "period_label": after["drawdown"]["period_label"],
    }
