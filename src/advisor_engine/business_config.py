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
`model.evaluate`, `bridge.bridge`, `cash.cash_range`, `measures` and `tax` use.

The tax rules table is the exception in kind, not in status: its rates, thresholds and
dates were read from IRS instructions (each row names its source), but nobody has reviewed
the port, and the 2026 forms were not out when it was checked. It is a planning estimate,
never advice, and it is off unless a brain switches it on.
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

# --- tax (tax.py): a planning estimate, off unless the brain switches it on -----------
TAX_SEED = 91          # DRAFT
TAX_PATHS = 400        # DRAFT: simulated years in the tax range
ASSET_LIFE_YEARS = 5   # DRAFT: straight-line life for an idea's purchase, until schedules exist

# The rules table. Every rate, threshold and date the tax estimate uses is here, with the IRS
# page it was read from. Never type a rate anywhere else.
TAX_RULES = {  # DRAFT: sourced, not reviewed; carried forward past checkedYear
    "version": "2026-11-14",
    "note": "Checked against the IRS instructions named in each row on Nov 14, 2026. The 2026 forms were not yet published; rows are carried forward to later years and say so.",
    "rows": [
        {"id": "us-fed-ccorp-rate", "jurisdiction": "US federal", "entity": "c_corp", "kind": "rate", "value": 0.21, "fromYear": 2018, "checkedYear": 2025,
         "effective": "Tax years beginning after Dec 31, 2017",
         "source": "IRS Instructions for Form 1120 (2025), Schedule J, line 1a", "url": "https://www.irs.gov/pub/irs-pdf/i1120.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-ccorp-est-threshold", "jurisdiction": "US federal", "entity": "c_corp", "kind": "estThreshold", "value": 500, "fromYear": 2018, "checkedYear": 2025,
         "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 1120 (2025), Estimated Tax Payments: installments are required when the year's tax is expected to be $500 or more",
         "url": "https://www.irs.gov/pub/irs-pdf/i1120.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-ccorp-est-dates", "jurisdiction": "US federal", "entity": "c_corp", "kind": "estDates", "value": [4, 6, 9, 12], "day": 15, "fromYear": 2018,
         "checkedYear": 2025, "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 1120 (2025): the 15th day of the 4th, 6th, 9th and 12th months of the tax year; the next business day when that is a weekend or holiday",
         "url": "https://www.irs.gov/pub/irs-pdf/i1120.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-ccorp-est-share", "jurisdiction": "US federal", "entity": "c_corp", "kind": "estShare", "value": 0.25, "fromYear": 2018, "checkedYear": 2025,
         "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 2220 (2025): each required installment is 25% of the required annual payment",
         "url": "https://www.irs.gov/pub/irs-pdf/i2220.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-ccorp-safe-harbor", "jurisdiction": "US federal", "entity": "c_corp", "kind": "safeHarbor", "value": 1, "largeCorp": 1000000, "fromYear": 2018,
         "checkedYear": 2025, "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 2220 (2025): the required annual payment is the smaller of this year's tax or last year's tax, when last year was a 12-month year with tax due. A corporation with taxable income of $1 million or more in any of the 3 prior years may use last year's tax for the first installment only",
         "url": "https://www.irs.gov/pub/irs-pdf/i2220.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-ccorp-balance-due", "jurisdiction": "US federal", "entity": "c_corp", "kind": "balanceDue", "value": 4, "juneYearEnd": 3, "day": 15, "fromYear": 2018,
         "checkedYear": 2025, "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 2220 (2025): the 15th day of the 4th month after the tax year ends (the 3rd month for a June 30 year end)",
         "url": "https://www.irs.gov/pub/irs-pdf/i2220.pdf", "checked": "2026-11-14"},
        {"id": "us-fed-meals", "jurisdiction": "US federal", "entity": "*", "kind": "mealsDeductible", "value": 0.5, "fromYear": 2018, "checkedYear": 2025,
         "effective": "Tax year 2025",
         "source": "IRS Instructions for Form 1120 (2025): business meals are generally 50% deductible; entertainment is generally not deductible",
         "url": "https://www.irs.gov/pub/irs-pdf/i1120.pdf", "checked": "2026-11-14"},
    ],
}
