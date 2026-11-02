"""HTTP API over the engine and the assistant: `uvicorn advisor_engine.api.main:app`.

Per signed-in adviser and per firm:
- Every figure comes from `execute_tool` / `run_turn`; routes only move engine output
  around and never compute.
- Model replies go through `run_turn`, so the number check, retry and withholding apply.
  The browser never talks to the model.
- Every turn and approval is audited with the adviser and firm that made it.
- Multi-factor sign-in is enforced here, not only in the browser: with
  ADVISOR_REQUIRE_MFA=required every route except /me needs a session that entered an
  authenticator code, and sensitive actions need a code entered in the last few minutes
  (see docs/mfa-plan.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from .. import questionnaire as qn
from ..assistant.chat import _make_client as make_llm_client
from ..assistant.chat import run_turn
from ..assistant.scenario import MockSuggester, suggest_shocks
from ..assistant.mock_client import MockClient
from ..assistant.numcheck import verify
from ..assistant.tools import engine_profile, execute_tool
from ..compare import compare_profiles
from ..data import get_market_data
from ..holdings import COLUMNS, load_holdings, validate_holdings
from ..stress import run_scenario, scenario_list, stress_test
from .metrics import REJECT_REASONS, REVIEW_CHECKS, review_presets, review_summary
from .auth import FRESH_MFA_SECONDS, User, current_user
from .auth_admin import AuthAdminError, MemoryAuthAdmin, SupabaseAuthAdmin, verified
from .settings import Settings, load_settings
from .store import LocalStore
from .supabase_store import SupabaseStorage, SupabaseStore

log_ = logging.getLogger("advisor_engine.api")

DRAFT_REQUEST = "Please write a client-ready explanation of this portfolio's risk, for my review."
TOOL_LABELS = {"get_risk_profile": "Risk profile", "get_holdings": "Holdings",
               "run_what_if": "What-if", "run_stress_test": "Stress test", "show_chart": "Chart",
               "refer_to_stress_test": "Stress test referral"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# Distinct 403 reasons, so the web app can tell "enter your code" from "awaiting approval".
MFA_REQUIRED = {"code": "mfa_required",
                "message": "Two-step sign-in is required. Enter the code from your authenticator app."}
MFA_FRESH_REQUIRED = {"code": "mfa_fresh_required",
                      "message": "Enter your authenticator code to confirm this action."}


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=100)


class ClientRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RegistrationIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    crd: str = Field(pattern=r"^[0-9]{1,8}$")  # FINRA CRD numbers are digits only
    firm_name: str = Field(min_length=1, max_length=200)


class ReviewIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    decision: Literal["approved", "rejected"]
    checks: list[str] = Field(default_factory=list, max_length=10)
    reason: str | None = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=1000)


class FactorIn(BaseModel):
    factor_id: str = Field(min_length=1, max_length=100)


class TurnIn(BaseModel):
    message: str = Field(default="", max_length=4000)
    draft: bool = False  # ask for a client-ready draft instead of answering `message`


class ApproveIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class HoldingRow(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    shares: float
    price: float
    sector: str = Field(default="", max_length=60)  # blank -> Unclassified
    asset_class: str = Field(default="", max_length=60)  # blank -> equity


class HoldingsIn(BaseModel):
    holdings: list[HoldingRow] = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=120)


class StressIn(BaseModel):
    scenario_id: str | None = None  # a preset, or else the custom shocks below
    sector_shocks: dict[str, float] = Field(default_factory=dict)
    default_shock: float = 0.0
    asset_class_shocks: dict[str, float] = Field(default_factory=dict)  # fixed_income, preferred, cash
    # A custom scenario's own words, and the AI suggestion it started from (if any), so
    # the result can say where its assumptions came from and the audit log records both.
    description: str = Field(default="", max_length=1000)
    suggested: dict | None = None


class SuggestIn(BaseModel):
    description: str = Field(min_length=1, max_length=1000)


def _assumptions(body: StressIn) -> dict:
    """Where a stress run's assumptions came from: a preset, the adviser, or an AI
    suggestion (kept as suggested, or edited by the adviser before running)."""
    if body.scenario_id:
        return {"source": "preset"}
    if not body.suggested:
        return {"source": "adviser", "description": body.description}

    def norm(d: dict | None) -> dict:
        return {k: round(float(v), 4) for k, v in (d or {}).items()}

    s = body.suggested
    same = (norm(s.get("sector_shocks")) == norm(body.sector_shocks)
            and norm(s.get("asset_class_shocks")) == norm(body.asset_class_shocks)
            and round(float(s.get("default_shock", 0.0)), 4) == round(body.default_shock, 4))
    return {"source": "ai_suggested" if same else "ai_suggested_edited",
            "description": body.description or s.get("description", ""),
            "label": s.get("label", ""), "model": s.get("model", "")}


class QuestionnaireIn(BaseModel):
    answers: dict[str, int]


def market_data_label() -> str:
    """The configured data source's label, or the reason it can't be built."""
    try:
        return get_market_data().returns_source
    except Exception:  # misconfigured (e.g. no key): profiles will say why
        return "unavailable"


def _frame(rows: list[HoldingRow]) -> pd.DataFrame:
    try:
        return validate_holdings(pd.DataFrame([r.model_dump() for r in rows]))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _engine_profile(holdings: pd.DataFrame) -> dict:
    """The same payload get_risk_profile returns, for holdings that aren't a stored file."""
    try:
        return engine_profile(holdings)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _holdings_rows(df: pd.DataFrame) -> list[dict]:
    return [{k: (float(v) if k in ("shares", "price", "value", "weight") else v)
             for k, v in row.items()} for row in df.to_dict("records")]


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


def _tool_view(call: dict) -> dict:
    """A tool call as the browser sees it: engine output, but not server file paths."""
    try:
        output = json.loads(call["output"])
    except ValueError:
        output = call["output"]
    return {"name": call["name"], "label": TOOL_LABELS.get(call["name"], call["name"]),
            "is_error": call["is_error"], "holdings_sha256": call.get("holdings_sha256"),
            "output": output}


@dataclass
class Ctx:
    """The signed-in adviser, a store session that acts as them, and how they signed in."""
    adv: dict
    db: object
    user: User


def make_store(settings: Settings):
    if settings.store == "supabase":
        return SupabaseStore(settings.supabase_db_url,
                             SupabaseStorage(settings.supabase_url, settings.supabase_secret_key),
                             cache_dir=settings.store_dir / "cache")
    return LocalStore(settings.store_dir)


def make_auth_admin(settings: Settings):
    if settings.auth == "supabase" and settings.supabase_secret_key:
        return SupabaseAuthAdmin(settings.supabase_url, settings.supabase_secret_key)
    return MemoryAuthAdmin()  # dev auth has no authenticators


def create_app(settings: Settings | None = None, store=None, auth_admin=None) -> FastAPI:
    settings = settings or load_settings()
    store = store or make_store(settings)
    auth_admin = auth_admin or make_auth_admin(settings)
    app = FastAPI(title="Advisor Engine API", version=__version__)
    app.state.settings = settings
    app.state.store = store
    @app.middleware("http")
    async def server_errors_as_json(request: Request, call_next):
        """Turn an unexpected error into a 500 *inside* the CORS layer.

        Starlette answers unhandled exceptions outside every middleware, so that response
        would carry no CORS headers and the browser would report a misleading "Failed to
        fetch". Registered before CORS, so CORS wraps it. Details go to the server log only.
        """
        try:
            return await call_next(request)
        except Exception:
            log_.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "Server error. It has been logged; please try again."},
                                status_code=500)

    # The database enforces its own multi-factor mode; one stricter than the API's would
    # leave password-only sessions past the API but shown nothing. Say so in the log.
    if hasattr(store, "mfa_mode"):
        try:
            db_mode = store.mfa_mode()
            if db_mode != settings.mfa:
                log_.warning("Multi-factor mode differs: API %s, database %s.", settings.mfa, db_mode)
        except Exception:
            log_.warning("Could not read the database's multi-factor mode.", exc_info=True)

    # In the deploy log, so a CORS mismatch is visible without guessing.
    log_.warning("CORS allowed origins: %s", ", ".join(settings.allowed_origins) or "(none)")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "X-Dev-User"],
    )

    def base_ctx(user: User = Depends(current_user)) -> Ctx:
        """The signed-in adviser, before any multi-factor or approval check (/me only)."""
        db = store.as_user(user.id, user.email, aal=user.aal, amr=user.amr)
        return Ctx(db.get_or_create_adviser(user.id, user.email), db, user)

    def any_ctx(c: Ctx = Depends(base_ctx)) -> Ctx:
        """Signed in with a second factor when that is required; active or not (for
        registration and an adviser's own authenticators)."""
        if settings.mfa == "required" and c.user.aal != "aal2":
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
        if settings.mfa == "off" or (settings.mfa == "enrol" and c.user.aal != "aal2"):
            return False
        if not c.user.mfa_fresh():
            raise HTTPException(403, MFA_FRESH_REQUIRED)
        return True

    def audit_or_log(c: Ctx, event: str, **fields) -> None:
        """Audit rows need an active firm; a pending adviser's goes to the server log."""
        if c.adv.get("status", "active") == "active":
            log(c, event, **fields)
        else:
            log_.warning("%s: adviser %s %s", event, c.adv["id"], fields)

    def factors_of(user_id: str) -> list[dict]:
        try:
            return auth_admin.list_factors(user_id)
        except LookupError:
            return []
        except AuthAdminError as e:
            raise HTTPException(502, str(e)) from None

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

    def log(c: Ctx, event: str, **fields) -> None:
        """Every engine run from a CFP tool is audited with its inputs and result."""
        c.db.write_audit(c.adv["firm_id"], {"event": event, "model": None,
                                            "adviser_id": c.adv["id"],
                                            "firm_id": c.adv["firm_id"], **session_fields(c),
                                            **fields})

    def session_fields(c: Ctx) -> dict:
        """How the adviser signed in, for audit rows (real sign-in only)."""
        return {"aal": c.user.aal} if settings.auth == "supabase" else {}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "engine_version": __version__}

    @app.get("/me")
    def me(c: Ctx = Depends(base_ctx)) -> dict:
        return {"adviser": c.adv, "firm": c.db.get_firm(c.adv["firm_id"]), "auth": settings.auth,
                "llm": settings.llm, "daily_turn_limit": settings.daily_turn_limit,
                "engine_version": __version__, "returns_source": market_data_label(),
                "operator": settings.is_operator(c.adv.get("email")),
                "mfa": {"mode": settings.mfa, "deadline": settings.mfa_deadline or None,
                        "aal": c.user.aal, "fresh_seconds": FRESH_MFA_SECONDS}}

    # --- multi-factor sign-in --------------------------------------------------------
    # Enrolling and entering codes happen between the browser and Supabase. These routes
    # are the parts the server must decide: recording an enrolment, removing a factor,
    # and the firm owner's view.

    @app.post("/me/mfa/enrolled")
    def mfa_enrolled(body: FactorIn, c: Ctx = Depends(any_ctx)) -> dict:
        """Audit a newly enrolled authenticator, once Supabase confirms it is verified."""
        factor = next((f for f in verified(factors_of(c.user.id)) if f["id"] == body.factor_id), None)
        if factor is None:
            raise HTTPException(404, "Authenticator not found.")
        audit_or_log(c, "mfa_enrolled", inputs={"factor_id": factor["id"],
                                                "friendly_name": factor["friendly_name"]})
        return factor

    @app.delete("/me/mfa/factors/{factor_id}", status_code=204)
    def mfa_remove(factor_id: str, c: Ctx = Depends(any_ctx)) -> None:
        """Remove one of the adviser's own authenticators: needs a fresh code, and never
        the last verified one while two-step sign-in is required."""
        factors = factors_of(c.user.id)
        factor = next((f for f in factors if f["id"] == factor_id), None)
        if factor is None:
            raise HTTPException(404, "Authenticator not found.")
        fresh = require_fresh_code(c)
        if (settings.mfa == "required" and factor["status"] == "verified"
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

    @app.get("/firm/mfa")
    def firm_mfa(c: Ctx = Depends(ctx)) -> dict:
        """The firm owner's view: which of the firm's advisers have set up two-step
        sign-in. Names, counts and dates only; nothing secret."""
        if c.adv.get("role") != "owner":
            raise HTTPException(403, "Only the firm's owner can see this.")
        rows = []
        for a in c.db.list_firm_advisers(c.adv["firm_id"]):
            factors = verified(factors_of(a["id"]))
            rows.append({"adviser_id": a["id"], "email": a["email"],
                         "full_name": a.get("full_name"), "role": a.get("role"),
                         "status": a.get("status", "active"), "enrolled": bool(factors),
                         "factor_count": len(factors),
                         "latest_factor_at": max((f["created_at"] or "" for f in factors),
                                                 default=None)})
        return {"mode": settings.mfa, "deadline": settings.mfa_deadline or None, "advisers": rows}

    @app.post("/me/registration")
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

    @app.get("/operator/metrics")
    def operator_metrics(c: Ctx = Depends(ctx)) -> dict:
        """Pilot usage across every firm (counts only), for the platform operator.
        Anyone else gets 404, so the route's existence isn't revealed."""
        if not settings.is_operator(c.adv.get("email")):
            raise HTTPException(404, "Not found.")
        return {**store.pilot_metrics(), "review_presets": review_presets()}

    @app.post("/operator/reviews")
    def review(body: ReviewIn, c: Ctx = Depends(ctx)) -> dict:
        """Approve or reject a pending sign-up. Who decided (the operator's signed-in email)
        and when are recorded automatically, with the checks and a one-line summary, in the
        append-only approval audit trail."""
        if not settings.is_operator(c.adv.get("email")):
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

    # --- clients and portfolios ------------------------------------------------------

    @app.get("/clients")
    def list_clients(c: Ctx = Depends(ctx)) -> list[dict]:
        return c.db.list_clients(c.adv["firm_id"])

    @app.post("/clients", status_code=201)
    def create_client(body: ClientIn, c: Ctx = Depends(ctx)) -> dict:
        return c.db.create_client(c.adv["firm_id"], c.adv["id"], body.model_dump())

    @app.get("/clients/{client_id}")
    def get_client(client_id: str, c: Ctx = Depends(ctx)) -> dict:
        client = own_client(c, client_id)
        answers = client.get("questionnaire")
        return {**client, "portfolios": c.db.list_portfolios(c.adv["firm_id"], client_id),
                # Scored on read, so a method change never leaves a stale stored score.
                "questionnaire_result": qn.score(answers) if answers else None}

    @app.patch("/clients/{client_id}")
    def rename_client(client_id: str, body: ClientRenameIn, c: Ctx = Depends(ctx)) -> dict:
        old = own_client(c, client_id)["name"]
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "The client name can't be blank.")
        client = c.db.rename_client(c.adv["firm_id"], client_id, name)
        if old != name:
            log(c, "client_renamed", client_id=client_id, inputs={"from": old, "to": name})
        return client

    @app.delete("/clients/{client_id}", status_code=204)
    def delete_client(client_id: str, c: Ctx = Depends(ctx)) -> None:
        """Remove a client from the firm's list. A soft delete: their portfolios, drafts
        and the audit trail are kept on record, but the app no longer shows or uses them."""
        client = own_client(c, client_id)
        fresh = require_fresh_code(c)
        try:
            c.db.delete_client(c.adv["firm_id"], client_id, c.adv["id"])
        except PermissionError:
            # The database checks the code too (it can differ by a clock tick, or by mode).
            raise HTTPException(403, MFA_FRESH_REQUIRED) from None
        log(c, "client_deleted", client_id=client_id, inputs={"name": client["name"]},
            fresh_code=fresh)

    @app.post("/clients/{client_id}/portfolios", status_code=201)
    async def upload_portfolio(client_id: str, file: UploadFile = File(...),
                               c: Ctx = Depends(ctx)) -> dict:
        own_client(c, client_id)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Holdings file is larger than 2 MB.")
        name = re.sub(r"[^\w.-]", "_", file.filename or "holdings.csv")
        if not name.lower().endswith(".csv"):
            raise HTTPException(422, "The file must be a .csv.")
        try:
            load_holdings(data)
        except Exception as e:  # any load failure means the file is not usable
            raise HTTPException(422, f"{name} was rejected: {e}") from e
        return c.db.add_portfolio(c.adv["firm_id"], client_id, c.adv["id"], name, data,
                                   hashlib.sha256(data).hexdigest())

    @app.get("/portfolios/{portfolio_id}/profile")
    def profile(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
        p = own_portfolio(c, portfolio_id)
        return {"holdings_sha256": p["sha256"], "profile": profile_of(c, portfolio_id)}

    # --- CFP tools: holdings, what-if, compare, stress, questionnaire ---------------

    @app.get("/portfolios/{portfolio_id}/holdings")
    def holdings(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
        own_portfolio(c, portfolio_id)
        return {"holdings": _holdings_rows(frame_of(c, portfolio_id))}

    @app.post("/clients/{client_id}/portfolios/holdings", status_code=201)
    def save_holdings(client_id: str, body: HoldingsIn, c: Ctx = Depends(ctx)) -> dict:
        """Save edited holdings as a new portfolio version; the original is never changed."""
        own_client(c, client_id)
        data = _frame(body.holdings)[COLUMNS].to_csv(index=False).encode()
        p = c.db.add_portfolio(c.adv["firm_id"], client_id, c.adv["id"], "edited-holdings.csv",
                               data, hashlib.sha256(data).hexdigest(),
                               label=body.label.strip(), source="editor")
        log(c, "portfolio_saved", client_id=client_id, portfolio_id=p["id"],
            holdings_sha256=p["sha256"])
        return p

    @app.post("/portfolios/{portfolio_id}/what-if")
    def what_if(portfolio_id: str, body: HoldingsIn, c: Ctx = Depends(ctx)) -> dict:
        p = own_portfolio(c, portfolio_id)
        before = profile_of(c, portfolio_id)
        after = _engine_profile(_frame(body.holdings))
        comparison = compare_profiles(before, after)
        log(c, "what_if", client_id=p["client_id"], portfolio_id=portfolio_id,
            holdings_sha256=p["sha256"],
            inputs={"holdings": [r.model_dump() for r in body.holdings]}, result=comparison)
        return {"before": before, "after": after, "comparison": comparison}

    @app.get("/portfolios/{portfolio_id}/compare/{other_id}")
    def compare(portfolio_id: str, other_id: str, c: Ctx = Depends(ctx)) -> dict:
        p, o = own_portfolio(c, portfolio_id), own_portfolio(c, other_id)
        before, after = profile_of(c, portfolio_id), profile_of(c, other_id)
        comparison = compare_profiles(before, after)
        log(c, "compare", client_id=p["client_id"], portfolio_id=portfolio_id,
            inputs={"other_portfolio_id": other_id, "other_client_id": o["client_id"]},
            result=comparison)
        return {"before": before, "after": after, "comparison": comparison}

    @app.get("/stress/scenarios")
    def scenarios(c: Ctx = Depends(ctx)) -> list[dict]:
        return scenario_list()

    @app.post("/portfolios/{portfolio_id}/stress")
    def stress(portfolio_id: str, body: StressIn, c: Ctx = Depends(ctx)) -> dict:
        p = own_portfolio(c, portfolio_id)
        df = frame_of(c, portfolio_id)
        try:
            if body.scenario_id:
                result = run_scenario(df, body.scenario_id)
            else:
                label = (body.suggested or {}).get("label") or "Custom scenario"
                result = {"scenario": {"id": "custom", "label": label,
                                       "sectors": body.sector_shocks,
                                       "default": body.default_shock,
                                       "asset_classes": body.asset_class_shocks},
                          **stress_test(df, body.sector_shocks, body.default_shock,
                                        body.asset_class_shocks)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        result["assumptions"] = _assumptions(body)
        log(c, "stress", client_id=p["client_id"], portfolio_id=portfolio_id,
            holdings_sha256=p["sha256"], inputs=body.model_dump(), result=result)
        return result

    @app.post("/portfolios/{portfolio_id}/stress/suggest")
    def suggest(portfolio_id: str, body: SuggestIn, c: Ctx = Depends(ctx)) -> dict:
        """AI-suggested shocks for a described scenario. Assumptions only: the adviser
        reviews them, and the engine computes the result when they run the scenario."""
        p = own_portfolio(c, portfolio_id)
        df = frame_of(c, portfolio_id)
        if not c.db.bump_turns(c.adv["id"], settings.daily_turn_limit):
            raise HTTPException(429, "Daily assistant limit reached; try again tomorrow.")
        try:
            client = MockSuggester() if settings.llm == "mock" else make_llm_client()
            suggestion = suggest_shocks(body.description, df, client)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # model API error, missing package, ...
            log_.exception("Scenario suggestion failed for portfolio %s", portfolio_id)
            raise HTTPException(502, assistant_error_message(e)) from e
        log(c, "scenario_suggested", client_id=p["client_id"], portfolio_id=portfolio_id,
            holdings_sha256=p["sha256"], model=suggestion["model"],
            inputs={"description": body.description}, result=suggestion)
        return suggestion

    @app.get("/questionnaire")
    def questionnaire(c: Ctx = Depends(ctx)) -> dict:
        return {"questions": qn.QUESTIONS, "method_version": qn.METHOD_VERSION,
                "method_status": qn.METHOD_STATUS}

    @app.put("/clients/{client_id}/questionnaire")
    def save_questionnaire(client_id: str, body: QuestionnaireIn, c: Ctx = Depends(ctx)) -> dict:
        own_client(c, client_id)
        try:
            result = qn.score(body.answers)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        c.db.save_questionnaire(c.adv["firm_id"], client_id, body.answers)
        log(c, "questionnaire_saved", client_id=client_id, inputs=body.answers, result=result)
        return result

    @app.get("/portfolios/{portfolio_id}/suitability")
    def suitability(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
        p = own_portfolio(c, portfolio_id)
        client = own_client(c, p["client_id"])
        if not client.get("questionnaire"):
            raise HTTPException(409, "Complete the client's risk questionnaire first.")
        result = qn.score(client["questionnaire"])
        return {"questionnaire": result,
                "checks": qn.check_portfolio(result, profile_of(c, portfolio_id))}

    @app.get("/portfolios/{portfolio_id}/drafts")
    def drafts(portfolio_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
        own_portfolio(c, portfolio_id)
        return [{k: v for k, v in d.items() if k != "tool_calls"}
                for d in c.db.list_drafts(c.adv["firm_id"], portfolio_id)]

    # --- assistant -------------------------------------------------------------------

    @app.get("/portfolios/{portfolio_id}/messages")
    def messages(portfolio_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
        own_portfolio(c, portfolio_id)
        return c.db.get_conversation(c.adv["firm_id"], portfolio_id)["messages"]

    @app.post("/portfolios/{portfolio_id}/turns")
    def ask(portfolio_id: str, body: TurnIn, c: Ctx = Depends(ctx)) -> dict:
        fid = c.adv["firm_id"]
        portfolio = own_portfolio(c, portfolio_id)
        question = DRAFT_REQUEST if body.draft else body.message.strip()
        if not question:
            raise HTTPException(422, "Ask a question or request a draft.")
        if not c.db.bump_turns(c.adv["id"], settings.daily_turn_limit):
            raise HTTPException(429, "Daily assistant limit reached; try again tomorrow.")
        client = c.db.get_client(fid, portfolio["client_id"]) or {}
        conversation = c.db.get_conversation(fid, portfolio_id)
        # One id per turn, on the audit record and on both chat messages, so an audit
        # entry can link back to the exact exchange in the conversation.
        turn_id = uuid.uuid4().hex
        try:
            result = run_turn(
                question, conversation["history"],
                MockClient() if settings.llm == "mock" else None,
                firm_profile={k: client[k] for k in ("objective", "tone") if client.get(k)},
                default_holdings=str(c.db.holdings_file(fid, portfolio_id)),
                data_dir=c.db.firm_dir(fid),
                audit_sink=lambda record: c.db.write_audit(fid, record),
                audit_extra={"event": "turn", "adviser_id": c.adv["id"], "firm_id": fid,
                             **session_fields(c),
                             "client_id": portfolio["client_id"], "portfolio_id": portfolio_id,
                             "turn_id": turn_id},
            )
        except Exception as e:  # missing package, model API error, ...
            # The raw error can carry request ids and account details; keep it server-side.
            log_.exception("Assistant turn failed for portfolio %s", portfolio_id)
            raise HTTPException(502, assistant_error_message(e)) from e

        draft_id = None
        if body.draft and not result.blocked:
            draft_id = c.db.create_draft(fid, {
                "portfolio_id": portfolio_id, "created_by": c.adv["id"],
                "original": result.reply, "tool_calls": result.tool_calls})["id"]
        reply = {"role": "assistant", "content": result.reply, "blocked": result.blocked,
                 "verification": result.verification,
                 "tool_calls": [_tool_view(call) for call in result.tool_calls], "draft_id": draft_id,
                 "turn_id": turn_id}
        conversation["history"] = result.history
        conversation["messages"] += [{"role": "user", "content": question, "turn_id": turn_id}, reply]
        c.db.save_conversation(fid, portfolio_id, conversation)
        return reply

    @app.post("/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: ApproveIn, c: Ctx = Depends(ctx)) -> dict:
        fid = c.adv["firm_id"]
        draft = c.db.get_draft(fid, draft_id)
        if draft is None:
            raise HTTPException(404, "Draft not found.")
        p = c.db.get_portfolio(fid, draft["portfolio_id"])
        if p is None or c.db.get_client(fid, p["client_id"]) is None:  # client deleted
            raise HTTPException(404, "Draft not found.")
        # An approved report can be edited again later (from Galton AI or the Client
        # report tab). Each revision is re-checked, re-approved and logged on its own as
        # `draft_revised`, with the text it replaced; nothing is overwritten silently.
        revising = bool(draft["approved"])
        if revising and body.text == draft.get("approved_text"):
            raise HTTPException(409, "This draft is already approved with this text.")
        # Edits are re-checked: an adviser can reword, but not add a figure the engine
        # never produced.
        check = verify(body.text, [call["output"] for call in draft["tool_calls"]])
        if not check["ok"]:
            raise HTTPException(422, {"message": "These figures are not from the engine.",
                                      "unmatched": check["unmatched"]})
        record = c.db.write_audit(fid, {
            "event": "draft_revised" if revising else "draft_approved", "model": None,
            "adviser_id": c.adv["id"],
            "approved_by_email": c.adv["email"], "firm_id": fid,
            "portfolio_id": draft["portfolio_id"], "draft_id": draft_id,
            "user_message": DRAFT_REQUEST, "tool_calls": draft["tool_calls"],
            "reply": draft["original"], "approved_text": body.text,
            **({"previous_text": draft.get("approved_text")} if revising else {}),
            "edited": body.text != draft["original"], "verification": check, "blocked": False,
            **session_fields(c),
        })
        draft.update(approved=True, approved_text=body.text, approved_by=c.adv["id"],
                     approved_at=record["timestamp"])
        c.db.save_draft(fid, draft)
        return draft

    @app.get("/audit")
    def audit(portfolio_id: str | None = None, c: Ctx = Depends(ctx)) -> list[dict]:
        """The firm's audit trail, newest first, as a summary (full records stay server-side)."""
        rows = c.db.read_audit(c.adv["firm_id"])
        if portfolio_id:
            rows = [r for r in rows if r.get("portfolio_id") == portfolio_id]
        keys = ("timestamp", "event", "engine_version", "model", "adviser_id", "client_id",
                "portfolio_id", "draft_id", "turn_id", "user_message", "blocked", "block_reason",
                "edited")
        return [{**{k: r.get(k) for k in keys},
                 "verified": (r.get("verification") or {}).get("ok")} for r in reversed(rows)]

    return app


app = create_app()
