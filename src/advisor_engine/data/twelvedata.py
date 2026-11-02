"""Twelve Data adapter: dividend-adjusted daily closes.

Endpoint: GET {base}/time_series?symbol=..&interval=1day&start_date=YYYY-MM-DD
&adjust=all&outputsize=5000&apikey=.. The response is
`{"meta": {...}, "values": [{"datetime": "2026-10-31", "close": "101.5", ...}], "status": "ok"}`,
newest first, with prices as strings. `adjust=all` adjusts for splits and dividends,
which is what the vendor-neutral layer expects. 5000 rows covers ten years of trading days.

Twelve Data reports most errors with HTTP 200 and a body of
`{"status": "error", "code": 401|403|404|429|..., "message": ...}`, so the body's code is
checked as well as the HTTP status.

The API key travels as a query parameter, so request URLs and httpx errors carry it.
Nothing from them reaches an error message: errors say what went wrong in words only.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import DataSourceError, NotInPlan, SymbolNotFound

BASE_URL = "https://api.twelvedata.com"
TIMEOUT_SECONDS = 20
OUTPUT_SIZE = 5000  # the API's maximum rows per request


def td_symbol(ticker: str) -> str:
    """Twelve Data writes share classes with a dot: BRK-B and BRK/B become BRK.B."""
    return ticker.strip().upper().replace("-", ".").replace("/", ".")


class TwelveDataSource:
    name = "Twelve Data"

    def __init__(self, api_key: str, *, client=None, base_url: str = BASE_URL):
        if not api_key:
            raise DataSourceError("TWELVE_DATA_API_KEY is not set.")
        if client is None:
            import httpx

            client = httpx.Client(timeout=TIMEOUT_SECONDS)
        self._key = api_key
        self._http = client
        self._base = base_url.rstrip("/")

    def _fail(self, code: int, symbol: str):
        if code == 401:
            raise DataSourceError("Twelve Data refused the request: check TWELVE_DATA_API_KEY.")
        if code == 403:  # the symbol (or adjusted history) is outside the account's plan
            raise NotInPlan(symbol)
        if code == 404 or code == 400:  # 400: "symbol not found / invalid" for bad tickers
            raise SymbolNotFound(symbol)
        if code == 429:
            raise DataSourceError("Twelve Data rate limit reached (API credits per minute); "
                                  "try again in a minute.")
        raise DataSourceError(f"Twelve Data returned an error (code {code}).")

    def daily_closes(self, symbol: str, start: date) -> pd.Series:
        params = {"symbol": td_symbol(symbol), "interval": "1day",
                  "start_date": start.isoformat(), "adjust": "all",
                  "outputsize": OUTPUT_SIZE, "apikey": self._key}
        try:
            r = self._http.get(f"{self._base}/time_series", params=params)
        except Exception as e:  # network errors carry the URL, and so the key
            raise DataSourceError(f"Couldn't reach Twelve Data ({type(e).__name__}).") from None
        if r.status_code >= 400:
            self._fail(r.status_code, symbol)
        try:
            data = r.json()
        except ValueError:
            raise DataSourceError("Twelve Data returned a response that isn't JSON.") from None
        if not isinstance(data, dict):
            raise DataSourceError("Twelve Data returned an unexpected response.")
        if data.get("status") == "error":
            try:
                code = int(data.get("code") or 0)
            except (TypeError, ValueError):
                code = 0
            self._fail(code, symbol)
        points = {}
        for row in data.get("values") or []:
            day, close = row.get("datetime"), row.get("close")
            if not day or close in (None, ""):
                continue
            try:
                points[day[:10]] = float(close)
            except (TypeError, ValueError):
                continue
        if not points:
            raise SymbolNotFound(symbol)
        s = pd.Series(points)
        s.index = pd.to_datetime(s.index)
        return s.sort_index()
