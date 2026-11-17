"""What moves the monthly bottom line from today's setup to an idea (the FIS skeleton's `bridge`).

The groups of change are applied in a fixed order, each on top of the ones before:

1. sales volume (`shock`, `offer`)
2. prices, with the price response (`price`, `elast`)
3. units in or out (`added`, `keep`, and switching units on or off)
4. unit pay (`terms`)
5. cost rates (`rate`)
6. monthly bills (`fixed`)

Each step's `delta` is the change in `profit` it adds. Steps that move it by less than
`BRIDGE_MIN_STEP` dollars a month are left out (their change still counts towards the next
step's starting point), so `from + sum(deltas)` equals `to` within that rounding, and `to`
is the idea's profit. One-time cash, owner salary and purchases do not move the monthly
bottom line, so they have no step.

Caveat: with interacting changes the split depends on the order (a price rise counted before
a removal is worth more than after it). The order is fixed so the same idea always gives the
same steps. Kept out of compare.py, which is the parked advisor product's.
"""

from __future__ import annotations

import copy

from .business_config import BRIDGE_MIN_STEP
from .jsmath import plain
from .levers import norm_idea
from .model import evaluate, term_of


def _clone(x):
    """The skeleton's `clone` is a JSON round trip: NaN becomes null, functions vanish."""
    return copy.deepcopy(plain(x))


def bridge(M: dict, today: dict | None, idea: dict | None) -> dict:
    """`{from, to, steps: [{label, delta}]}` from `today` to `idea`. See the module docstring."""
    today = norm_idea(M, today)
    idea = norm_idea(M, idea)
    p = _clone(today)
    steps: list[dict] = []
    prev = evaluate(M, p)["profit"]

    def step(label, change):
        nonlocal prev
        change(p)
        now = evaluate(M, p)["profit"]
        if abs(now - prev) >= BRIDGE_MIN_STEP:
            steps.append({"label": label, "delta": now - prev})
        prev = now

    def volume(x):
        x["shock"] = idea.get("shock")
        x["offer"] = _clone(idea.get("offer") or {})

    def prices(x):
        x["price"] = _clone(idea.get("price") or {})
        x["elast"] = idea.get("elast")

    def units(x):
        x["added"] = _clone(idea.get("added") or [])
        x["keep"] = idea.get("keep")
        # units switched on or off by the idea (or by today's setup), without their pay changes
        names = dict.fromkeys(list(_keys(idea.get("terms"))) + list(_keys(today.get("terms"))))
        for n in names:
            ti, tt = term_of(M, idea, n), term_of(M, x, n)
            if (ti.get("on") is not False) != (tt.get("on") is not False):
                x["terms"][n] = {**(x["terms"].get(n) or {}), "on": ti.get("on") is not False}

    def pay(x):
        x["terms"] = _clone(idea.get("terms") or {})

    def rates(x):
        x["rate"] = _clone(idea.get("rate") or {})

    def bills(x):
        x["fixed"] = _clone(idea.get("fixed") or [])

    V = M["V"]
    step("Sales volume", volume)
    step("Prices", prices)
    step(V["Units"] + " in or out", units)
    step(V["Unit"] + " pay" if V["people"] else V["Unit"] + " terms", pay)
    step("Cost rates", rates)
    step("Monthly bills", bills)
    return {"from": evaluate(M, today)["profit"], "to": prev, "steps": steps}


def _keys(obj) -> list:
    """`Object.keys(obj || {})` for an object; anything else has no keys here."""
    return list(obj) if isinstance(obj, dict) else []
