"""Tool definitions and handlers the model can call.

The model gets one tool, `get_risk_profile`. Everything it returns is the
engine's own output, serialised as JSON; the model never sees a way to compute.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..holdings import load_holdings
from ..risk import risk_profile
from ..synthetic import synthetic_returns
from ._paths import DATA_DIR, REPO_ROOT

GET_RISK_PROFILE = {
    "name": "get_risk_profile",
    "description": (
        "Compute the engine's risk profile for a holdings CSV: effective independent bets, "
        "market-adjusted and sector-level concentration, sector exposure, drawdown "
        "sensitivity, and per-position risk facts (risk_share, risk_to_weight, "
        "avg_corr_to_rest). The file must be inside the data/ directory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "holdings_path": {
                "type": "string",
                "description": "Path to a holdings CSV (columns: ticker, shares, price, sector).",
            }
        },
        "required": ["holdings_path"],
    },
}

TOOLS = [GET_RISK_PROFILE]

# Labels where the returns came from, so the model can say so in its answer.
RETURNS_SOURCE = "synthetic"


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    holdings_sha256: str | None = None


def get_returns(holdings: pd.DataFrame) -> pd.DataFrame:
    """Return history for every holdings ticker. The one place real data plugs in.

    TODO: replace with real price data once licensing is confirmed, and update
    RETURNS_SOURCE to match. Contract: columns cover every `holdings["ticker"]`.
    """
    classes = list(holdings["asset_class"]) if "asset_class" in holdings else None
    return synthetic_returns(list(holdings["ticker"]), list(holdings["sector"]),
                             asset_classes=classes)


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


def execute_tool(
    name: str,
    tool_input: dict,
    *,
    data_dir: Path | None = None,
    returns_fn=None,
) -> ToolResult:
    """Run a tool call. Problems come back as error text, never as exceptions."""
    if name != GET_RISK_PROFILE["name"]:
        return ToolResult(f"Unknown tool: {name}", is_error=True)
    try:
        path = resolve_holdings_path(str((tool_input or {}).get("holdings_path", "")), data_dir)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        holdings = load_holdings(str(path))
        returns = (returns_fn or get_returns)(holdings)
        profile = risk_profile(holdings, returns).to_dict()
    except (ValueError, OSError, pd.errors.ParserError) as e:
        return ToolResult(f"Error: {e}", is_error=True)
    except Exception as e:  # keep the loop alive; the type name helps debugging
        return ToolResult(f"Error ({type(e).__name__}): {e}", is_error=True)
    payload = {"returns_source": RETURNS_SOURCE, **profile}
    return ToolResult(json.dumps(payload), holdings_sha256=sha)
