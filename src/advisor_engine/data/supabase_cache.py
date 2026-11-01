"""Month-end price cache in Supabase Postgres (`market.prices`, `market.fetches`).

Same interface as the on-disk `PriceCache` (`get_many` / `put_many`), but it survives
deploys and is shared by every API instance, so each symbol is fetched from the vendor
once a day in total rather than once per instance after each restart.

- Reads are batched: one connection and two queries for all of a profile's symbols.
  A process-local copy then serves repeat profiles until the row's own freshness
  runs out, so most profiles don't touch the database at all.
- Freshness is decided by the database clock (`fetched_at`), so instances agree.
- The cache must never break a profile: if the database can't be reached, reads are
  misses (the vendor is asked instead) and writes are skipped, with a warning logged.

The tables are in the `market` schema, which only the API's connection role can use
(see supabase/migrations/20261103000000_market_price_cache.sql). This connection does
not switch to `authenticated` the way SupabaseSession does: the data isn't firm data,
and advisers must not be able to read licensed prices directly.
"""

from __future__ import annotations

import logging
import threading
import time

import pandas as pd

from .cache import MAX_AGE_SECONDS, month_index

log = logging.getLogger("advisor_engine.data")


class SupabasePriceCache:
    def __init__(self, db_url: str, max_age: float = MAX_AGE_SECONDS, clock=time.monotonic):
        self.db_url = db_url
        self.max_age = max_age
        self.clock = clock
        # (source, symbol) -> (closes, monotonic time it stops being fresh)
        self._memory: dict[tuple[str, str], tuple[pd.Series, float]] = {}
        self._lock = threading.Lock()

    def _connect(self):
        import psycopg

        # Transaction pooler: no prepared statements (as SupabaseStore).
        return psycopg.connect(self.db_url, prepare_threshold=None, connect_timeout=5)

    # --- reads ---------------------------------------------------------------------

    def get_many(self, source: str, symbols: list[str]) -> dict[str, pd.Series]:
        now = self.clock()
        out, ask = {}, []
        with self._lock:
            for s in symbols:
                hit = self._memory.get((source, s))
                if hit and hit[1] > now:
                    out[s] = hit[0]
                else:
                    ask.append(s)
        if not ask:
            return out
        try:
            with self._connect() as conn:
                fresh = conn.execute(
                    "select symbol, extract(epoch from fetched_at + make_interval(secs => %s)"
                    " - now()) from market.fetches where source = %s and symbol = any(%s)"
                    " and fetched_at > now() - make_interval(secs => %s)",
                    [self.max_age, source, ask, self.max_age]).fetchall()
                left = {sym: float(secs) for sym, secs in fresh}
                rows = conn.execute(
                    "select symbol, month_end, close from market.prices"
                    " where source = %s and symbol = any(%s) order by symbol, month_end",
                    [source, list(left)]).fetchall() if left else []
        except Exception as e:  # the cache must never break a profile
            log.warning("Price cache read failed (%s); asking the vendor instead.",
                        type(e).__name__)
            return out
        found: dict[str, tuple[list, list]] = {}
        for sym, month_end, close in rows:
            dates, values = found.setdefault(sym, ([], []))
            dates.append(month_end)
            values.append(float(close))
        with self._lock:
            for sym, (dates, values) in found.items():
                closes = pd.Series(values, index=month_index(dates), dtype=float)
                self._memory[(source, sym)] = (closes, now + left[sym])
                out[sym] = closes
        return out

    # --- writes --------------------------------------------------------------------

    def put_many(self, source: str, closes: dict[str, pd.Series]) -> None:
        if not closes:
            return
        symbols, months, values = [], [], []
        for sym, series in closes.items():
            for month_end, close in series.items():
                symbols.append(sym)
                months.append(pd.Timestamp(month_end).date())
                values.append(float(close))
        try:
            with self._connect() as conn, conn.transaction():
                # Adjusted closes are restated after every dividend, so a symbol's history
                # is replaced as a whole rather than merged month by month.
                conn.execute("delete from market.prices where source = %s and symbol = any(%s)",
                             [source, list(closes)])
                conn.execute(
                    "insert into market.prices (source, symbol, month_end, close)"
                    " select %s, * from unnest(%s::text[], %s::date[], %s::float8[])"
                    " on conflict (source, symbol, month_end) do update set close = excluded.close",
                    [source, symbols, months, values])
                conn.execute(
                    "insert into market.fetches (source, symbol, fetched_at)"
                    " select %s, unnest(%s::text[]), now()"
                    " on conflict (source, symbol) do update set fetched_at = excluded.fetched_at",
                    [source, list(closes)])
        except Exception as e:
            log.warning("Price cache write failed (%s); prices will be fetched again.",
                        type(e).__name__)
            return
        until = self.clock() + self.max_age
        with self._lock:
            for sym, series in closes.items():
                self._memory[(source, sym)] = (series, until)
