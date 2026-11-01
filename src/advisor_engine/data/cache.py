"""On-disk cache of month-end closes, one small CSV per vendor and symbol.

Month-end prices only change once a month, so the engine fetches a symbol once and
then at most once per `max_age`, instead of on every profile. Shared by all firms:
market prices are not client data. It lives under `data/`, which git ignores, so
licensed prices are never committed.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pandas as pd

MAX_AGE_SECONDS = 24 * 3600

_SAFE = re.compile(r"[^A-Za-z0-9.\-^=]")


class PriceCache:
    def __init__(self, root: Path, max_age: float = MAX_AGE_SECONDS, clock=time.time):
        self.root = Path(root)
        self.max_age = max_age
        self.clock = clock
        self._lock = threading.Lock()

    def path(self, source: str, symbol: str) -> Path:
        # Symbols come from uploaded CSVs: keep them to a safe file name.
        return self.root / _SAFE.sub("_", source) / f"{_SAFE.sub('_', symbol.upper())}.csv"

    def get(self, source: str, symbol: str) -> pd.Series | None:
        """Cached month-end closes, or None if missing or older than `max_age`."""
        p = self.path(source, symbol)
        try:
            if self.clock() - p.stat().st_mtime > self.max_age:
                return None
            frame = pd.read_csv(p, index_col=0, parse_dates=True)
        except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
            return None
        s = frame.iloc[:, 0].astype(float)
        s.index.name, s.name = None, None  # same shape as a freshly fetched series
        return s

    def put(self, source: str, symbol: str, closes: pd.Series) -> None:
        p = self.path(source, symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        closes.rename("close").to_csv(tmp, index_label="month_end")
        with self._lock:
            tmp.replace(p)  # atomic, so a reader never sees half a file
