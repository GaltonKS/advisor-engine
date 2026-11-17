"""Checks run on a company's brain and snapshot before anyone sees them (the FIS skeleton's
`check`).

`check(brain, data)` returns `[{level, msg, vars}]`. "fix" items block showing the pilot;
"note" items are worth reading. A message holding a count or a share is a template:
`{name}` placeholders, the figures in `vars` (as in measures.py). Everything else in a
message is words.

What it checks: the brain names the company, lists QuickBooks first among its systems and
has at least three starter questions; every headline, view and panel layout names real
measures and panels (four of each, no repeats, no id clashes, no tax numbers with taxes
off); the snapshot can be read, has 12 weeks of sales (52 for seasons), units, a Profit and
Loss and a Balance Sheet; pay terms name known units and pay kinds; starter questions that
quote a name use one in the data; facts and tax profile fields not yet confirmed; whether
the detail ties to the books (and the brain says so when it does not); the tax profile is
complete and holds nothing that looks like an EIN or a Social Security number.
"""

from __future__ import annotations

import json
import re

from .charts import PANELS, panel_list
from .levers import levers_on
from .measures import GROUP, LIBRARY, esc
from .model import build
from .tax import TAX_ENTITIES, TAX_PROFILE, tax_of


def _count(n):
    return {"value": n, "unit": "count", "decimals": 0}


def check(brain: dict, data: dict) -> list[dict]:
    out: list[dict] = []

    def add(level, msg, vars=None):
        out.append({"level": level, "msg": msg, "vars": vars or {}})

    co = brain.get("company") or {}
    if not co.get("name"):
        add("fix", "The brain has no company name.")
    if not co.get("owner"):
        add("note", "No owner name: the assistant will say “the owner”.")
    if not co.get("logo"):
        add("note", "No logo yet: the header shows the company's initial instead.")
    if not brain.get("theme") or not brain["theme"].get("accent"):
        add("note", "No accent color set: the default blue is used.")
    sys = brain.get("systems") or []
    if not sys:
        add("fix", "No connected systems are listed.")
    elif sys[0].get("key") != "quickbooks":
        add("note", "QuickBooks is not the first system listed. It is the spine of the product and should lead.")
    st = (brain.get("assistant") or {}).get("starters") or []
    if len(st) < 3:
        add("fix", "Fewer than 3 starter questions for the assistant.")
    try:
        M = build(data, brain)
    except Exception as e:  # noqa: BLE001 - the reason is the check's finding
        add("fix", "The still data could not be read: " + esc(e))
        return out

    own = brain.get("metrics") or {}

    def known(k):
        return k in LIBRARY or k in own

    for k in brain.get("headline") or []:
        if not known(k):
            add("fix", esc(f"Headline metric “{k}” is not in the metric library or the brain."))
    tax_on = levers_on(brain).get("taxes")

    def is_tax(k):
        return GROUP.get(k) == "Taxes" and k not in own

    for k in brain.get("headline") or []:
        if not tax_on and is_tax(k):
            add("fix", esc(f"Headline metric “{k}” is a tax number, but taxes are switched off in this brain, so its tile would read n/a. Set levers.taxes to true, or take it out of the headline."))
    if not brain.get("headline"):
        add("fix", "No headline metrics chosen.")

    seen_v: set = set()
    for v in brain.get("views") or []:
        vid = v.get("id") if v else None
        nm = (v and v.get("name")) or vid or "(unnamed)"
        ks = v["keys"] if v and isinstance(v.get("keys"), list) else []
        if not vid or not v.get("name"):
            add("fix", "A view in BRAIN.views needs an id and a name.")
        elif vid in seen_v or vid in ("default", "custom"):
            add("fix", esc(f"View id “{vid}” is already taken. Pick another."))
        seen_v.add(vid)
        if len(ks) != 4:
            add("fix", esc(f"View “{nm}” has ") + "{n} numbers; a view is exactly four.", {"n": _count(len(ks))})
        for k in ks:
            if not known(k):
                add("fix", esc(f"View “{nm}” uses “{k}”, which is not in the metric library or the brain."))
            elif not tax_on and is_tax(k):
                add("fix", esc(f"View “{nm}” uses “{k}”, a tax number, but taxes are switched off in this brain. Set levers.taxes to true, or choose another number."))

    if brain.get("panels") is not None:
        b = brain["panels"]
        if not isinstance(b, list) or len(b) != 4:
            add("fix", "BRAIN.panels is the standard four panels under the numbers: exactly four panel keys.")
        else:
            for i, k in enumerate(b):
                if k not in PANELS:
                    add("fix", esc(f"BRAIN.panels uses “{k}”, which is not a panel. The panels are: " + ", ".join(PANELS) + "."))
                elif b.index(k) != i:
                    add("fix", esc(f"BRAIN.panels lists “{k}” twice."))
                elif not tax_on and PANELS[k]["group"] == "Taxes":
                    add("fix", esc(f"BRAIN.panels uses “{k}”, a tax panel, but taxes are switched off in this brain, so it would read “The tax estimate is not available.” Set levers.taxes to true, or choose another panel."))
    seen_p: set = set()
    have_p = {x["key"] for x in panel_list(M)}
    for v in brain.get("panelViews") or []:
        vid = v.get("id") if v else None
        nm = (v and v.get("name")) or vid or "(unnamed)"
        ks = v["keys"] if v and isinstance(v.get("keys"), list) else []
        if not vid or not v.get("name"):
            add("fix", "A layout in BRAIN.panelViews needs an id and a name.")
        elif vid in seen_p or vid in ("default", "custom"):
            add("fix", esc(f"Panel layout id “{vid}” is already taken. Pick another."))
        seen_p.add(vid)
        if len(ks) != 4:
            add("fix", esc(f"Panel layout “{nm}” has ") + "{n} panels; a layout is exactly four.", {"n": _count(len(ks))})
        for i, k in enumerate(ks):
            if k not in PANELS:
                add("fix", esc(f"Panel layout “{nm}” uses “{k}”, which is not a panel."))
            elif ks.index(k) != i:
                add("fix", esc(f"Panel layout “{nm}” lists “{k}” twice."))
            elif k not in have_p:
                why = "needs levers.taxes to be true." if PANELS[k]["group"] == "Taxes" and not tax_on else "cannot be drawn on this data."
                add("note", esc(f"Panel layout “{nm}” will be hidden: “{k}” {why}"))

    if len(M["weeks"]) < 12:
        add("fix", "Fewer than 12 weeks of sales: the cash range cannot be worked out.")
    elif len(M["weeks"]) < 52:
        add("note", "Under a year of sales ({n} weeks): seasons are thin.", {"n": _count(len(M["weeks"]))})
    if not M["units"]:
        add("fix", esc("No " + M["V"]["units"] + " in the sales data."))
    if not M["pnl"]["months"]:
        add("note", "No Profit and Loss loaded: the books table is empty.")
    if not M["balance"]:
        add("note", "No Balance Sheet loaded: cash in the bank is typed in, not read from the books.")
    for n, t in M["unitTerms"].items():
        if not any(u["name"] == n for u in M["units"]):
            add("note", esc(f"Pay terms are set for “{n}”, who is not in the sales data."))
        kind = (t or {}).get("type")
        if kind and kind not in M["types"]:
            add("fix", esc(f"“{n}” is paid by “{kind}”, which this brain does not offer."))
    names = [u["name"].lower() for u in M["units"]] + [o["name"].lower() for o in M["off"]]
    for q in st:
        m = re.search(r"[“\"]([^”\"]+)[”\"]", q)
        if m and m.group(1).lower() not in names:
            add("note", esc(f"A starter question names “{m.group(1)}”, which is not in the data."))
    un = [f for f in M["FL"] if not f.get("confirmed")]
    if un:
        add("note", "{n} of {of} facts are not confirmed by the owner yet: "
            + esc(", ".join(f.get("l") or "" for f in un[:6]) + ("…" if len(un) > 6 else "") + "."),
            {"n": _count(len(un)), "of": _count(len(M["FL"]))})
    cov = M["coverage"]
    if cov and (cov["ratio"] < 0.95 or cov["ratio"] > 1.05):
        add("note", "Detailed sales add up to {ratio} of the income in the books. Say why on every page (BRAIN.coverage).",
            {"ratio": {"value": cov["ratio"], "unit": "share", "decimals": 0}})
    if M["sample"]:
        add("note", "This is sample data. Replace pilot/data.js before a client sees it.")
    if brain.get("upload") is not None and not isinstance(brain["upload"], bool):
        add("fix", "BRAIN.upload is true or false (left out means true: the owner can bring in QuickBooks CSV files on Connected systems).")
    lv = brain.get("levers")
    if lv and lv.get("taxes") is not None and not isinstance(lv["taxes"], bool):
        add("fix", "BRAIN.levers.taxes is true or false (left out means false: no tax estimate anywhere).")
    if tax_on:
        X = tax_of(M)
        if not X or not X.get("ok"):
            add("fix", esc("Taxes are switched on but the estimate cannot be worked out: " + ((X and X.get("why")) or "no reason given")))
        else:
            if X["missing"]:
                labels = dict(TAX_PROFILE)
                add("fix", esc("The tax profile is missing: " + ", ".join(labels.get(k, k) for k in X["missing"]) + "."))
            if not X["computed"]:
                add("fix" if X["entity"] and X["entity"] not in TAX_ENTITIES else "note", esc("Tax: " + X["why"]))
            uc = X["unconfirmed"]
            if uc:
                add("note", "{n} of {of} tax profile fields are not confirmed by the owner yet: "
                    + esc(", ".join(p["l"] for p in uc[:6]) + ("…" if len(uc) > 6 else "") + "."),
                    {"n": _count(len(uc)), "of": _count(len(X["profile"]))})
            um = X["unmapped"]
            if um:
                one = len(um) == 1
                add("note", "{n} cost account" + (" has" if one else "s have") + " no tax category and " + ("is" if one else "are")
                    + " taken as booked: " + esc(", ".join(u["account"] for u in um[:6]) + ("…" if len(um) > 6 else "") + "."),
                    {"n": _count(len(um))})
            est = sum(1 for x in X["map"] if x["status"] == "estimate" and x["cat"] != "deductible")
            if est:
                add("note", "{n} tax categor" + ("y is an estimate" if est == 1 else "ies are estimates") + " for the accountant to review.",
                    {"n": _count(est)})
            if re.search(r"\b\d{2}-\d{7}\b|\b\d{3}-\d{2}-\d{4}\b", json.dumps(data.get("tax") or {}, ensure_ascii=False)):
                add("fix", "The tax data holds something that looks like an EIN or a Social Security number. Remove it: no government ID belongs in a pilot.")
    if not brain.get("coverage") and cov and cov["ratio"] < 0.95:
        add("fix", "The data does not cover the whole business and BRAIN.coverage is empty.")
    return out
