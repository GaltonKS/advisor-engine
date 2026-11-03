"""What every route shares: who is signed in, the checks they must pass, and the helpers
that read and audit on their behalf.

`create_app` (main.py) puts the settings, the store and the auth admin on `app.state`;
the providers below read them from there, so routers never capture them in a closure.

Dependency chain (docs/mfa-plan.md): `base_ctx` (only `/me`) -> `any_ctx` (in
`required` mode, 403 `mfa_required` below aal2) -> `ctx` (approved advisers only). The
multi-factor check comes before the approval check.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd
from fastapi import Depends, HTTPException, Request

from ..assistant.tools import execute_tool
from ..holdings import load_holdings
from .auth import User, current_user
from .auth_admin import AuthAdminError
from .settings import Settings
from .store_base import Store, StoreSession

log_ = logging.getLogger("advisor_engine.api")

# Distinct 403 reasons, so the web app can tell "enter your code" from "awaiting approval".
MFA_REQUIRED = {"code": "mfa_required",
                "message": "Two-step sign-in is required. Enter the code from your authenticator app."}
MFA_FRESH_REQUIRED = {"code": "mfa_fresh_required",
                      "message": "Enter your authenticator code to confirm this action."}


# --- app-wide objects, set by create_app ---------------------------------------------

def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> Store:
    """The store itself (owner connection), for the few routes that need it: operator
    views and factor-removal permits. Everything else goes through `Ctx.db`."""
    return request.app.state.store


def get_auth_admin(request: Request):
    return request.app.state.auth_admin


# --- the signed-in adviser -------------------------------------------------------------

@dataclass
class Ctx:
    """The signed-in adviser, a store session that acts as them, and how they signed in."""
    adv: dict
    db: StoreSession
    user: User
    settings: Settings


def base_ctx(user: User = Depends(current_user), store: Store = Depends(get_store),
             settings: Settings = Depends(get_settings)) -> Ctx:
    """The signed-in adviser, before any multi-factor or approval check (/me only)."""
    db = store.as_user(user.id, user.email, aal=user.aal, amr=user.amr)
    return Ctx(db.get_or_create_adviser(user.id, user.email), db, user, settings)


def any_ctx(c: Ctx = Depends(base_ctx)) -> Ctx:
    """Signed in with a second factor when that is required; active or not (for
    registration and an adviser's own authenticators)."""
    if c.settings.mfa == "required" and c.user.aal != "aal2":
        raise HTTPException(403, MFA_REQUIRED)
    return c


def ctx(c: Ctx = Depends(any_ctx)) -> Ctx:
    """An approved adviser. New sign-ups wait as pending until the operator verifies
    them; row-level security already shows them nothing, and this says why."""
    status = c.adv.get("status", "active")
    if status == "rejected":
        raise HTTPException(403, "Your registration wasn't approved.")
    if status != "active":
        raise HTTPException(403, "Your account is awaiting approval.")
    return c


def require_fresh_code(c: Ctx) -> bool:
    """For sensitive actions: a code entered within FRESH_MFA_SECONDS, even inside a
    verified session. True when one was checked (recorded on the audit row).

    Skipped when MFA is off, and in the `enrol` grace period for sessions without a
    second factor (they have no code to give yet; that is what `enrol` allows)."""
    mfa = c.settings.mfa
    if mfa == "off" or (mfa == "enrol" and c.user.aal != "aal2"):
        return False
    if not c.user.mfa_fresh():
        raise HTTPException(403, MFA_FRESH_REQUIRED)
    return True


# --- audit -----------------------------------------------------------------------------

def session_fields(c: Ctx) -> dict:
    """How the adviser signed in, for audit rows (real sign-in only)."""
    return {"aal": c.user.aal} if c.settings.auth == "supabase" else {}


def log(c: Ctx, event: str, **fields) -> None:
    """Every engine run from a CFP tool is audited with its inputs and result."""
    c.db.write_audit(c.adv["firm_id"], {"event": event, "model": None,
                                        "adviser_id": c.adv["id"],
                                        "firm_id": c.adv["firm_id"], **session_fields(c),
                                        **fields})


def audit_or_log(c: Ctx, event: str, **fields) -> None:
    """Audit rows need an active firm; a pending adviser's goes to the server log."""
    if c.adv.get("status", "active") == "active":
        log(c, event, **fields)
    else:
        log_.warning("%s: adviser %s %s", event, c.adv["id"], fields)


# --- firm-scoped reads (another firm's ids answer 404) ---------------------------------

def own_client(c: Ctx, client_id: str) -> dict:
    client = c.db.get_client(c.adv["firm_id"], client_id)
    if client is None:
        raise HTTPException(404, "Client not found.")
    return client


def own_portfolio(c: Ctx, portfolio_id: str) -> dict:
    p = c.db.get_portfolio(c.adv["firm_id"], portfolio_id)
    # A deleted client's portfolios stay on record but are no longer usable.
    if p is None or c.db.get_client(c.adv["firm_id"], p["client_id"]) is None:
        raise HTTPException(404, "Portfolio not found.")
    return p


def profile_of(c: Ctx, portfolio_id: str) -> dict:
    """Engine output for a stored portfolio, through the same tool the model uses."""
    fid = c.adv["firm_id"]
    res = execute_tool("get_risk_profile",
                       {"holdings_path": str(c.db.holdings_file(fid, portfolio_id))},
                       data_dir=c.db.firm_dir(fid))
    if res.is_error:
        raise HTTPException(422, res.text)
    return json.loads(res.text)


def frame_of(c: Ctx, portfolio_id: str) -> pd.DataFrame:
    return load_holdings(str(c.db.holdings_file(c.adv["firm_id"], portfolio_id)))


# --- model errors ----------------------------------------------------------------------

def assistant_error_message(e: Exception) -> str:
    """What an adviser sees when a model call fails; the details go to the server log.

    Reads the HTTP status the Anthropic SDK puts on its errors (without importing it):
    401/403 and 400/404 are configuration problems (key, workspace, model name), 429
    and 5xx/529 are temporary. Anything else gets the generic message.
    """
    status = getattr(e, "status_code", None)
    if status in (400, 401, 403, 404) or isinstance(e, RuntimeError) and "anthropic" in str(e):
        return ("The assistant isn't set up correctly, so it can't answer right now. "
                "Your administrator can find the details in the server log.")
    if status == 429 or (isinstance(status, int) and status >= 500):
        return "The assistant is busy right now. Please try again in a minute."
    return "The assistant is unavailable right now. Please try again."


# --- authenticators --------------------------------------------------------------------

def factors_of(auth_admin, user_id: str) -> list[dict]:
    try:
        return auth_admin.list_factors(user_id)
    except LookupError:
        return []
    except AuthAdminError as e:
        raise HTTPException(502, str(e)) from None
