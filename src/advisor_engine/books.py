"""Read the CSV reports QuickBooks exports into pieces of a small-business snapshot.

A port of the FIS pilot skeleton's ``app/upload.js``. Given the text of a CSV export,
:func:`read` works out which report it is from its title block and reads it into the
part of the snapshot it fills, in the same JSON shape as the skeleton's ``data.js``
(``pnl``, ``balance``, ``sales``, ``customers``, ``ar``, ``ap``). :func:`apply` then
puts several files' pieces into a copy of an existing snapshot, replacing the
``sources`` lines they supersede and recording which pieces are now the owner's.

Six reports are read, in the QuickBooks Online or Desktop CSV layout:

* Profit and Loss with one column per month (``pnl``),
* Balance Sheet (``balance``: bank total, receivable, payable),
* A/R and A/P Aging Summary (``ar`` / ``ap``: the TOTAL row's buckets),
* a sales detail, Sales by Product/Service Detail (Desktop: Sales by Item Detail),
  read into weekly rows by unit and offering (``sales``),
* optionally Sales by Customer Summary (``customers``, as shares only).

Rules carried over from the JS:

* **Nothing is guessed.** A file that cannot be read comes back with ``ok: False`` and
  a plain ``error`` saying why. Refusals are *returned as values*, not raised, exactly
  as the JS does, so a batch of files can be read and each one's result shown. The
  error texts are kept word for word.
* A Profit and Loss whose accounts do not add up to the report's own Total Income or
  Net Income (within $1 or 0.1%, whichever is larger) is refused.
* **Customer names never come out.** The sales detail and the customer summary read
  names only to count customers and work out shares; no name is in any result.
* Dates are read as US month/day/year, and all date arithmetic is on calendar days
  (the JS works in UTC, so there are no time zones to worry about).

Pure: no I/O, no clock, no environment. :func:`apply` takes the upload time as ``now``
(the JS falls back to the browser clock). Output is plain JSON-serialisable dicts,
lists, strings and numbers.

Porting notes (where Python and JavaScript differ, the JS behaviour is kept):

* ``Math.round`` rounds halves up, toward +infinity, not to even (:func:`_js_round`).
* Sums fold left to right from 0 (:func:`_sum`); Python 3.12+'s ``sum`` compensates
  float error and would differ from the JS in the last bit.
* Regular expressions use JavaScript's whitespace set, ASCII ``\\d`` / ``\\b``, and
  ``\\Z`` for ``$`` (Python's ``$`` also matches before a final newline).
* ``Date.UTC`` maps years 0 to 99 to 1900 to 1999; :func:`day_key` therefore refuses
  a date whose year is below 100, as the JS does.

The JS names map as: ``csv`` -> :func:`parse_csv`, ``num`` -> :func:`num`,
``monthKey`` -> :func:`month_key`, ``dayKey`` -> :func:`day_key`, ``top`` ->
:func:`top`, ``periodOf`` -> :func:`period_of`, ``kindOf`` -> :func:`kind_of`,
``readPnl`` / ``readBalance`` / ``readAging`` / ``readCustomers`` / ``readSales`` ->
:func:`read_pnl` / :func:`read_balance` / :func:`read_aging` / :func:`read_customers` /
:func:`read_sales`, ``shares`` -> :func:`shares`, ``read`` -> :func:`read`, ``apply``
-> :func:`apply`.
"""

from __future__ import annotations

import calendar
import copy
import math
import re
from datetime import date, timedelta
from typing import Any, Iterable

__all__ = [
    "REPORTS", "SLOTS", "CORE",
    "parse_csv", "num", "month_key", "day_key", "top", "period_of", "kind_of",
    "read_pnl", "read_balance", "read_aging", "read_customers", "read_sales",
    "shares", "read", "apply",
]

# ---------------------------------------------------------------------------------------
# JavaScript semantics the reader depends on
# ---------------------------------------------------------------------------------------

# The characters JavaScript's \s and String.prototype.trim treat as white space.
_WS_CHARS = ("\t\n\v\f\r           "
             "       　﻿")
_WS_BODY = "".join("\\u%04x" % ord(c) for c in _WS_CHARS)
_WS = "[" + _WS_BODY + "]"
# Characters JavaScript's `.` does not match.
_DOT = "[^\\n\\r\\u2028\\u2029]"

_A = re.ASCII
_AI = re.ASCII | re.IGNORECASE


def _rx(pattern: str, flags: int = _A) -> re.Pattern:
    """Compile a pattern written with `\\s` meaning JavaScript's white space set
    (outside character classes only)."""
    return re.compile(pattern.replace("\\s", _WS), flags)


def _trim(s: str) -> str:
    return s.strip(_WS_CHARS)


def _js_round(x: float) -> float:
    """``Math.round``: the nearest integer, halves toward +infinity."""
    if not math.isfinite(x):
        return x
    r = math.floor(x)
    return r + 1 if x - r >= 0.5 else r


def _round2(v: float) -> float:
    return _js_round(v * 100) / 100


def _round4(v: float) -> float:
    return _js_round(v * 10000) / 10000


def _sum(values: Iterable[float]) -> float:
    """Left-to-right sum from 0, as ``reduce((s, v) => s + v, 0)`` does."""
    s = 0
    for v in values:
        s = s + v
    return s


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _get(row: list, j: int):
    """``row[j]`` as JavaScript reads it: out of range (including negative) is missing."""
    return row[j] if 0 <= j < len(row) else None


def _locale(n: float) -> str:
    """``Number.prototype.toLocaleString("en-US")`` for the whole numbers it is used on."""
    if isinstance(n, float):
        if math.isnan(n):
            return "NaN"
        if math.isinf(n):
            return "-∞" if n < 0 else "∞"
        if n == int(n):
            n = int(n)
    return f"{n:,}"


def _usd0(v: float) -> str:
    """Whole dollars with thousands separators: ``-$1,235``."""
    return ("-" if v < 0 else "") + "$" + _locale(_js_round(abs(v)))


def _p2(n: int) -> str:
    return str(n).rjust(2, "0")


def _utc_date(y: int, m: int, d: int) -> date:
    """``new Date(Date.UTC(y, m - 1, d))``: years 0 to 99 mean 1900 to 1999, and a month
    or day out of range rolls over into the next (or previous) month."""
    if 0 <= y <= 99:
        y += 1900
    y, m0 = y + (m - 1) // 12, (m - 1) % 12
    return date(y, m0 + 1, 1) + timedelta(days=d - 1)


def _iso(d: date) -> str:
    return f"{d.year}-{_p2(d.month)}-{_p2(d.day)}"


def _to_d(s: str) -> date:
    y, m, d = (int(x) for x in str(s).split("-"))
    return _utc_date(y, m, d)


def _last_of_month(y: int, m: int) -> date:
    """The last day of month ``m`` (1 to 12) of year ``y``."""
    if 0 <= y <= 99:
        y += 1900
    return date(y, m, calendar.monthrange(y, m)[1])


_MON_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MON = ("(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
        "|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONRE = re.compile("^" + _MON + r"\Z", _AI)


_MON_LOWER = [m.lower() for m in _MON_NAMES]


def _mon_idx(name: str) -> int:
    """0 for January ... 11 for December, from the first three letters; -1 if none."""
    k = name[:3].lower()
    return _MON_LOWER.index(k) if k in _MON_LOWER else -1


def _month_label(key: str) -> str:
    a = str(key).split("-")
    return _MON_NAMES[int(a[1]) - 1] + " " + a[0]


def _yr(y: str | int) -> int:
    """Two-digit years: 00 to 69 are 2000s, 70 to 99 are 1900s."""
    y = int(y)
    return (2000 + y if y < 70 else 1900 + y) if y < 100 else y


# ---------------------------------------------------------------------------------------
# Text to cells, and the cell readers
# ---------------------------------------------------------------------------------------

def parse_csv(text: str | None) -> list[list[str]]:
    """Split CSV text into rows of trimmed cells.

    The separator is sniffed from the first six lines: tab if tabs outnumber both commas
    and semicolons, semicolon if semicolons are more than twice the commas, else comma.
    A leading byte-order mark is dropped. Quoted cells may hold separators, line breaks
    and doubled quotes; a quote opens a quoted cell only at the start of a cell (a quote
    in the middle of a cell is kept as text). Lines end with LF, CRLF or a lone CR.
    Blank lines come back as rows with one empty cell; :func:`read` drops them.

    Caveat: the delimiter guess does not change how numbers are read, so a semicolon
    file with decimal commas ("1.234,50") is misread by :func:`num` (the JS does the
    same; QuickBooks' US exports use commas and decimal points).
    """
    text = "" if text is None else str(text)
    if text.startswith("﻿"):
        text = text[1:]
    head = "\n".join(re.split(r"\r?\n", text)[:6])
    tabs, commas, semis = head.count("\t"), head.count(","), head.count(";")
    d = "\t" if tabs > commas and tabs > semis else ";" if semis > commas * 2 else ","
    rows: list[list[str]] = []
    row: list[str] = []
    cell = ""
    quoted = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quoted:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    cell += '"'
                    i += 1
                else:
                    quoted = False
            else:
                cell += c
        elif c == '"' and cell == "":
            quoted = True
        elif c == d:
            row.append(cell)
            cell = ""
        elif c in "\n\r":
            if c == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            row.append(cell)
            cell = ""
            rows.append(row)
            row = []
        else:
            cell += c
        i += 1
    if cell != "" or row:
        row.append(cell)
        rows.append(row)
    return [[_trim(x) for x in r] for r in rows]


_NUM_PARENS = re.compile("^\\(" + _DOT + "*\\)\\Z")
_NUM_STRIP = re.compile("[$," + _WS_BODY + "]")
_NUM_OK = re.compile(r"^-?(\d+\.?\d*|\.\d+)\Z", _A)


def num(s: Any) -> float:
    """Read an amount as QuickBooks writes it; ``nan`` when it is not one.

    Accepts thousands separators, a dollar sign, spaces, accounting brackets for a
    negative ("(1,234.50)"), a leading minus or a trailing one ("12-"), and the Unicode
    minus sign. Brackets and a trailing minus together cancel out. Anything else
    (letters, exponents, two decimal points) is ``nan``: no partial reads.
    """
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s) if math.isfinite(s) else math.nan
    t = _trim("" if s is None else str(s))
    if not t:
        return math.nan
    neg = False
    if _NUM_PARENS.match(t):
        neg = True
        t = t[1:-1]
    t = _NUM_STRIP.sub("", t).replace("−", "-")
    if t.endswith("-") and not t.startswith("-"):
        neg = not neg
        t = t[:-1]
    if not _NUM_OK.match(t):
        return math.nan
    v = float(t)
    return -v if neg else v


_MK_ISO = re.compile(r"^(\d{4})-(\d{1,2})\Z", _A)
_MK_US = re.compile(r"^(\d{1,2})/(\d{4})\Z", _A)
_MK_NAME = re.compile(r"^([a-z]+)[ -](\d{2}|\d{4})\Z", _A)
_SPACES = _rx(r"\s+")


def month_key(s: Any) -> str | None:
    """A month column heading as ``"YYYY-MM"``, or ``None`` if it is not one.

    Reads "Jan 2025", "Sep 25" (Desktop's two-digit year), "September 2025", "Jan-25",
    "2025-1" and "1/2025". Two-digit years 00 to 69 are 2000s, 70 to 99 are 1900s.
    "Total" and day dates such as "Mar 5" are not months.
    """
    s = _trim(_SPACES.sub(" ", re.sub(r"[,.]", " ", _trim(str(s or "")).lower())))
    if not s:
        return None
    m = _MK_ISO.match(s)
    if m and 1 <= int(m[2]) <= 12:
        return m[1] + "-" + _p2(int(m[2]))
    m = _MK_US.match(s)
    if m and 1 <= int(m[1]) <= 12:
        return m[2] + "-" + _p2(int(m[1]))
    m = _MK_NAME.match(s)
    if m and _MONRE.match(m[1]):
        return f"{_yr(m[2])}-{_p2(_mon_idx(m[1]) + 1)}"
    return None


_DK_US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\Z", _A)
_DK_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})", _A)
_DK_MDY = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})\Z", _A)
_DK_DMY = re.compile(r"^(\d{1,2}) ([A-Za-z]{3,9})\.?,? (\d{4})\Z", _A)


def day_key(s: Any) -> str | None:
    """A date cell as ``"YYYY-MM-DD"``, or ``None`` if it is not a real date.

    Reads US "3/5/2025" (month first; two-digit years as in :func:`month_key`),
    "2025-03-05" (anything after the date, such as a time, is ignored), "Mar 5, 2025"
    and "5 Mar 2025". Impossible dates ("2/30/2025", "13/45/2025") are ``None``, and so
    is any year below 100, which JavaScript's ``Date.UTC`` would move into the 1900s.
    """
    s = _trim(str(s or ""))
    m = _DK_US.match(s)
    if m:
        mo, d, y = int(m[1]), int(m[2]), _yr(m[3])
    else:
        m = _DK_ISO.match(s)
        if m:
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
        else:
            m = _DK_MDY.match(s)
            if m and _MONRE.match(m[1]):
                mo, d, y = _mon_idx(m[1]) + 1, int(m[2]), int(m[3])
            else:
                m = _DK_DMY.match(s)
                if m and _MONRE.match(m[2]):
                    d, mo, y = int(m[1]), _mon_idx(m[2]) + 1, int(m[3])
                else:
                    return None
    if y < 100:
        return None
    try:
        return _iso(date(y, mo, d))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------
# The title block: which report, which dates
# ---------------------------------------------------------------------------------------

def top(rows: list[list[str]]) -> str:
    """The title block: the first ten rows' non-empty cells, joined with " | "."""
    out = [" ".join(c for c in r if c) for r in rows[:10]]
    return " | ".join(x for x in out if x)


_D = r"(?:[A-Za-z]+\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})"
_SEP = r"\s*(?:-|–|—|through|thru|to)\s*"
_P_ASOF = _rx("as of (" + _D + ")", _AI)
_P_RANGE = _rx("(" + _D + ")" + _SEP + "(" + _D + ")", _AI)
_P_DAYS = _rx(r"\b" + _MON + r" (\d{1,2})(?:,? (\d{4}))?" + _SEP + _MON + r" (\d{1,2}),? (\d{4})", _AI)
_P_MONTHS = _rx(r"\b" + _MON + r"(?: (\d{4}))?" + _SEP + _MON + r" (\d{4})", _AI)


def period_of(text: str) -> dict:
    """The dates a report's title states: ``{"from", "to", "asOf"}``, each ISO or None.

    Looked for, in order: "As of <date>"; a range of two full dates ("January 1 -
    December 31, 2025", "11/03/2025 - 11/01/2026"); a range of days where only the
    second carries a year ("Nov 3 - Jan 4, 2026", the first year inferred as the one
    before when the first date falls later in the year); and a range of months
    ("January through December 2025", "November 2025 - October 2026"), from the first
    of the first month to the last day of the second. Separators: hyphen, en or em
    dash, "through", "thru", "to".

    Caveat (kept from the JS): the month-range form does not check its start year, so a
    title with a year below 1000 would give an unpadded ``from``.
    """
    out = {"from": None, "to": None, "asOf": None}
    m = _P_ASOF.search(text)
    if m:
        out["asOf"] = day_key(m[1])
    m = _P_RANGE.search(text)
    if m:
        out["from"], out["to"] = day_key(m[1]), day_key(m[2])
    if not out["from"]:
        m = _P_DAYS.search(text)
        if m:
            m1, d1, m2, d2, y2 = _mon_idx(m[1]) + 1, int(m[2]), _mon_idx(m[4]) + 1, int(m[5]), int(m[6])
            y1 = int(m[3]) if m[3] else (y2 - 1 if (m1 > m2 or (m1 == m2 and d1 > d2)) else y2)
            out["from"] = day_key(f"{y1}-{_p2(m1)}-{_p2(d1)}")
            out["to"] = day_key(f"{y2}-{_p2(m2)}-{_p2(d2)}")
    if not out["from"]:
        m = _P_MONTHS.search(text)
        if m:
            m1, m2, y2 = _mon_idx(m[1]) + 1, _mon_idx(m[3]) + 1, int(m[4])
            y1 = int(m[2]) if m[2] else (y2 - 1 if m1 > m2 else y2)
            out["from"] = f"{y1}-{_p2(m1)}-01"
            out["to"] = _iso(_last_of_month(y2, m2))
    return out


REPORTS = {
    "pnl": "Profit and Loss",
    "balance": "Balance Sheet",
    "ar": "A/R Aging Summary",
    "ap": "A/P Aging Summary",
    "sales": "Sales detail",
    "customers": "Sales by Customer Summary",
}

_K_AR = re.compile(r"a/r aging|accounts receivable aging|receivable aging", _A)
_K_AP = re.compile(r"a/p aging|accounts payable aging|payable aging", _A)
_K_PNL = _rx(r"profit\s*(and|&)\s*loss|income statement")
_K_PNL_OTHER = re.compile(r"by class|by customer|by location|by job|detail|prev(ious)? year"
                          r"|ytd comparison|% of income", _A)
_K_SALES = re.compile(r"sales by (product|service|item)|product/service|item detail|sales detail", _A)
_K_AMOUNT = re.compile(r"^(amount|sales|total)\Z", _A)


def kind_of(rows: list[list[str]]) -> str:
    """Which report a file is, from its title block (the fingerprint of an export).

    Returns ``"pnl"``, ``"balance"``, ``"ar"``, ``"ap"``, ``"sales"``, ``"customers"``,
    ``"pnl:other"`` for a Profit and Loss in a version that cannot be read (by class,
    customer, location or job, a detail, a previous-year or YTD comparison, or "% of
    income" columns), or ``""`` when it is none of them.

    The title (first ten rows, lower case) is matched in this order: aging reports,
    balance sheet, profit and loss / income statement, sales by customer (not a
    detail), then the sales detail titles. Failing a title, a file whose first 30 rows
    include a "Date" heading next to an "Amount", "Sales" or "Total" heading is taken
    as a sales detail.
    """
    t = top(rows).lower()
    if _K_AR.search(t):
        return "ar"
    if _K_AP.search(t):
        return "ap"
    if "balance sheet" in t:
        return "balance"
    if _K_PNL.search(t):
        return "pnl:other" if _K_PNL_OTHER.search(t) else "pnl"
    if "sales by customer" in t and "detail" not in t:
        return "customers"
    if _K_SALES.search(t):
        return "sales"
    for r in rows[:30]:
        lo = [c.lower() for c in r]
        if "date" in lo and any(_K_AMOUNT.match(c) for c in lo):
            return "sales"
    return ""


def _label_of(r: list[str], until: int) -> str:
    """The first non-empty cell before column ``until``: a row's label."""
    for i in range(min(until, len(r))):
        if r[i]:
            return r[i]
    return ""


def _norm(s: Any) -> str:
    return _trim(_SPACES.sub(" ", str(s or "").lower()))


def _fail(kind: str, msg: str) -> dict:
    return {"kind": kind, "report": REPORTS.get(kind, ""), "ok": False, "error": msg,
            "warnings": [], "lines": [], "data": None}


# ---------------------------------------------------------------------------------------
# Profit and Loss, columns by month
# ---------------------------------------------------------------------------------------

_S_SKIP = re.compile(r"^(ordinary income/expense|other income/expense)\Z", _A)
_S_INCOME = re.compile(r"^(income|revenue)\Z", _A)
_S_COGS = re.compile(r"^cost of (goods sold|sales|revenue)\Z", _A)
_S_EXPENSE = re.compile(r"^(expenses?|operating expenses?)\Z", _A)
_S_OTHER_EXP = re.compile(r"^other expenses?\Z", _A)
_T_TOTAL = re.compile("^total (?:for )?(" + _DOT + r"+)\Z", _A)
_T_GROSS = re.compile(r"^(gross profit|gross margin)\Z", _A)
_T_ORDINARY = re.compile(r"^net (operating|ordinary) income\Z", _A)
_T_OTHERNET = re.compile(r"^net other income(/expense)?\Z", _A)
_T_NET = re.compile(r"^(net income|net profit|net earnings)\Z", _A)


def _section_of(low: str) -> str:
    """The section a heading row opens (normalised label), or "" for none."""
    if _S_SKIP.match(low):
        return ""
    if _S_INCOME.match(low):
        return "income"
    if _S_COGS.match(low):
        return "cogs"
    if _S_EXPENSE.match(low):
        return "expense"
    if low == "other income":
        return "otherIncome"
    if _S_OTHER_EXP.match(low):
        return "otherExpense"
    return ""


def _total_key(low: str) -> str | None:
    """Which total a row with numbers is ("income", "net", "sub" ...), or None for an account."""
    m = _T_TOTAL.match(low)
    if m:
        s = m[1]
        if _S_INCOME.match(s):
            return "income"
        if _S_COGS.match(s):
            return "cogs"
        if _S_EXPENSE.match(s):
            return "expense"
        if s == "other income":
            return "otherIncome"
        if _S_OTHER_EXP.match(s):
            return "otherExpense"
        return "sub"
    if _T_GROSS.match(low):
        return "gross"
    if _T_ORDINARY.match(low):
        return "ordinary"
    if _T_OTHERNET.match(low):
        return "otherNet"
    if _T_NET.match(low):
        return "net"
    return None


def read_pnl(rows: list[list[str]]) -> dict:
    """Read a Profit and Loss with one column per month into ``data.pnl``.

    Method: the heading row is the first of the first 30 with at least two month
    columns (:func:`month_key`); columns are put in date order and the "Total" column
    is ignored. Rows are labelled by their first cell left of the first month column.
    A label row with no numbers opens a section (Income, Cost of Goods Sold, Expenses,
    Other Income, Other Expenses; "Ordinary Income/Expense" and "Other Income/Expense"
    only group them). A row with numbers is either a total (``Total ...``, Gross
    Profit, Net Operating/Ordinary Income, Net Other Income, Net Income), kept for the
    check, or an account in the current section. Sub-account totals ("Total for
    Repairs") are recognised and not counted twice. Accounts give
    ``{account, group, values}`` with group income / cogs / expense / other; other
    expenses are negated so that Net Income = income - cogs - expense + other.
    Blank cells read as 0; values are rounded to the cent.

    The check: the accounts must add to the report's Total Income, and then to its
    Net Income, in every month, within the larger of $1 and 0.1%; otherwise the file
    is refused. With neither total present the accounts are taken as they are, with
    a warning.

    Caveats: a sub-account and its parent both carrying amounts are both counted (the
    check against the totals catches that). Rows with numbers before the first section
    are left out, with a count in the warnings. Month columns must not start in the
    first column (there would be no label column, and no account is found).
    """
    h, vc = -1, []
    for i in range(min(len(rows), 30)):
        cols = [(j, k) for j, k in ((j, month_key(c)) for j, c in enumerate(rows[i])) if k]
        if len(cols) >= 2:
            h, vc = i, cols
            break
    if h < 0:
        return _fail("pnl", "This Profit and Loss has no column for each month. In QuickBooks, set “Display columns by” to Month, then export it again.")
    vc = sorted(vc, key=lambda x: x[1])
    if len({x[1] for x in vc}) != len(vc):
        return _fail("pnl", "Some months appear twice (a comparison or a total column is mixed in). Export the Profit and Loss with columns by Month only.")
    first = min(x[0] for x in vc)
    months = [x[1] for x in vc]
    n = len(months)
    section, lines, tot, skipped = "", [], {}, 0
    for r in rows[h + 1:]:
        label = _label_of(r, first)
        if not label:
            continue
        vals = [num(_get(r, j)) for j, _ in vc]
        low = _norm(label)
        if not any(math.isfinite(x) for x in vals):
            s = _section_of(low)
            if s:
                section = s
            continue
        v = [x if math.isfinite(x) else 0 for x in vals]
        t = _total_key(low)
        if t:
            tot[t] = v
            continue
        if not section:
            skipped += 1
            continue
        lines.append({
            "account": _SPACES.sub(" ", label),
            "group": "other" if section in ("otherIncome", "otherExpense") else section,
            "values": [_round2(x) for x in ([-x for x in v] if section == "otherExpense" else v)],
        })
    if not lines:
        return _fail("pnl", "No accounts were found under Income, Cost of Goods Sold or Expenses. This does not look like a Profit and Loss by month.")
    if not any(ln["group"] == "income" for ln in lines):
        return _fail("pnl", "No income accounts were found (a section called Income). The Profit and Loss is needed with its Income section.")

    def g(grp: str, i: int) -> float:
        return _sum(ln["values"][i] for ln in lines if ln["group"] == grp)

    warnings: list[str] = []
    income = [g("income", i) for i in range(n)]
    net = [g("income", i) - g("cogs", i) - g("expense", i) + g("other", i) for i in range(n)]

    def miss(what: str, mine: list[float], rep: list[float]) -> str:
        for i in range(n):
            if abs(mine[i] - rep[i]) > max(1, 0.001 * abs(rep[i])):
                return (f"The accounts do not add up to the report's own {what} in {_month_label(months[i])}: "
                        f"they add to {_usd0(mine[i])} and the report says {_usd0(rep[i])}. The file may use a "
                        "layout this page cannot read. Export it again with columns by Month and no filters.")
        return ""

    bad, checked = "", []
    if "income" in tot:
        bad = miss("Total Income", income, tot["income"])
        checked.append("Total Income")
    if not bad and "net" in tot:
        bad = miss("Net Income", net, tot["net"])
        checked.append("Net Income")
    if bad:
        return _fail("pnl", bad)
    if not checked:
        warnings.append("The report has no Total Income or Net Income row to check the accounts against, so they are taken as they come.")
    if n < 12:
        warnings.append(f"Only {n} months. A year or more lets the seasons show; with less, they are thin.")
    if skipped:
        warnings.append(f"{skipped} row{'' if skipped == 1 else 's'} with numbers before the first section were left out.")
    last = min(12, n)
    inc12, net12 = _sum(income[-last:]), _sum(net[-last:])
    out_lines = [
        f"{n} months, {_month_label(months[0])} to {_month_label(months[n - 1])}; {len(lines)} accounts",
        f"Income, last {last} months: {_usd0(inc12)}; net income {_usd0(net12)}",
        ("The accounts add up to the report's own " + " and ".join(checked) + ", month by month.") if checked else "",
    ]
    return {"kind": "pnl", "report": REPORTS["pnl"], "ok": True, "warnings": warnings,
            "data": {"pnl": {"months": months, "lines": lines}},
            "lines": [x for x in out_lines if x],
            "summary": {"months": n, "from": months[0], "to": months[n - 1], "accounts": len(lines),
                        "income": inc12, "net": net12, "tied": len(checked) > 0}}


# ---------------------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------------------

_TOTAL_CELL = re.compile(r"^total\Z", _AI)


def _pick_col(rows: list[list[str]]) -> int | None:
    """The value column: the last of two or more date columns (a comparison layout), or
    a lone "Total" heading on a short row. None means "the last number on each row"."""
    for r in rows[:30]:
        mc, tc = [], []
        for j, c in enumerate(r):
            if month_key(c):
                mc.append(j)
            elif _TOTAL_CELL.match(c):
                tc.append(j)
        if len(mc) >= 2:
            return mc[-1]
        if len(tc) == 1 and len([c for c in r if c]) <= 3:
            return tc[0]
    return None


_B_CASH = [re.compile(r"^total (bank accounts|checking/savings|cash and cash equivalents|cash)\Z", _A),
           re.compile(r"^total (bank|cash)\b", _A),
           re.compile(r"^cash and cash equivalents\Z", _A)]
_B_AR = [re.compile(r"^total .*accounts receivable", _A), re.compile(r"accounts receivable", _A)]
_B_AP = [re.compile(r"^total .*accounts payable", _A), re.compile(r"accounts payable", _A)]


def read_balance(rows: list[list[str]]) -> dict:
    """Read a Balance Sheet into ``data.balance``: ``{asOf, cash, ar, ap}``.

    Method: each figure is the value of the first row whose label (first non-empty
    cell, lower case) matches, trying patterns in order. Cash: "Total Bank Accounts"
    (Online), "Total Checking/Savings" (Desktop), "Total Cash and Cash Equivalents" or
    "Total Cash", then any "Total Bank ..." / "Total Cash ...", then "Cash and Cash
    Equivalents". Receivable and payable: a "Total ... Accounts Receivable/Payable" row,
    else any row naming it. The value is read from the column :func:`_pick_col` finds,
    or else the last number on the row (Desktop indents labels and puts totals in a
    later column). The date is the title's "As of".

    Refused without a bank total. A missing receivable, payable or date is a warning;
    ``ar`` / ``ap`` are then None and ``asOf`` None.
    """
    col = _pick_col(rows)

    def value_of(r: list[str]) -> float:
        if col is not None:
            return num(_get(r, col))
        idx = next((i for i, c in enumerate(r) if c), -1)
        for j in range(len(r) - 1, idx, -1):
            v = num(r[j])
            if math.isfinite(v):
                return v
        return math.nan

    def find(patterns: list[re.Pattern]) -> float | None:
        for p in patterns:
            for r in rows:
                lab = _norm(_label_of(r, len(r)))
                if lab and p.search(lab):
                    v = value_of(r)
                    if math.isfinite(v):
                        return v
        return None

    cash, ar, ap = find(_B_CASH), find(_B_AR), find(_B_AP)
    if cash is None:
        return _fail("balance", "The bank accounts total was not found (a row called Total Bank Accounts, or Total Checking/Savings in Desktop). Export the Balance Sheet Standard for all accounts.")
    per = period_of(top(rows))
    warnings = []
    if not per["asOf"]:
        warnings.append("No “as of” date was found in the title, so the snapshot date is not updated.")
    if ar is None:
        warnings.append("No Accounts Receivable row was found.")
    if ap is None:
        warnings.append("No Accounts Payable row was found.")
    lines = [
        "Cash in the bank " + _usd0(cash) + (", as of " + per["asOf"] if per["asOf"] else ""),
        ("Owed by customers " + _usd0(ar) if ar is not None else "")
        + (("; " if ar is not None else "") + "owed to suppliers " + _usd0(ap) if ap is not None else ""),
    ]
    return {"kind": "balance", "report": REPORTS["balance"], "ok": True, "warnings": warnings,
            "data": {"balance": {"asOf": per["asOf"], "cash": _round2(cash),
                                 "ar": None if ar is None else _round2(ar),
                                 "ap": None if ap is None else _round2(ap)}},
            "lines": [x for x in lines if x],
            "summary": {"cash": cash, "ar": ar, "ap": ap, "asOf": per["asOf"]}}


# ---------------------------------------------------------------------------------------
# A/R and A/P Aging Summary
# ---------------------------------------------------------------------------------------

_BUCKET = _rx(r"^(current|\d+\s*[-–]\s*\d+(\s*days)?|(>|over|more than)\s*\d+(\s*days)?"
              r"|\d+\s*(\+|and over|and older|or more|plus)(\s*days)?)\Z", _AI)
_BN_CURRENT = re.compile(r"^current\Z", _AI)
_BN_RANGE = _rx(r"^(\d+)\s*[-–]\s*(\d+)")
_BN_OVER = _rx(r"^(?:>|over|more than)\s*(\d+)", _AI)
_BN_PLUS = _rx(r"^(\d+)\s*(?:\+|and over|and older|or more|plus)", _AI)
_BN_INCLUSIVE = _rx(r"^\d+\s*\+|and|or more|plus", _AI)
_BN_OVER_WORD = re.compile(r"^(>|over|more)", _AI)


def _bucket_name(c: str) -> str:
    """The snapshot's name for an aging column: "Current", "1 to 30 days", "Over 90 days".

    "91 and over" and "91+" include 91, so they become "Over 90 days"; "> 90" and
    "Over 90" stay "Over 90 days".
    """
    t = _trim(c)
    if _BN_CURRENT.match(t):
        return "Current"
    m = _BN_RANGE.match(t)
    if m:
        return m[1] + " to " + m[2] + " days"
    m = _BN_OVER.match(t) or _BN_PLUS.match(t)
    if m:
        k = int(m[1])
        inclusive = _BN_INCLUSIVE.search(t) and not _BN_OVER_WORD.match(t)
        return f"Over {k - 1 if inclusive else k} days"
    return t


def read_aging(rows: list[list[str]], kind: str) -> dict:
    """Read an A/R (``kind="ar"``) or A/P (``"ap"``) Aging Summary into ``{asOf, buckets}``.

    Method: the heading row is the first of the first 30 with three or more aging
    columns (Current, 1 - 30, 31 - 60, "91 and over", "> 90", "90+" ...). The buckets
    are the TOTAL row's amounts, as ``[name, amount]`` pairs in column order (see
    :func:`_bucket_name`). Without a TOTAL row the named rows are added up here, with a
    warning. If the buckets do not add to the report's Total column (within the larger
    of $1 and 0.1%) that is a warning, not a refusal. Customer and supplier names are
    only used to tell rows apart and never come out.
    """
    h, cols = -1, []
    for i in range(min(len(rows), 30)):
        c = [j for j, x in enumerate(rows[i]) if _BUCKET.match(_trim(x))]
        if len(c) >= 3:
            h, cols = i, c
            break
    if h < 0:
        return _fail(kind, "The aging columns (Current, 1 - 30, 31 - 60 …) were not found. Export the " + REPORTS[kind] + " again.")
    first = min(cols)
    total_col = next((j for j, c in enumerate(rows[h]) if _TOTAL_CELL.match(c)), -1)
    tot_row, acc, body = None, [0] * len(cols), 0
    for r in rows[h + 1:]:
        lab = _label_of(r, first)
        if not lab:
            continue
        vals = [num(_get(r, j)) for j in cols]
        if not any(math.isfinite(v) for v in vals):
            continue
        if _TOTAL_CELL.match(lab):
            tot_row = r
            continue
        for k, v in enumerate(vals):
            if math.isfinite(v):
                acc[k] += v
        body += 1
    if tot_row is not None:
        vals = []
        for j in cols:
            v = num(_get(tot_row, j))
            vals.append(v if math.isfinite(v) else 0)
    else:
        vals = acc
    buckets = [[_bucket_name(rows[h][j]), _round2(vals[k])] for k, j in enumerate(cols)]
    warnings = []
    total = _sum(vals)
    if tot_row is not None and total_col >= 0:
        t = num(_get(tot_row, total_col))
        if math.isfinite(t) and abs(t - total) > max(1, 0.001 * abs(t)):
            warnings.append("The buckets add to " + _usd0(total) + " but the report's Total says " + _usd0(t) + ".")
    if tot_row is None and not body:
        return _fail(kind, "No amounts were found under the aging columns.")
    if tot_row is None:
        warnings.append("No TOTAL row was found, so the rows were added up here.")
    per = period_of(top(rows))
    if not per["asOf"]:
        warnings.append("No “as of” date was found in the title.")
    who = "customers" if kind == "ar" else "suppliers"
    return {"kind": kind, "report": REPORTS[kind], "ok": True, "warnings": warnings,
            "data": {kind: {"asOf": per["asOf"], "buckets": buckets}},
            "lines": [_usd0(total) + " owed " + ("by" if kind == "ar" else "to") + " " + who
                      + (", as of " + per["asOf"] if per["asOf"] else ""),
                      " · ".join(b[0] + " " + _usd0(b[1]) for b in buckets)],
            "summary": {"total": total, "asOf": per["asOf"]}}


# ---------------------------------------------------------------------------------------
# Customers as shares only
# ---------------------------------------------------------------------------------------

def shares(amounts: list[float], once_share: float | None) -> dict | None:
    """How concentrated sales are across customers, without who they are.

    Method: positive amounts only, largest first. ``top20`` is the share of sales from
    the top 20% of customers (``round(0.2 n)`` of them), ``top1`` the largest customer's
    share, ``lorenz`` 51 points ``[share of customers, share of sales]`` from the largest
    customer down, and ``once`` the share of customers seen only once (passed in, since
    only a detail report can tell). Shares are rounded to 4 decimals. ``None`` with
    fewer than 5 customers, where shares would say too much about individuals.
    """
    a = sorted((v for v in amounts if v > 0), reverse=True)
    n, total = len(a), _sum(a)
    if n < 5 or total <= 0:
        return None
    cum, c = [], 0
    for v in a:
        c += v
        cum.append(c / total)

    def at(f: float) -> float:
        k = _js_round(f * n)
        return 0 if k <= 0 else cum[min(n, k) - 1]

    lorenz = [[_round4(i / 50), _round4(at(i / 50))] for i in range(51)]
    return {"count": n, "top20": _round4(at(0.2)), "top1": _round4(a[0] / total),
            "once": None if once_share is None else _round4(once_share), "lorenz": lorenz}


_C_AMOUNT = re.compile(r"^(total|amount|sales|total sales)\Z", _AI)


def read_customers(rows: list[list[str]]) -> dict:
    """Read a Sales by Customer Summary into ``data.customers`` (shares only).

    The amount column is the first "Total", "Amount", "Sales" or "Total Sales" heading
    that is not in the first column. Every labelled row with an amount except TOTAL is
    one customer. ``once`` stays None: the summary cannot tell repeat customers.
    Names are read only to tell rows apart and are dropped.
    """
    h, col = -1, -1
    for i in range(min(len(rows), 30)):
        j = next((j for j, c in enumerate(rows[i]) if _C_AMOUNT.match(c)), -1)
        if j > 0:
            h, col = i, j
            break
    if h < 0:
        return _fail("customers", "No Total or Amount column was found in this Sales by Customer Summary.")
    amts = []
    for r in rows[h + 1:]:
        lab = _label_of(r, col)
        if not lab or _TOTAL_CELL.match(lab):
            continue
        v = num(_get(r, col))
        if math.isfinite(v):
            amts.append(v)
    s = shares(amts, None)
    if not s:
        return _fail("customers", "Fewer than 5 customers with sales were found, so shares cannot be worked out.")
    return {"kind": "customers", "report": REPORTS["customers"], "ok": True,
            "warnings": ["The share of customers who came only once is not in this report, so it is left blank."],
            "data": {"customers": s},
            "lines": [f"{_locale(s['count'])} customers; the top 20% bring in {_locale(_js_round(s['top20'] * 100))}% of sales",
                      "Names were dropped as the file was read."],
            "summary": {"count": s["count"], "top20": s["top20"]}}


# ---------------------------------------------------------------------------------------
# Sales detail: weekly rows by unit and offering
# ---------------------------------------------------------------------------------------

_SD_DATE = re.compile(r"^(date|transaction date|trans date)\Z", _A)
_SD_AMOUNT = re.compile(r"^(amount|sales|sales amount|total sales)\Z", _A)
_SD_QTY = re.compile(r"^(qty|quantity)\Z", _A)
_SD_CUST = re.compile(r"^(customer|customer name|name|client|patient)\Z", _A)
_SD_UNIT = re.compile(r"^(class|location|department|employee|provider|staff|stylist|barber"
                      r"|technician|sales rep|rep|chair|dentist|doctor)\Z", _A)
_SD_ITEM = re.compile(r"^(product/service|product|service|item)\Z", _A)
_STARTS_TOTAL = re.compile(r"^total", _AI)


def _index(cells: list[str], test) -> int:
    return next((j for j, x in enumerate(cells) if test(x)), -1)


def read_sales(rows: list[list[str]], unit_words: Iterable[str] = ()) -> dict:
    """Read a sales detail into weekly rows by unit and offering (``data.sales``).

    Columns: the heading row is the first of the first 30 with a Date and an Amount
    heading. Optional: Qty (else 1 per line), the customer (Customer / Name / Client /
    Patient), the unit (Class, Location, Employee, Staff, Barber ... or one of
    ``unit_words``, the brain's word for a unit in singular and plural) and the item.
    Without an item column the offering is the group heading the lines sit under, as
    QuickBooks lays the report out; without a unit everything is one unit, "All sales".

    Lines: a row with a real date and an amount is a sale. A labelled row with no
    amount (not a "Total ...") starts a new group; an undated row with an amount is
    counted and skipped, with a warning.

    Weeks run Monday to Sunday. The period is the title's date range when it has one,
    else the first to the last sale (a last sale on a Friday to Sunday then ends its
    week). Only whole weeks inside the period are kept: lines in partial weeks at
    either edge are left out of the rows, with a warning. At least 12 whole weeks are
    needed, else the file is refused.

    Output: ``weeks`` (the Mondays), ``units`` and ``offerings`` sorted by sales, largest
    first (each offering's ``price`` is amount over quantity; ``costPct`` 0 and ``kind``
    "" until set), ``rows`` as ``[week, unit, offering, quantity, amount]`` sorted by
    week, unit, offering, and ``byMonth``: every calendar month lying wholly inside the
    period with the sum of *all* its dated lines (partial-week lines included), for the
    tie to the books. With a customer column, ``customers`` holds :func:`shares` of the
    kept lines; names are dropped.
    """
    words = {w for w in (_norm(x) for x in (unit_words or [])) if w}
    h, c = -1, {}
    for i in range(min(len(rows), 30)):
        lo = [_norm(x) for x in rows[i]]
        d = _index(lo, _SD_DATE.match)
        a = _index(lo, _SD_AMOUNT.match)
        if d >= 0 and a >= 0:
            h = i
            c = {"d": d, "a": a, "q": _index(lo, _SD_QTY.match), "cust": _index(lo, _SD_CUST.match),
                 "unit": _index(lo, lambda x: x in words or _SD_UNIT.match(x)),
                 "item": _index(lo, _SD_ITEM.match)}
            break
    if h < 0:
        return _fail("sales", "No Date and Amount columns were found. Export Sales by Product/Service Detail (in Desktop, Sales by Item Detail) with the Class or Location column shown.")
    tx, group, no_date = [], "", 0
    for r in rows[h + 1:]:
        day = day_key(_get(r, c["d"]))
        lab = _label_of(r, c["d"])
        a = num(_get(r, c["a"]))
        if day:
            if not math.isfinite(a):
                continue
            q = num(_get(r, c["q"])) if c["q"] >= 0 else math.nan
            unit = _get(r, c["unit"]) if c["unit"] >= 0 else None
            item = _get(r, c["item"]) if c["item"] >= 0 else None
            tx.append({"day": day, "amt": a, "qty": q if math.isfinite(q) else 1,
                       "unit": unit or "", "off": item or group or "All sales",
                       "cust": (_get(r, c["cust"]) or "") if c["cust"] >= 0 else ""})
        elif lab and not math.isfinite(a) and not _STARTS_TOTAL.match(lab):
            group = _SPACES.sub(" ", lab)
        elif any(r) and math.isfinite(a) and not _STARTS_TOTAL.match(lab):
            no_date += 1
    if not tx:
        return _fail("sales", "No dated sales lines were found under the Date column (dates like 03/05/2025 are read).")
    warnings = []
    per = period_of(top(rows))
    lo_day = min(t["day"] for t in tx)
    hi_day = max(t["day"] for t in tx)
    stated = bool(per["from"] and per["to"] and per["from"] <= per["to"])
    frm = _to_d(per["from"] if stated else lo_day)
    to = _to_d(per["to"] if stated else hi_day)
    if not (per["from"] and per["to"]) and to.weekday() >= 4:
        to = to + timedelta(days=6 - to.weekday())  # a Friday, Saturday or Sunday ends the last week
    first_mon = frm if frm.weekday() == 0 else frm + timedelta(days=7 - frm.weekday())
    last_sun = to if to.weekday() == 6 else to - timedelta(days=to.weekday() + 1)
    n_w = ((last_sun - first_mon).days + 1) // 7
    if n_w < 12:
        return _fail("sales", f"Only {max(0, n_w)} whole weeks of sales were found. At least 12 are needed to work out the cash range; a year is better. Export a longer period.")
    a_from, a_to = _iso(first_mon), _iso(last_sun)
    cut = [t for t in tx if a_from <= t["day"] <= a_to]
    if len(cut) < len(tx):
        warnings.append(_locale(len(tx) - len(cut)) + " lines in partial weeks at the start or end were left out of the weekly rows.")
    if c["unit"] < 0:
        warnings.append("No Class or Location column was found, so sales cannot be split by person or place. Show it in QuickBooks (Customize, Display columns), then export again.")
    if no_date:
        warnings.append(f"{no_date} rows with an amount but no date were skipped.")

    def unit_name(t: dict) -> str:
        return t["unit"] or "All sales"

    tot: dict[str, float] = {}
    for t in cut:
        tot[unit_name(t)] = tot.get(unit_name(t), 0) + t["amt"]
    units = sorted(tot, key=lambda u: -tot[u])
    u_idx = {u: i for i, u in enumerate(units)}
    o_agg: dict[str, dict] = {}
    for t in cut:
        o_agg.setdefault(t["off"], {"q": 0, "a": 0})
    for t in cut:
        o = o_agg[t["off"]]
        o["q"] += t["qty"]
        o["a"] += t["amt"]
    offs = sorted(o_agg, key=lambda o: -o_agg[o]["a"])
    o_idx = {o: i for i, o in enumerate(offs)}
    weeks = [_iso(first_mon + timedelta(days=7 * i)) for i in range(n_w)]
    cell: dict[tuple, list] = {}
    for t in cut:
        w = (_to_d(t["day"]) - first_mon).days // 7
        k = (w, u_idx[unit_name(t)], o_idx[t["off"]])
        e = cell.setdefault(k, [w, k[1], k[2], 0, 0])
        e[3] += t["qty"]
        e[4] += t["amt"]
    out = sorted(([e[0], e[1], e[2], _round2(e[3]), _round2(e[4])] for e in cell.values()),
                 key=lambda e: (e[0], e[1], e[2]))
    bm: dict[str, float] = {}
    for t in tx:
        m = t["day"][:7]
        bm[m] = bm.get(m, 0) + t["amt"]
    i_from, i_to = _iso(frm), _iso(to)
    by_month = []
    for m in sorted(bm):
        y, mo = int(m[:4]), int(m[5:])
        if _iso(_utc_date(y, mo, 1)) >= i_from and _iso(_last_of_month(y, mo)) <= i_to:
            by_month.append({"month": m, "amount": _round2(bm[m])})
    offerings = [{"name": o, "price": _round2(o_agg[o]["a"] / o_agg[o]["q"]) if o_agg[o]["q"] > 0 else 0,
                  "costPct": 0, "kind": ""} for o in offs]
    customers = None
    if c["cust"] >= 0:
        by: dict[str, dict] = {}
        for t in cut:
            if not t["cust"]:
                continue
            e = by.setdefault(t["cust"], {"a": 0, "n": 0})
            e["a"] += t["amt"]
            e["n"] += 1
        arr = list(by.values())
        customers = shares([e["a"] for e in arr],
                           len([e for e in arr if e["n"] == 1]) / len(arr) if arr else None)
        by.clear()
    total = _sum(t["amt"] for t in cut)
    lines = [
        f"{n_w} whole weeks, {weeks[0]} to {a_to}; {_locale(len(cut))} lines, {_usd0(total)} in sales",
        f"{len(units)} {'units' if c['unit'] >= 0 else 'unit'} ({', '.join(units[:4])}{', …' if len(units) > 4 else ''}), {len(offerings)} offerings",
        (_locale(customers["count"]) + " customers were counted for shares; their names were dropped as the file was read.")
        if customers else "No customer column, so customer shares are not filled.",
    ]
    return {"kind": "sales", "report": REPORTS["sales"], "ok": True, "warnings": warnings,
            "data": {"sales": {"grain": "week", "weeks": weeks, "units": units, "offerings": offerings,
                               "byMonth": by_month, "rows": out},
                     "customers": customers},
            "lines": [x for x in lines if x],
            "summary": {"weeks": n_w, "lines": len(cut), "sales": total, "units": len(units),
                        "offerings": len(offerings), "customers": customers["count"] if customers else 0}}


# ---------------------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------------------

def _utf16_prefix(s: str, units: int) -> str:
    """``String.prototype.slice(0, units)``: JavaScript counts UTF-16 code units."""
    return s.encode("utf-16-le", "surrogatepass")[:2 * units].decode("utf-16-le", "surrogatepass")


def read(text: str | None, name: str = "", *, unit_words: Iterable[str] = ()) -> dict:
    """Read one exported CSV file: detect the report (:func:`kind_of`) and read it.

    Returns a result dict, never raises for a bad file. Every result has ``kind``
    (``""`` when the report is not recognised), ``report`` (its display name), ``ok``,
    ``warnings``, ``lines`` (plain-language read-back), ``data`` (the snapshot piece, or
    None), ``name``, ``rows`` (non-blank rows read) and ``detail`` (the first line).
    A successful read adds ``summary``; a refusal has ``ok: False`` and ``error``, the
    reason in words for the owner. A file with fewer than three non-blank rows is
    refused before detection and its result has only ``name`` added, not ``rows`` or
    ``detail`` (as in the JS).

    ``unit_words`` are the brain's words for a unit (singular, plural), also accepted
    as the sales detail's unit column heading.
    """
    try:
        rows = parse_csv(text)
    except Exception:  # parse_csv does not raise on any text; kept for parity with the JS
        return {**_fail("", "The file could not be read as CSV."), "name": name}
    rows = [r for r in rows if any(r)]
    if len(rows) < 3:
        return {**_fail("", "The file is empty or has too few rows to be a report."), "name": name}
    kind = kind_of(rows)
    if kind == "pnl:other":
        out = _fail("pnl", "This Profit and Loss is a different version (by class, customer, detail or a comparison). Export Profit and Loss with columns by Month.")
    elif kind == "pnl":
        out = read_pnl(rows)
    elif kind == "balance":
        out = read_balance(rows)
    elif kind in ("ar", "ap"):
        out = read_aging(rows, kind)
    elif kind == "customers":
        out = read_customers(rows)
    elif kind == "sales":
        out = read_sales(rows, unit_words)
    else:
        first = next((c for c in rows[0] if c), "")
        out = _fail("", "This file was not recognized as one of the six reports. Expected a title like “Profit and Loss”, “Balance Sheet”, “A/R Aging Summary”, “A/P Aging Summary”, “Sales by Product/Service Detail” or “Sales by Customer Summary”. The first line says: “" + _utf16_prefix(first, 60) + "”.")
    out["name"] = name or ""
    out["rows"] = len(rows)
    lines = out.get("lines") or []
    out["detail"] = (lines[0] if lines else "") or ""
    return out


# ---------------------------------------------------------------------------------------
# Put the pieces into the snapshot
# ---------------------------------------------------------------------------------------

SLOTS = [
    {"key": "pnl", "label": "Profit and Loss", "how": "Reports, Profit and Loss. Show columns by Month, for the last 12 to 24 months. Export as CSV."},
    {"key": "balance", "label": "Balance Sheet", "how": "Reports, Balance Sheet (Standard), as of today. Export as CSV."},
    {"key": "sales", "label": "Sales detail", "how": "Reports, Sales by Product/Service Detail (Desktop: Sales by Item Detail), same period, with Class or Location shown. Export as CSV."},
    {"key": "ar", "label": "A/R Aging Summary", "how": "Reports, A/R Aging Summary, as of today. Export as CSV."},
    {"key": "ap", "label": "A/P Aging Summary", "how": "Reports, A/P Aging Summary, as of today. Export as CSV."},
    {"key": "customers", "label": "Sales by Customer Summary", "how": "Optional. The sales detail also gives customer shares when it shows the customer. Export as CSV.", "optional": True},
]
CORE = ["pnl", "balance", "sales"]

_R_PNL = re.compile(r"profit|p&l|income statement", _AI)
_R_BAL = re.compile(r"balance sheet", _AI)
_R_AR = re.compile(r"a/r|receivable", _AI)
_R_AP = re.compile(r"a/p|payable", _AI)
_R_NOT_SALES = re.compile(r"profit|p&l|balance|a/r|a/p|aging", _AI)
_R_CUST = re.compile(r"customer|patient|client|day sheet", _AI)


def apply(base: dict | None, slots: dict, *, now: str, system: str | None = None) -> dict:
    """Put the pieces read from the owner's files into a copy of a snapshot.

    ``slots`` maps a slot key (``pnl``, ``balance``, ``sales``, ``ar``, ``ap``,
    ``customers``) to a read result, or a dict with at least its ``data``, ``name``,
    ``report``, ``detail`` and ``rows``; slots without data are ignored. ``now`` is the
    upload time as an ISO string (the JS takes the browser clock when it is missing);
    ``system`` is the source system, "quickbooks" by default. ``base`` is not changed.

    Each file replaces its own piece and nothing else:

    * ``sources``: the lines a file supersedes are dropped (matched on the report name;
      for the sales detail and customers, lines from the base's own sales / customer
      system) and one line per file is added, marked as the owner's CSV export.
    * ``balance``: cash always, receivable and payable when the file has them, and the
      ``cash0`` fact is set to the new cash and marked unconfirmed.
    * ``sales``: unit and offering names take the base snapshot's spelling where they
      match ignoring case and spacing, and offerings keep the base's ``costPct`` and
      ``kind``. Notes name units with no pay terms and pay terms for units no longer in
      the sales. Customer shares from the sales detail fill ``customers`` unless a
      customer summary was given too.
    * ``meta``: ``asOf`` is the latest date the files state (balance, aging; else the
      Profit and Loss's last month end, else the last sales week), ``preparedOn`` the
      upload day, and ``sample`` stays true for a sample snapshot until all of
      :data:`CORE` (pnl, balance, sales) are the owner's. ``meta.uploaded`` records the
      time, the slots filled and the base pieces ``still`` as prepared.

    Returns ``{"data", "slots", "still", "notes"}``.
    """
    base = base if base is not None else {}
    slots = slots or {}
    key = system or "quickbooks"
    D = copy.deepcopy(base)
    got = [s["key"] for s in SLOTS if slots.get(s["key"]) and slots[s["key"]].get("data")]

    def has(k: str) -> bool:
        return k in got

    def piece(k: str):
        return copy.deepcopy(slots[k]["data"])

    notes: list[str] = []
    bs, bc = base.get("sales") or {}, base.get("customers") or {}
    sales_customers = has("sales") and slots["sales"]["data"].get("customers")

    def replaced(s: dict) -> bool:
        rep = str(s.get("report") or "")
        return bool(
            (has("pnl") and _R_PNL.search(rep)) or (has("balance") and _R_BAL.search(rep))
            or (has("ar") and _R_AR.search(rep)) or (has("ap") and _R_AP.search(rep))
            or (has("sales") and bs.get("system") and s.get("system") == bs.get("system") and not _R_NOT_SALES.search(rep))
            or ((has("customers") or sales_customers) and bc.get("system") and s.get("system") == bc.get("system")
                and _R_CUST.search(rep)))

    D["sources"] = [s for s in (D.get("sources") or []) if not replaced(s)]
    for k in got:
        sl = slots[k]
        detail = sl.get("detail") or ""
        name = sl.get("name")
        D["sources"].append({"system": key, "report": sl.get("report") or REPORTS[k],
                             "api": "CSV export brought in by the owner",
                             "detail": detail + (" · " if detail else "") + "file " + ("undefined" if name is None else str(name)),
                             "rows": sl.get("rows") or 0})
    if has("pnl"):
        D["pnl"] = piece("pnl")["pnl"]
    if has("balance"):
        b = piece("balance")["balance"]
        old = D.get("balance") or {}
        D["balance"] = {**old, "asOf": b.get("asOf") or old.get("asOf") or None, "cash": b["cash"]}
        if b.get("ar") is not None:
            D["balance"]["ar"] = b["ar"]
        if b.get("ap") is not None:
            D["balance"]["ap"] = b["ap"]
        src = "Balance Sheet, your file " + str(slots["balance"].get("name")) + (", as of " + b["asOf"] if b.get("asOf") else "")
        D["facts"] = D.get("facts") or []
        f = next((x for x in D["facts"] if x.get("k") == "cash0"), None)
        if f is not None:
            f["v"], f["src"], f["confirmed"] = b["cash"], src, False
        else:
            D["facts"].append({"k": "cash0", "l": "Cash in the bank today", "v": b["cash"], "u": "$",
                               "group": "cash", "src": src, "confirmed": False})
    if has("ar"):
        D["ar"] = piece("ar")["ar"]
    if has("ap"):
        D["ap"] = piece("ap")["ap"]
    if has("sales"):
        s = piece("sales")["sales"]
        bu = {_norm(n): n for n in (bs.get("units") or [])}
        bo = {_norm(o.get("name")): o for o in (bs.get("offerings") or [])}

        def offering(o: dict) -> dict:
            m = bo.get(_norm(o["name"]))
            return {**o, "name": m.get("name"), "costPct": m.get("costPct") or 0, "kind": m.get("kind") or ""} if m else o

        D["sales"] = {**s, "system": key, "units": [bu.get(_norm(n)) or n for n in s["units"]],
                      "offerings": [offering(o) for o in s["offerings"]]}
        terms = D.get("unitTerms") or {}
        no_terms = [n for n in D["sales"]["units"] if not terms.get(n)]
        if no_terms and terms:
            notes.append(f"{len(no_terms)} {'unit has' if len(no_terms) == 1 else 'units have'} no pay terms yet: "
                         + ", ".join(no_terms[:4]) + (", …" if len(no_terms) > 4 else "") + ".")
        lost = [n for n in terms if n not in D["sales"]["units"]]
        if lost:
            notes.append("Pay terms are set for " + ", ".join(lost[:3]) + (", …" if len(lost) > 3 else "")
                         + ", who " + ("is" if len(lost) == 1 else "are") + " not in the new sales.")
        costed = len([o for o in D["sales"]["offerings"] if (bo.get(_norm(o["name"])) or {}).get("costPct")])
        if not costed and D["sales"]["offerings"]:
            notes.append("Direct cost of each offering is not in these reports: it starts at 0% until set.")
        if sales_customers and not has("customers"):
            D["customers"] = {"system": key, **piece("sales")["customers"]}
    if has("customers"):
        D["customers"] = {"system": key, **piece("customers")["customers"]}
    if D.get("customers"):
        D["customers"]["system"] = D["customers"].get("system") or key
    if has("sales") and not has("customers") and not sales_customers:
        notes.append("The sales detail did not show the customer, so customer shares stay as they were.")
    stamp = now
    ds = []
    if has("balance") and D["balance"].get("asOf"):
        ds.append(D["balance"]["asOf"])
    if has("ar") and D["ar"].get("asOf"):
        ds.append(D["ar"]["asOf"])
    if has("ap") and D["ap"].get("asOf"):
        ds.append(D["ap"]["asOf"])
    if not ds and has("pnl"):
        m = D["pnl"]["months"][-1]
        ds.append(_iso(_last_of_month(int(m[:4]), int(m[5:]))))
    if not ds and has("sales"):
        ds.append(D["sales"]["weeks"][-1])
    bp, bsl = base.get("pnl"), base.get("sales")
    base_has = {"pnl": bool(bp and bp.get("months")), "balance": bool(base.get("balance")),
                "sales": bool(bsl and bsl.get("rows")), "ar": bool(base.get("ar")),
                "ap": bool(base.get("ap")), "customers": bool(base.get("customers"))}
    still = [s["key"] for s in SLOTS if not has(s["key"]) and base_has[s["key"]]
             and not (s["key"] == "customers" and sales_customers)]
    all_core = all(has(k) for k in CORE)
    sample = bool((base.get("meta") or {}).get("sample")) and not all_core
    meta = {**(D.get("meta") or {})}
    old_meta = D.get("meta") or {}
    meta.update({"asOf": max(ds) if ds else old_meta.get("asOf"), "preparedOn": stamp[:10], "sample": sample,
                 "uploaded": {"at": stamp, "slots": got, "still": still, "partSample": sample}})
    if not ds and "asOf" not in old_meta:
        del meta["asOf"]  # undefined in the JS, so JSON leaves it out
    D["meta"] = meta
    return {"data": D, "slots": got, "still": still, "notes": notes}
