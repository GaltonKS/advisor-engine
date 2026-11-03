# Advisor Analysis Engine (prototype)

Decision-support engine for advisors. The engine computes every number; an LLM
layer only calls the engine and explains results. It never produces figures
itself.

## Status

Prototype, step 2 of the MVP: **risk profile** from a holdings CSV.

- Effective independent bets (PCA on a Ledoit-Wolf shrunk covariance matrix)
- Market-adjusted concentration: portfolio beta, share of risk from the market,
  and effective bets on the residuals (equal-weighted universe stand-in until a
  broad index series is supplied via `market`)
- Sector-level effective bets
- Sector exposure
- Drawdown sensitivity (historical and covariance-based stress)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
python -m advisor_engine.demo
```

## Assistant (LLM layer)

```bash
pip install -e ".[dev,llm]"
cp .env.example .env             # then set ANTHROPIC_API_KEY
python -m advisor_engine.assistant                      # sample portfolio
python -m advisor_engine.assistant --holdings data/my_book.csv   # must be inside data/
```

Type questions at the prompt; `/quit` exits. The model name comes from
`ADVISOR_MODEL` (default `claude-sonnet-5-5`).

Works with **no API key**: mode is chosen by `ADVISOR_LLM=mock|anthropic`. If unset, it
is `mock` without `ANTHROPIC_API_KEY` and `anthropic` with one (a key in `.env` counts).
Mock mode is an offline stand-in that still goes through the real tool call, number
check and audit log. The assistant prints the active mode.

### Market data

Returns are synthetic by default. To use real prices from Twelve Data or Financial
Modeling Prep, set `ADVISOR_MARKET_DATA` and the vendor's key (see
[Environment variables](#environment-variables)), either on the host (Render:
Environment settings) or locally in `.env` (`cp .env.example .env`; the real environment
wins over the file).

The engine uses monthly returns from dividend-adjusted closes: up to 120 months, over
the window every holding shares (at least 24). Profiles then say `returns_source: Twelve Data` (or `FMP`)
and carry `returns_as_of`, the last month-end used. Another vendor plugs in by adding
one adapter in `src/advisor_engine/data/` (see `fmp.py`, `twelvedata.py`).
Twelve Data's free tier allows 8 requests a minute: a first load of a large portfolio
can hit that limit (prices are cached for 24 hours afterwards).

### Number-check guardrail

The model gets one tool, `get_risk_profile`, and is told never to compute or invent
a figure. It is not trusted to obey: after it answers, `assistant/numcheck.py`
extracts every number in the reply and checks it against all tool output in the
conversation. A figure matches if it equals a tool value rounded to the precision the
reply used (21.1% matches 0.2113). If anything doesn't match, the model gets one
automatic retry; if it still fails, the reply is withheld and the unmatched figures
are listed.

Limits: spelled-out numbers ("twelve") are not detected; rounding to the reply's
precision means a coarse figure ("5") can match a nearby tool value; figures the
user typed in their own question are not allowed unless a tool also returned them.

### Audit log

Every turn appends one JSON line to `data/audit/audit.jsonl`: timestamp, engine
version, model, SHA-256 of the holdings file, tool calls and outputs, the model's
reply, the verification result, and whether the reply was blocked. API keys are never
written. `data/` is gitignored, so the log stays local.

### Firm profile

`prompts.FIRM_PROFILE` (default `None`) takes `objective`, `tone` and `disclaimer`.
Objective and tone go into the system prompt. The disclaimer is appended verbatim to
verified replies by code, so the model never rewrites it. The wording is yours to
supply; none is built in.

## API (for the React app, in progress)

```bash
pip install -e ".[dev,llm,api]"
uvicorn advisor_engine.api.main:app --reload
```

Locally it runs with dev auth: send `X-Dev-User: <any name>` instead of signing in. Dev
auth always uses the mock assistant. Setting `SUPABASE_URL` switches to Supabase sign-in
(plus `SUPABASE_JWT_SECRET` on projects that still use the legacy shared secret). Data is
stored per firm under `data/api/`. See `docs/migration-plan.md` for the full plan.

## Environment variables

Every variable the project reads, in one place (`.env.example` lists the same ones).
The Python package reads them in `src/advisor_engine/config.py` and
`src/advisor_engine/api/settings.py` only. A local `.env` is loaded once per process and
never overrides the real environment. Secrets belong in `.env` or the host's settings,
never in git and never in a `VITE_*` variable.

**Market data**

| Variable | Default | Meaning |
|---|---|---|
| `ADVISOR_MARKET_DATA` | `synthetic` | `synthetic`, `td` (Twelve Data; `twelvedata`, `twelve_data`, `twelve-data` also work) or `fmp` |
| `TWELVE_DATA_API_KEY` | | Twelve Data key, for `td` (secret) |
| `FMP_API_KEY` | | FMP key, for `fmp` (secret) |
| `ADVISOR_MARKET_INDEX` | `SPY` | market index symbol for real data |
| `ADVISOR_MARKET_CACHE` | `supabase` when `SUPABASE_DB_URL` is set, else `disk` | where month-end prices are cached: `supabase`, `disk` (`data/market_cache`) or a directory |

**Galton AI (LLM)**

| Variable | Default | Meaning |
|---|---|---|
| `ADVISOR_LLM` | `anthropic` if `ANTHROPIC_API_KEY` is set, else `mock` | `mock` or `anthropic`. The API uses the mock whatever this says unless sign-in is Supabase |
| `ANTHROPIC_API_KEY` | | Anthropic key (secret; redacted from the audit log) |
| `ADVISOR_MODEL` | `claude-sonnet-5-5` | model name for the assistant and scenario suggestions |

**API (FastAPI backend)**

| Variable | Default | Meaning |
|---|---|---|
| `SUPABASE_URL` | | Supabase project URL. Set means Supabase sign-in |
| `ADVISOR_AUTH` | `supabase` if `SUPABASE_URL` is set, else `dev` | `supabase` or `dev` (trusted `X-Dev-User` header; refuses to run on Render) |
| `SUPABASE_JWT_SECRET` | | legacy HS256 secret; empty means tokens are verified via JWKS |
| `SUPABASE_DB_URL` | | Postgres connection (transaction pooler). Set means the Supabase store; needs `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Required on Render |
| `SUPABASE_SECRET_KEY` | | `sb_secret_...` key, for Storage and Auth admin calls (secret, server only) |
| `ALLOWED_ORIGINS` | `http://localhost:5173` with dev auth, else none | comma-separated CORS origins, no trailing slash |
| `ADVISOR_API_STORE` | `data/api` | local store directory (and the Supabase store's file cache) |
| `ADVISOR_DAILY_TURN_LIMIT` | `200` | assistant turns and scenario suggestions per adviser per day |
| `ADVISOR_OPERATORS` | | comma-separated operator emails (Pilot metrics page) |
| `ADVISOR_REQUIRE_MFA` | `off` | `off`, `enrol` or `required` (always `off` with dev auth) |
| `ADVISOR_MFA_DEADLINE` | | ISO date two-step sign-in becomes required, shown during `enrol` |
| `RENDER` | | set by Render itself; turns on the deploy checks above |

The operator command (`python -m advisor_engine.api.admin`) reads `SUPABASE_DB_URL`,
and for `reset-mfa` also `SUPABASE_URL` and `SUPABASE_SECRET_KEY`, from the real
environment only (not `.env`).

**Development and tests**

| Variable | Default | Meaning |
|---|---|---|
| `ADVISOR_NO_DOTENV` | | set to skip `.env` entirely (the test suite sets it) |
| `TEST_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/postgres` | admin connection for the Postgres-backed tests |

**Web app (`web/`, build time) and desktop shell (`desktop/`)**

| Variable | Meaning |
|---|---|
| `VITE_API_URL` | the API's URL |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` | Supabase project and its publishable key (never the secret key) |
| `VITE_DEV_USER` | dev sign-in name (local only) |
| `VITE_PLAUSIBLE_DOMAIN`, `VITE_PLAUSIBLE_SCRIPT` | Plausible page-view analytics (off when unset) |
| `ADVISOR_APP_URL`, `ADVISOR_SUPABASE_URL` | desktop shell: the app to load, and the Supabase origin it may reach |

## Layout

```
src/advisor_engine/
    holdings.py    load and validate holdings CSV
    synthetic.py   synthetic return generator (no licensed data needed)
    risk.py        the risk profile
    demo.py        end-to-end run on the sample portfolio
    assistant/     LLM layer: tools, prompts, numcheck, audit, chat loop, CLI, mock client
    api/           FastAPI backend for the React app in web/
data/sample/       small synthetic portfolios, safe to commit
tests/
```

## Data and ownership

- Everything under `data/` except `data/sample/` is gitignored. Do not commit
  licensed price or fundamentals data until commercial-use licensing is confirmed.
- Methodology provenance and ownership are not yet written down.

## Versioning

`advisor_engine.__version__` is the engine version the audit log will record.
Tag releases in git (`git tag v0.1.0`) when it changes.
