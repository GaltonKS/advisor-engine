"""Question bank and every tunable number for the three-part risk profile (`profile.py`).

ALL NUMBERS IN THIS MODULE ARE DRAFT PLACEHOLDERS. Compliance has not reviewed the
option values, noise levels, mapping tables, thresholds or the provisional rule. They
are a starting point chosen so the method behaves sensibly, not an established
instrument. Never present them as final. Change any of them and bump `METHOD_VERSION`,
since stored answers are re-scored on every read and snapshots record the version.

Scale: every answer is read as a point on one 1-to-10 scale, where higher means more
risk: more willing to take it (willingness), more able to bear it (ability), or a plan
that needs more of it (need).
"""

from __future__ import annotations

METHOD_VERSION = "draft-2"
METHOD_STATUS = ("Draft method (draft-2), pending compliance review: every weight, "
                 "mapping and threshold is a placeholder.")

SCALE_MIN = 1.0
SCALE_MAX = 10.0

DIMENSIONS = ("willingness", "ability", "need")

# --- question bank --------------------------------------------------------------------
# kind "choice": the answer is an option index; the option's `value` is the reading.
# kind "loss_limits": {"uncomfortable_pct", "act_pct"} as fractions; `act_pct` is mapped
#   to the scale through `table`.
# kind "number": a number in [min, max], mapped to the scale through `table`.
# noise_sd: how unreliable one answer is, in scale points (lower = more trusted).
# Tables are piecewise-linear (x, scale value) knots, x ascending; values outside the
# first/last knot take the end value.

QUESTIONS: list[dict] = [
    {
        "id": "w_reaction",
        "dimension": "willingness",
        "kind": "choice",
        "prompt": "If the portfolio fell sharply over a year, what would they do?",
        "options": [  # DRAFT values
            {"label": "Sell everything", "value": 1.5},
            {"label": "Sell some", "value": 3.5},
            {"label": "Hold and wait", "value": 5.5},
            {"label": "Buy a little more", "value": 7.5},
            {"label": "Buy a lot more", "value": 9.5},
        ],
        "noise_sd": 1.5,  # DRAFT
    },
    {
        "id": "w_experience",
        "dimension": "willingness",
        "kind": "choice",
        "prompt": "How experienced are they with investing?",
        "options": [  # DRAFT values: a narrow range, experience says little on its own
            {"label": "None", "value": 3.0},
            {"label": "Limited", "value": 4.0},
            {"label": "Moderate", "value": 5.0},
            {"label": "Good", "value": 6.0},
            {"label": "Extensive", "value": 7.0},
        ],
        "noise_sd": 3.0,  # DRAFT: low weight
    },
    {
        "id": "w_loss_limits",
        "dimension": "willingness",
        "kind": "loss_limits",
        "prompt": ("Over a year, at what fall in value would they become uncomfortable, "
                   "and at what fall would they act?"),
        "min": 0.0,
        "max": 0.5,
        # DRAFT: act_pct (fraction) -> scale.
        "table": [(0.0, 1.0), (0.05, 2.0), (0.10, 3.5), (0.20, 5.5), (0.30, 7.5),
                  (0.40, 9.0), (0.50, 10.0)],
        "noise_sd": 1.0,  # DRAFT: the most concrete answer, so the most trusted
    },
    {
        "id": "a_income_share",
        "dimension": "ability",
        "kind": "choice",
        "prompt": "How much of their income will come from this portfolio?",
        "options": [  # DRAFT values
            {"label": "Most of it", "value": 2.0},
            {"label": "A large part", "value": 3.5},
            {"label": "Some", "value": 5.5},
            {"label": "A little", "value": 7.5},
            {"label": "None", "value": 9.0},
        ],
        "noise_sd": 1.5,  # DRAFT
    },
    {
        "id": "a_cash_months",
        "dimension": "ability",
        "kind": "choice",
        "prompt": "How many months of spending do they hold in cash reserves?",
        "options": [  # DRAFT values
            {"label": "Under 3 months", "value": 2.5},
            {"label": "3 to 6 months", "value": 4.5},
            {"label": "6 to 12 months", "value": 6.5},
            {"label": "Over 12 months", "value": 8.5},
        ],
        "noise_sd": 2.0,  # DRAFT
    },
    {
        "id": "a_horizon_years",
        "dimension": "ability",
        "kind": "number",
        "prompt": "In how many years will they need most of this money?",
        "unit": "years",
        "min": 0.0,
        "max": 60.0,
        # DRAFT: years -> scale.
        "table": [(0.0, 1.0), (3.0, 3.0), (5.0, 4.5), (10.0, 6.5), (15.0, 8.0),
                  (20.0, 9.0), (30.0, 10.0)],
        "noise_sd": 1.5,  # DRAFT
    },
    {
        "id": "n_required_return",
        "dimension": "need",
        "kind": "number",
        "prompt": "What annual return does the plan require?",
        "unit": "fraction",
        "min": -0.10,
        "max": 0.30,
        # DRAFT: required annual return (fraction) -> scale. Anchor from the approved
        # mockup: 5.8% maps to 6.1 (on the 0.04 -> 4.3, 0.06 -> 6.3 segment).
        "table": [(0.0, 1.0), (0.02, 2.5), (0.04, 4.3), (0.06, 6.3), (0.08, 8.0),
                  (0.10, 9.3), (0.12, 10.0)],
        "noise_sd": 1.0,  # DRAFT
    },
]

QUESTIONS_BY_ID: dict[str, dict] = {q["id"]: q for q in QUESTIONS}

# --- provisional rule (DRAFT) ---------------------------------------------------------
# The profile stays provisional until every person has this many willingness answers
# and ability and need each have this many.
MIN_WILLINGNESS_ANSWERS = 2
MIN_ABILITY_ANSWERS = 1
MIN_NEED_ANSWERS = 1

# --- flag thresholds (DRAFT), in scale points; each fires at or above its value -------
PARTNER_GAP = 2.0                # partners' willingness means differ by this much
NEED_OVER_CEILING = 0.5          # need above the ceiling by this much
PORTFOLIO_OVER_CEILING = 0.5     # portfolio risk reading above the ceiling by this much
ABILITY_BELOW_WILLINGNESS = 1.5  # ability below household willingness by this much
WIDE_BAND = 2.0                  # a dimension's band (the ± half-width) this wide
