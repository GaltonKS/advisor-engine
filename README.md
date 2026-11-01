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

### Market data

Returns are synthetic by default. To use real prices from Financial Modeling Prep, set:

| Variable | Value |
|---|---|
| `ADVISOR_MARKET_DATA` | `fmp` (default `synthetic`) |
| `FMP_API_KEY` | your FMP key (secret: `.env` or the host's settings, never git) |
| `ADVISOR_MARKET_INDEX` | the market index symbol (default `SPY`) |
| `ADVISOR_MARKET_CACHE` | where month-end prices are cached: `supabase` (default when `SUPABASE_DB_URL` is set), `disk`, or a directory (default `data/market_cache`) |

The engine uses monthly returns from dividend-adjusted closes: up to 120 months, over
the window every holding shares (at least 24). Profiles then say `returns_source: FMP`
and carry `returns_as_of`, the last month-end used. Another vendor plugs in by adding
one adapter in `src/advisor_engine/data/` (see `fmp.py`).

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

## Run the demo

Works with **no API key**: without `ANTHROPIC_API_KEY` the assistant runs in mock mode, an
offline stand-in that still goes through the real tool call, number check and audit log.

```bash
pip install -e ".[dev,llm,ui]"
streamlit run src/advisor_engine/ui/app.py
```

The app opens at http://localhost:8501. A banner shows the mode (MOCK or LIVE) and that the
data is synthetic. Pick or upload a portfolio in the sidebar (uploads are validated and saved
to a per-session folder under `data/uploads/`, which is gitignored, so visitors never see
each other's files), ask questions, and open "How this was checked" under
any reply to see the engine output and the verification result. "Simulate invented number"
makes the mock add a figure the engine never produced, so you can watch the reply get
withheld. "Draft client explanation" produces an editable DRAFT; Approve records it in the
audit log and sends nothing.

Mode is chosen by `ADVISOR_LLM=mock|anthropic`. If unset, it is `mock` without an API key
and `anthropic` with one (a key in `.env` counts). `ADVISOR_LLM=mock` forces the offline mode.
The terminal assistant (`python -m advisor_engine.assistant`) uses the same setting and
prints the active mode.

### Share a demo link (Streamlit Community Cloud)

Vercel and other serverless hosts can't run Streamlit (it needs a long-running server with
WebSockets). Streamlit Community Cloud can:

1. At https://share.streamlit.io, choose **Create app** and pick this repo and branch.
2. Main file path: `src/advisor_engine/ui/app.py`. It installs `requirements.txt`.
3. Leave the secrets empty to run in mock mode. Adding `ANTHROPIC_API_KEY` there turns on
   live mode for anyone with the link, and there is no sign-in yet.

The audit log and uploads are written to the app's disk, which is wiped on restart.

## API (for the React app, in progress)

```bash
pip install -e ".[dev,llm,api]"
uvicorn advisor_engine.api.main:app --reload
```

Locally it runs with dev auth: send `X-Dev-User: <any name>` instead of signing in. Dev
auth always uses the mock assistant. Setting `SUPABASE_URL` switches to Supabase sign-in
(plus `SUPABASE_JWT_SECRET` on projects that still use the legacy shared secret). Data is
stored per firm under `data/api/`. See `docs/migration-plan.md` for the full plan.

## Layout

```
src/advisor_engine/
    holdings.py    load and validate holdings CSV
    synthetic.py   synthetic return generator (no licensed data needed)
    risk.py        the risk profile
    demo.py        end-to-end run on the sample portfolio
    assistant/     LLM layer: tools, prompts, numcheck, audit, chat loop, CLI, mock client
    ui/            Streamlit demo app
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
