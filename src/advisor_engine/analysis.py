"""Engine-level operations on a holdings frame, shared by the assistant's tools and the API.

Nothing here knows about the model: these are the steps between loaded holdings and
engine output (market data from the configured source, the risk profile payload, and
what-if changes to holdings). `assistant/tools.py` wraps them as model tools; the API
routes call them directly.
"""

from __future__ import annotations

import pandas as pd

from .data import as_of as returns_as_of
from .data import get_market_data
from .holdings import COLUMNS, validate_holdings
from .risk import risk_profile

# Labels for the synthetic data (the default source). With real data the labels come from
# the configured source: see `data.get_market_data()`.
RETURNS_SOURCE = "synthetic"
MARKET_SOURCE = "synthetic market factor"


def get_returns(holdings: pd.DataFrame) -> pd.DataFrame:
    """Return history for every holdings ticker, from the configured market data source
    (`data.get_market_data()`: synthetic by default, or a vendor such as FMP).
    Contract: columns cover every `holdings["ticker"]`."""
    return get_market_data().returns(holdings)


def get_market_returns(returns: pd.DataFrame) -> pd.Series:
    """The market index return series aligned with `returns`, from the same source."""
    return get_market_data().market(returns)


def engine_profile(holdings: pd.DataFrame, returns_fn=None) -> dict:
    """The get_risk_profile payload for a holdings frame (also used by the API).

    Market sensitivity needs a market series on the same periods; with an injected
    `returns_fn` (tests, custom data) there is none, so those figures are left out.
    """
    if returns_fn is not None:
        returns = returns_fn(holdings)
        return {"returns_source": RETURNS_SOURCE, **risk_profile(holdings, returns).to_dict()}
    data = get_market_data()
    returns = data.returns(holdings)
    profile = risk_profile(holdings, returns, market=data.market(returns),
                           market_name=data.market_source)
    out = {"returns_source": data.returns_source, **profile.to_dict()}
    if (last := returns_as_of(returns)) is not None:
        out["returns_as_of"] = last  # dated (real) data only
    return out


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
