"""A company's words and pay kinds, resolved from its brain (company config, not method).

The engine never changes per company; the brain renames its nouns (a unit is a barber, a
technician or a truck) and says which pay kinds its sector uses. These two functions turn
a brain into the resolved form the engine reads. Missing entries fall back to neutral
defaults, never to another company's words.
"""

from __future__ import annotations

from .business_config import TERM_TYPE_DEFAULTS


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def vocab(brain: dict | None) -> dict:
    """The brain's words for unit, offering, ticket and customer, singular and plural.

    Each entry in `brain["vocab"]` is `[one, many]` or a single word (plural adds "s").
    `people` is False when a unit is not a person (a location, a truck), which hides
    take-home pay on screen.
    """
    v = (brain or {}).get("vocab") or {}
    co = (brain or {}).get("company") or {}

    def pair(x, d):
        if isinstance(x, list) and len(x) == 2:
            return x
        if isinstance(x, str) and x:
            return [x, x + "s"]
        return d

    u = pair(v.get("unit"), ["team member", "team members"])
    o = pair(v.get("offering"), ["service", "services"])
    t = pair(v.get("ticket"), ["sale", "sales"])
    c = pair(v.get("customer"), ["customer", "customers"])
    business = v.get("business") or "business"
    return {
        "unit": u[0], "units": u[1], "Unit": _cap(u[0]), "Units": _cap(u[1]),
        "offering": o[0], "offerings": o[1], "Offering": _cap(o[0]), "Offerings": _cap(o[1]),
        "ticket": t[0], "tickets": t[1], "Ticket": _cap(t[0]), "Tickets": _cap(t[1]),
        "customer": c[0], "customers": c[1], "Customers": _cap(c[1]),
        "business": business, "Business": _cap(business), "owner": co.get("owner") or "the owner",
        "people": v.get("unitsArePeople") is not False,
    }


def term_types(brain: dict) -> dict:
    """The pay kinds this company offers, in the brain's order, each with its label and
    starting figure (the brain's, over the library defaults).

    share: the unit takes a % of its own sales; the business keeps the rest and bears the costs.
    fixedIn: the unit pays the business a fixed amount and keeps its sales (booth rent).
    wage: the business pays the unit a fixed amount and keeps the sales (salary).
    none: nothing changes hands (a product line, a channel). Used only when nothing else is.
    Kinds the library does not know are ignored.
    """
    terms = brain.get("terms")
    src = (terms.get("types") if terms else None)
    if src is None:
        src = {"share": {}, "wage": {}}
    out = {k: {**TERM_TYPE_DEFAULTS[k], **(src[k] or {})} for k in src if k in TERM_TYPE_DEFAULTS}
    if not out:
        out["none"] = dict(TERM_TYPE_DEFAULTS["none"])
    return out
