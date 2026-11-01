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
