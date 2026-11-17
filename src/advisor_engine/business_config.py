"""Every tunable number in the small-business engine, in one place. ALL DRAFT.

Same shape as `profile_config.py`: each number is marked `# DRAFT`, none is calibrated,
and results carry `METHOD_STATUS`. Bump `METHOD_VERSION` when any of them changes, since
results recorded under one version are not comparable with another.

Each value is the FIS pilot skeleton's (`app/engine.js`, commented where it lives there),
ported unchanged so Python and the skeleton agree to the cent. Agreeing with the skeleton
does not make a number right: the price response, the tone thresholds and the cash
simulation's settings are placeholders until a company's own history or a reviewer
replaces them.

Each step of the port adds the numbers it reads: this holds what `model.build`,
`model.evaluate`, `bridge.bridge`, `cash.cash_range` and `measures` use.
"""

from __future__ import annotations

METHOD_VERSION = "draft-1"  # DRAFT: bump when any number below changes
METHOD_STATUS = "draft: uncalibrated, not reviewed"  # DRAFT

# --- assumptions a brain may override (build: `A`) ---------------------------------
ASSUMPTION_DEFAULTS = {
    "elasticity": -0.5,   # DRAFT: % change in tickets for each +1% in price, until history replaces it
    "horizonWeeks": 13,   # DRAFT: weeks the cash range looks ahead
    "paths": 2000,        # DRAFT: simulated paths in the cash range
    "keepPct": 0,         # DRAFT: share of a removed unit's sales that stays (not read by the engine yet)
}

# --- levers: what an idea may change, unless the brain switches it off (leversOn) ----
LEVER_DEFAULTS = {
    "units": True,         # DRAFT
    "unitPay": True,       # DRAFT
    "prices": True,        # DRAFT
    "volume": True,        # DRAFT
    "monthlyBills": True,  # DRAFT
    "costRates": True,     # DRAFT
    "oneTimeCash": True,   # DRAFT
    "taxes": False,        # DRAFT: off unless a brain switches it on
}

# --- pay terms: the starting figure offered for each kind (TYPE_DEFAULTS) ------------
TERM_TYPE_DEFAULTS = {
    "share": {"label": "Commission", "unit": "% of their sales", "def": 50},  # DRAFT
    "fixedIn": {"label": "Fixed rent", "per": "week", "def": 250},            # DRAFT
    "wage": {"label": "Salary", "per": "month", "def": 4000},                 # DRAFT
    "none": {"label": "No direct pay"},
}

# --- the sales window and the fit (build) --------------------------------------------
WINDOW_WEEKS = {12: 52, 3: 13}  # DRAFT: basis (months) -> weeks of sales averaged
SEASON_MIN_WEEKS = 3            # DRAFT: weeks a calendar month needs before it gets its own season factor
FIT_MIN_WEEKS = 12              # DRAFT: weeks needed to measure the week-to-week swing (and so the cash range)
PHI_MAX = 0.9                   # DRAFT: cap on week-to-week carry-over of a swing
TREND_WEEKS = 13                # DRAFT: the trend compares the latest 13 weeks ...
YEAR_WEEKS = 52                 # DRAFT: ... with the same weeks this many weeks earlier, when there is the history
TREND_YEAR_WEEKS = YEAR_WEEKS + TREND_WEEKS  # derived: weeks needed for the year-on-year trend (65)
TREND_QUARTER_WEEKS = 2 * TREND_WEEKS        # derived: weeks needed for the trend against the weeks before (26)
ACTIVE_WEEKS = 13               # DRAFT: a unit with sales in the last 13 weeks is active

# --- does the detail tie to the books? (PLAYBOOK "Does it tie?") ----------------------
TIE_TOLERANCE = 0.05  # DRAFT: detail within 5% of Profit and Loss income ties

# --- the bridge: steps that move the monthly bottom line by less than this are not listed --
BRIDGE_MIN_STEP = 1  # DRAFT: dollars a month

# --- the cash range (cash) ------------------------------------------------------------
CASH_SEED = 77           # DRAFT: fixed so every idea is run on the same draws, and a result is repeatable
MAX_HORIZON_WEEKS = 104  # DRAFT: the longest look-ahead a brain may ask for
MAX_PATHS = 100000       # DRAFT: the most simulated paths a caller may ask for

# --- tones on the headline measures (measures.py) ----------------------------------------
TONE = {
    "trend_bad": -0.05,           # DRAFT: year-on-year trend below this is "bad"
    "trend_good": 0.05,           # DRAFT: above this is "good"
    "top_customers_warn": 0.8,    # DRAFT: top 20% of customers above this share warns
    "breach_bad": 0.25,           # DRAFT: chance of dipping under the floor above this is "bad"
    "breach_warn": 0.1,           # DRAFT: above this warns
    "bills_bad": 1,               # DRAFT: under a month of bills in the bank is "bad"
    "bills_warn": 2,              # DRAFT: under two months warns
    "ar_late_warn": 0.25,         # DRAFT: over 30 days late above this share of A/R warns
    "top_unit_over_even": 2,      # DRAFT: top unit above this multiple of an even split warns
    "tax_due_soon_days": 30,      # DRAFT: a tax payment due within this many days warns
}
