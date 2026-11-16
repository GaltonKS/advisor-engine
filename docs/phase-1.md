# Phase 1 — platform guardrails

Work order for a Claude Code session in `GaltonKS/advisor-engine`.
Suggested home: `docs/phase-1.md`. Base: `main` at `8e83759` (v0.9.0).

## Context

The repo is being extended from an advisor product into a small-business one.
Decided 16 Nov 2026:

- **One repo.** Small-business modules go beside the advisor ones, not in a fork.
- **The advisor product is parked, not deleted.** Frozen: no new features, no new
  migrations, no calendar time. Its tests stay green.
- **Tenancy:** firm = the business, one company per firm, for the pilot.
- **Hosting:** Render + Supabase. **Delivery:** web first, installable; no Electron
  shell; native app later.
- **QuickBooks Online only.** Desktop is out of scope.

Phase 1 is everything that is safe to do *before* those modules exist. It touches only
the platform both products share, and it is the last work that needs no decisions from
anyone else.

## Rules for this phase

1. **Delete nothing.** Not advisor modules, not `data/`, not the market tables.
   Cleanup happens after the Phase 2 parity gate, when we know what the business
   modules need from the shared layer. Removing code now means touching the modules
   that keep 571 tests green, for no new capability, right before a port that leans on
   the same layer.
2. **Do not refactor advisor code.** If a change serves only the parked side, it does
   not get made.
3. **Do not bump `__version__`.** Engine output is unchanged in this phase.
4. **Do not loosen `numcheck` tolerances** to make anything pass.
5. **Run `pytest` before committing.** The suite is seconds.
6. **Do not push without being asked.** Branch and commit locally; the human opens the PR.

## Tasks

### 1. Read K/M/B suffixes in the number check

**Why.** A reply written in business units is not checked at all. `_TOKEN` ends in
`(?![\w]|\.\d)`, so a letter straight after the digits kills the whole match including
the backtrack. `"$13.9K"` and `"$1.2M"` produce **zero** tokens and `verify()` returns
`ok` with `checked == 0`. An invented `"$99.9K"` against a tool value of `13912.4`
passes silently. Reproduced on `8e83759`.

**Change** (`src/advisor_engine/assistant/numcheck.py`):

- Add `_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}`.
- Add `|[kKmMbB](?!\w)` to the `suffix` alternation in `_TOKEN`.
- Add `scale: float = 1.0` to `Token`.
- In `_tokens`: look the suffix up in `_SCALE`, multiply the value by it, pass it on the
  `Token`. A scaled figure is **always** `kind == "bare"` — it is money or a count and
  must never match the percent pool.
- In `_check`: the written unit carries the multiplier, so
  `unit = (10 ** -tok.decimals) * tok.scale` and `strict = min(0.5 * tok.scale, 0.25 * unit)`.
  With `scale == 1.0` both are identical to today, so non-suffixed numbers are untouched.
- Extend `_LABEL` (currently just the `1-in-100` metric name) with account types and
  calendar dates — see task 1b.

**Acceptance** — add to `tests/test_numcheck.py`, with `BOOKS = {"kept_per_month": 13912.4, "year_sales": 553174}`:

| Reply | Expected |
|---|---|
| `The shop keeps $13.9K a month.` | ok, `checked == 1` |
| `The shop keeps $99.9K a month.` | not ok, `no matching tool figure` |
| `Sales were $1.2M for the year.` | not ok, `no matching tool figure` |
| `Sales were $553,174 for the year.` | ok, `checked == 1` |
| `$13.9K` vs `{"x": 13949.0}` | ok (nearest 100) |
| `$13.9K` vs `{"x": 13951.0}` | not ok |
| `$21.1K of risk` vs `{"top_component_share": 0.2113}` | not ok (no percent pool) |

Also check `extract_numbers` scales: `$13.9K` → `13900.0`, `1.5B` → `1500000000.0`,
`-2.5M` → `-2500000.0`.

### 1b. Account types and dates become labels

**Why.** Two false positives exist on current main — `401(k)` and `Nov 10` are both
flagged as unverified figures today. The suffix change would make the first worse:
`401k` would read as 401,000 and `403b` as 403 billion.

**Change.** Extend `_LABEL` with `\b40[13]\s?\(?[kb]\)?` and month-name dates in both
orders (`Nov 10`, `10 November`), case-insensitive. Labels are blanked with spaces of
equal length before tokenising, so offsets are preserved.

**Acceptance.** `401(k)`, `401k`, `403(b)`, `403b`, `Nov 10`, `10 November`,
`Sept. 3` all give `checked == 0`. `In Nov 2026 sales were $553,174.` still checks
exactly one figure — the year is skipped by the existing rule, the month name does not
swallow it.

### 1c. Does the advisor side already emit suffixes?

Read `assistant/prompts.py` and `assistant/mock_client.py`. If advisor replies can write
`K` or `M`, the hole was live in the shipped product, not only ahead of us. Report the
finding; do not change the prompt in this phase.

### 2. "Read, never write" as a rule with a test

**Why.** No route, tool or job may ever write to QuickBooks or a sector system. It needs
to be a rule in `CLAUDE.md` beside the core rule, and enforced in code the way the
layering rule is — not left to good intentions once connectors arrive.

**Change.** Add the rule to `CLAUDE.md`. Add a test modelled on
`tests/test_layering.py`, which already parses the AST: walk the modules under the
shared and ingestion packages and fail on any non-GET HTTP call (`post`, `put`, `patch`,
`delete`) on an `httpx` client or module.

**Acceptance.** The test passes today (there are no such calls) and fails if one is
added. Prove the second half with a temporary fixture, then remove it.

### 3. Write the freeze into `CLAUDE.md`

**Why.** "Parked" only holds if the file that steers Claude Code sessions says so.
Without it a session will helpfully refactor advisor code, tidy shared code *for*
`risk.py`, or improve tests nobody cares about any more. That is the drag the parking
decision was meant to avoid, and it arrives disguised as helpfulness.

**Change.** A short section stating: the advisor engine is frozen; no new advisor
features or migrations; changes that serve only the advisor side are not made; its tests
stay green because a shared-layer change that breaks them has probably broken something
real; deleting is a later decision, after the Phase 2 parity gate.

### 4. Mark the advisor tests so they run as a group

**Why.** So a shared-layer change that breaks the parked side is visible at a glance,
rather than buried in 571 results.

**Change.** Register an `advisor` marker in `pyproject.toml` and apply it to the test
files that only exercise advisor-specific modules (risk, holdings, market data,
questionnaire, household profile, asset classes, charts). Judge by what each file
imports; do not guess from the filename.

**Acceptance.** `pytest`, `pytest -m advisor` and `pytest -m "not advisor"` all pass,
and the two subsets add up to the whole. Note the command in `CLAUDE.md`.

### 5. Fix the README status line

`README.md` still says *"Prototype, step 2 of the MVP: risk profile from a holdings
CSV."* at v0.9.0, with 22 test files, a FastAPI backend on Render, Supabase with RLS,
two-step sign-in and a React app. Anyone outside the two founders reads that first and
forms the wrong impression of what exists. Replace it with an accurate one-paragraph
status. Do not restructure the rest of the README.

## Definition of done

- `pytest` green locally, and CI green including the Postgres-backed tests. Those 159
  tests could not be run in the sandbox where this was drafted, so CI is the real check
  on them. They exercise `SupabaseStore` and RLS, which `numcheck` does not touch.
- `CLAUDE.md` carries two new rules: read-never-write, and the freeze.
- `README.md` describes v0.9.0.
- Nothing deleted. `__version__` unchanged.
- One branch, commits scoped per task, PR opened by a human.
