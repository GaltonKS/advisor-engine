"""Health, the signed-in adviser, registration and two-step sign-in.

Enrolling and entering codes happen between the browser and Supabase. The multi-factor
routes here are the parts the server must decide: recording an enrolment, removing a
factor, and the firm owner's view.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ... import __version__
from ...data import get_market_data
from ..auth import FRESH_MFA_SECONDS
from ..auth_admin import AuthAdminError, verified
from ..deps import (Ctx, any_ctx, audit_or_log, base_ctx, ctx, factors_of, get_auth_admin,
                    get_store, log_, require_fresh_code)
from ..schemas import FactorIn, RegistrationIn

router = APIRouter()


def market_data_label() -> str:
    """The configured data source's label, or the reason it can't be built."""
    try:
        return get_market_data().returns_source
    except Exception:  # misconfigured (e.g. no key): profiles will say why
        return "unavailable"


@router.get("/health")
def health() -> dict:
    return {"ok": True, "engine_version": __version__}


@router.get("/me")
def me(c: Ctx = Depends(base_ctx)) -> dict:
    settings = c.settings
    return {"adviser": c.adv, "firm": c.db.get_firm(c.adv["firm_id"]), "auth": settings.auth,
            "llm": settings.llm, "daily_turn_limit": settings.daily_turn_limit,
            "engine_version": __version__, "returns_source": market_data_label(),
            "operator": settings.is_operator(c.adv.get("email")),
            "mfa": {"mode": settings.mfa, "deadline": settings.mfa_deadline or None,
                    "aal": c.user.aal, "fresh_seconds": FRESH_MFA_SECONDS}}


# --- multi-factor sign-in --------------------------------------------------------------

@router.post("/me/mfa/enrolled")
def mfa_enrolled(body: FactorIn, c: Ctx = Depends(any_ctx),
                 auth_admin=Depends(get_auth_admin)) -> dict:
    """Audit a newly enrolled authenticator, once Supabase confirms it is verified."""
    factor = next((f for f in verified(factors_of(auth_admin, c.user.id))
                   if f["id"] == body.factor_id), None)
    if factor is None:
        raise HTTPException(404, "Authenticator not found.")
    audit_or_log(c, "mfa_enrolled", inputs={"factor_id": factor["id"],
                                            "friendly_name": factor["friendly_name"]})
    return factor


@router.delete("/me/mfa/factors/{factor_id}", status_code=204)
def mfa_remove(factor_id: str, c: Ctx = Depends(any_ctx), store=Depends(get_store),
               auth_admin=Depends(get_auth_admin)) -> None:
    """Remove one of the adviser's own authenticators: needs a fresh code, and never
    the last verified one while two-step sign-in is required."""
    factors = factors_of(auth_admin, c.user.id)
    factor = next((f for f in factors if f["id"] == factor_id), None)
    if factor is None:
        raise HTTPException(404, "Authenticator not found.")
    fresh = require_fresh_code(c)
    if (c.settings.mfa == "required" and factor["status"] == "verified"
            and len(verified(factors)) == 1):
        raise HTTPException(409, "You can't remove your only authenticator while two-step "
                                 "sign-in is required. Add another one first.")
    # Supabase refuses to delete a verified factor without this permit, so a browser
    # can't skip these checks by unenrolling directly (see the migration).
    store.allow_factor_removal(c.user.id, factor_id)
    try:
        auth_admin.delete_factor(c.user.id, factor_id)
    except LookupError:
        raise HTTPException(404, "Authenticator not found.") from None
    except AuthAdminError as e:
        raise HTTPException(502, str(e)) from None
    audit_or_log(c, "mfa_factor_removed", fresh_code=fresh,
                 inputs={"factor_id": factor_id, "friendly_name": factor["friendly_name"]})


@router.get("/firm/mfa")
def firm_mfa(c: Ctx = Depends(ctx), auth_admin=Depends(get_auth_admin)) -> dict:
    """The firm owner's view: which of the firm's advisers have set up two-step
    sign-in. Names, counts and dates only; nothing secret."""
    if c.adv.get("role") != "owner":
        raise HTTPException(403, "Only the firm's owner can see this.")
    rows = []
    for a in c.db.list_firm_advisers(c.adv["firm_id"]):
        factors = verified(factors_of(auth_admin, a["id"]))
        rows.append({"adviser_id": a["id"], "email": a["email"],
                     "full_name": a.get("full_name"), "role": a.get("role"),
                     "status": a.get("status", "active"), "enrolled": bool(factors),
                     "factor_count": len(factors),
                     "latest_factor_at": max((f["created_at"] or "" for f in factors),
                                             default=None)})
    return {"mode": c.settings.mfa, "deadline": c.settings.mfa_deadline or None,
            "advisers": rows}


@router.post("/me/registration")
def register(body: RegistrationIn, c: Ctx = Depends(any_ctx)) -> dict:
    """A pending adviser's name, CRD number and firm, for the operator to verify."""
    try:
        adv = c.db.submit_registration(body.full_name.strip(), body.crd,
                                       body.firm_name.strip(), user_id=c.adv["id"])
    except PermissionError as e:
        raise HTTPException(409, "Registration is only for accounts awaiting approval.") from e
    # Audit rows need an active firm, so this goes to the server log for the operator.
    log_.warning("Registration submitted: adviser %s, CRD %s", adv["id"], body.crd)
    return {"adviser": adv}
