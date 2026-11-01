# Migration plan: Streamlit → React + FastAPI on Render, Supabase for sign-in and data

## What doesn't change

The engine computes every number; the LLM only explains. In the new stack that means:

- **Interactions are engine runs.** A what-if, comparison or stress scenario is an API call
  that reruns the engine. The browser never calculates.
- **The model is only called on the server.** Every explanation goes through `run_turn`
  (number check, retry, withholding). The browser never calls the model API.
- **Everything is audited per adviser and firm:** every engine run, explanation and approval.

## Decisions

| Question | Decision | What it means in code |
|---|---|---|
| Who can sign up | Open sign-up now; CRD checks later | First sign-in creates a firm owned by that adviser. Supabase "Confirm email" stays on. A daily assistant limit per adviser (`ADVISOR_DAILY_TURN_LIMIT`, default 200) caps cost from open sign-up. An adviser `status` field is reserved for CRD checks. |
| Data separation | Each firm sees only its own data | Every store read is scoped by `firm_id` (another firm's ids answer 404). Each firm's files are in their own folder, which is the engine tool's `data_dir`, so the path guard stops the model reading across firms. Supabase row-level security repeats this in the database. |
| Client data | Real client data soon | Paid Supabase plan (backups, SOC 2) and a written retention policy before real data. Audit log is append-only. Review the Anthropic data-handling terms before live mode sees client holdings. |
| Live model | Mock until sign-in works, then live | Enforced in code: with dev auth the API always uses the mock, and dev auth refuses to start on Render. |

## Target setup

```
React (Vite + TS)              FastAPI (Render web service)            Supabase
Render static site             - verifies Supabase access tokens       - Auth (email, MFA)
- Supabase JS for sign-in ──▶  - advisor_engine (unchanged)       ──▶  - Postgres: firms, advisers,
- KVS mockup UI                - run_turn / numcheck / audit             clients, portfolios, drafts,
- charts, what-if controls     - firm-scoped store                       audit_events (append-only)
                                                                        - Storage: holdings CSVs
```

## Phases

### Phase 0: secure the live demo (done)
- Streamlit uploads are per session (`data/uploads/<random id>/`), so visitors don't see each other's files.
- GitHub Actions CI runs `pytest` on Python 3.10 and 3.12.
- Devcontainer entry point fixed.

### Phase 1: FastAPI backend (done, on a local store)
`src/advisor_engine/api/`: `GET /health`, `GET /me`, `GET|POST /clients`,
`GET /clients/{id}`, `POST /clients/{id}/portfolios`, `GET /portfolios/{id}/profile`,
`GET /portfolios/{id}/messages`, `POST /portfolios/{id}/turns`,
`POST /drafts/{id}/approve`, `GET /audit`. Supabase tokens are verified (JWKS or the
legacy HS256 secret). Persistence is `LocalStore`, which Phase 2 replaces.

### Phase 2: Supabase (code done; migration to apply)
- Project `znkwkvcnxtahwinrxbvn`: Pro, East US (North Virginia), JWT signing keys,
  "Confirm email" on, TOTP MFA available.
- `supabase/migrations/`: tables for firms, advisers (`role`, `status`, `crd`), clients,
  portfolios, conversations, drafts, `audit_events` (insert-only) and usage. Row-level
  security on every table, keyed on the caller's firm. Private `holdings` Storage bucket.
- `SupabaseStore` runs every query as the signed-in adviser, so the database enforces
  firm separation on the API's own queries. The API tests run against both stores, and
  database tests check the rules hold even when the API passes the wrong firm.
- Remaining: apply the migration to the project (`supabase/README.md`) and set
  `SUPABASE_DB_URL` / `SUPABASE_SECRET_KEY` where the API runs.

### Phases 3 and 4: React app with the CFP tools (built together)
Built in one pass so the app's structure fits the tools from the start. Done:
engine modules (`stress.py`, `questionnaire.py`, `compare.py`; synthetic returns seeded
per ticker so what-if is meaningful, engine 0.2.0), API routes for each, a migration for
questionnaire answers and labelled portfolio versions, and `web/` with every tab listed
in `web/README.md`. Not yet: strategy options (needs an optimizer and real data), and "explain this" for what-if / stress results (needs new model
tools so the number check covers them). Two-step sign-in (MFA) has since been built:
see docs/mfa-plan.md.

### Phase 3 (original notes): React app matching Streamlit
- Vite, React, TypeScript, React Router, TanStack Query, Supabase JS (sign-in only), Tailwind with the KVS tokens, Recharts.
- Screens: sign-in / request access, clients list with recent clients, client analysis (headline cards, risk sources, positions, sectors, drawdown, chat with tool chips and sources, draft editor, audit panel).
- Done when it does everything the Streamlit app does.

### Phase 4: CFP tools beyond chat
Each tool has its own screen and an "Explain this" action that sends its result to the assistant.

| Feature | Needs |
|---|---|
| Holdings editor (edit, add, remove positions in a table) | API |
| Position drill-down | API |
| What-if (change weights, remove a position, add cash; before and after) | `POST /portfolios/{id}/what-if` reruns the engine |
| Compare (current vs proposed, or two portfolios) | Engine run twice plus a diff endpoint |
| Risk questionnaire (six questions, risk level, mismatch flags) | New scoring module in the engine |
| Stress scenarios (presets) | New engine functions |
| Client report (PDF of the approved draft and charts) | Server-side rendering |
| Meeting prep (what changed since the last review) | Audit history plus engine reruns |
| Strategy options (three candidates) | Optimizer; after real market data |

Each new engine feature follows CLAUDE.md "A new engine metric", and each new kind of figure gets number-check tests.

### Phase 5: deploy on Render
`render.yaml` with a web service (`uvicorn advisor_engine.api.main:app`) and a static site
(the React build). Settings: `SUPABASE_URL`, `ALLOWED_ORIGINS`, `ANTHROPIC_API_KEY`, and
optionally `ADVISOR_LLM`, `ADVISOR_MODEL`, `ADVISOR_DAILY_TURN_LIMIT`, `ADVISOR_REQUIRE_MFA`,
`ADVISOR_MFA_DEADLINE` (two-step sign-in, see docs/mfa-plan.md). Use the paid Starter tier
(the free tier goes to sleep) and Python 3.12. Do not deploy with `LocalStore`: Render's
disk is wiped on deploy, so it waits for Phase 2.

### Phase 6: tidy up
Retire Streamlit or keep it internal. Update CLAUDE.md and README.
