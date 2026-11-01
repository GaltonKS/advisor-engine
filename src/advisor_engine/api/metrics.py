"""Pilot metrics for the platform operator: usage across every firm, as counts only.

Built by the stores (`SupabaseStore.pilot_metrics`, `LocalStore.pilot_metrics`) from the
audit log, advisers and clients, and served only to operators (`Settings.operator_emails`)
at GET /operator/metrics. No client names, holdings or questions: counts, dates, and the
approval queue (which the operator needs to act on).

- Weekly active advisers: distinct advisers with any audited action in each of the last
  `WEEKS` weeks (weeks start Monday, UTC). The audit log records actions (questions,
  what-ifs, stress tests, approvals...), not page views, so an adviser who only looked
  at screens that week isn't counted.
- Feature use: audited actions in the last `FEATURE_DAYS` days, by event.
- Funnel: signed up -> registration submitted -> approved -> added a client -> ran an
  analysis. Advisers active before approval existed count as approved without a
  registration.
"""

from __future__ import annotations

WEEKS = 12
FEATURE_DAYS = 30
# Audit events that mean an adviser analysed a portfolio (the last funnel step).
ANALYSIS_EVENTS = ("turn", "what_if", "compare", "stress", "scenario_suggested")


# --- approval reviews ------------------------------------------------------------------
# The operator approves or rejects new sign-ups from the Pilot metrics page. Every
# decision is kept for good in public.adviser_reviews (append-only), with who decided
# (the operator's signed-in email), when, the checks confirmed and a one-line summary.

# BrokerCheck checks: all must be confirmed before approving.
REVIEW_CHECKS = [
    ("name_matches", "Name matches BrokerCheck"),
    ("currently_registered", "Currently registered (investment adviser representative or broker)"),
    ("firm_matches", "Current firm on BrokerCheck matches the firm given"),
    ("email_verified", "Email domain belongs to that firm, or employment confirmed by phone"),
    ("disclosures_reviewed", "Disclosures reviewed"),
]
# Rejection reasons. "other" needs a note.
REJECT_REASONS = [
    ("not_registered", "Not currently registered"),
    ("mismatch", "Name or firm doesn't match BrokerCheck"),
    ("unverifiable", "Couldn't verify identity or employment"),
    ("duplicate", "Duplicate account"),
    ("other", "Other (see note)"),
]
_CHECK_SHORT = {
    "name_matches": "name matches",
    "currently_registered": "currently registered",
    "firm_matches": "firm matches",
    "email_verified": "email/employment verified",
    "disclosures_reviewed": "disclosures reviewed",
}


def review_presets() -> dict:
    return {"checks": [{"key": k, "label": v} for k, v in REVIEW_CHECKS],
            "reject_reasons": [{"key": k, "label": v} for k, v in REJECT_REASONS]}


def review_summary(decision: str, crd: str | None, checks: list[str], reason: str | None) -> str:
    """The one-line record of a decision, written for an auditor."""
    where = f"BrokerCheck CRD {crd}" if crd else "BrokerCheck (no CRD given)"
    if decision == "approved":
        done = "; ".join(_CHECK_SHORT[k] for k, _ in REVIEW_CHECKS if k in checks)
        return f"Approved. {where}: {done}."
    label = dict(REJECT_REASONS).get(reason or "", reason or "no reason")
    return f"Rejected. {where}: {label}."
