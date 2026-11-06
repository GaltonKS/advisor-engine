"""Tunable numbers and the question bank for the household risk profile (`profile.py`).

EVERY NUMBER IN THIS FILE IS A DRAFT PLACEHOLDER. None of it has been reviewed by
compliance, none of it is calibrated, and none of it should be presented as final.
Changing any of it changes every recomputed profile, so bump `METHOD_VERSION` when it
does (snapshots keep the version they were computed with).

All scores are on one 1 to 10 scale: 1 is the most cautious reading, 10 the most
risk-taking. `noise_sd` is how unreliable one answer is as a reading of the client's
latent score, in points on that scale: lower means more trusted (weight 1 / noise_sd²).
"""

from __future__ import annotations

METHOD_VERSION = "draft-2"
METHOD_STATUS = "Draft risk profile method (draft-2), pending compliance review."

SCALE_MIN = 1.0
SCALE_MAX = 10.0

DIMENSIONS = ("willingness", "ability", "need")

# Who a dimension is about: willingness per person, ability per household, need per plan.
PER_PERSON = {"willingness"}

# --- mapping tables (piecewise linear, clamped at both ends) ---------------------------

# DRAFT: the loss at which the client says they would act (sell), as a fraction, to a
# willingness score. Accepting a bigger fall before acting reads as more willing.
ACT_PCT_TABLE: list[tuple[float, float]] = [  # DRAFT
    (0.00, 1.0),
    (0.05, 2.0),
    (0.10, 3.5),
    (0.15, 5.0),
    (0.20, 6.0),
    (0.25, 7.0),
    (0.35, 8.5),
    (0.50, 10.0),
]

# DRAFT: the plan's required annual return, as a fraction, to a need score. The 5.8% ->
# 6.1 point is the approved mockup's anchor and is pinned by a test.
REQUIRED_RETURN_TABLE: list[tuple[float, float]] = [  # DRAFT
    (0.000, 1.0),
    (0.020, 2.5),
    (0.040, 4.4),
    (0.058, 6.1),  # mockup anchor
    (0.080, 8.0),
    (0.100, 9.2),
    (0.120, 10.0),
]

# DRAFT: years until most of the money is needed, to an ability score.
HORIZON_TABLE: list[tuple[float, float]] = [  # DRAFT
    (0.0, 1.0),
    (1.0, 1.5),
    (3.0, 3.0),
    (5.0, 4.5),
    (10.0, 6.5),
    (15.0, 8.0),
    (25.0, 10.0),
]

# --- question bank ----------------------------------------------------------------------
# kind "choice": the answer is an option key. "loss_limits": {"uncomfortable_pct",
# "act_pct"}, fractions, act >= uncomfortable; only act_pct is scored. "number": a plain
# number within [min, max], scored through `table`.

LOSS_LIMIT_MAX = 0.5

QUESTIONS: list[dict] = [
    {
        "id": "w_reaction", "dimension": "willingness", "kind": "choice",
        "prompt": "If the portfolio fell sharply over a year, what would they do?",
        "noise_sd": 2.0,  # DRAFT
        "options": [  # DRAFT values
            {"key": "sell_all", "label": "Sell everything", "value": 1.0},
            {"key": "sell_some", "label": "Sell some", "value": 3.0},
            {"key": "hold", "label": "Hold and wait", "value": 5.5},
            {"key": "buy_little", "label": "Buy a little more", "value": 7.5},
            {"key": "buy_lot", "label": "Buy a lot more", "value": 9.5},
        ],
    },
    {
        "id": "w_experience", "dimension": "willingness", "kind": "choice",
        "prompt": "How experienced are they with investing?",
        "noise_sd": 3.0,  # DRAFT: low weight, experience says little about willingness
        "options": [  # DRAFT values
            {"key": "none", "label": "None", "value": 2.0},
            {"key": "limited", "label": "Limited", "value": 4.0},
            {"key": "moderate", "label": "Moderate", "value": 5.5},
            {"key": "good", "label": "Good", "value": 7.0},
            {"key": "extensive", "label": "Extensive", "value": 8.5},
        ],
    },
    {
        "id": "w_loss_limits", "dimension": "willingness", "kind": "loss_limits",
        "prompt": "At what fall in value would they feel uncomfortable, and at what fall "
                  "would they act?",
        "noise_sd": 1.25,  # DRAFT: the most concrete answer, so the most trusted
        "min": 0.0, "max": LOSS_LIMIT_MAX, "table": ACT_PCT_TABLE,
    },
    {
        "id": "a_income_share", "dimension": "ability", "kind": "choice",
        "prompt": "How much of their income will come from this portfolio?",
        "noise_sd": 1.5,  # DRAFT
        "options": [  # DRAFT values
            {"key": "most", "label": "Most of it", "value": 2.0},
            {"key": "large", "label": "A large part", "value": 3.5},
            {"key": "some", "label": "Some", "value": 5.5},
            {"key": "little", "label": "A little", "value": 7.5},
            {"key": "none", "label": "None", "value": 9.0},
        ],
    },
    {
        "id": "a_cash_months", "dimension": "ability", "kind": "choice",
        "prompt": "How many months of spending do they hold in cash reserves?",
        "noise_sd": 2.0,  # DRAFT
        "options": [  # DRAFT values
            {"key": "under_3", "label": "Under 3", "value": 2.5},
            {"key": "3_6", "label": "3 to 6", "value": 4.5},
            {"key": "6_12", "label": "6 to 12", "value": 6.5},
            {"key": "over_12", "label": "Over 12", "value": 8.5},
        ],
    },
    {
        "id": "a_horizon_years", "dimension": "ability", "kind": "number",
        "prompt": "In how many years will they need most of this money?",
        "unit": "years",
        "noise_sd": 1.5,  # DRAFT
        "min": 0.0, "max": 60.0, "table": HORIZON_TABLE,
    },
    {
        "id": "n_required_return", "dimension": "need", "kind": "number",
        "prompt": "What annual return does the plan require?",
        "unit": "fraction",
        "noise_sd": 1.0,  # DRAFT: a planning calculation, fairly exact
        "min": 0.0, "max": 0.25, "table": REQUIRED_RETURN_TABLE,
    },
]

# --- household rules and flags ----------------------------------------------------------

# DRAFT: a profile is provisional until every person has this many willingness answers
# and ability and need each have at least the minimum.
MIN_WILLINGNESS_ANSWERS_PER_PERSON = 2  # DRAFT
MIN_ABILITY_ANSWERS = 1  # DRAFT
MIN_NEED_ANSWERS = 1  # DRAFT

# DRAFT flag thresholds, in points on the 1 to 10 scale. A flag fires at the threshold.
PARTNER_GAP = 2.0  # DRAFT: partners' willingness means differ by at least this
NEED_OVER_CEILING = 0.5  # DRAFT: need above the ceiling by at least this
PORTFOLIO_OVER_CEILING = 0.5  # DRAFT: portfolio risk reading above the ceiling by at least this
ABILITY_BELOW_WILLINGNESS = 1.5  # DRAFT: ability below household willingness by at least this
WIDE_BAND = 2.0  # DRAFT: a band (±) wider than this (strictly) is flagged

FLAG_SEVERITY = {  # DRAFT
    "partner_gap": "warn",
    "need_exceeds_ceiling": "warn",
    "portfolio_exceeds_ceiling": "warn",
    "ability_below_willingness": "info",
    "wide_band": "info",
}
