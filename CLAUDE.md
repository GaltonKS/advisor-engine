# CLAUDE.md

Guidance for Claude (and anyone else) working in this repository. README.md is the
user-facing guide; this file covers how the code is put together and the rules to keep
when extending it.

## What this project is

A prototype decision-support engine for financial advisors. Given a holdings CSV it
computes a risk profile (effective independent bets, market-adjusted and sector-level
concentration, sector exposure, drawdown sensitivity, per-position risk). An LLM layer
explains those results in plain language. A FastAPI backend and a React app in `web/`
wrap both for advisers.

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
pip install -e ".[dev,llm,api]"    # everything (anthropic, fastapi)

pytest                             # full suite; never calls the real API
pytest tests/test_risk.py -q       # one file
python -m advisor_engine.demo      # print the sample risk profile as JSON
python -m advisor_engine.assistant [--holdings data/<file>.csv]   # terminal chat
uvicorn advisor_engine.api.main:app --reload                       # API, :8000 (dev auth)
cd web && npm install && VITE_DEV_USER=alice npm run dev           # React app, :5173
cd web && npm test && npm run build                                # web tests, typecheck + bundle
```

Requires Python >= 3.10.

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
    charts.py          chart specs from engine output (risk by group, risk vs size, what-if)
    demo.py            end-to-end run on data/sample/sample_holdings.csv
    data/
        __init__.py    get_market_data(): the configured source (synthetic | fmp), SyntheticData
        base.py        PriceSource protocol, PriceHistoryData (month-end prices -> returns), errors
        cache.py       PriceCache: month-end closes per vendor/symbol on disk, 24h freshness
        supabase_cache.py SupabasePriceCache: same interface in Postgres (market schema), shared
        fmp.py         FMPSource: dividend-adjusted daily closes from FMP's stable API
    assistant/
        _paths.py      REPO_ROOT, DATA_DIR, DEFAULT_HOLDINGS, AUDIT_PATH
        tools.py       tool schemas (TOOLS: risk profile, holdings, what-if, stress, show_chart), execute_tool(),
                       engine_profile(), get_returns() / get_market_returns(), path guard
        prompts.py     SYSTEM_PROMPT, FIRM_PROFILE, build_system_prompt()
        chat.py        run_turn(): tool loop, number check, retry, audit; llm_mode()
        numcheck.py    verify(reply, tool_outputs): every figure must match tool output
        audit.py       append_audit(): one JSON line per turn, API keys redacted
        mock_client.py offline stand-in for the Anthropic client (templated replies)
        scenario.py    AI-suggested stress shocks for a described scenario (+ MockSuggester)
        cli.py         terminal REPL (`python -m advisor_engine.assistant`)
    api/
        settings.py    env config; dev auth forces mock and refuses to run on Render
        auth.py        current_user(): Supabase access token (JWKS/HS256) or X-Dev-User; aal/amr
        auth_admin.py  Supabase Auth admin calls (list/delete a user's MFA factors), secret key
        store.py       LocalStore: firm-scoped clients, portfolios, drafts, audit (dev, tests)
        supabase_store.py SupabaseStore: same methods on Postgres (RLS) + Storage
        main.py        FastAPI routes over execute_tool / run_turn / verify
        admin.py       operator commands: pending sign-ups, approve, mfa-mode, reset-mfa (`python -m advisor_engine.api.admin`)
        metrics.py     pilot metrics definitions (weekly active, feature use, funnel) for the operator page
web/                   React app (see web/README.md); formats engine output, computes nothing
desktop/               Electron shell that loads the web app (see desktop/README.md); no figures, no Node in the page
web/src/components/charts.tsx  draws chart specs (see "Charts" below)
web/src/components/Logo.tsx    GaltonKS logo as SVG (see "Brand" below)
web/public/brand/      source logo artwork (light logo, dark app icon)
docs/migration-plan.md React + FastAPI + Supabase plan and the decisions behind it
docs/mfa-plan.md       two-step sign-in: decisions, what was built, how to switch it on
supabase/migrations/   Postgres schema, row-level security, append-only audit (see supabase/README.md)
data/sample/           synthetic portfolios, the only committed data
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
- Market data comes only from `data.get_market_data()` (used by `get_returns`,
  `get_market_returns` and `engine_profile` in `tools.py`). It is synthetic unless
  `ADVISOR_MARKET_DATA=fmp` (see "Real market data" below). Every profile carries its
  `returns_source`, a market series (the synthetic factor, or `ADVISOR_MARKET_INDEX`) so
  beta and correlation to the market are always present, and with real data
  `returns_as_of`. The API uses `engine_profile` too, so tool and screen figures agree.
- The model's tools: `get_risk_profile`, `get_holdings`, `run_what_if` (changes applied
  by `apply_changes`, differences from `compare.compare_profiles`), `run_stress_test`,
  `show_chart` (specs from `charts.py`; see "Charts") and `refer_to_stress_test`.
  What-if additions must use figures the adviser supplied (the prompt says so).
- Profiles also carry `sector_risk` / `asset_class_risk` (weight and summed risk share
  per group). The prompt's glossary says what each field means; keep it in step with
  new fields, since a real figure quoted with the wrong meaning passes the number check.
- Hypothetical events (wars, elections, rate moves, crashes) are not forecasts the
  engine makes. The Assistant says so, may run a fitting preset with `run_stress_test`,
  and calls `refer_to_stress_test`, which runs nothing and returns no figures; the web
  app turns it into an "Open in Stress test" button carrying the question over.
- AI-suggested scenarios (`scenario.py`, `POST /portfolios/{id}/stress/suggest`): the AI
  suggests **assumptions only** (a shock per sector / asset class and a default, via
  structured output limited to the portfolio's own sectors and the shockable classes).
  The adviser edits them in the Stress test tab; the engine computes every result.
  The AI's explanation may quote only its own shocks: it is number-checked against
  them and withheld otherwise. Results carry `assumptions.source` (preset / adviser /
  ai_suggested / ai_suggested_edited) and the screen labels assumptions and results
  separately. Never let the AI produce a portfolio result, loss or value.
- Live suggestions follow the same gate as the Assistant (`Settings.llm`, so Supabase
  sign-in only) and the same daily limit; dev auth and tests use `MockSuggester`. The
  request uses `output_config.format` (current models reject forced `tool_choice`) and
  server-side refusal fallbacks (`fallbacks="default"`).
- `run_turn` asks for automatic prompt caching (`chat.CACHE_CONTROL`), so each tool-loop
  call and each follow-up within the TTL reads tools, system prompt and history from
  cache. Keep that prefix byte-stable: `TOOLS` in a fixed order, nothing per-request
  (dates, ids) in the system prompt. Each audit record carries `usage` (API calls and
  input / output / cache-read / cache-write tokens summed over the turn), or `null` for
  clients that report none (mock, test fakes).
- Older tool results are not resent. When a turn ends, `chat.compact_history` keeps the
  newest result of each tool in full and replaces older ones (≥ `COMPACT_MIN_CHARS`, not
  errors) with a short note telling the model to call the tool again. The full text stays
  on the block as `_full`; `_wire` strips `_`-prefixed keys from every request, and the
  number check reads `_full`, so figures from earlier results still verify. Compacting
  only between turns keeps each turn's requests append-only for the cache. Never send
  `_full` to the API or drop it from saved history.
- `chat.MAX_TOKENS` (16,000) is the output cap per call. Thinking counts against it, so
  keep it well above a normal reply; you pay for tokens generated, not the cap. A
  response that stops at the cap (`stop_reason == "max_tokens"`) is withheld with
  `block_reason="max_tokens"`, not retried, its tool calls are not run, and the turn is
  not kept in history.
- The firm disclaimer is appended verbatim by code in `chat.py`, never given to the
  model and never run through the number check.
- Mode: `ADVISOR_LLM=mock|anthropic`. Unset means mock without `ANTHROPIC_API_KEY`,
  anthropic with one. Model name comes from `ADVISOR_MODEL` (default in `chat.py`).
- The audit log must never contain API keys; `audit._redact` is the safety net, but
  don't put secrets in records in the first place.

### API (`api/`)
- Routes move engine output around; they compute nothing. Model replies only come from
  `run_turn`, and draft approvals re-run `verify` on the edited text.
- Every store read takes a `firm_id`; another firm's ids answer 404, never 403. Pass the
  firm's directory as `data_dir` to `execute_tool` / `run_turn` so the path guard confines
  the model to that firm, and its audit path (`<firm dir>/audit/`) so logs stay per firm.
- Audit records from the API carry `adviser_id`, `firm_id` and, where relevant,
  `client_id` / `portfolio_id` (via `run_turn(audit_extra=...)`).
- Each Galton AI turn gets a `turn_id`. It is stored on the audit record and on both chat
  messages (question and reply). The audit trail's "What" column and Review prep's
  Recent activity (both `AuditTab.ActivityLink`) link to
  `?tab=assistant&turn=<id>`, or `&draft=<id>` for an approval, and the Galton AI tab
  highlights that exchange. Turns logged before turn ids existed fall back to
  `&question=<text>`, which only matches messages that have no `turn_id`. Keep the id
  on any new conversation message.
- New sign-ups are `pending` until the operator approves them (`app.ensure_adviser`
  creates them so; `LocalStore(require_approval=True)` mirrors it, off for dev). Pending
  advisers resolve to no firm, so RLS shows them nothing, and every route except `/me`
  and `POST /me/registration` answers 403 "Your account is awaiting approval" (the `ctx`
  dependency; `any_ctx` is for those two only). Registration records name, CRD number
  (digits, up to 8) and firm name through `app.submit_registration`, own row, pending
  only. `app.approve_adviser(email, firm_id?)` activates them, optionally as a member of
  an existing firm; only the owner connection can run it (`api/admin.py` or the SQL
  Editor), never an adviser. Verify the CRD on FINRA BrokerCheck before approving.
- Operators (`ADVISOR_OPERATORS`, comma-separated emails -> `Settings.operator_emails`)
  get `/me` `operator: true` and the **Pilot metrics** page (`/operator`, `pages/PilotMetrics.tsx`).
  `GET /operator/metrics` reads across every firm, so it runs on the store's owner
  connection (`store.pilot_metrics()`), never a user session, and answers 404 to anyone
  who isn't an operator. It returns counts, dates and the approval queue only: never
  client names, holdings or question text. Add a figure there, not in the page.
- Approval reviews: operators approve or reject pending sign-ups from the Pilot metrics
  page (`POST /operator/reviews`, 404 to non-operators). Approval needs every
  `metrics.REVIEW_CHECKS` confirmed; rejection needs a `REJECT_REASONS` key (and a note
  for "other"). The reviewer is the operator's signed-in email, never a client-supplied
  field; `review_summary` writes the one-line record. `app.review_adviser` changes the
  status and appends to `public.adviser_reviews` in one transaction. That table is
  append-only (triggers refuse UPDATE/DELETE/TRUNCATE, even for the owner) and private
  (no anon/authenticated rights). `app.approve_adviser` (SQL Editor, admin command) goes
  through it as `sql:<db user>` with no checks. Rejected advisers (`status='rejected'`)
  see a "not approved" page and get 403 "Your registration wasn't approved."
- Live model calls require `auth == "supabase"` (`Settings.llm`). Don't add another path to
  the real client.
- Two-step sign-in (docs/mfa-plan.md). `Settings.mfa` is `ADVISOR_REQUIRE_MFA` (`off` /
  `enrol` / `required`), always `off` with dev auth. Dependencies run `base_ctx` (only
  `/me`) → `any_ctx` (in `required`, 403 `{"code": "mfa_required"}` below aal2) → `ctx`
  (approval). The MFA check comes before the approval check.
- Sensitive actions call `require_fresh_code(c)`: an authenticator code in the token's
  `amr` within `auth.FRESH_MFA_SECONDS`, else 403 `{"code": "mfa_fresh_required"}`; the
  audit row records `fresh_code`. Today: deleting a client, removing an authenticator.
  Anything new of that kind (email or password change, bulk export) goes through an API
  route with this check, never straight from the browser to Supabase.
- `store.as_user(..., aal=user.aal, amr=user.amr)` forwards the verified level and code
  times, so the database's restrictive `mfa_required` policies (`app.mfa_ok()`, mode in
  `app.security_settings`) and its fresh-code check see what the API saw. The database mode is switched with `admin mfa-mode`, after the
  API's, never before. Factor lists and removals go through `auth_admin`; never return a
  secret or QR code from the API. `GET /firm/mfa` is for the firm's owner only.
- The database backs the fresh-code checks, so calling Supabase directly can't skip them
  (`20261107000000_mfa_direct_paths.sql`). A trigger on `auth.mfa_factors` refuses to
  delete a verified factor without a one-time permit (`store.allow_factor_removal`,
  two minutes), which the API issues only after its own checks, and `admin reset-mfa`
  issues too; unverified factors and user deletions pass. A trigger on `public.clients`
  refuses changes to `deleted_at` / `deleted_by` by `authenticated` without a fresh code
  (`app.mfa_fresh_ok()`, the same rule and 300 s window as `require_fresh_code`; keep
  them in step). Both raise SQLSTATE `PT403` (HTTP 403 in Supabase's REST API).
- Client explanations (drafts) stay editable after approval. `POST /drafts/{id}/approve`
  on an approved draft re-runs `verify` on the new text and logs `draft_revised` with
  `previous_text`; identical text answers 409. The report only prints approved text.
  In the web app the open draft is derived from the conversation (the last reply's
  `draft_id`), so it stays in Galton AI until the next question; the Client report tab
  lists every draft for the portfolio (`?report=<draft id>`) and edits them with the
  same `DraftEditor`.
- **New chat** (`DELETE /portfolios/{id}/messages`) clears the portfolio's conversation,
  both the messages shown and the model's `history`, and logs `chat_cleared` (only
  when there was something to clear). Old turns are not kept in the conversation:
  their full records stay in the audit log, and drafts stay in the Client report.
  Audit-trail links to a cleared turn show "isn't in this chat".
- Responses never include server file paths (see `_tool_view`).
- Clients can be renamed (`PATCH /clients/{id}`) and deleted (`DELETE /clients/{id}`).
  Deleting is a soft delete (`deleted_at`): `get_client` / `list_clients` skip the row and
  `own_portfolio` 404s once its client is gone, but portfolios, drafts and audit records
  stay on record. Never hard-delete; `authenticated` has no DELETE grant on `clients`.
  Both actions are audited (`client_renamed`, `client_deleted`).
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
- Analytics is Plausible page views only (`lib/analytics.ts`), on when
  `VITE_PLAUSIBLE_DOMAIN` is set and never with the dev sign-in. Plausible gets a page
  *type* from `pageType` (e.g. `/clients/:client/assistant`), never the real address:
  no client or portfolio ids, no query text (audit links carry questions in `?question=`).
  Public pages report `/sign-in` and `/privacy`, pending advisers `/awaiting-approval`.
  No custom events, props, user ids or session recording: the Privacy Policy describes
  page views only, so anything more needs an approved policy update first.
- Two-step sign-in in the browser: `lib/mfa.ts` (`mfaStep`) picks the screen from `/me`'s
  `mfa` and the Supabase session; `lib/api.ts` `request` turns 403 `mfa_fresh_required`
  into the `StepUpProvider` code dialog and retries once, and `mfa_required` into a
  re-check. `auth.headers()` reads the latest session (a ref), so the retry carries the
  new token. QR codes and set-up keys are shown once and never stored.
- `/privacy` (`pages/Privacy.tsx`) is the approved Privacy Policy, public (shown before the
  auth gate in `main.tsx`) and linked from the sign-in page and the app footer. It is legal
  text: change it only from a new approved version, together with
  `public/legal/GaltonKS-Privacy-Policy.pdf` and `PRIVACY_EFFECTIVE_DATE`.

### Brand (`web/src/components/Logo.tsx`, `web/src/index.css`)
- The product is **GaltonKS — Knowledge System** (formerly "KVS / Knowledge Vault
  System"). Source artwork is in `web/public/brand/`: `galtonks-logo-light.webp` (on
  paper) and `galtonks-icon-dark.webp` (app icon, on navy). Don't recolour or redraw
  them; use them as the reference.
- On screen the logo is drawn as SVG by `Logo.tsx`: `LogoMark` (three stacked plates:
  navy grid, pale grid, dotted plate shading navy to gold) and `Logo` (mark plus the
  GALTONKS / KNOWLEDGE SYSTEM wordmark, wide-tracked caps, "KS" in gold). `Logo onDark`
  is the dark-icon version for navy grounds. `Brand` in `Layout.tsx` links it home.
- Favicon `web/public/favicon.svg` (mark on navy with a gold rim) and
  `apple-touch-icon.png` (the dark icon at 180px); `theme-color` is the navy.
- Installable web app: `web/public/manifest.webmanifest` with `icon-192.png`,
  `icon-512.png` and `icon-maskable-512.png` (all the dark icon; the maskable one keeps
  it inside the centre 80%). `lib/install.ts` drives the header's **Install app**
  button. No service worker on purpose: client data is never cached in the browser.
- **Navy is the highlight colour.** `--color-navy` `#000815` is the logo's navy, sampled
  from the dark icon. It is close to black, so it only reads as navy over an area. Use
  it for:
  - The header band on every page (`.navy-band`: navy with a 2px gold rule under it,
    dark logo, cream text).
  - The active nav link and active workspace tab (navy text over the gold underline),
    and hover states on links and client cards.
  - `.btn-dark`.
- Gold stays the primary accent: primary buttons, eyebrows, underlines, stat-card rules.
  Paper and card surfaces are unchanged.
- Text on navy uses the on-navy tokens only, all checked at ≥ 9:1 on `#000815`:
  - `--color-card` for primary text.
  - `--color-on-navy-muted` `#b9bfca` for secondary text.
  - `--color-gold-bright` `#e3ad33`, the logo gold, for accents.
  `--color-gold-bright` is for navy grounds only; on paper it is below 3:1, so gold text
  on paper stays `--color-gold-dark`.
- `--color-navy-soft` `#e7eaf0` is a navy tint for highlighted backgrounds.
- Navy is a brand colour, not a chart colour. Charts keep the gold ramp below. Adding
  navy to a chart means re-running the dataviz validator first.
- The assistant is called **Galton AI** on screen (`ui.ASSISTANT_NAME`; "AI" stays in
  the name so advisers and clients always know it's AI output). `GaltonName` draws it
  in navy on paper and card grounds, and its workspace tab stays navy even when not
  selected. On navy grounds (header badge, `.btn-dark`) use the on-navy tokens instead.
  Code, routes and audit events still say `assistant`.
- Status red and green keep their meaning on navy too (the dev sign-in badge uses a
  light red that passes on navy).

### Charts (`charts.py`, `web/src/components/charts.tsx`)
How they work:
- The model calls `show_chart` with a kind and, for what-if, the same changes as
  `run_what_if`. The engine builds the spec in `charts.py`; the model never supplies a
  figure, a label or a title (the what-if option name is built from the changes).
- A spec carries the figures, copied from engine output, plus axis ranges, which items
  to highlight or label, a caveat and a source. The browser positions marks and formats
  values with `lib/format.ts`; it never derives a figure. A chart that needs a new
  figure gets it from the engine first, with tests.
- Every chart has a hover tooltip on each mark, a Table toggle with the same figures,
  a legend when there are two or more series, and a source line.
- Before shipping a chart change, draw it in the running app, screenshot it and look
  at it: check for label collisions, clipping and overflow.

Layout (from the adviser's design files):
- **Risk by sector / asset class:** horizontal bars, because sector names read
  better on the left. Each row has two thin bars: weight on top in the pale shade,
  share of risk below in the solid shade. Rows are sorted by share of risk, highest
  first. Names sit on the left, right-aligned; values sit just past the end of each
  bar; a "Risk ÷ weight" column is on the far right, with ratios above 1.0 in bold.
  The legend is one line, top-left under the subtitle.
- **What-if before/after:**
  - *Risk mix:* two 100% stacked horizontal bars, Current above the option, with each
    percentage inside its segment. The legend is one row directly under the bars.
  - *Headline measures:* volatility, independent bets and stress loss each get a
    dumbbell row. A hollow dot is Current and a filled dot is the option, joined by a
    gold line, with values above the dots. Each row has its own zero-based scale.
    The two-dot legend sits top-right of the section.
- **Risk vs size:** one dot per holding, weight (x) against share of risk (y), with a
  dashed risk = size line and the region above it shaded. Holdings at or above
  `charts.HIGHLIGHT_RATIO` × weight get the solid fill. Dot size repeats weight.
  At most `MAX_POINT_LABELS` tickers are labelled, placed so they never overlap.

Size:
- Charts are drawn at the chat column's width (`VW` = 700 viewBox units, about
  680–720px), so 12-unit labels display at 12px or larger. Never draw at another
  width and scale down. Height follows content: about 3:2 for the scatter, and about
  400px for the sector chart and 450px for the what-if at typical row counts.

Colours (KVS mockup tokens, as `.viz-root` CSS variables in `web/src/index.css`):

| Role | Hex |
|---|---|
| Page background | `#F6EFE3` |
| Card / chart surface | `#FFFCF4` |
| Primary text (ink) | `#1C1A16` |
| Secondary text | `#5E584C` |
| Gold label text (the only gold allowed for text) | `#6B5113` |
| Gridlines, borders | `#E2D6BE` |
| Four-step ramp, darkest to lightest | `#6B5113`, `#906F22`, `#B08B3A`, `#C9AB66` |
| Main data fill (share of risk, highlighted dots) | `#906F22` |
| Paired pale fill (weight) | `#C9AB66` |

- The mockup's `#B8913A` (about 2.9:1 on the surface) and its two light ramp steps
  (`#D6BC82`, `#EADDBE`) failed the dataviz validator. The ramp above is the nearest
  passing version: a single hue with monotone lightness, and a light end at or above
  2:1. The two light steps are still below 3:1, so every mark using them carries a
  written value or an outline. Never use `#B8913A` for text.
- Re-run the dataviz skill's validator (`validateOrdinal` for the ramp, `validate`
  for categorical sets) against `#FFFCF4` whenever a chart colour changes.
- Status red and green stay reserved for status (e.g. losses), never as a series colour.

Risk sources ("Where the risk comes from"):
- The engine's `risk_sources` is four buckets, sorted by share: Largest common factor,
  Second-largest factor, Third-largest factor, All smaller factors. The Overview bars
  and the what-if risk mix use these exact names, in this order, with the ramp from
  darkest (largest) to lightest, as whole-number percentages.
- The mockup named these "Overall market", "Growth versus value" and "Rates and
  defensives". The engine's factors are statistical (unnamed principal components),
  so those names would be invented. Use them only once the engine has a named
  factor model that backs them.

Number formats (screen and report alike):
- Volatility: one decimal with % (3.5%). Independent bets: one decimal (1.6).
- Stress loss: true minus sign (−8.1%, never -8.1%): `format.lossSigned`.
- Risk sources: whole-number percentages. Ratios: two decimals with × (1.21×).

Source line, caveat, fonts:
- Every chart carries a source line under it in one format (`format.sourceLine`):
  "Risk decomposition · 120 monthly synthetic returns · engine v0.6.0 · holdings
  25b4d477". It always says where the figures came from. Synthetic data has no "as of"
  date, so there is no date until real data is connected.
- Synthetic charts say so in the eyebrow and the caveat.
- Fonts: serif titles, sans-serif body, monospaced numbers. Georgia, Helvetica and
  Menlo in the design files are stand-ins. A printed or PDF report must embed the
  real brand fonts.

Client report (not yet charted):
- Charts in the report must use engine output only and show the same figures as the
  screen. The design files' sector and risk-vs-size numbers were rough estimates for
  layout testing only. Each report chart carries the source line, and the report uses
  the same footer disclaimer wording as the screens.

### Tests
- Tests never hit the network. Assistant tests use a scripted `FakeClient`. API tests use
  `create_app(Settings(auth="dev", store_dir=tmp_path), store=...)` and FastAPI's
  `TestClient`, once per store; token tests sign HS256 tokens with a test secret.
- The `supabase_db` fixture (conftest.py) makes a fresh Postgres database with
  `tests/supabase_shim.sql` and the migrations applied, signs up and approves the dev
  users (alice, bob, carol; dave stays a never-approved sign-up); it skips without Postgres
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
- `src/advisor_engine/data/` splits the job in two. A vendor adapter (`PriceSource`) only
  returns a symbol's dividend-adjusted daily closes, or raises `SymbolNotFound`.
  Everything else is vendor-neutral, in `PriceHistoryData`: month-end closes of complete
  months only, the longest window every holding and the index share (at most
  `N_PERIODS` = 120, at least `MIN_PERIODS` = 24, or a `ValueError` naming the short
  tickers), simple monthly returns, and the cache. Cash is not priced: it keeps the
  synthetic near-constant return (a zero-variance column breaks the correlations).
- Adapters raise `SymbolNotFound` for an unknown ticker and `NotInPlan` for one the
  account's plan excludes (FMP: HTTP 402, or an error message about the subscription).
  `monthly_closes` names every failing ticker in one message, says so separately when the
  market index itself is excluded (fix: upgrade, or `ADVISOR_MARKET_INDEX`), and still
  caches the symbols that did load.
- To add a vendor, write an adapter like `fmp.py`, add a branch in `data._build`, and
  test it with `httpx.MockTransport` (tests never hit the network). Error messages are
  shown to advisers: never include the API key or the request URL (FMP puts the key in
  the query string), and raise from `None` so the httpx cause isn't chained.
- Config: `ADVISOR_MARKET_DATA` (synthetic | fmp), `FMP_API_KEY`, `ADVISOR_MARKET_INDEX`
  (default SPY), `ADVISOR_MARKET_CACHE` (default `data/market_cache`). The key is a
  secret: Render/`.env` only, never `VITE_*`, never logged.
- `returns_as_of` is quoted as month and year ("to the end of October 2026"): years are
  not figures to the number check, days are. The prompt says so; keep it that way.
- Caches implement `get_many(source, symbols)` / `put_many(source, {symbol: closes})`,
  fresh for 24 hours. With `SUPABASE_DB_URL` set the default is `SupabasePriceCache`
  (`ADVISOR_MARKET_CACHE=supabase|disk|<dir>` overrides): tables `market.prices` and
  `market.fetches`, shared by every firm and every API instance, so prices survive
  deploys. Freshness uses the database clock. Reads are batched (two queries per
  profile), with a process-local copy on top. A symbol's history is replaced as a
  whole on write, because adjusted closes are restated after every dividend.
- The cache never breaks a profile: a database error is logged, reads become misses
  (the vendor is asked) and writes are skipped.
- `market` is not keyed on `app.current_firm_id()` on purpose: prices are not client
  data. They are licensed, though, so the schema is not exposed by Supabase's REST API,
  RLS is on with no policies, and anon/authenticated have no rights. Only the API's own
  connection (the owner role) uses it, without switching to `authenticated`. Keep
  advisers and browsers away from raw vendor prices.
- The disk cache lives under `data/`, which git ignores. Do not commit licensed price
  or fundamentals data.

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
- Number check limits: spelled-out numbers aren't detected, coarse figures can match
  nearby values, and numbers the user typed aren't allowed unless a tool returned them.
- Methodology provenance and data ownership are not yet documented.
- Real data: a what-if that adds a ticker with a short history shortens the shared
  window, so before and after can cover different months. Prices are fetched on demand
  (no nightly refresh, by choice), and vendor API calls are not logged or counted yet.
