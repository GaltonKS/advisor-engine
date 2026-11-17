# Phase 2 — the engine, to parity

Work order for a Claude Code session in `GaltonKS/advisor-engine`.
Suggested home: `docs/phase-2.md`. Starts after Phase 1 lands.

## Context

Port the FIS pilot skeleton's engine into Python under the repo's conventions.

The skeleton is not a prototype. It is a specified product: an engine and screens that
never change, plus a `brain.js` (who the company is, its words, its numbers, what it can
model) and a `data.js` (a QuickBooks-shaped snapshot) per company. It carries an
18-metric library, a 12-panel library, a tax engine whose rates sit in a sourced rules
table, 54 tests, and a playbook for making a pilot from it. `PLAYBOOK.md` and
`README.md` are the specification — read both before writing any Python.

**The data shape is the most valuable thing in it.** `data.js` — `pnl`, `balance`,
`sales` (weekly rows keyed by unit and offering), `customers` as shares and a Lorenz
curve, `ar`/`ap` buckets, `facts` with `src` and `confirmed`, `unitTerms` — is
deliberately shaped like QuickBooks so a snapshot can be swapped for a live connection
with nothing above the data layer changing. Adopt it. Do not design a new one.

What the skeleton lacks, and what this port adds: the number check, server-side
computation, per-person sign-in, and a real ingestion pipeline.

**This phase ships no screens.**

## Prerequisites

1. The skeleton, in full. `app/engine.js` (771 lines), `app/upload.js` (314),
   `test/engine.test.js` (195), `test/tax.test.js`, `test/upload.test.js`,
   `test/qb-fixtures.js`, and the three worked examples under `examples/`.
2. Phase 1 merged. Non-negotiable — see the first item under "Three things that will
   bite".
3. Elite's `data.js`, and the skeleton's outputs for it captured as JSON.

## Rules

1. **Engine modules are pure.** No I/O, no network, no clock, no `os.environ`. Nothing
   imported from `assistant/`, `api/` or the shared layer. Update
   `tests/test_layering.py` to know the new modules.
2. **Plain JSON-serialisable output.** `float()` / `int()` on numpy scalars.
3. **`ValueError` with a clear message for bad input.** The skeleton's `fail()` in
   `upload.js` returns a plain reason per refusal — keep every one of those reasons.
4. **Docstrings state the method and its caveats.** The skeleton's dense one-liners are
   not a style to copy; they are a thing to translate and explain.
5. **Uncalibrated numbers are DRAFT**, in `profile_config.py`'s shape: one module, each
   marked, under a `METHOD_VERSION`, surfaced as `method_status`. That covers lever
   presets, `assumptions.elasticity` (−0.5 in the barbershop brain) and tax.
6. **Delete nothing.** Cleanup is phase 2.5.
7. Bump `__version__` — engine output changes in this phase.

## What ports where

| Skeleton (`app/engine.js`) | Lines | Python |
| --- | --- | --- |
| `rng`, `gauss` | 25–26 | `rand.py` — exact port, see below |
| `sum` `mean` `sd` `quant` `clamp` `fin` `n2` | 17–24 | stdlib / numpy |
| `usd` `usdk` `pct` `num` `fmtD` `signed` | 31–39 | **Does not port.** See below |
| `vocab`, `termTypes` | 41–69 | Company config, not engine |
| `termDefaults` `termOf` `perMonth` `termText` | 70–86 | `model.py` |
| `blankIdea`, `normIdea`, `leversOn`, `FOCUS` | 87–96, 563–564 | `levers.py` |
| `build(DATA, BRAIN, opts)` | 97–163 | `model.py`, exposed via `analysis.company_profile()` |
| `evaluate(M, idea)` | 164–205 | `model.py`, exposed via `analysis.apply_idea()` |
| `cash(M, E, opts)` | 206–225 | `cash.py` |
| `bridge(M, today, idea)` | 226–262 | `compare.py`, alongside the existing `compare_profiles` |
| `TAX_RULES` … `taxRangeText` | 263–406 | `tax.py` — last, optional |
| `METRICS` `metric` `viewMetrics` `viewKeys` | 407–505 | the metric library module |
| `PANELS` `panelList` `panelKeys` `costLines` | 506–562 | `charts.py` |
| `rulesPrompt` `ideaFromAI` `explainPrompt` `resultSummary` `plainWords` | 565–697 | `assistant/` — prompts and tools, **not** engine |
| `chartFor` | 698–709 | `charts.py` |
| `check(BRAIN, DATA)` | 710–765 | a validation module, with tests |

| Skeleton (`app/upload.js`) | Python |
| --- | --- |
| `csv` `num` `monthKey` `dayKey` | `books.py` helpers |
| `top` `periodOf` `kindOf` — report detection from the title block | `books.py` — this is the fingerprinting Phase 3 builds on |
| `readPnl` `readBalance` `readAging` `readCustomers` + the sales detail reader | `books.py`, one parser per report |
| `REPORTS`, `fail` | the refusal reasons, kept verbatim |

Desktop is out of scope, so drop the qbXML path. Keep `test/qb-fixtures.js` — it
generates QuickBooks layouts and is the only test data `books.py` will have until a real
export arrives.

## Three things that will bite

### 1. `usdk` is why Phase 1 comes first

Line 33 of `engine.js` formats money as `$553.2K` above $10,000 and `$1.2M` above $1M.
That is the engine's *native* money format, so essentially every headline figure Elite
produces is written in the exact notation the number check silently skipped before
Phase 1.

**And the formatters must not port into the engine.** The repo's rule is that the engine
returns numbers and `web/src/lib/format.ts` formats them. If a Python engine returns
`"$553.2K"` as a string, the raw value never reaches the tool output, so `numcheck` has
nothing to match against and the guarantee quietly becomes vacuous. Engine returns
numbers. Browser formats. Port `usdk`'s *rules* into `format.ts` if the look is wanted.

### 2. `rng` is mulberry32, so parity can be exact

```js
function rng(seed) { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); … }
```

About eight lines, and `gauss` is standard Box-Muller with a cached spare. Both port to
Python exactly, which means the cash simulation can match the skeleton path for path and
the gate can assert **to the cent** rather than to a tolerance.

Port them into their own module with their own tests. The traps are `Math.imul`
(32-bit signed multiply, not Python's `*`), `|0` wrapping, `>>>` being an unsigned
shift, and JavaScript's `/ 4294967296` producing a float. Test the first twenty draws
for a known seed against the JavaScript before trusting anything downstream.

### 3. A brain holds executable config

`brain.js` allows per-company metrics as closures:

```js
metrics: { top4_share: { l: "Top 4 barbers' share of sales", f: (M, T) => … } }
```

That cannot live in a `companies` row in Postgres. Do not invent an expression language.
Nearly every case is a parameterised library metric — `top4_share` is plainly
`top_n_share` with `n = 4`. Port the library with parameters, and where a company truly
needs something new, it becomes engine code behind a key, with a test, for everyone.
That is already the skeleton's own rule: never change the engine for one company.

## Build order

1. **`rand.py`** — mulberry32 and Box-Muller, with the known-seed test. Nothing
   downstream is trustworthy until this matches.
2. **`facts.py`** — the owner's confirmed numbers, each with `src` and `confirmed`.
   Keeps the store-answers-not-scores rule.
3. **`books.py`** — the `upload.js` readers, one parser per report, every refusal reason
   preserved. Carry `qb-fixtures.js` over as test data.
4. **`sales.py`** — weekly rows by unit and offering, tickets, average ticket, and the
   tie to the books. The skeleton's "does it tie?" check is a step a human performs; make
   it a named engine result, because Phase 3 turns it into a gate.
5. **`model.py`** — pay terms, `build`, `evaluate`.
6. **`levers.py`** — `blankIdea`, `normIdea`, `leversOn`, the presets, marked DRAFT.
7. **`cash.py`** — the 13-week range, recording seed and path count.
8. **`compare.py`** — add `bridge`.
9. **The metric library** — 18 metrics, views, the pick-list rules. Name it so it is not
   confused with the existing `api/metrics.py`, which is operator pilot metrics.
10. **`charts.py`** — the 12 panels and `chartFor`.
11. **`analysis.py`** — `company_profile()`, `apply_idea()`.
12. **The brain check** — `check(BRAIN, DATA)` as a validation module.
13. **`tax.py`** — last. More built than the plan assumed (`test/tax.test.js` is larger
    than the engine tests), and off unless a company switches it on. Drop it first if
    the gate is close and time is short.

The assistant pieces (`rulesPrompt`, `ideaFromAI`, `explainPrompt`) are Phase 4 work,
not Phase 2. `ideaFromAI` maps onto the repo's existing `scenario.py` pattern: the model
proposes structured assumptions, the engine computes every result.

## The gate

**Python gives the same numbers as the skeleton, to the cent.**

Three layers, in this order:

1. **Port `test/engine.test.js` to pytest.** Its 22 tests are the engine's contract, not
   just coverage — "today's setup adds up", "the bridge adds up to the change in the
   bottom line", "cash range is ordered, repeatable, and moves the right way",
   "commission against booth rent", "the owner's corrections flow through". Port them
   first and they will catch most errors before fixtures exist.
2. **The three worked examples as fixtures.** `examples/barbershop`,
   `examples/home-services`, `examples/home-services-tax` each have a brain and a data
   file. Run the skeleton, capture its output as JSON, commit it, assert Python matches.
3. **Elite's real data.** The same, against the one real dataset, tied to DaySmart's own
   totals.

Cash asserts to the cent because of `rand.py`. If it does not, fix `rand.py` rather than
loosening the gate.

Nothing in Phase 3 starts until all three pass.

## Definition of done

- Every module above exists with tests, under the conventions in "Rules".
- `tests/test_layering.py` knows the new modules and passes.
- All three gate layers pass; the parity test's docstring states exactly what it
  asserts.
- No formatter in the engine. Every engine return value is a number.
- Uncalibrated numbers in one module, marked DRAFT, under a `METHOD_VERSION`, surfaced
  as `method_status`.
- `__version__` bumped; `CLAUDE.md`'s layout section updated.
- Nothing deleted. No screens, no routes, no migrations.
