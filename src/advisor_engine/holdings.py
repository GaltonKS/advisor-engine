"""Load and validate a holdings CSV.

Required columns: ticker, shares, price. A sector column is optional: holdings
without one (no column, or a blank cell) are labelled `UNCLASSIFIED`, and the engine
withholds or caveats the figures that depend on sector rather than treating them as
one sector. Long-only for the MVP: negative shares are rejected.

An asset class column is optional too. Its labels are mapped to one of
`ASSET_CLASSES` by `classify_asset_class` (a fixed keyword list, documented there):
blank means equity, which keeps files without the column unchanged, and a label the
list doesn't recognise becomes "other", modelled like equity and reported as such.
Common spellings of standard sectors are mapped to one name (`SECTOR_ALIASES`), so
"Healthcare" and "Health Care" are one sector and match the stress presets.

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

REQUIRED = ["ticker", "shares", "price"]
COLUMNS = [*REQUIRED, "sector", "asset_class"]
UNCLASSIFIED = "Unclassified"
ASSET_CLASSES = ("equity", "fixed_income", "preferred", "cash", "other")

# Spellings of the standard sectors (the names the stress presets use) as written by
# other classification schemes. Exact matches only, after case and spacing.
STANDARD_SECTORS = ("Communication Services", "Consumer Discretionary", "Consumer Staples",
                    "Energy", "Financials", "Health Care", "Industrials", "Materials",
                    "Real Estate", "Technology", "Utilities")
SECTOR_ALIASES = {
    **{s.lower(): s for s in STANDARD_SECTORS},
    "communication": "Communication Services",
    "communications": "Communication Services",
    "telecommunication services": "Communication Services",
    "telecommunications": "Communication Services",
    "telecom services": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "financial": "Financials",
    "financial services": "Financials",
    "healthcare": "Health Care",
    "industrial": "Industrials",
    "basic materials": "Materials",
    "information technology": "Technology",
    "utility": "Utilities",
}

# Header names that mean exactly one required column. Kept short on purpose: an alias
# is a claim about what someone else's column means. No alias for sector. A trailing
# "($)" is dropped before lookup, so "Share Price ($)" matches "share price".
# Deliberately absent: cost columns ("avg cost", "cost basis") are what was paid, not
# the current price, and "market value" is a position total, not a per-share price.
ALIASES = {
    "symbol": "ticker",
    "quantity": "shares",
    "qty": "shares",
    "share price": "price",
    "price per share": "price",
    "last price": "price",
    "current price": "price",
    "market price": "price",
    "asset class": "asset_class",
    "asset type": "asset_class",
}

# Ticker cells that mark a broker export's summary line rather than a holding. Such a
# row is skipped only when its shares and price are also blank.
_SUMMARY_LABELS = {"", "total", "totals", "grand total", "subtotal", "account total",
                   "portfolio total"}

_DELIMITERS = (",", ";", "\t", "|")
_HEADER_SEARCH_LINES = 20


def _norm_header(name: object) -> str:
    s = str(name).replace("﻿", "").strip().strip('"').strip()
    s = re.sub(r"\s+", " ", s).lower()
    s = re.sub(r"\s*\(\$\)$", "", s)
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
    """Parse a holdings CSV (path or raw bytes) into the holdings columns.

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
            "Expected a header row with ticker, shares, price (and optionally sector)."
        )
    header_line, delim = found

    df = pd.read_csv(io.StringIO(text), sep=delim, skiprows=header_line, dtype=str,
                     keep_default_na=False, skipinitialspace=True)
    df.columns = [_norm_header(c) for c in df.columns]
    if df.columns.duplicated().any():
        dups = sorted(set(df.columns[df.columns.duplicated()]) & set(COLUMNS))
        if dups:
            raise ValueError(f"Holdings file has more than one column for: {dups}")
    for optional in ("sector", "asset_class"):
        if optional not in df.columns:
            df[optional] = ""
    df = df[COLUMNS]
    # Excel often leaves trailing blank rows, and broker exports add a totals line
    # (no ticker or "Total", with only the value columns filled). Drop those only.
    blank = lambda v: str(v).strip() == ""  # noqa: E731
    summary = df["ticker"].map(lambda t: str(t).strip().rstrip(":").lower() in _SUMMARY_LABELS)
    df = df[~(summary & df["shares"].map(blank) & df["price"].map(blank))]

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


def classify_asset_class(label: object) -> str:
    """Map a free-text asset class label to one of `ASSET_CLASSES`.

    First match wins, on the lower-cased label: "cash" / "money market" -> cash;
    "preferred" -> preferred; "fixed income" / "bond" / "treasur" / "muni" ->
    fixed_income; "equity" / "equities" / "stock" -> equity. Blank -> equity (files
    without the column are treated as before). Anything else -> other.
    """
    s = re.sub(r"[\s_-]+", " ", str(label)).strip().lower()
    if s in ("", "nan"):
        return "equity"
    for words, cls in ((("cash", "money market"), "cash"),
                       (("preferred",), "preferred"),
                       (("fixed income", "bond", "treasur", "muni"), "fixed_income"),
                       (("equity", "equities", "stock"), "equity")):
        if any(w in s for w in words):
            return cls
    return "other"


def load_holdings(source: str | Path | bytes) -> pd.DataFrame:
    return validate_holdings(read_holdings_csv(source))


def validate_holdings(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Holdings file is missing columns: {missing}")

    df = df.copy()
    for optional in ("sector", "asset_class"):
        if optional not in df.columns:
            df[optional] = ""
    df = df[COLUMNS]
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["asset_class"] = df["asset_class"].fillna("").map(classify_asset_class)
    sector = df["sector"].fillna("").astype(str).str.strip()
    sector = sector.map(lambda s: SECTOR_ALIASES.get(re.sub(r"\s+", " ", s).lower(), s))
    # One spelling for "no sector", whether blank or typed in any case.
    df["sector"] = sector.where((sector != "") & (sector.str.lower() != UNCLASSIFIED.lower()),
                                UNCLASSIFIED)

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
