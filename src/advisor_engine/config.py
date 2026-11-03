"""Process configuration read from the environment (the host, or a local `.env`).

Every environment variable the Python package reads is read here or in
`api/settings.py` (the API's own settings: sign-in, Supabase, CORS, limits, MFA).
The full list, with defaults, is in README.md ("Environment variables") and
`.env.example`.

`_env.load_env()` loads a local `.env` once per process without overriding the real
environment. The functions below call it where the code they replace did; the admin
command's `supabase_admin()` never did, so it reads the real environment only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ._env import load_env

# --- Galton AI (LLM) --------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-5-5"


def llm_mode() -> str:
    """"mock" or "anthropic". ADVISOR_LLM wins; otherwise live only if an API key is set."""
    load_env()
    mode = os.environ.get("ADVISOR_LLM", "").strip().lower()
    if mode in ("mock", "anthropic"):
        return mode
    if mode:
        raise ValueError(f"ADVISOR_LLM must be 'mock' or 'anthropic', not {mode!r}.")
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "mock"


def model_name() -> str:
    """ADVISOR_MODEL, or DEFAULT_MODEL when it is unset (a set-but-empty value is kept,
    as before). Callers load `.env` first."""
    return os.environ.get("ADVISOR_MODEL", DEFAULT_MODEL)


def anthropic_api_key() -> str:
    """ANTHROPIC_API_KEY as currently set (the audit log redacts it). The Anthropic SDK
    reads the variable itself; this is never passed to it or logged."""
    return os.environ.get("ANTHROPIC_API_KEY", "")


# --- market data ------------------------------------------------------------------------

DEFAULT_INDEX = "SPY"  # S&P 500 ETF: a broad index with dividend-adjusted history
MODE_ALIASES = {"td": "twelvedata", "twelve_data": "twelvedata", "twelve-data": "twelvedata"}
KEY_VARS = {"fmp": "FMP_API_KEY", "twelvedata": "TWELVE_DATA_API_KEY"}


@dataclass(frozen=True)
class MarketDataConfig:
    """Which market data source to build (`data.get_market_data`). Frozen, so the data
    layer can cache one source per configuration."""
    mode: str           # synthetic | fmp | twelvedata (aliases resolved)
    api_key: str        # the selected vendor's key only; empty for synthetic
    index: str          # market index symbol
    cache_setting: str  # ADVISOR_MARKET_CACHE as given: "", supabase, disk, or a directory
    db_url: str         # SUPABASE_DB_URL, for the shared price cache


def market_data_config() -> MarketDataConfig:
    load_env()
    mode = os.environ.get("ADVISOR_MARKET_DATA", "synthetic").strip().lower() or "synthetic"
    mode = MODE_ALIASES.get(mode, mode)
    return MarketDataConfig(
        mode=mode,
        api_key=os.environ.get(KEY_VARS.get(mode, ""), "").strip() if mode in KEY_VARS else "",
        index=os.environ.get("ADVISOR_MARKET_INDEX", DEFAULT_INDEX).strip().upper() or DEFAULT_INDEX,
        cache_setting=os.environ.get("ADVISOR_MARKET_CACHE", "").strip(),
        db_url=os.environ.get("SUPABASE_DB_URL", "").strip(),
    )


# --- operator command (api/admin.py) ------------------------------------------------------

@dataclass(frozen=True)
class SupabaseAdminConfig:
    db_url: str      # SUPABASE_DB_URL: the API's own (owner) database connection
    url: str         # SUPABASE_URL, for reset-mfa
    secret_key: str  # SUPABASE_SECRET_KEY, for reset-mfa


def supabase_admin() -> SupabaseAdminConfig:
    """What `python -m advisor_engine.api.admin` connects with. Reads the real
    environment only (no `.env`), as the command always has."""
    return SupabaseAdminConfig(db_url=os.environ.get("SUPABASE_DB_URL", ""),
                               url=os.environ.get("SUPABASE_URL", ""),
                               secret_key=os.environ.get("SUPABASE_SECRET_KEY", ""))
