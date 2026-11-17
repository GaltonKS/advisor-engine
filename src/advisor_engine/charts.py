"""Chart specs built from engine output, for the assistant and the web app to draw.

A chart spec is plain data: the figures to plot (copied from engine output, never
derived here beyond picking and ordering them), axis ranges, which items to label or
highlight, and a source line. The browser draws a spec and formats its numbers; it
computes nothing. The model only chooses a chart kind (the `show_chart` tool), so it
can never put a figure on a chart.

Kinds:
- risk_by_group: weight vs share of risk per sector or asset class, with risk / weight.
- risk_vs_size: one dot per holding, weight (x) against share of risk (y).
- what_if: the risk mix (four risk sources) and headline measures, current vs option.

Design rules (colours, sizes, labels) live with the web components and in CLAUDE.md;
this module only decides content.
"""

from __future__ import annotations

import math

from . import __version__
from .jsmath import mean
from .model import row_of
from .tax import tax_of

# Holdings whose share of risk is at least this multiple of their weight are
# highlighted on the risk-vs-size chart ("well above" their size, not just above).
HIGHLIGHT_RATIO = 1.2
# At most this many dots carry a ticker label, so labels never crowd the plot.
MAX_POINT_LABELS = 10


def _nice_max(x: float, step: float = 0.05) -> float:
    """Smallest multiple of `step` at or above `x` (axis range, not a figure)."""
    return max(step, math.ceil(x / step - 1e-9) * step)


def _source(profile: dict, label: str, holdings_sha256: str | None) -> dict:
    return {
        "label": label,
        "n_periods": profile["n_periods"],
        "period_label": profile["drawdown"]["period_label"],
        "returns_source": profile.get("returns_source", ""),
        "engine_version": profile.get("engine_version", __version__),
        "holdings_sha256": holdings_sha256,
    }


def _caveat(profile: dict) -> str:
    if profile.get("returns_source") == "synthetic":
        return "Synthetic returns: illustrative, not a forecast."
    return "Based on historical returns; not a forecast."


def risk_by_group(profile: dict, group: str = "sector", holdings_sha256: str | None = None) -> dict:
    """Weight and share of risk per sector (or asset class), highest risk share first."""
    if group not in ("sector", "asset_class"):
        raise ValueError("group must be 'sector' or 'asset_class'.")
    data = profile["sector_risk" if group == "sector" else "asset_class_risk"]
    rows = [{"label": k, "weight": v["weight"], "risk_share": v["risk_share"],
             "risk_to_weight": v["risk_to_weight"]} for k, v in data.items()]
    note = ""
    cov = profile.get("sector_coverage") or {}
    if group == "sector" and cov.get("n_unclassified"):
        note = "Unclassified holdings have no sector on file; they are not one sector."
    name = "sector" if group == "sector" else "asset class"
    return {
        "kind": "risk_by_group",
        "group": group,
        "title": f"Risk by {name}",
        "subtitle": f"Share of portfolio weight against share of portfolio risk, by {name}.",
        "rows": rows,
        "axis_max": _nice_max(max([r["weight"] for r in rows] + [r["risk_share"] for r in rows])),
        "note": note,
        "caveat": _caveat(profile),
        "source": _source(profile, "Risk decomposition", holdings_sha256),
    }


def risk_vs_size(profile: dict, holdings_sha256: str | None = None) -> dict:
    """Each holding's weight against its share of risk; dots above the diagonal carry
    more risk than their weight suggests."""
    pts = [{"ticker": p["ticker"], "weight": p["weight"], "risk_share": p["risk_share"],
            "risk_to_weight": p["risk_to_weight"],
            "highlight": p["risk_to_weight"] >= HIGHLIGHT_RATIO} for p in profile["positions"]]
    # Label the highlighted holdings and the biggest ones, up to the cap.
    order = sorted(pts, key=lambda p: (not p["highlight"], -max(p["weight"], p["risk_share"])))
    labelled = {p["ticker"] for p in order[:MAX_POINT_LABELS]}
    for p in pts:
        p["label"] = p["ticker"] in labelled
    return {
        "kind": "risk_vs_size",
        "title": "Risk versus size",
        "subtitle": "Each dot is one holding. Dots above the line carry more risk than their "
                    "weight suggests.",
        "points": pts,
        "x_max": _nice_max(max(p["weight"] for p in pts), 0.02),
        "y_max": _nice_max(max(p["risk_share"] for p in pts), 0.02),
        "highlight_rule": f"Share of risk at least {HIGHLIGHT_RATIO:.1f}× weight",
        "caveat": _caveat(profile),
        "source": _source(profile, "Risk decomposition", holdings_sha256),
    }


def what_if(before: dict, after: dict, comparison: dict, option_label: str = "Option",
            holdings_sha256: str | None = None) -> dict:
    """Risk mix and headline measures for the current portfolio and the option.

    Measure values are the engine comparison's own before/after figures; loss sizes are
    positive there (kind "loss") and are drawn as losses.
    """
    rows = {r["key"]: r for r in comparison["headline"]}
    period = comparison["period_label"]
    spec_rows = [
        ("period_volatility", "Volatility", f"{period}; lower is calmer"),
        ("effective_bets", "Independent bets", "higher is more diversified"),
        ("stress_loss_1_in_100", "Stress loss", f"smaller loss in a severe {period} period"),
    ]
    measures = [{"key": k, "label": label, "hint": hint, "kind": rows[k]["kind"],
                 "current": rows[k]["before"], "option": rows[k]["after"]}
                for k, label, hint in spec_rows if k in rows]
    return {
        "kind": "what_if",
        "title": f"What-if: current portfolio vs {option_label}",
        "subtitle": f"How the risk mix and headline measures change with {option_label}.",
        "option_label": option_label[:1].upper() + option_label[1:],
        "mix": [{"label": "Current", "sources": before["concentration"]["risk_sources"]},
                {"label": option_label[:1].upper() + option_label[1:],
                 "sources": after["concentration"]["risk_sources"]}],
        "measures": measures,
        "added": comparison["added"],
        "removed": comparison["removed"],
        "caveat": _caveat(after),
        "source": _source(after, "What-if comparison", holdings_sha256),
    }


# --- small business: the Overview's panels (FIS skeleton PANELS) -----------------------
# Which panels (charts and tables under the headline numbers) exist, which can be drawn on a
# company's data, and which four to show. Drawing them is the browser's job; a panel draws
# engine output only. The owner picks a layout the same way as the headline numbers.

PANEL_GROUPS = ["Sales", "Cash", "The books", "Money owed", "Taxes"]
PANEL_DEFAULT = ["weekly", "cash", "sources", "books"]


def cost_lines(M: dict, n: int | None = None) -> list[dict]:
    """The Profit and Loss lines that cost money (cogs and expense), each averaged over the
    latest `n` months (default 3), biggest first; lines that average 0 or less are left out."""
    k = n or 3
    idx = list(range(len(M["pnl"]["months"])))[-k:]

    def val(line, i):
        vals = line.get("values") or []
        v = vals[i] if i < len(vals) else None
        try:
            v = float(v) if v is not None and v != "" else 0.0
        except (TypeError, ValueError):
            v = float("nan")
        return v if v == v and v else 0.0

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
                                           and _fin(M["customers"].get("top20")))},
    "cash": {"group": "Cash", "kind": "Range chart", "l": lambda V, M: f"Cash in the bank, next {M['A']['horizonWeeks']} weeks",
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


def _fin(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def panel_list(M: dict) -> list[dict]:
    """Every panel that can be drawn on this data: `{key, l, group, kind}`."""
    out = []
    for k, p in PANELS.items():
        try:
            ok = p["ok"](M)
        except Exception:  # noqa: BLE001 - a panel that cannot be checked is not offered
            ok = False
        if ok:
            out.append({"key": k, "l": p["l"](M["V"], M), "group": p["group"], "kind": p["kind"]})
    return out


def panel_default(M: dict) -> list[str]:
    """The brain's standard four if it gives four distinct real panels, else the library's."""
    b = M["BRAIN"].get("panels")
    if isinstance(b, list) and len(b) == 4 and all(k in PANELS and b.index(k) == i for i, k in enumerate(b)):
        return list(b)
    return list(PANEL_DEFAULT)


def panel_presets(M: dict) -> list[dict]:
    """Ready-made layouts that work on this data: the brain's own first, then the library's."""
    have = {x["key"] for x in panel_list(M)}

    def ok(p):
        return (isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"] and p.get("name")
                and isinstance(p.get("keys"), list) and len(p["keys"]) == 4
                and all(k in have and p["keys"].index(k) == i for i, k in enumerate(p["keys"])))

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
        ks = [k for i, k in enumerate(chosen) if k in have and chosen.index(k) == i][:4]
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


def _js_str(v) -> str:
    """JavaScript's String(v) for the values a pay term holds."""
    if v is None:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _terms_key(t: dict) -> str:
    # The skeleton compares `type + rate + rent + wage` as one concatenated string.
    return "".join(_js_str(t.get(k)) for k in ("type", "rate", "rent", "wage"))


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
