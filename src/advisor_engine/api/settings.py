"""API configuration, read once from the environment.

Rules enforced here rather than left to deployment discipline:
- Live model calls need real sign-in. With dev auth the assistant is always the mock,
  whatever ADVISOR_LLM says, so an unauthenticated server can never spend API credits.
- Dev auth (a trusted `X-Dev-User` header) refuses to start on Render, so a
  misconfigured deploy fails loudly instead of letting anyone pick an identity.
- On Render the data must live in Supabase (SUPABASE_DB_URL): the local store would be
  wiped on every deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..assistant._paths import DATA_DIR
from ..assistant.chat import llm_mode

DEFAULT_DAILY_TURN_LIMIT = 200


@dataclass(frozen=True)
class Settings:
    auth: str  # "supabase" | "dev"
    store_dir: Path
    supabase_url: str = ""
    supabase_jwt_secret: str = ""  # legacy HS256 secret; empty means verify via JWKS
    supabase_db_url: str = ""  # Postgres (transaction pooler); set means SupabaseStore
    supabase_secret_key: str = ""  # sb_secret_... (or legacy service_role), Storage only
    allowed_origins: list[str] = field(default_factory=list)
    daily_turn_limit: int = DEFAULT_DAILY_TURN_LIMIT
    # Emails of the platform operators (lower case): only they see the pilot metrics page,
    # which reads across every firm. Set with ADVISOR_OPERATORS (comma-separated).
    operator_emails: tuple[str, ...] = ()

    def is_operator(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() in self.operator_emails

    @property
    def store(self) -> str:
        return "supabase" if self.supabase_db_url else "local"

    @property
    def llm(self) -> str:
        """"mock" or "anthropic". Live only once sign-in is real (see module docstring)."""
        return llm_mode() if self.auth == "supabase" else "mock"


def load_settings() -> Settings:
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    auth = os.environ.get("ADVISOR_AUTH", "").strip().lower() or (
        "supabase" if supabase_url else "dev"
    )
    if auth not in ("supabase", "dev"):
        raise ValueError(f"ADVISOR_AUTH must be 'supabase' or 'dev', not {auth!r}.")
    if auth == "dev" and os.environ.get("RENDER"):
        raise RuntimeError("Dev auth is for local use only; set SUPABASE_URL on Render.")
    if auth == "supabase" and not supabase_url:
        raise RuntimeError("ADVISOR_AUTH=supabase needs SUPABASE_URL.")
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if os.environ.get("RENDER") and not db_url:
        raise RuntimeError("On Render, set SUPABASE_DB_URL: the local store is wiped on deploy.")
    if db_url and not (supabase_url and secret_key):
        raise RuntimeError("SUPABASE_DB_URL also needs SUPABASE_URL and SUPABASE_SECRET_KEY.")
    # Browsers send the origin with no trailing slash ("https://app.example.com"), and CORS
    # compares it exactly, so "https://app.example.com/" would silently never match.
    origins = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
               if o.strip().rstrip("/")]
    return Settings(
        auth=auth,
        store_dir=Path(os.environ.get("ADVISOR_API_STORE", DATA_DIR / "api")),
        supabase_url=supabase_url,
        supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET", ""),
        supabase_db_url=db_url,
        supabase_secret_key=secret_key,
        allowed_origins=origins or (["http://localhost:5173"] if auth == "dev" else []),
        daily_turn_limit=int(os.environ.get("ADVISOR_DAILY_TURN_LIMIT", DEFAULT_DAILY_TURN_LIMIT)),
        operator_emails=tuple(e.strip().lower() for e in
                              os.environ.get("ADVISOR_OPERATORS", "").split(",") if e.strip()),
    )
