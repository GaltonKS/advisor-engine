"""Load and validate a holdings CSV.

Required columns: ticker, shares, price, sector.
Long-only for the MVP: negative shares are rejected.

Files saved from Excel or exported by brokers rarely match the sample exactly, so
`read_holdings_csv` accepts a few layout differences that have only one reading:
header case and spacing, a byte-order mark, comma / semicolon / tab / pipe
separators, title rows above the header, a short list of column aliases, and
currency symbols or thousands separators in numbers. Anything ambiguous (a number
that could be read two ways, a header it can't find) is rejected with a message
saying what was found, never guessed: a misread figure would flow into every result.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

REQUIRED = ["ticker", "shares", "price", "sector"]

# Header names that mean exactly one required column. Kept short on purpose: an alias
# is a claim about what someone else's column means. No alias for sector.
ALIASES = {
    "symbol": "ticker",
    "quantity": "shares",
    "qty": "shares",
    "last price": "price",
    "current price": "price",
    "market price": "price",
    "price ($)": "price",
}

_DELIMITERS = (",", ";", "\t", "|")
_HEADER_SEARCH_LINES = 20


def _norm_header(name: object) -> str:
    s = str(name).replace("﻿", "").strip().strip('"').strip()
    s = re.sub(r"\s+", " ", s).lower()
    return ALIASES.get(s, s)


def _decode(data: bytes) -> str:
    # utf-8-sig drops Excel's byte-order mark; cp1252 covers Excel on Windows.
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _find_header(text: str) -> tuple[int, str] | None:
    """(line index, delimiter) of the first line naming every required column."""
    lines = text.splitlines()[:_HEADER_SEARCH_LINES]
    for i, line in enumerate(lines):
        for delim in _DELIMITERS:
            if delim not in line:
                continue
            cells = {_norm_header(c) for c in next(csv.reader([line], delimiter=delim))}
            if all(col in cells for col in REQUIRED):
                return i, delim
    return None


def _first_line_cells(text: str) -> list[str]:
    for line in text.splitlines():
        if line.strip():
            delim = max(_DELIMITERS, key=line.count)
            return [c.strip() for c in next(csv.reader([line], delimiter=delim)) if c.strip()]
    return []


_US_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")    # 1,234.50
_EU_GROUPED = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")    # 1.234,50
_DECIMAL_COMMA = re.compile(r"^\d+,(\d{1,2}|\d{4,})$")    # 185,50 (never 3 digits)
_ONE_SEP_THREE = re.compile(r"^\d{1,3}[.,]\d{3}$")        # 1,200 or 1.200


def _parse_number(raw: object, delim: str) -> float:
    """One cell to a float, or ValueError if empty, not a number or ambiguous.

    Comma-separated files use US conventions (comma groups thousands). In
    semicolon-separated files (Excel in many non-US regions) a comma may be the
    decimal mark, so "1,200" or "1.200" could mean 1200 or 1.2 and is rejected.
    """
    s = str(raw).strip()
    negative = s.startswith("(") and s.endswith(")")  # accounting style (5) = -5
    s = s.strip("()").replace("$", "").replace(" ", "").replace(" ", "")
    if s.startswith("-"):
        negative, s = True, s[1:]
    if not s or s.lower() == "nan":
        raise ValueError("is empty")

    if delim == ";":
        if _ONE_SEP_THREE.match(s):
            raise ValueError("is ambiguous (thousands or decimal separator?)")
        if _EU_GROUPED.match(s) or _DECIMAL_COMMA.match(s):
            s = s.replace(".", "").replace(",", ".")
    elif _US_GROUPED.match(s):
        s = s.replace(",", "")

    try:
        value = float(s)
    except ValueError:
        raise ValueError("is not a number") from None
    return -value if negative else value


def read_holdings_csv(source: str | Path | bytes) -> pd.DataFrame:
    """Parse a holdings CSV (path or raw bytes) into the four required columns.

    Values come back as found; `validate_holdings` checks them. Numbers in shares and
    price are converted here because their format depends on the file's separator.
    """
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    text = _decode(data)
    found = _find_header(text)
    if found is None:
        cells = _first_line_cells(text)
        missing = REQUIRED if not cells else [
            c for c in REQUIRED if c not in {_norm_header(x) for x in cells}]
        raise ValueError(
            f"Holdings file is missing columns: {missing}. "
            f"Columns found: {cells or 'none'}. "
            "Expected a header row with ticker, shares, price, sector."
        )
    header_line, delim = found

    df = pd.read_csv(io.StringIO(text), sep=delim, skiprows=header_line, dtype=str,
                     keep_default_na=False, skipinitialspace=True)
    df.columns = [_norm_header(c) for c in df.columns]
    if df.columns.duplicated().any():
        dups = sorted(set(df.columns[df.columns.duplicated()]) & set(REQUIRED))
        if dups:
            raise ValueError(f"Holdings file has more than one column for: {dups}")
    df = df[REQUIRED]
    # Excel often leaves trailing blank rows; drop rows with nothing in them only.
    df = df[~df.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)]

    for col in ("shares", "price"):
        parsed = []
        for row_no, raw in zip(df.index, df[col]):
            try:
                parsed.append(_parse_number(raw, delim))
            except ValueError as e:
                # +2: header line plus 1-based rows, counted from the header.
                line = header_line + row_no + 2
                raise ValueError(f"Line {line}: {col} value {str(raw)!r} {e}.") from None
        df[col] = parsed
    return df.reset_index(drop=True)


def load_holdings(source: str | Path | bytes) -> pd.DataFrame:
    return validate_holdings(read_holdings_csv(source))


def validate_holdings(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Holdings file is missing columns: {missing}")

    df = df[REQUIRED].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["sector"] = df["sector"].astype(str).str.strip()

    if (df["ticker"] == "").any():
        raise ValueError("Every holding needs a ticker.")
    if df["ticker"].duplicated().any():
        dups = sorted(df.loc[df["ticker"].duplicated(), "ticker"].unique())
        raise ValueError(f"Duplicate tickers: {dups}")

    for col in ("shares", "price"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    if (df["shares"] < 0).any():
        raise ValueError("Negative shares found; short positions are out of scope.")
    if (df["price"] <= 0).any():
        raise ValueError("Prices must be positive.")

    df["value"] = df["shares"] * df["price"]
    total = df["value"].sum()
    if total <= 0:
        raise ValueError("Portfolio has zero total value.")
    df["weight"] = df["value"] / total
    return df.reset_index(drop=True)
