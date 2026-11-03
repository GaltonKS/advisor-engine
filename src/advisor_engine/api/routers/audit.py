"""The firm's audit trail, as a summary (full records stay server-side)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import Ctx, ctx
from .stress import _stress_summary

router = APIRouter()


@router.get("/audit")
def audit(portfolio_id: str | None = None, c: Ctx = Depends(ctx)) -> list[dict]:
    """The firm's audit trail, newest first, as a summary (full records stay server-side)."""
    rows = c.db.read_audit(c.adv["firm_id"])
    if portfolio_id:
        rows = [r for r in rows if r.get("portfolio_id") == portfolio_id]
    keys = ("timestamp", "event", "engine_version", "model", "adviser_id", "client_id",
            "portfolio_id", "draft_id", "turn_id", "user_message", "blocked", "block_reason",
            "edited", "run_id")
    return [{**{k: r.get(k) for k in keys},
             "verified": (r.get("verification") or {}).get("ok"),
             **({"scenario": _stress_summary(r)} if r.get("event") == "stress" else {})}
            for r in reversed(rows)]
