"""Tool definitions and handlers the model can call.

Every tool returns the engine's own output, serialised as JSON; the model never sees a
way to compute. Tools:
- get_risk_profile: the full risk profile of a holdings file.
- get_holdings: the holdings as loaded (shares, price, value, weight, sector, class).
- run_what_if: the profile after removing, resizing or adding positions, with the
  engine's before/after comparison (compare.compare_profiles).
- run_stress_test: a preset or custom shock scenario (stress.py).
Every file path goes through `resolve_holdings_path`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..compare import compare_profiles
from ..holdings import COLUMNS, load_holdings, validate_holdings
from ..risk import risk_profile
from ..stress import SCENARIOS, run_scenario, stress_test
from ..synthetic import synthetic_market, synthetic_returns
from ._paths import DATA_DIR, REPO_ROOT

_PATH = {"type": "string",
         "description": "Path to a holdings CSV (columns: ticker, shares, price, optional sector "
                        "and asset class)."}

GET_RISK_PROFILE = {
    "name": "get_risk_profile",
    "description": (
        "Compute the engine's risk profile for a holdings CSV: effective independent bets, "
        "market-adjusted and sector-level concentration, sector and asset-class exposure, "
        "risk share by sector and asset class, drawdown sensitivity, market sensitivity "
        "(beta and correlation to the market, per position and for the portfolio), and "
        "per-position risk facts. The file must be inside the data/ directory."
    ),
    "input_schema": {"type": "object", "properties": {"holdings_path": _PATH},
                     "required": ["holdings_path"]},
}

GET_HOLDINGS = {
    "name": "get_holdings",
    "description": (
        "The holdings as the engine loaded them: ticker, shares, price, value, weight, "
        "sector and asset class per position, plus the total value. Use it for dollar "
        "amounts and share counts."
    ),
    "input_schema": {"type": "object", "properties": {"holdings_path": _PATH},
                     "required": ["holdings_path"]},
}

RUN_WHAT_IF = {
    "name": "run_what_if",
    "description": (
        "Re-run the risk profile with changed holdings and return the engine's before/after "
        "comparison (headline figures, sectors, positions added/removed) and the full "
        "profile after the change. Use it for questions like 'what if we sold X', "
        "'without these positions', 'if we halved Y'. Only add a position or set a share "
        "count with figures the adviser gave you; never make one up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "holdings_path": _PATH,
            "remove": {"type": "array", "items": {"type": "string"},
                       "description": "Tickers to remove entirely."},
            "set_shares": {"type": "object", "additionalProperties": {"type": "number"},
                           "description": "New share count per existing ticker (0 removes it)."},
            "add": {
                "type": "array",
                "description": "New positions, with figures supplied by the adviser.",
                "items": {"type": "object",
                          "properties": {"ticker": {"type": "string"}, "shares": {"type": "number"},
                                         "price": {"type": "number"}, "sector": {"type": "string"},
                                         "asset_class": {"type": "string"}},
                          "required": ["ticker", "shares", "price"]},
            },
        },
        "required": ["holdings_path"],
    },
}

RUN_STRESS_TEST = {
    "name": "run_stress_test",
    "description": (
        "Apply an instant shock scenario to the holdings and return the portfolio's move, "
        "the change in value, and the effect by sector, asset class and position. Use a "
        "preset scenario_id, or give custom shocks (fractions, e.g. -0.2 for a 20% fall). "
        "Presets (illustrative, not calibrated): "
        + "; ".join(f"{k}: {v['description']}" for k, v in SCENARIOS.items())
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "holdings_path": _PATH,
            "scenario_id": {"type": "string", "enum": list(SCENARIOS)},
            "sector_shocks": {"type": "object", "additionalProperties": {"type": "number"},
                              "description": "Custom: shock per sector name."},
            "default_shock": {"type": "number",
                              "description": "Custom: shock for sectors not named."},
            "asset_class_shocks": {"type": "object", "additionalProperties": {"type": "number"},
                                   "description": "Custom: shocks for fixed_income, preferred, cash."},
        },
        "required": ["holdings_path"],
    },
}

TOOLS = [GET_RISK_PROFILE, GET_HOLDINGS, RUN_WHAT_IF, RUN_STRESS_TEST]

# Labels where the returns came from, so the model can say so in its answer.
RETURNS_SOURCE = "synthetic"
# Names the market series `get_market_returns` supplies (reported as market_proxy).
MARKET_SOURCE = "synthetic market factor"


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    holdings_sha256: str | None = None


def get_returns(holdings: pd.DataFrame) -> pd.DataFrame:
    """Return history for every holdings ticker. One of the two places real data plugs in.

    TODO: replace with real price data once licensing is confirmed, and update
    RETURNS_SOURCE to match. Contract: columns cover every `holdings["ticker"]`.
    """
    classes = list(holdings["asset_class"]) if "asset_class" in holdings else None
    return synthetic_returns(list(holdings["ticker"]), list(holdings["sector"]),
                             asset_classes=classes)


def get_market_returns(returns: pd.DataFrame) -> pd.Series:
    """A market index return series aligned with `returns`. The other place real data
    plugs in: with licensed data, return a broad index here and update MARKET_SOURCE.
    """
    return synthetic_market(len(returns)).set_axis(returns.index)


def engine_profile(holdings: pd.DataFrame, returns_fn=None) -> dict:
    """The get_risk_profile payload for a holdings frame (also used by the API).

    Market sensitivity needs a market series on the same periods; with an injected
    `returns_fn` (tests, custom data) there is none, so those figures are left out.
    """
    returns = (returns_fn or get_returns)(holdings)
    if returns_fn is None:
        profile = risk_profile(holdings, returns, market=get_market_returns(returns),
                               market_name=MARKET_SOURCE)
    else:
        profile = risk_profile(holdings, returns)
    return {"returns_source": RETURNS_SOURCE, **profile.to_dict()}


def resolve_holdings_path(raw: str, data_dir: Path | None = None) -> Path:
    """Resolve `raw` and require that it is a CSV inside the data directory.

    Relative paths are taken relative to the repo root. `resolve()` collapses
    ".." and follows symlinks, so a link inside data/ that points elsewhere is
    rejected too.
    """
    root = (data_dir or DATA_DIR).resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (data_dir.parent if data_dir else REPO_ROOT) / p
    p = p.resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Holdings file must be inside {root.name}/.")
    if p.is_relative_to(root / "audit"):
        raise ValueError("The audit directory is not readable by the assistant.")
    if p.suffix.lower() != ".csv":
        raise ValueError("Holdings file must be a .csv.")
    if not p.is_file():
        raise ValueError(f"Holdings file not found: {p.name}")
    return p


def holdings_rows(df: pd.DataFrame) -> list[dict]:
    return [{k: (float(v) if k in ("shares", "price", "value", "weight") else v)
             for k, v in row.items()} for row in df.to_dict("records")]


def apply_changes(holdings: pd.DataFrame, tool_input: dict) -> pd.DataFrame:
    """Holdings after a what-if's remove / set_shares / add, validated again."""
    df = holdings[COLUMNS].copy()
    have = set(df["ticker"])

    def tickers(xs) -> list[str]:
        return [str(t).strip().upper() for t in xs]

    remove = tickers(tool_input.get("remove") or [])
    set_shares = {str(k).strip().upper(): v for k, v in (tool_input.get("set_shares") or {}).items()}
    unknown = sorted((set(remove) | set(set_shares)) - have)
    if unknown:
        raise ValueError(f"Not in the holdings: {unknown}")
    for t, n in set_shares.items():
        df.loc[df["ticker"] == t, "shares"] = float(n)
    remove += [t for t, n in set_shares.items() if float(n) == 0]
    df = df[~df["ticker"].isin(remove)]

    added = pd.DataFrame(tool_input.get("add") or [], columns=COLUMNS).fillna("")
    if len(added):
        added["ticker"] = tickers(added["ticker"])
        clash = sorted(set(added["ticker"]) & set(df["ticker"]))
        if clash:
            raise ValueError(f"Already held (use set_shares instead): {clash}")
        df = pd.concat([df, added], ignore_index=True)
    if df.empty:
        raise ValueError("The change would leave no holdings.")
    return validate_holdings(df)


def execute_tool(
    name: str,
    tool_input: dict,
    *,
    data_dir: Path | None = None,
    returns_fn=None,
) -> ToolResult:
    """Run a tool call. Problems come back as error text, never as exceptions."""
    if name not in {t["name"] for t in TOOLS}:
        return ToolResult(f"Unknown tool: {name}", is_error=True)
    tool_input = tool_input or {}
    try:
        path = resolve_holdings_path(str(tool_input.get("holdings_path", "")), data_dir)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        holdings = load_holdings(str(path))
        if name == "get_risk_profile":
            payload = engine_profile(holdings, returns_fn)
        elif name == "get_holdings":
            payload = {"holdings": holdings_rows(holdings),
                       "total_value": float(holdings["value"].sum()),
                       "n_positions": int(len(holdings))}
        elif name == "run_what_if":
            before = engine_profile(holdings, returns_fn)
            after = engine_profile(apply_changes(holdings, tool_input), returns_fn)
            payload = {"returns_source": RETURNS_SOURCE,
                       "changes": {k: tool_input[k] for k in ("remove", "set_shares", "add")
                                   if tool_input.get(k)},
                       "comparison": compare_profiles(before, after), "after": after}
        else:  # run_stress_test
            if tool_input.get("scenario_id"):
                payload = run_scenario(holdings, tool_input["scenario_id"])
            else:
                payload = {"scenario": {"id": "custom", "label": "Custom scenario"},
                           **stress_test(holdings, tool_input.get("sector_shocks") or {},
                                         float(tool_input.get("default_shock") or 0.0),
                                         tool_input.get("asset_class_shocks"))}
    except (ValueError, OSError, pd.errors.ParserError) as e:
        return ToolResult(f"Error: {e}", is_error=True)
    except Exception as e:  # keep the loop alive; the type name helps debugging
        return ToolResult(f"Error ({type(e).__name__}): {e}", is_error=True)
    return ToolResult(json.dumps(payload), holdings_sha256=sha)
