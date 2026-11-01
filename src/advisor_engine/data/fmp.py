"""Financial Modeling Prep adapter: dividend-adjusted daily closes.

Endpoint: GET {base}/historical-price-eod/dividend-adjusted?symbol=..&from=YYYY-MM-DD
&apikey=.. (FMP "stable" API). The response is a list of daily rows, newest first,
with `date` and `adjClose`; the older v3 shape (`{"historical": [...]}`) and a plain
`close` are accepted too, so an account on either API works.

The API key travels as a query parameter, so request URLs and httpx errors carry it.
Nothing from them reaches an error message: errors say what went wrong in words only.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import DataSourceError, SymbolNotFound

BASE_URL = "https://financialmodelingprep.com/stable"
TIMEOUT_SECONDS = 20


def fmp_symbol(ticker: str) -> str:
    """FMP writes share classes with a dash: BRK.B and BRK/B become BRK-B."""
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


class FMPSource:
    name = "FMP"

    def __init__(self, api_key: str, *, client=None, base_url: str = BASE_URL):
        if not api_key:
            raise DataSourceError("FMP_API_KEY is not set.")
        if client is None:
            import httpx

            client = httpx.Client(timeout=TIMEOUT_SECONDS)
        self._key = api_key
        self._http = client
        self._base = base_url.rstrip("/")

    def daily_closes(self, symbol: str, start: date) -> pd.Series:
        params = {"symbol": fmp_symbol(symbol), "from": start.isoformat(), "apikey": self._key}
        try:
            r = self._http.get(f"{self._base}/historical-price-eod/dividend-adjusted",
                               params=params)
        except Exception as e:  # network errors carry the URL, and so the key
            raise DataSourceError(f"Couldn't reach FMP ({type(e).__name__}).") from None
        if r.status_code in (401, 403):
            raise DataSourceError("FMP refused the request: check FMP_API_KEY and that the "
                                  "plan includes dividend-adjusted price history.")
        if r.status_code == 429:
            raise DataSourceError("FMP rate limit reached; try again in a minute.")
        if r.status_code >= 400:
            raise DataSourceError(f"FMP returned an error (HTTP {r.status_code}).")
        try:
            data = r.json()
        except ValueError:
            raise DataSourceError("FMP returned a response that isn't JSON.") from None
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            raise DataSourceError("FMP refused the request: check FMP_API_KEY and the plan.")
        rows = data.get("historical", []) if isinstance(data, dict) else data
        points = {}
        for row in rows or []:
            close = row.get("adjClose", row.get("close"))
            if row.get("date") and close is not None:
                points[row["date"]] = float(close)
        if not points:
            raise SymbolNotFound(symbol)
        s = pd.Series(points)
        s.index = pd.to_datetime(s.index)
        return s.sort_index()
