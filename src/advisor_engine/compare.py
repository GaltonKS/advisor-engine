"""Before/after comparison of two risk profiles (for what-if and portfolio compare).

Every figure is read from the two engine outputs; the only arithmetic is the
difference after - before, done here so no caller (UI or model) has to compute it.

Loss figures (kind "loss") are compared as sizes: the profile stores them as negative
returns, which would make a smaller loss read as a positive change. Here a loss of 9.3%
after 9.6% is a change of -0.3%: the loss shrank.
"""

from __future__ import annotations

import copy

from .business_config import BRIDGE_MIN_STEP
from .levers import norm_idea
from .model import evaluate, term_of

# (key, label, how to read it) for the headline figures; paths into RiskProfile.to_dict().
HEADLINE = [
    (("concentration", "effective_bets"), "Effective independent bets", "number"),
    (("concentration", "top_component_share"), "Largest single source of risk", "fraction"),
    (("concentration", "market_adjusted", "residual_effective_bets"),
     "Independent bets after removing the market", "number"),
    (("concentration", "sector_level", "sector_effective_bets"), "Independent sector bets", "number"),
    (("drawdown", "period_volatility"), "Volatility per period", "fraction"),
    (("drawdown", "stress_loss_1_in_100"), "1-in-100 stress loss", "loss"),
    (("drawdown", "historical_max_drawdown"), "Largest fall from a peak", "loss"),
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


# --- small business: what moves the bottom line from today's setup to an idea --------

def bridge(M: dict, today: dict | None, idea: dict | None) -> dict:
    """The change in the monthly bottom line (`profit`) from `today` to `idea`, one group
    of changes at a time (the FIS skeleton's `bridge`).

    The groups are applied in a fixed order, each on top of the ones before: sales volume,
    prices (with the price response), units in or out (added units, removals, and how much
    of a removed unit's sales stays), unit pay, cost rates, monthly bills. Each step's
    `delta` is the change in profit it adds; steps under `BRIDGE_MIN_STEP` dollars are
    left out. `from + sum(deltas) == to` within that rounding, and `to` is the idea's profit.

    Caveat: with interacting changes the split depends on the order (a price rise counted
    before a removal is worth more than after it). The order is fixed so the same idea
    always gives the same steps.
    """
    today = norm_idea(M, today)
    idea = norm_idea(M, idea)
    p = copy.deepcopy(today)
    steps: list[dict] = []
    prev = evaluate(M, p)["profit"]

    def step(label, fn):
        nonlocal prev
        fn(p)
        now = evaluate(M, p)["profit"]
        if abs(now - prev) >= BRIDGE_MIN_STEP:
            steps.append({"label": label, "delta": now - prev})
        prev = now

    def volume(x):
        x["shock"] = idea.get("shock")
        x["offer"] = copy.deepcopy(idea.get("offer") or {})

    def prices(x):
        x["price"] = copy.deepcopy(idea.get("price") or {})
        x["elast"] = idea.get("elast")

    def units(x):
        x["added"] = copy.deepcopy(idea.get("added") or [])
        x["keep"] = idea.get("keep")
        names = dict.fromkeys(list((idea.get("terms") or {})) + list((today.get("terms") or {})))
        for n in names:
            ti, tt = term_of(M, idea, n), term_of(M, x, n)
            if (ti.get("on") is not False) != (tt.get("on") is not False):
                x["terms"][n] = {**(x["terms"].get(n) or {}), "on": ti.get("on") is not False}

    V = M["V"]
    step("Sales volume", volume)
    step("Prices", prices)
    step(V["Units"] + " in or out", units)
    step(V["Unit"] + " pay" if V["people"] else V["Unit"] + " terms",
         lambda x: x.__setitem__("terms", copy.deepcopy(idea.get("terms") or {})))
    step("Cost rates", lambda x: x.__setitem__("rate", copy.deepcopy(idea.get("rate") or {})))
    step("Monthly bills", lambda x: x.__setitem__("fixed", copy.deepcopy(idea.get("fixed") or [])))
    return {"from": evaluate(M, today)["profit"], "to": prev, "steps": steps}
