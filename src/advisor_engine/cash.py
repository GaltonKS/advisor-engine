"""The next weeks in the bank, as a range (the FIS skeleton's `cash`).

Each simulated path starts from today's cash (`cash0`) and adds, week by week:

    swinging income  V * season(week) * exp(vol * z - vol**2 / 2)
    + steady income  (rent received less wages paid less bills paid through the month) * 12/52
    - bills paid on the 1st, in the week that holds the 1st
    - the owner's draw * 12/52
    + one-time amounts in their week (an idea's `once`; an idea's `assets` go out)
    - tax payments in their week (only when the tax picture is passed in)

where V is the business's sales-driven monthly take (`varMo`) as a weekly figure, with the
seasons taken out of the window's level (`deseason`), and z is a standard normal with
week-to-week carry-over `phi`: z' = phi*z + sqrt(1 - phi**2) * e. The lognormal factor has
mean 1, so the swing does not move the middle of the range.

The range is the 10th, 50th and 90th percentile across paths in each week, and the chance
that a path dips under the cash floor at any point.

Every idea is run on the same draws (seed `CASH_SEED`, `paths` paths), so two ideas differ
only by what the idea changes, and a result is repeatable. The seed and path count are on
the result.

Caveats: the swing and carry-over come from one history of weekly sales; bills, rent and
wages are taken as certain; collections timing (A/R) is not modelled; nothing beyond the
horizon is said.
"""

from __future__ import annotations

import math

from .business_config import CASH_SEED, METHOD_STATUS, METHOD_VERSION
from .jsmath import add_days, clamp, finite, js_or, js_round, n2, quant, total
from .rand import gauss, rng
from .sales import season_factor


def cash_range(M: dict, E: dict, *, paths: int | None = None, tax: dict | None = None) -> dict | None:
    """The cash range for an evaluated idea `E` on model `M`, or None without enough
    weekly sales to measure the swing. See the module docstring."""
    f = M["fit"]
    if not finite(f["vol"]) or not M["weeks"]:
        return None
    H = M["A"]["horizonWeeks"]
    N = paths or M["A"]["paths"]
    g = gauss(rng(CASH_SEED))
    F = M["F"]
    last = M["weeks"][-1]["dt"]
    starts = [add_days(last, 7 * w) for w in range(1, H + 1)]
    bills = [E["fixedFirst"] if any(add_days(s, i).day == 1 for i in range(7)) else 0 for s in starts]
    fac = [season_factor(f, s) for s in starts]
    V = E["varMo"] * 12 / 52 * f["deseason"]
    steady = (E["steadyMo"] - E["fixedEven"]) * 12 / 52
    draw_wk = F["draw"] * 12 / 52
    ph = f["phi"] or 0
    sg = f["vol"]
    once = [0] * H
    idea = E.get("idea") or {}
    for o in idea.get("once") or []:
        w = int(clamp(js_round(js_or(n2(o.get("wk")), 1)), 1, H))
        a = n2(o.get("amt"))
        if finite(a):
            once[w - 1] += a
    for o in idea.get("assets") or []:
        w = int(clamp(js_round(js_or(n2(o.get("wk")), 1)), 1, H))
        a = n2(o.get("cost"))
        if finite(a) and a > 0:
            once[w - 1] -= a
    tax_out = [0] * H
    if tax and tax.get("ok"):
        for p in tax.get("payments") or []:
            for w, s in enumerate(starts):
                if s <= p["date"] < add_days(s, 7):
                    once[w] -= p["amount"]
                    tax_out[w] += p["amount"]
    root = math.sqrt(1 - ph * ph)
    half = sg * sg / 2
    cols = [[0.0] * N for _ in range(H + 1)]
    breach = 0
    for p in range(N):
        z = g()
        c = F["cash0"]
        mn = c
        cols[0][p] = c
        for w in range(H):
            z = ph * z + root * g()
            c += V * fac[w] * math.exp(sg * z - half) + steady - bills[w] - draw_wk + once[w]
            cols[w + 1][p] = c
            if c < mn:
                mn = c
        if mn < F["floor"]:
            breach += 1

    def qs(x):
        return [quant(col, x) for col in cols]

    return {
        "dates": [add_days(last, 7)] + [add_days(s, 7) for s in starts],
        "p10": qs(0.1), "p50": qs(0.5), "p90": qs(0.9), "pBreach": breach / N, "H": H, "floor": F["floor"],
        "taxOut": tax_out, "taxTotal": total(tax_out),
        "seed": CASH_SEED, "paths": N, "method_version": METHOD_VERSION, "method_status": METHOD_STATUS,
    }
