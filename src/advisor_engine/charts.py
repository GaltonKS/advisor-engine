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
