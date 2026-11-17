"""The owner's numbers: what the models need that no report holds, each with its source.

A fact is `{k, l, v, u, group, when, src, confirmed}`:
- `group` "cash" (cash0, floor, draw), "rate" (a cost that moves with sales, as a % of
  sales) or "fixed" (a monthly bill; `when` is "first" for bills paid on the 1st, "even"
  for ones paid through the month).
- `src` says where the figure came from (a report and account, or "placeholder until
  the owner sets it"); `confirmed` is false until the owner confirms it.

Store answers, not results: the owner's corrections are kept as rows and the model is
rebuilt from the prepared facts plus the latest correction of each, on every read
(`latest_corrections`). Nothing here overwrites a prepared fact.
"""

from __future__ import annotations

from .jsmath import fin

GROUPS = ("cash", "rate", "fixed")
REQUIRED = ("cash0", "floor", "draw")


def resolve(facts: list[dict] | None, corrections: dict | None = None,
            confirmed: dict | None = None) -> tuple[list[dict], dict]:
    """The facts in force: (list of fact copies, {key: value}).

    A correction replaces a fact's value when it is a finite number. `confirmed` sets a
    fact's confirmed flag (None leaves it as prepared). The three cash facts are always
    present; one that is missing or not a number counts as 0.
    """
    fl = [dict(f) for f in (facts or [])]
    values: dict = {}
    for f in fl:
        o = (corrections or {}).get(f.get("k"))
        values[f.get("k")] = o if fin(o) else f.get("v")
        if confirmed and confirmed.get(f.get("k")) is not None:
            f["confirmed"] = bool(confirmed[f.get("k")])
    for k in REQUIRED:
        if not fin(values.get(k)):
            values[k] = 0
    return fl, values


def latest_corrections(rows: list[dict]) -> tuple[dict, dict]:
    """The owner's correction rows -> (corrections, confirmed) for `resolve`.

    Each row is `{k, v, confirmed, recorded_at}`; the latest row per fact wins (by
    `recorded_at`, then by position). A row with `v` None retracts the correction.
    """
    latest: dict = {}
    for i, r in enumerate(rows):
        key = r.get("k")
        stamp = (r.get("recorded_at") or "", i)
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, r)
    corrections, confirmed = {}, {}
    for key, (_, r) in latest.items():
        if r.get("v") is not None:
            corrections[key] = r["v"]
        if r.get("confirmed") is not None:
            confirmed[key] = bool(r["confirmed"])
    return corrections, confirmed


def validate(facts: list[dict]) -> list[str]:
    """Plain reasons a fact list is not usable; empty when it is."""
    out = []
    seen = set()
    for f in facts or []:
        k = f.get("k")
        if not k or not f.get("l"):
            out.append("A fact needs a key (k) and a label (l).")
            continue
        if k in seen:
            out.append(f"The fact “{k}” is listed twice.")
        seen.add(k)
        if f.get("group") not in GROUPS:
            out.append(f"The fact “{k}” has group “{f.get('group')}”; it must be cash, rate or fixed.")
        if not f.get("src"):
            out.append(f"The fact “{k}” has no source (src).")
    for k in REQUIRED:
        if k not in seen:
            out.append(f"The required fact “{k}” is missing.")
    return out
