"""The Overview's panels (the FIS skeleton's PANELS) and which picture answers a question.

Which panels (charts and tables under the headline numbers) exist, which can be drawn on a
company's data, and which four to show: the owner picks a ready-made layout (the brain's own
first, then the library's) or any four from the list, the same way as the headline measures.
Drawing them is the browser's job, from engine output only. `chart_for` picks the picture
that answers the assistant's question, from its focus and what the idea changes.

Kept out of charts.py, which is the parked advisor product's.
"""

from __future__ import annotations

from .jsmath import NAN, fin, js_or, js_string, mean, to_number
from .model import row_of
from .tax import tax_of

PANEL_GROUPS = ["Sales", "Cash", "The books", "Money owed", "Taxes"]
PANEL_DEFAULT = ["weekly", "cash", "sources", "books"]


def cost_lines(M: dict, n: int | None = None) -> list[dict]:
    """The Profit and Loss lines that cost money (cogs and expense), each averaged over the
    latest `n` months (default 3), biggest first; lines that average 0 or less are left out.
    A value is read as the skeleton reads it (`+v || 0`: text that is a number counts)."""
    k = n or 3
    idx = list(range(len(M["pnl"]["months"])))[-k:]

    def val(line, i):
        vals = line.get("values") or []
        return js_or(to_number(vals[i]) if i < len(vals) else NAN, 0)

    out = [{"l": line.get("account"), "v": mean(val(line, i) for i in idx) if idx else 0}
           for line in (M["pnl"].get("lines") or []) if line.get("group") in ("cogs", "expense")]
    return sorted((x for x in out if x["v"] > 0), key=lambda x: -x["v"])


def _has_buckets(x) -> bool:
    return bool(x and isinstance(x.get("buckets"), list) and x["buckets"])


def _tax_panel_ok(M):
    X = tax_of(M)
    return bool(X and X.get("ok"))


PANELS = {
    "weekly": {"group": "Sales", "kind": "Line chart", "l": lambda V, M: "Weekly sales", "ok": lambda M: len(M["weeks"]) >= 2},
    "tickets": {"group": "Sales", "kind": "Line chart", "l": lambda V, M: V["Tickets"] + " a week",
                "ok": lambda M: len(M["weeks"]) >= 2 and any(w["n"] > 0 for w in M["weeks"])},
    "avgticket": {"group": "Sales", "kind": "Line chart", "l": lambda V, M: "Average " + V["ticket"] + " by week",
                  "ok": lambda M: len(M["weeks"]) >= 2 and any(w["n"] > 0 for w in M["weeks"])},
    "sources": {"group": "Sales", "kind": "Bar chart", "l": lambda V, M: "Where sales come from",
                "ok": lambda M: len(M["units"]) > 0 or len(M["offerings"]) > 0},
    "concentration": {"group": "Sales", "kind": "Curve", "l": lambda V, M: "How concentrated sales are",
                      "ok": lambda M: bool(M["customers"] and isinstance(M["customers"].get("lorenz"), list)
                                           and len(M["customers"]["lorenz"]) >= 3
                                           and fin(M["customers"].get("top20")))},
    "cash": {"group": "Cash", "kind": "Range chart", "l": lambda V, M: "Cash in the bank, next {weeks} weeks",
             "vars": lambda M: {"weeks": {"value": M["A"]["horizonWeeks"], "unit": "count", "decimals": 0, "plain": True}},
             "ok": lambda M: len(M["weeks"]) >= 12},
    "books": {"group": "The books", "kind": "Table", "l": lambda V, M: "The books", "ok": lambda M: len(M["pnl"]["months"]) > 0},
    "income": {"group": "The books", "kind": "Bar chart", "l": lambda V, M: "Income by month", "ok": lambda M: len(M["pnl"]["months"]) >= 2},
    "net": {"group": "The books", "kind": "Bar chart", "l": lambda V, M: "Net income by month", "ok": lambda M: len(M["pnl"]["months"]) >= 2},
    "costs": {"group": "The books", "kind": "Bar chart", "l": lambda V, M: "Where the money goes",
              "ok": lambda M: len(M["pnl"]["months"]) > 0 and len(cost_lines(M)) > 0},
    "ar": {"group": "Money owed", "kind": "Bar chart", "l": lambda V, M: "Owed by " + V["customers"], "ok": lambda M: _has_buckets(M["ar"])},
    "ap": {"group": "Money owed", "kind": "Bar chart", "l": lambda V, M: "Owed to suppliers", "ok": lambda M: _has_buckets(M["ap"])},
    "tax": {"group": "Taxes", "kind": "Table", "l": lambda V, M: "Income tax, next 12 months", "ok": _tax_panel_ok},
}
PANEL_PRESETS = [
    {"id": "picture", "name": "Sales picture", "keys": ["weekly", "tickets", "sources", "concentration"]},
    {"id": "monthly", "name": "The books, month by month", "keys": ["books", "income", "net", "costs"]},
    {"id": "costs", "name": "Cost watch", "keys": ["costs", "net", "books", "cash"]},
    {"id": "cashfirst", "name": "Cash and collections", "keys": ["cash", "ar", "ap", "weekly"]},
    {"id": "owed", "name": "Who owes whom", "keys": ["ar", "ap", "income", "costs"]},
    {"id": "trends", "name": "Trends", "keys": ["weekly", "income", "net", "avgticket"]},
    {"id": "customers", "name": "Customers", "keys": ["sources", "concentration", "tickets", "avgticket"]},
    {"id": "taxes", "name": "Taxes and cash", "keys": ["tax", "cash", "net", "books"]},
]


def _key(k) -> bool:
    """A key that can name a panel or a measure: text. (A list or an object would be read by
    JavaScript as text and could never match; here it simply does not.)"""
    return isinstance(k, str)


def panel_list(M: dict) -> list[dict]:
    """Every panel that can be drawn on this data: `{key, l, group, kind}`."""
    out = []
    for k, p in PANELS.items():
        try:
            ok = p["ok"](M)
        except Exception:  # noqa: BLE001 - a panel that cannot be checked is not offered
            ok = False
        if ok:
            # a label that holds a figure is a template, its figure in vars (as in measures.py)
            entry = {"key": k, "l": p["l"](M["V"], M), "group": p["group"], "kind": p["kind"]}
            if "vars" in p:
                entry["vars"] = p["vars"](M)
            out.append(entry)
    return out


def panel_default(M: dict) -> list[str]:
    """The brain's standard four if it gives four distinct real panels, else the library's."""
    b = M["BRAIN"].get("panels")
    if isinstance(b, list) and len(b) == 4 and all(_key(k) and k in PANELS and b.index(k) == i for i, k in enumerate(b)):
        return list(b)
    return list(PANEL_DEFAULT)


def panel_presets(M: dict) -> list[dict]:
    """Ready-made layouts that work on this data: the brain's own first, then the library's."""
    have = {x["key"] for x in panel_list(M)}

    def ok(p):
        return (isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"] and p.get("name")
                and isinstance(p.get("keys"), list) and len(p["keys"]) == 4
                and all(_key(k) and k in have and p["keys"].index(k) == i for i, k in enumerate(p["keys"])))

    mine = [{"id": p["id"], "name": p["name"], "keys": list(p["keys"]), "own": True}
            for p in (M["BRAIN"].get("panelViews") or []) if ok(p)]
    taken = {p["id"] for p in mine} | {"default", "custom"}
    return mine + [{"id": p["id"], "name": p["name"], "keys": list(p["keys"]), "own": False}
                   for p in PANEL_PRESETS if p["id"] not in taken and ok(p)]


def panel_keys(M: dict, sel) -> list[str]:
    """Which four panels to draw for a saved choice `{preset, keys}`; anything unusable falls
    back to the standard four, and a custom pick is topped up from them."""
    dflt = panel_default(M)
    if not isinstance(sel, dict):
        return dflt
    if sel.get("preset") == "custom":
        have = [x["key"] for x in panel_list(M)]
        chosen = sel.get("keys") if isinstance(sel.get("keys"), list) else []
        ks = [k for i, k in enumerate(chosen) if _key(k) and k in have and chosen.index(k) == i][:4]
        if not ks:
            return dflt
        for k in dflt + have:
            if len(ks) >= 4:
                break
            if k in have and k not in ks:
                ks.append(k)
        return ks
    p = next((x for x in panel_presets(M) if x["id"] == sel.get("preset")), None)
    return p["keys"] if p else dflt


def _plus(a, b):
    """JavaScript's `a + b`: text if either side is text, otherwise numbers."""
    if isinstance(a, str) or isinstance(b, str):
        return (a if isinstance(a, str) else _text(a)) + (b if isinstance(b, str) else _text(b))
    return to_number(a) + to_number(b) if a is not _MISSING and b is not _MISSING else NAN


_MISSING = object()


def _text(v) -> str:
    return "undefined" if v is _MISSING else js_string(v)


def _terms_key(t: dict):
    """The skeleton compares two pay terms as `type + rate + rent + wage`, which is text when
    the type is text (as it always is from a brain) and arithmetic otherwise."""
    out = t.get("type", _MISSING)
    for k in ("rate", "rent", "wage"):
        out = _plus(out, t.get(k, _MISSING))
    return out


def chart_for(M: dict, focus: str, T: dict, E: dict) -> str:
    """Which picture answers a question, from the assistant's `focus` and what the idea
    changes: "cash", "unit:take" / "unit:sales" (by unit), "offering", or "bridge"."""
    idea = E["idea"]

    def unit_changed():
        if idea.get("added"):
            return True
        for r in E["rows"]:
            td = row_of(T, r["name"])
            if td and (td["on"] != r["on"] or (abs(td["take"] - r["take"]) >= 1 and _terms_key(td["t"]) != _terms_key(r["t"]))):
                return True
        return False

    offer_change = bool(idea.get("price")) or bool(idea.get("offer"))
    if focus == "cash":
        return "cash"
    if focus == "units":
        return "unit:take" if M["V"]["people"] else "unit:sales"
    if focus == "sales":
        return "offering" if offer_change and not unit_changed() else "unit:sales"
    return "bridge"
