"""Sales detail: weekly rows by unit and offering, and what they say about the business.

Input is the snapshot's `sales` table, shaped like QuickBooks line items rolled up by week:
`weeks` (Mondays), `units` (Class, Location or Employee names), `offerings` (Products and
Services, with `price` and `costPct`), `rows` of `[week, unit, offering, quantity, amount]`
and `byMonth` (the detail's exact monthly totals, for the tie to the books).

From it come the weekly totals, each unit's and offering's sales a month over the window,
the fit the cash range runs on (seasons, week-to-week swing, carry-over, trend) and
whether the detail ties to the Profit and Loss.

Method notes:
- A month is 30.4375 days (365.25 / 12), so "a month" over a window of n weeks is n*7/30.4375.
- A week's season is the calendar month of its Thursday (Monday + 3 days). A month gets a
  season factor (its weeks' mean over the mean of all weeks) only with at least
  `SEASON_MIN_WEEKS` weeks; otherwise it counts as 1. With one year of history this is
  one observation per month, so a season factor is also that month's noise.
- The swing (`vol`) is the standard deviation of log weekly sales after taking the
  seasons out, and `phi` the lag-1 carry-over of those logs (least squares through the
  origin, clamped to [0, `PHI_MAX`]). Both need `FIT_MIN_WEEKS` weeks.
- The trend compares the last 13 weeks with the same 13 a year earlier when there are
  `TREND_YEAR_WEEKS` weeks; otherwise with the 13 before, seasons left in (one year of
  history cannot separate a trend from a season), which says so (`trendKind`).
"""

from __future__ import annotations

import math

from .business_config import (
    ACTIVE_WEEKS, FIT_MIN_WEEKS, PHI_MAX, SEASON_MIN_WEEKS, TIE_TOLERANCE, TREND_QUARTER_WEEKS, TREND_WEEKS,
    TREND_YEAR_WEEKS, WINDOW_WEEKS, YEAR_WEEKS,
)
from .jsmath import NAN, add_days, clamp, fin, js_div, js_or, mean, parse_day, sd, to_number, total

DAYS_PER_MONTH = 30.4375


def weekly(sales: dict) -> list[dict]:
    """One entry per week: `{i, dt, s (sales), n (tickets)}`. Rows naming a week that
    does not exist are ignored."""
    weeks = [{"i": i, "dt": parse_day(w), "s": 0.0, "n": 0.0} for i, w in enumerate(sales["weeks"])]
    for r in sales["rows"]:
        w = weeks[int(r[0])]  # indexes are checked by model.check_snapshot
        w["s"] += to_number(r[4])  # a null amount counts as 0, as in JavaScript
        w["n"] += to_number(r[3])
    return weeks


def window(n_weeks: int, basis: int) -> dict:
    """The weeks averaged into "a month": the last 52 (basis 12) or 13 (basis 3)."""
    n_win = min(n_weeks, WINDOW_WEEKS[basis])
    return {"w0": n_weeks - n_win, "nWin": n_win, "months": (n_win * 7 / DAYS_PER_MONTH) or 1}


def units_and_offerings(sales: dict, weeks: list[dict], w0: int, months: float) -> dict:
    """Each unit's and offering's sales and quantity a month over the window.

    Units with no sales ever are dropped; units are sorted by sales a month, highest first
    (ties keep the snapshot's order). `active` is a sale in the last `ACTIVE_WEEKS` weeks,
    `inWindow` a sale inside the window. An offering's price is its list price, or its
    average price in the window when none is given. `blendCost` is the sales-weighted
    direct cost rate.
    """
    n_w = len(weeks)
    n_o = len(sales["offerings"])
    U = [{"i": i, "name": name, "rev": 0.0, "qty": 0.0, "svc": [0.0] * n_o, "first": None, "last": None}
         for i, name in enumerate(sales["units"])]
    O = [{"i": i, "name": o.get("name"), "price": o.get("price"), "cost": to_number(js_or(o.get("costPct"), 0)) / 100,
          "kind": o.get("kind") or "", "rev": 0.0, "qty": 0.0} for i, o in enumerate(sales["offerings"])]
    for r in sales["rows"]:
        wk, ui, oi = int(r[0]), int(r[1]), int(r[2])  # indexes are checked by model.check_snapshot
        u, o = U[ui], O[oi]
        if u["first"] is None or wk < u["first"]:
            u["first"] = wk
        if u["last"] is None or wk > u["last"]:
            u["last"] = wk
        if wk >= w0:
            amount, qty = to_number(r[4]), to_number(r[3])
            u["rev"] += amount
            u["qty"] += qty
            u["svc"][oi] += amount
            o["rev"] += amount
            o["qty"] += qty
    tot = total(u["rev"] for u in U)
    units = [{
        "name": u["name"], "mo": u["rev"] / months, "qty": u["qty"] / months, "svc": [v / months for v in u["svc"]],
        "share": u["rev"] / tot if tot > 0 else 0,
        "first": None if u["first"] is None else weeks[u["first"]]["dt"],
        "last": None if u["last"] is None else weeks[u["last"]]["dt"],
        "active": u["last"] is not None and u["last"] >= n_w - ACTIVE_WEEKS,
        "inWindow": u["rev"] > 0,
    } for u in U if u["rev"] > 0 or u["last"] is not None]
    units.sort(key=lambda u: -u["mo"])
    off = [{
        "i": o["i"], "name": o["name"], "kind": o["kind"], "cost": o["cost"],
        "price": o["price"] if fin(o["price"]) and o["price"] > 0 else (o["rev"] / o["qty"] if o["qty"] else 0),
        "mo": o["rev"] / months, "n": o["qty"] / months,
    } for o in O]
    offerings = sorted(off, key=lambda o: -o["mo"])
    blend = total(o["rev"] * o["cost"] for o in O) / tot if tot > 0 else 0
    return {"units": units, "off": off, "offerings": offerings, "blendCost": blend}


def season_factor(fit: dict, dt) -> float:
    """The season factor for the week starting `dt` (1 when that month has none)."""
    v = fit["season"][add_days(dt, 3).month - 1]
    return 1 if v is None else v


def fit(weeks: list[dict], w0: int) -> dict:
    """Level, seasons, swing, carry-over and trend of weekly sales (see the module notes)."""
    out: dict = {"season": [None] * 12}
    m_all = mean(w["s"] for w in weeks)
    by: list[list] = [[] for _ in range(12)]
    for w in weeks:
        by[add_days(w["dt"], 3).month - 1].append(w["s"])
    for i, a in enumerate(by):
        if len(a) >= SEASON_MIN_WEEKS and m_all > 0:
            out["season"][i] = mean(a) / m_all
    out["seasonN"] = sum(1 for v in out["season"] if v is not None)  # a count, so an int
    n_w = len(weeks)
    if n_w >= FIT_MIN_WEEKS:
        # a month with no sales has a season factor of 0: JavaScript's 0/0 is NaN, dropped here
        adj = [v for v in (js_div(w["s"], season_factor(out, w["dt"])) for w in weeks) if v > 0]
        mu = mean(adj)
        lr = [math.log(v / mu) for v in adj]
        out["vol"] = sd(lr)
        nu = de = 0.0
        for i in range(1, len(lr)):
            nu += lr[i] * lr[i - 1]
            de += lr[i - 1] * lr[i - 1]
        out["phi"] = clamp(nu / de if de > 0 else 0, 0, PHI_MAX)
    else:
        out["vol"] = NAN
        out["phi"] = 0
    win = weeks[w0:]
    out["weeksN"] = n_w
    out["salesWk"] = mean(w["s"] for w in win)
    out["qtyWk"] = mean(w["n"] for w in win)
    n_sum = total(w["n"] for w in win)
    out["avgTicket"] = total(w["s"] for w in win) / n_sum if n_sum > 0 else NAN
    raw = mean(w["s"] for w in win)
    # Skeleton behaviour, kept for parity: a window holding a month with no sales gives
    # NaN here (0/0), and with it a cash range of NaN. Flagged in docs/phase-2.md.
    des = mean(js_div(w["s"], season_factor(out, w["dt"])) for w in win)
    out["deseason"] = des / raw if raw > 0 else 1
    latest = [w["s"] for w in weeks[-TREND_WEEKS:]]
    if n_w >= TREND_YEAR_WEEKS:
        a, b = mean(latest), mean(w["s"] for w in weeks[-TREND_YEAR_WEEKS:-YEAR_WEEKS])
        out["trend"], out["trendKind"] = (a / b - 1 if b > 0 else NAN), "year"
    elif n_w >= TREND_QUARTER_WEEKS:
        a, b = mean(latest), mean(w["s"] for w in weeks[-TREND_QUARTER_WEEKS:-TREND_WEEKS])
        out["trend"], out["trendKind"] = (a / b - 1 if b > 0 else NAN), "quarter"
    else:
        out["trend"], out["trendKind"] = NAN, ""
    return out


def coverage(pnl_months: list[dict], by_month: list[dict] | None) -> dict | None:
    """The detail's monthly totals against Profit and Loss income, over the months both
    have: `{ratio, detail, books, months}`, or None when they share none (or the books
    show no income)."""
    if not pnl_months or not by_month:
        return None
    bm = {x["month"]: to_number(x["amount"]) for x in by_month}
    d = b = 0.0
    n = 0
    for m in pnl_months:
        if m["key"] in bm:
            d += bm[m["key"]]
            b += m["income"]
            n += 1
    if n and b > 0:
        return {"ratio": d / b, "detail": d, "books": b, "months": n}
    return None


def tie_to_books(cov: dict | None) -> dict:
    """Does the detail tie to the books? A named result, because the next phase gates on it.

    `status` is "ties" (detail within `TIE_TOLERANCE` of book income over the shared
    months), "does_not_tie" (find out why before going further: a second point of sale,
    booth renters whose sales are not the shop's income, cash against accrual basis; a
    real gap belongs in the brain's coverage note) or "cannot_check" (no shared months).
    """
    if cov is None:
        return {"status": "cannot_check", "ties": None, "ratio": None, "gap": None, "detail": None,
                "books": None, "months": 0, "tolerance": TIE_TOLERANCE}
    ties = abs(cov["ratio"] - 1) <= TIE_TOLERANCE
    return {"status": "ties" if ties else "does_not_tie", "ties": ties, "ratio": cov["ratio"],
            "gap": cov["detail"] - cov["books"], "detail": cov["detail"], "books": cov["books"],
            "months": cov["months"], "tolerance": TIE_TOLERANCE}
