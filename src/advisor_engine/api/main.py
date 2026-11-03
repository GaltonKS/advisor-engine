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

This module builds the app (settings, store, auth admin on `app.state`, middleware) and
includes the routers in `routers/`; shared dependencies are in `deps.py`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from .auth_admin import MemoryAuthAdmin, SupabaseAuthAdmin
from .routers import account, assistant, audit, clients, operator, portfolios, profiling, stress
from .settings import Settings, load_settings
from .store import LocalStore
from .supabase_store import SupabaseStorage, SupabaseStore

log_ = logging.getLogger("advisor_engine.api")

# Included in this order (the order routes were declared before the split).
ROUTERS = (account, operator, clients, portfolios, stress, profiling, assistant, audit)


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
    # Read by the providers in deps.py (get_settings, get_store, get_auth_admin).
    app.state.settings = settings
    app.state.store = store
    app.state.auth_admin = auth_admin

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

    for module in ROUTERS:
        app.include_router(module.router)
    return app


app = create_app()
