"""The federal income tax estimate: a placeholder until Phase 2, step 7 ports it.

The measures, panels and brain check ask `tax_of` for the tax picture. With the brain's
`levers.taxes` off (the default), there is none, exactly as in the FIS skeleton: tax measures
read "Taxes are switched off in the brain" and the tax panel is not offered. With it on, the
estimate is not worked out yet and says so plainly, rather than showing a number.
"""

from __future__ import annotations

from .jsmath import get, truthy
from .levers import levers_on

NOT_PORTED = "The tax estimate is not in this version of the engine yet."


def tax_of(M: dict, T: dict | None = None) -> dict | None:
    """None when the brain has taxes off. Otherwise `{ok: False, why}`: the skeleton's own two
    first reasons (no tax profile; no Profit and Loss), word for word, and until step 7, that
    the estimate is not ported yet."""
    if not M or not truthy(levers_on(M["BRAIN"]).get("taxes")):
        return None
    TX = M["DATA"].get("tax")
    if not truthy(get(TX, "profile")):
        return {"ok": False, "why": "No tax profile yet. It holds what the books do not: how the business is taxed, when its tax year ends and what has already been paid."}
    if not M["pnl"]["months"]:
        return {"ok": False, "why": "Needs the Profit and Loss by month."}
    return {"ok": False, "why": NOT_PORTED}
