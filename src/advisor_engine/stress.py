"""Stress scenarios: an instant shock per sector and asset class, applied by weight.

Method: each holding's value moves by one shock, chosen in this order:
1. its asset class's shock, if the scenario gives one (fixed_income, preferred, cash);
2. cash that the scenario doesn't name holds its value (shock 0);
3. its sector's shock, if the scenario names the sector;
4. otherwise the scenario default.
The portfolio's move is the weight-sum of those shocks, so the result is exact for the
stated shocks: there is no model or history behind it.

Limits, which the result carries in `basis`:
- The presets are illustrative shapes (a broad fall, a sector sell-off, rising rates,
  a recession), not calibrated to any historical episode. Review them before using the
  figures with clients.
- Every holding in a sector (or asset class) moves by the same amount; stock-specific
  moves, bond duration and credit differences, gaps and correlations beyond the
  stated shocks are ignored.
"""

from __future__ import annotations

import pandas as pd

BASIS = ("Illustrative preset shocks per sector and asset class, not calibrated to a "
         "historical period.")

# Asset classes a scenario may shock directly. Equity (and "other") always moves by
# sector or the default, so the sector presets keep their meaning.
SHOCKABLE_CLASSES = ("fixed_income", "preferred", "cash")

SCENARIOS: dict[str, dict] = {
    "broad_selloff": {
        "label": "Broad market sell-off",
        "description": "Stocks fall 20%; preferreds fall 10%; bonds rise 2% as investors "
                       "move to safety; cash holds its value.",
        "default": -0.20,
        "sectors": {},
        "asset_classes": {"fixed_income": 0.02, "preferred": -0.10},
    },
    "tech_selloff": {
        "label": "Technology sell-off",
        "description": "Technology falls 30% and Communication Services 20%; other sectors "
                       "fall 5%; preferreds fall 2%; bonds and cash hold their value.",
        "default": -0.05,
        "sectors": {"Technology": -0.30, "Communication Services": -0.20},
        "asset_classes": {"fixed_income": 0.0, "preferred": -0.02},
    },
    "rising_rates": {
        "label": "Rising rates",
        "description": "Rate-sensitive sectors fall most: Real Estate and Utilities 15%, "
                       "Technology 12%; Financials fall 2%; other sectors fall 6%; bonds "
                       "fall 6% and preferreds 10%; cash holds its value.",
        "default": -0.06,
        "sectors": {"Real Estate": -0.15, "Utilities": -0.15, "Technology": -0.12,
                    "Financials": -0.02},
        "asset_classes": {"fixed_income": -0.06, "preferred": -0.10},
    },
    "recession": {
        "label": "Recession",
        "description": "Cyclical sectors fall 25%; defensive sectors (Consumer Staples, "
                       "Health Care, Utilities) fall 8%; other sectors fall 15%; bonds rise "
                       "3%; preferreds fall 10%; cash holds its value.",
        "default": -0.15,
        "sectors": {"Consumer Discretionary": -0.25, "Industrials": -0.25, "Energy": -0.25,
                    "Financials": -0.25, "Materials": -0.25, "Consumer Staples": -0.08,
                    "Health Care": -0.08, "Utilities": -0.08},
        "asset_classes": {"fixed_income": 0.03, "preferred": -0.10},
    },
}


def scenario_list() -> list[dict]:
    """The presets as data, for the API and UI to show what each one assumes."""
    return [{"id": k, **v} for k, v in SCENARIOS.items()]


def stress_test(holdings: pd.DataFrame, sector_shocks: dict[str, float],
                default_shock: float, asset_class_shocks: dict[str, float] | None = None) -> dict:
    """Apply shocks (fractions, e.g. -0.3) to a validated holdings frame.

    See the module docstring for which shock each holding takes. Returns the
    portfolio's return and value change, plus the same per sector, per asset class and
    per position (largest loss first). A sector's or asset class's `shock` is the
    weighted average of its holdings' shocks. Shocks must be between -100% and +100%.
    """
    shocks = {str(k): float(v) for k, v in sector_shocks.items()}
    class_shocks = {str(k): float(v) for k, v in (asset_class_shocks or {}).items()}
    unknown = sorted(set(class_shocks) - set(SHOCKABLE_CLASSES))
    if unknown:
        raise ValueError(f"Asset class shocks apply to {list(SHOCKABLE_CLASSES)}, not {unknown}.")
    for v in [*shocks.values(), *class_shocks.values(), default_shock]:
        if not -1.0 <= v <= 1.0:
            raise ValueError("Shocks must be between -100% and +100%.")

    h = holdings.copy()
    if "asset_class" not in h:
        h["asset_class"] = "equity"
    by_sector_shock = h["sector"].map(shocks).fillna(float(default_shock))
    by_class_shock = h["asset_class"].map({"cash": 0.0, **class_shocks})
    h["shock"] = by_class_shock.fillna(by_sector_shock)
    h["contribution"] = h["weight"] * h["shock"]  # share of the portfolio's move
    h["value_change"] = h["value"] * h["shock"]

    def groups(col: str) -> list[dict]:
        g = h.groupby(col).agg(weight=("weight", "sum"), contribution=("contribution", "sum"))
        g["shock"] = g["contribution"] / g["weight"]
        return [{col: k, **{c: float(v) for c, v in row.items()}}
                for k, row in g.sort_values("contribution").iterrows()]

    positions = h.sort_values("contribution")[
        ["ticker", "sector", "asset_class", "weight", "shock", "contribution", "value_change"]]
    return {
        "portfolio_return": float(h["contribution"].sum()),
        "portfolio_value": float(h["value"].sum()),
        "value_change": float(h["value_change"].sum()),
        "default_shock": float(default_shock),
        "asset_class_shocks": class_shocks,
        "sectors": groups("sector"),
        "asset_classes": groups("asset_class"),
        "positions": [{k: (float(v) if k not in ("ticker", "sector", "asset_class") else v)
                       for k, v in row.items()} for row in positions.to_dict("records")],
        "basis": BASIS,
    }


def run_scenario(holdings: pd.DataFrame, scenario_id: str) -> dict:
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    sc = SCENARIOS[scenario_id]
    return {"scenario": {"id": scenario_id, **sc},
            **stress_test(holdings, sc["sectors"], sc["default"], sc.get("asset_classes"))}
