"""An idea: the change an owner wants to try, against today's setup.

An idea is plain data, so it can be saved, compared and suggested by the assistant (which
proposes ideas; the engine computes every result):

    name      a label
    terms     {unit name: {on, type, rate, rent, wage}}: pay changes, or on: False to remove a unit
    added     [{name, mo (sales a month), type, rate | rent | wage}]: new units
    keep      % of a removed unit's sales that stays with the others (0 to 100)
    price     {offering name: new price}
    elast     % change in quantity for each +1% in price (the brain's assumption by default)
    shock     % change in all sales
    offer     {offering name: % change in quantity sold}
    fixed     [{l, mo}]: monthly bills added (negative for a saving)
    rate      {rate fact key: new % of sales}
    once      [{l, amt, wk}]: one-time cash in (or out, negative) in week wk of the range
    salary    dollars a month moved from the owner's draw into owner salary (tax only)
    assets    [{l, cost, wk}]: equipment bought in week wk (cash out; cost spread for tax)

A brain can switch levers off (`levers`); an idea from the assistant is limited to the
levers that are on. Defaults are in business_config.LEVER_DEFAULTS (DRAFT).
"""

from __future__ import annotations

from .business_config import ASSUMPTION_DEFAULTS, LEVER_DEFAULTS

FOCUS = ["sales", "profit", "units", "cash"]


def blank_idea(M: dict | None = None) -> dict:
    """An idea that changes nothing (today's setup), with the model's price response."""
    el = M["A"]["elasticity"] if M and M.get("A") else ASSUMPTION_DEFAULTS["elasticity"]
    return {"name": "", "terms": {}, "added": [], "keep": 0, "price": {}, "elast": el, "shock": 0, "offer": {},
            "fixed": [], "rate": {}, "once": [], "salary": 0, "assets": []}


def norm_idea(M: dict | None, idea: dict | None) -> dict:
    """`idea` with every missing part filled from the blank idea (extra keys are kept).

    An empty part (null, false, 0, "", an empty list or object) means no change, as it does
    in the skeleton, so it is filled from the blank idea too."""
    out = blank_idea(M)
    for k, v in (idea.items() if isinstance(idea, dict) else ()):
        if k in _MAPS + _LISTS and _empty(v):
            continue
        out[k] = v
    return out


_MAPS = ("price", "offer", "rate", "terms")
_LISTS = ("added", "fixed", "once", "assets")


def _empty(v) -> bool:
    """null, false, 0, "", [] or {}: in the skeleton each of these means "no change"."""
    return v is None or v is False or v == 0 or v == "" or v == [] or v == {}


def check_idea(idea) -> None:
    """Refuse an idea whose shape is wrong, with a plain reason.

    Values inside a right shape are read the way the skeleton reads them (text numbers,
    out-of-range percents clamped, unknown names ignored), and an empty part means no change.
    A wrong shape is refused, where the skeleton either throws (`added: "two more"`) or
    silently ignores the change (`price: "cheaper"`): an instruction the model cannot carry
    out should say so, not vanish.
    """
    if _empty(idea):
        return
    if not isinstance(idea, dict):
        raise ValueError("An idea is an object of changes (price, terms, added, ...).")
    for key in _MAPS:
        v = idea.get(key)
        if not _empty(v) and not isinstance(v, dict):
            raise ValueError(f"The idea's \u201c{key}\u201d must map names to values.")
    for name, t in (idea.get("terms") or {}).items() if isinstance(idea.get("terms"), dict) else ():
        if t is not None and not isinstance(t, dict):  # null: no change for that unit
            raise ValueError(f"The idea's pay change for \u201c{name}\u201d must be an object (on, type, rate, rent, wage).")
    for key in _LISTS:
        v = idea.get(key)
        if not _empty(v) and (not isinstance(v, list) or not all(isinstance(x, dict) for x in v)):
            raise ValueError(f"The idea's \u201c{key}\u201d must be a list of objects.")
    for a in idea.get("added") or []:
        if not _empty(a.get("name")) and not isinstance(a.get("name"), str):
            raise ValueError("An added unit's name must be text.")


def levers_on(brain: dict) -> dict:
    """Which levers this company may model: the defaults, with the brain's switches over them."""
    lv = brain.get("levers")
    return {**LEVER_DEFAULTS, **(lv if isinstance(lv, dict) else {})}
