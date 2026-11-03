"""The platform operator's routes: pilot metrics and sign-up reviews.

Both read across every firm, so they use the store's owner connection, never a user
session, and answer 404 to anyone who isn't an operator (the routes' existence isn't
revealed).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import Ctx, ctx, get_store
from ..metrics import REJECT_REASONS, REVIEW_CHECKS, review_presets, review_summary
from ..schemas import ReviewIn

router = APIRouter()


@router.get("/operator/metrics")
def operator_metrics(c: Ctx = Depends(ctx), store=Depends(get_store)) -> dict:
    """Pilot usage across every firm (counts only), for the platform operator.
    Anyone else gets 404, so the route's existence isn't revealed."""
    if not c.settings.is_operator(c.adv.get("email")):
        raise HTTPException(404, "Not found.")
    return {**store.pilot_metrics(), "review_presets": review_presets()}


@router.post("/operator/reviews")
def review(body: ReviewIn, c: Ctx = Depends(ctx), store=Depends(get_store)) -> dict:
    """Approve or reject a pending sign-up. Who decided (the operator's signed-in email)
    and when are recorded automatically, with the checks and a one-line summary, in the
    append-only approval audit trail."""
    if not c.settings.is_operator(c.adv.get("email")):
        raise HTTPException(404, "Not found.")
    known = {k for k, _ in REVIEW_CHECKS}
    checks = [k for k, _ in REVIEW_CHECKS if k in set(body.checks)]
    if set(body.checks) - known:
        raise HTTPException(422, "Unknown check.")
    reason = None
    if body.decision == "approved":
        if len(checks) != len(REVIEW_CHECKS):
            raise HTTPException(422, "Confirm every BrokerCheck check before approving.")
    else:
        reason = body.reason
        if reason not in dict(REJECT_REASONS):
            raise HTTPException(422, "Choose a reason for rejecting.")
        if reason == "other" and not body.note.strip():
            raise HTTPException(422, "Add a note explaining the rejection.")
        checks = []
    pending = {p["email"].lower(): p for p in store.pilot_metrics()["pending"]}
    target = pending.get(body.email.strip().lower())
    if target is None:
        raise HTTPException(404, "No pending sign-up with that email.")
    summary = review_summary(body.decision, target.get("crd"), checks, reason)
    try:
        return store.review_adviser(body.email.strip(), body.decision, c.adv["email"], checks,
                                    reason, summary, body.note.strip() or None)
    except LookupError as e:
        raise HTTPException(404, "No pending sign-up with that email.") from e
