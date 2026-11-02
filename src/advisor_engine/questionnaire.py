"""Client risk questionnaire: six questions scored to a risk level from 1 to 10.

Method (draft; `METHOD_STATUS` says so wherever the score is shown):
- Each answer scores 1 (most cautious) to 5 (most risk-taking). The total runs 6-30.
- Risk level = 1 + (total - 6) * 9 / 24, rounded half up, so 6 points is level 1 and
  30 is 10.
- One check against the portfolio: its worst historical drop (peak to trough, from the
  engine's risk profile) against the largest loss the client said they could accept.
  Both are peak-to-trough losses, so they compare like with like. The engine flags a
  mismatch; it does not say what to do about it.

The questions, weights and mapping are a starting point to review with compliance, not
an established instrument. Changing them changes every stored score, so bump
`METHOD_VERSION` when they do.
"""

from __future__ import annotations

import math

METHOD_VERSION = "draft-1"
METHOD_STATUS = "Draft scoring method, pending compliance review."

QUESTIONS: list[dict] = [
    {"id": "time_horizon", "text": "When will the client need most of this money?",
     "options": ["Within 3 years", "3 to 5 years", "5 to 10 years", "10 to 15 years",
                 "More than 15 years"]},
    {"id": "income_need", "text": "How much of their income will come from this portfolio?",
     "options": ["Most of it", "A large part", "Some", "A little", "None"]},
    {"id": "loss_reaction", "text": "If the portfolio fell sharply over a year, what would they do?",
     "options": ["Sell everything", "Sell some", "Hold and wait", "Buy a little more",
                 "Buy a lot more"]},
    {"id": "max_loss", "text": "What is the largest fall in value they could accept?",
     "options": ["5%", "10%", "15%", "25%", "35% or more"]},
    {"id": "experience", "text": "How experienced are they with investing?",
     "options": ["None", "Limited", "Moderate", "Good", "Extensive"]},
    {"id": "liquidity", "text": "How much of their savings might they need at short notice?",
     "options": ["More than half", "A quarter to a half", "A tenth to a quarter",
                 "Less than a tenth", "None"]},
]

# The loss each `max_loss` answer states, as a fraction (answer index -> loss).
TOLERABLE_LOSS = [0.05, 0.10, 0.15, 0.25, 0.35]


def score(answers: dict[str, int]) -> dict:
    """Score `answers` ({question id: option index 0-4}); every question is required."""
    missing = [q["id"] for q in QUESTIONS if q["id"] not in answers]
    if missing:
        raise ValueError(f"Unanswered questions: {missing}")
    for q in QUESTIONS:
        a = answers[q["id"]]
        if not isinstance(a, int) or not 0 <= a < len(q["options"]):
            raise ValueError(f"Answer to {q['id']} must be an option index 0-4.")
    total = sum(answers[q["id"]] + 1 for q in QUESTIONS)
    return {
        "score": total,
        "max_score": 5 * len(QUESTIONS),
        # Halves round up (4.5 -> 5); Python's round() would send them to the even side.
        "risk_level": 1 + math.floor((total - len(QUESTIONS)) * 9 / (4 * len(QUESTIONS)) + 0.5),
        "risk_level_max": 10,
        "tolerable_loss": TOLERABLE_LOSS[answers["max_loss"]],
        "answers": {q["id"]: q["options"][answers[q["id"]]] for q in QUESTIONS},
        "method_version": METHOD_VERSION,
        "method_status": METHOD_STATUS,
    }


def check_portfolio(result: dict, profile: dict) -> list[dict]:
    """Compare a scored questionnaire with a risk profile (`RiskProfile.to_dict()`)."""
    drop = abs(profile["drawdown"]["historical_max_drawdown"])
    limit = result["tolerable_loss"]
    return [{
        "id": "drawdown_vs_tolerance",
        "label": "Largest fall from a peak vs largest loss the client can accept",
        "portfolio_value": drop,
        "client_limit": limit,
        "within": drop <= limit,
    }]
