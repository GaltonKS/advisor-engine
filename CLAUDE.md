# CLAUDE.md

Guidance for Claude (and anyone else) working in this repository. README.md is the
user-facing guide; this file covers how the code is put together and the rules to keep
when extending it.

## What this project is

A prototype decision-support engine for financial advisors. Given a holdings CSV it
computes a risk profile (effective independent bets, market-adjusted and sector-level
concentration, sector exposure, drawdown sensitivity, per-position risk). An LLM layer
explains those results in plain language, and a Streamlit app wraps both for demos.

**The core rule: the engine computes every number; the LLM never does.** The model only
calls engine tools and explains their output. Code enforces this (see "Number check"
below) rather than trusting the prompt. Any change that lets a figure reach the advisor
without coming from the engine breaks the product's main guarantee.

Current status: risk profile plus CFP tools (what-if, compare, stress scenarios, risk
questionnaire), served by a FastAPI backend on Render with Supabase, and a React app in
`web/`. Returns are synthetic until licensed market data is confirmed. The stress presets
and questionnaire scoring are drafts pending compliance review.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # engine + pytest
pip install -e ".[dev,llm,ui,api]" # everything (anthropic, streamlit, fastapi)

pytest                             # full suite; never calls the real API
pytest tests/test_risk.py -q       # one file
python -m advisor_engine.demo      # print the sample risk profile as JSON
python -m advisor_engine.assistant [--holdings data/<file>.csv]   # terminal chat
streamlit run src/advisor_engine/ui/app.py                         # demo UI, :8501
uvicorn advisor_engine.api.main:app --reload                       # API, :8000 (dev auth)
cd web && npm install && VITE_DEV_USER=alice npm run dev           # React app, :5173
cd web && npm test && npm run build                                # web tests, typecheck + bundle
```

Requires Python >= 3.10. `tests/test_ui.py` is skipped if Streamlit isn't installed.

## Layout

```
src/advisor_engine/
    __init__.py        __version__ (engine version recorded in every audit line)
    holdings.py        load/validate holdings CSV -> adds value and weight columns
    synthetic.py       market + sector (+ rates) factor model by asset class (stand-in for real data)
    risk.py            all risk math; risk_profile() -> RiskProfile.to_dict()
    stress.py          preset/custom sector + asset-class shock scenarios (illustrative, uncalibrated)
    questionnaire.py   six-question client risk score (draft method) + drawdown check
    compare.py         before/after differences between two risk profiles
    demo.py            end-to-end run on data/sample/sample_holdings.csv
    assistant/
        _paths.py      REPO_ROOT, DATA_DIR, DEFAULT_HOLDINGS, AUDIT_PATH
        tools.py       tool schemas (TOOLS: risk profile, holdings, what-if, stress), execute_tool(),
                       engine_profile(), get_returns() / get_market_returns(), path guard
        prompts.py     SYSTEM_PROMPT, FIRM_PROFILE, build_system_prompt()
        chat.py        run_turn(): tool loop, number check, retry, audit; llm_mode()
        numcheck.py    verify(reply, tool_outputs): every figure must match tool output
        audit.py       append_audit(): one JSON line per turn, API keys redacted
        mock_client.py offline stand-in for the Anthropic client (templated replies)
        cli.py         terminal REPL (`python -m advisor_engine.assistant`)
    ui/app.py          Streamlit demo; lays out engine output, computes nothing
    ui/theme.py        KVS stylesheet + escaped HTML builders (cards, bars, audit panel)
    api/
        settings.py    env config; dev auth forces mock and refuses to run on Render
        auth.py        current_user(): Supabase access token (JWKS/HS256) or X-Dev-User
        store.py       LocalStore: firm-scoped clients, portfolios, drafts, audit (dev, tests)
        supabase_store.py SupabaseStore: same methods on Postgres (RLS) + Storage
        main.py        FastAPI routes over execute_tool / run_turn / verify
web/                   React app (see web/README.md); formats engine output, computes nothing
docs/migration-plan.md React + FastAPI + Supabase plan and the decisions behind it
supabase/migrations/   Postgres schema, row-level security, append-only audit (see supabase/README.md)
data/sample/           synthetic portfolios, the only committed data
.streamlit/config.toml KVS palette when running `streamlit run` from the repo root
tests/                 one test file per module area
```

## Data flow

```
holdings CSV --load_holdings--> DataFrame(ticker, shares, price, sector, value, weight)
             --get_returns-----> returns DataFrame (columns = tickers, rows = periods)
             --risk_profile----> RiskProfile --to_dict--> JSON tool result
LLM reply --numcheck.verify against all tool results--> shown, retried once, or withheld
every turn --append_audit--> data/audit/audit.jsonl
```

## Key conventions

### Engine (`risk.py`, `holdings.py`, `synthetic.py`)
- Pure functions returning plain floats, lists and dicts (JSON-serialisable). Cast numpy
  scalars with `float()` / `int()` before returning.
- `risk_profile` requires >= 24 return periods and return history for every ticker;
  raise `ValueError` with a clear message for bad input.
- Holdings are long-only: negative shares and non-positive prices are rejected.
- `holdings.read_holdings_csv` is the one CSV parser (uploads and stored files both use
  it via `load_holdings`). It accepts layouts with a single reading (header case, BOM,
  `;`/tab/`|` separators, title rows, a short alias list, `$` and thousands separators)
  and rejects ambiguous numbers rather than guessing.
- Sector is optional. Missing or blank sectors become `holdings.UNCLASSIFIED`, which is
  never treated as a sector: no shared synthetic sector factor, listed last in
  `sector_exposure`, and `sector_level` drops `sector_effective_bets` (with `withheld`)
  while any holding is unclassified. `sector_coverage` reports what is missing.
  `compare` skips a headline figure that either side withheld.
- Asset class is optional too: `holdings.classify_asset_class` maps labels to
  `ASSET_CLASSES` (blank -> equity, unrecognised -> "other", modelled as equity). The
  synthetic model gives bonds and preferreds a shared rates factor and little market
  exposure, and cash a near-constant return; equity draws are unchanged by the option.
  Profiles report `asset_class_exposure` and each position's `asset_class`.
- Standard sector spellings are mapped to one name by `holdings.SECTOR_ALIASES` (exact
  matches only), so they line up with the stress presets.
- `shrunk_covariance` applies Ledoit-Wolf to the correlations and rescales by sample
  volatilities, so shrinkage never changes a holding's own volatility (shrinking the
  covariance would give cash a volatility it doesn't have). Keep it that way.
- Stress shocks resolve per holding: asset-class shock (fixed_income, preferred, cash)
  if given, else cash holds at 0, else sector shock, else the default. Group `shock`
  values are weighted averages.
- Weights are passed in the same order as `returns.columns` / `holdings["ticker"]`.
- With no `market` series, the equal-weighted universe stands in and `portfolio_beta`
  / `market_share_of_risk` are deliberately removed from the output (they would be
  artifacts). Keep that behaviour unless a real index series is supplied.
- Drawdown and volatility figures carry a `period_label` (default `"monthly"`).
- Docstrings explain the method and its caveats; comments explain *why*. Match that.
- Synthetic returns are seeded per series (market, sector, ticker), so a ticker's history
  never depends on the other holdings. What-if and compare rely on that; keep it.
- Before/after differences come from `compare.compare_profiles`, never from a caller.
  Loss figures there are sizes (kind `"loss"`), so a smaller loss is a negative change.

### LLM layer (`assistant/`)
- `execute_tool` never raises: errors come back as `ToolResult(..., is_error=True)`.
- Holdings paths must resolve inside `data/`, must be `.csv`, and may not be under
  `data/audit/`. Keep all file access going through `resolve_holdings_path`.
- `get_returns` and `get_market_returns` in `tools.py` are **the only places real market
  data plugs in**. When they change, update `RETURNS_SOURCE` / `MARKET_SOURCE` to match.
  The synthetic market factor stands in as the market today, so profiles from
  `engine_profile` carry beta and correlation to the market (per position and
  portfolio). The API uses `engine_profile` too, so tool and screen figures agree.
- The model's tools: `get_risk_profile`, `get_holdings`, `run_what_if` (changes applied
  by `apply_changes`, differences from `compare.compare_profiles`) and `run_stress_test`.
  What-if additions must use figures the adviser supplied (the prompt says so).
- Profiles also carry `sector_risk` / `asset_class_risk` (weight and summed risk share
  per group). The prompt's glossary says what each field means; keep it in step with
  new fields, since a real figure quoted with the wrong meaning passes the number check.
- The firm disclaimer is appended verbatim by code in `chat.py`, never given to the
  model and never run through the number check.
- Mode: `ADVISOR_LLM=mock|anthropic`. Unset means mock without `ANTHROPIC_API_KEY`,
  anthropic with one. Model name comes from `ADVISOR_MODEL` (default in `chat.py`).
- The audit log must never contain API keys; `audit._redact` is the safety net, but
  don't put secrets in records in the first place.

### UI (`ui/app.py`)
- Displays engine output only. Any figure shown goes through the same
  `get_risk_profile` tool (so the path rules and audit log apply). Uploads are
  validated and saved to `data/uploads/<random session id>/`; a session lists only
  the samples and its own uploads.
- Styling lives in `ui/theme.py`. Its builders take strings already formatted from
  engine output and HTML-escape them (tickers and sectors can come from uploads). Model
  replies are rendered with plain `st.markdown`, never with `unsafe_allow_html`.
- Mockup sections the engine can't back yet (strategy options, client risk level,
  adviser sign-in, named risk factors) are left out rather than filled with placeholders.

### API (`api/`)
- Routes move engine output around; they compute nothing. Model replies only come from
  `run_turn`, and draft approvals re-run `verify` on the edited text.
- Every store read takes a `firm_id`; another firm's ids answer 404, never 403. Pass the
  firm's directory as `data_dir` to `execute_tool` / `run_turn` so the path guard confines
  the model to that firm, and its audit path (`<firm dir>/audit/`) so logs stay per firm.
- Audit records from the API carry `adviser_id`, `firm_id` and, where relevant,
  `client_id` / `portfolio_id` (via `run_turn(audit_extra=...)`).
- Live model calls require `auth == "supabase"` (`Settings.llm`). Don't add another path to
  the real client.
- Responses never include server file paths (see `_tool_view`).
- Routes use only the session from `store.as_user(...)` (the `Ctx` dependency). With
  Supabase that session runs every query as `authenticated` with the caller's claims, so
  row-level security applies to the API itself. Audit records go through
  `db.write_audit` (`run_turn(audit_sink=...)`), never straight to a file.
- Schema changes are new files in `supabase/migrations/` (never edit an applied one).
  Every table gets RLS keyed on `app.current_firm_id()`; add tests to
  `tests/test_supabase_store.py` that query without a firm filter.

### Web (`web/`)
- Shows API output only. `src/lib/format.ts` formats numbers; nothing in `web/` sums,
  subtracts or derives a figure. Need a new figure? Add it to the engine and return it.
- Engine runs from CFP tools (what-if, compare, stress, questionnaire) are audited by the
  API; the browser never calls the model.
- `VITE_*` variables ship to the browser: only the Supabase publishable key, never the
  secret key.

### Tests
- Tests never hit the network. Assistant tests use a scripted `FakeClient`; UI tests
  use Streamlit's `AppTest` with `ADVISOR_LLM=mock` and a temp `AUDIT_PATH`. API tests use
  `create_app(Settings(auth="dev", store_dir=tmp_path), store=...)` and FastAPI's
  `TestClient`, once per store; token tests sign HS256 tokens with a test secret.
- The `supabase_db` fixture (conftest.py) makes a fresh Postgres database with
  `tests/supabase_shim.sql` and the migrations applied; it skips without Postgres
  (`TEST_DATABASE_URL`). CI runs a Postgres service and fails if those tests skip.
- Write audit output to `tmp_path` in tests (monkeypatch `audit.AUDIT_PATH` or pass
  `audit_path=`), never to the real `data/audit/`.
- Run `pytest` before committing; the suite is fast (seconds).

## Adding things

### A new engine metric
1. Implement it as a function in `risk.py` (or a new engine module) that returns plain
   numbers/dicts, with a docstring giving the method and its limits.
2. Add it to `RiskProfile` / `risk_profile()` so it appears in `to_dict()`. Add new keys
   alongside existing ones rather than renaming; the prompt, mock client and UI read
   them by name.
3. Test it in `tests/test_risk.py` against a case with a known answer.
4. If the model should explain it, describe the field (in plain-language terms) in
   `prompts.SYSTEM_PROMPT`, and teach `mock_client.py` to mention it if relevant.
5. If it's a new kind of figure (e.g. a count or a value in different units), check
   `numcheck.verify` still matches how the model will quote it; add cases to
   `tests/test_numcheck.py`.

### A new tool for the model
1. Define its schema next to `GET_RISK_PROFILE` in `tools.py` and add it to `TOOLS`.
2. Handle it in `execute_tool`, returning engine output as JSON and errors as
   `ToolResult(is_error=True)`. Route any file paths through `resolve_holdings_path`.
3. Its output is automatically included in what the number check allows; make sure
   every figure the model might quote is actually in that output.
4. Update `mock_client.py` so mock mode can exercise it, and add tests in
   `tests/test_assistant.py` using `FakeClient`.

### Real market data
- Replace the body of `tools.get_returns` (contract: columns cover every holdings
  ticker) and set `RETURNS_SOURCE`. Replace `tools.get_market_returns` with a broad
  index on the same periods and set `MARKET_SOURCE`.
- Do not commit licensed price or fundamentals data. Everything in `data/` except
  `data/sample/` is gitignored for that reason.

### Prompt or firm-profile changes
- `SYSTEM_PROMPT` rules on numbers are a first line of defence only; the number check
  is the enforcement. Don't loosen `numcheck` tolerances to make a prompt change pass.
- New firm-profile keys go through `build_system_prompt`; anything that must appear
  word-for-word (like a disclaimer) should be added by code, not by the model.

## Versioning

Bump `advisor_engine.__version__` (and `version` in `pyproject.toml`) when engine output
changes, since the audit log records it; tag releases in git (`git tag v0.1.0`).

## Known gaps

- README mentions `cp .env.example .env`, but `.env.example` is not in the repo yet.
  `.env` needs `ANTHROPIC_API_KEY` (and optionally `ADVISOR_LLM`, `ADVISOR_MODEL`).
- `ui/app.py` imports `altair`, which arrives through Streamlit rather than being
  listed in the `ui` extra.
- Number check limits: spelled-out numbers aren't detected, coarse figures can match
  nearby values, and numbers the user typed aren't allowed unless a tool returned them.
- Methodology provenance and data ownership are not yet documented.
