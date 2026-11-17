/* The skeleton's tax estimate (FIS.tax, taxOf) and the Overview with taxes switched on, for
   tests/test_tax_parity.py.

   Usage:  node capture_tax.js <skeleton dir> <out dir>      (after capture_build.js)
   Writes <out>/home-services-tax/tax.json.

   On the tax example, as shipped and corrected on the 13-week basis: the tax picture and the
   cash range with its payments for ideas that move tax; the Overview's tax measures, views,
   panels and brain check. Then tax-profile variants, each stored with its exact data: every
   entity, other year ends, the large-corporation rule, a short or missing prior year, an
   overpayment, a tax under the installment threshold, adjustments, tax depreciation for the
   next year, unplaced accounts, no profile, no Profit and Loss, a government-ID lookalike.
   A case where the skeleton throws records the error. Dates as YYYY-MM-DD, NaN as null. */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]), out = path.resolve(process.argv[3]);
globalThis.window = globalThis;
const FIS = require(path.join(dir, "app/engine.js")); globalThis.FIS = FIS;
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/engine.js"))).digest("hex");
const p2 = n => String(n).padStart(2, "0"), day = d => d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate());
const plain = v => {
  if (v instanceof Date) return day(v);
  if (ArrayBuffer.isView(v)) return Array.from(v).map(plain);
  if (Array.isArray(v)) return v.map(plain);
  if (v instanceof Map || typeof v === "function") return undefined;
  if (typeof v === "number") return isFinite(v) ? v : null;
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v)) { const x = plain(v[k]); if (x !== undefined) Object.defineProperty(o, k, { value: x, enumerable: true, writable: true, configurable: true }); } return o; }
  return v;
};
const clone = x => JSON.parse(JSON.stringify(x));
const safe = f => { try { return plain(f()); } catch (e) { return { error: e.constructor.name + ": " + e.message }; } };
const ex = "home-services-tax", d = path.join(out, ex);
const BRAIN = (() => { const b = path.join(dir, "examples", ex, "brain.js"); delete require.cache[require.resolve(b)]; return require(b); })();
const DATA = JSON.parse(fs.readFileSync(path.join(d, "data.json")));

function ideas(M) {
  const I = () => FIS.blankIdea(M), list = [], add = (n, f) => { const x = I(); f(x); list.push([n, x]); };
  list.push(["today", I()]);
  add("owner_salary", x => { x.salary = 6000; });
  add("buy_asset", x => { x.assets.push({ l: "Van", cost: 38000, wk: 4 }); });
  add("buy_two_assets_late", x => { x.assets.push({ l: "Truck", cost: 52000, wk: 30 }, { l: "Free", cost: 0 }, { cost: "1200", wk: 6.5 }); });
  add("sales_down_15", x => { x.shock = -15; });
  add("price_up", x => { x.price[M.off[0].name] = M.off[0].price * 1.1; });
  add("everything", x => { x.shock = -5; x.salary = 3000; x.assets.push({ l: "Van", cost: 38000, wk: 2 }); x.fixed.push({ l: "Lease", mo: 900 }); });
  return list;
}

function run(D, opts) {
  const M = FIS.build(D, BRAIN, opts || {}), T = FIS.evaluate(M, null), r = { ideas: {} };
  for (const [n, idea] of ideas(M)) { const E = FIS.evaluate(M, idea);
    let X; try { X = FIS.tax(M, E, { base: T }); } catch (e) { X = null; }
    r.ideas[n] = { idea: plain(idea), tax: safe(() => FIS.tax(M, E, { base: T })), cash: safe(() => FIS.cash(M, E, X ? { tax: X } : {})) }; }
  r.taxOf = safe(() => FIS.taxOf(M, T));
  r.rangePaths100 = safe(() => FIS.tax(M, T, { base: T, paths: 100 }));
  const C = FIS.cash(M, T);
  r.metrics = {}; ["tax_liability_year", "tax_next_payment", "tax_cash_90d"].forEach(k => { r.metrics[k] = FIS.metric(M, k, T, C); });
  r.viewMetrics = FIS.viewMetrics(M, T, C); r.viewPresets = FIS.viewPresets(M, T, C);
  r.panelList = FIS.panelList(M); r.panelPresets = FIS.panelPresets(M); r.panelDefault = FIS.panelDefault(M);
  r.check = FIS.check(BRAIN, D);
  return r;
}

const runs = { base: run(DATA, {}) };
{ const M0 = FIS.build(DATA, BRAIN, {}), f0 = M0.rates[0], x0 = M0.fixed[0];
  runs.corrected = Object.assign(run(DATA, { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 }),
    { opts: { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 } }); }

/* profile variants: each stores its data; the Python test builds from exactly that */
const variants = {};
const vary = (name, f) => { const D = clone(DATA); f(D, D.tax.profile); const M = FIS.build(D, BRAIN, {}), T = FIS.evaluate(M, null);
  const I = FIS.blankIdea(M); I.salary = 2000; I.assets.push({ l: "Van", cost: 20000, wk: 3 });
  let XT = null; try { XT = FIS.tax(M, T, { base: T }); } catch (e) { /* recorded below */ }
  /* a variant only changes tax, pnl and meta: store those, and the test lays them over data.json */
  variants[name] = { overrides: { tax: D.tax, pnl: D.pnl, meta: D.meta }, today: safe(() => FIS.tax(M, T, { base: T })), idea: plain(I), ideaTax: safe(() => FIS.tax(M, FIS.evaluate(M, I), { base: T })),
    cash: safe(() => FIS.cash(M, T, XT ? { tax: XT } : {})), check: FIS.check(BRAIN, D),
    metrics: Object.fromEntries(["tax_liability_year", "tax_next_payment", "tax_cash_90d"].map(k => [k, FIS.metric(M, k, T, FIS.cash(M, T))])) }; };
for (const e of ["s_corp", "partnership", "sole_prop", "llc", "trust"]) vary("entity_" + e, (D, P) => { P.entity.v = e; });
vary("entity_missing", (D, P) => { delete P.entity; });
vary("year_end_june", (D, P) => { P.yearEndMonth.v = 6; });
vary("year_end_march", (D, P) => { P.yearEndMonth.v = 3; });
vary("year_end_text", (D, P) => { P.yearEndMonth.v = "9"; });
vary("large_corp", (D, P) => { P.largeCorp.v = true; });
vary("prior_year_short", (D, P) => { P.priorYearFull.v = false; });
vary("prior_tax_zero", (D, P) => { P.priorYearTax.v = 0; });
vary("prior_tax_missing", (D, P) => { delete P.priorYearTax; });
vary("overpaid", (D, P) => { P.paidThisYear.v = 90000; });
{ /* find an income level whose federal tax is above 0 and under the $500 installment threshold */
  const scaled = f => { const D = clone(DATA); D.pnl.lines.forEach(l => { if (l.group === "income") l.values = l.values.map(v => Math.round(v * f)); }); return D; };
  const liab = f => { const M = FIS.build(scaled(f), BRAIN, {}), T = FIS.evaluate(M, null); return FIS.tax(M, T, { base: T }).cur.liability; };
  let lo = 0.6, hi = 1; for (let i = 0; i < 40; i++) { const mid = (lo + hi) / 2; if (liab(mid) > 300) hi = mid; else lo = mid; }
  const f = hi; vary("small_tax_under_threshold", D => { D.pnl.lines.forEach(l => { if (l.group === "income") l.values = l.values.map(v => Math.round(v * f)); }); }); }
vary("loss_making", (D, P) => { D.pnl.lines.forEach(l => { if (l.group === "income") l.values = l.values.map(v => v * 0.4); }); });
vary("adjustments", (D, P) => { P.adjustments = [{ l: "Section 179 election", amount: -15000, reason: "Accountant", status: "reviewed" }, { l: "Next year only", amount: 4000, taxYear: 2027 },
  { amount: "2500", src: "Text amount" }, { l: "Not a number", amount: "lots" }, null]; });
/* deliberate difference, recorded to show it: JavaScript takes "toString" as a status (every object inherits it) */
vary("adjustment_status_builtin", (D, P) => { P.adjustments = [{ l: "Bad status", amount: 100, status: "toString" }]; P.entity.v = "toString"; });
vary("tax_depreciation_next_year", (D, P) => { P.taxDepreciation.taxYear = 2027; P.taxDepreciation.confirmed = true; });
vary("tax_depreciation_missing", (D, P) => { delete P.taxDepreciation; });
vary("all_confirmed", (D, P) => { Object.values(P).forEach(f => { if (f && typeof f === "object" && !Array.isArray(f)) f.confirmed = true; }); });
vary("unplaced_and_odd_categories", D => { D.pnl.lines.forEach(l => { if (l.account === "Rent") { delete l.tax; } if (l.account === "Merchant fees") l.tax = "sometimes";
  if (l.account === "Vehicle fuel") { l.tax = "deductible"; l.taxStatus = "reviewed"; } });
  D.pnl.lines.push({ account: "Officer salaries", group: "expense", values: D.pnl.months.map(() => 3000) }, { account: "Lobbying dues", group: "expense", values: D.pnl.months.map(() => 50) },
    { account: "Client entertainment", group: "expense", values: D.pnl.months.map(() => 75) }, { account: "Federal income tax", group: "other", values: D.pnl.months.map(() => -400) }); });
vary("no_profile", D => { delete D.tax.profile; });
vary("no_books", D => { D.pnl = { months: [], lines: [] }; });
vary("government_id", (D, P) => { P.state.v = "EIN 12-3456789"; });
vary("as_of_mid_year", D => { D.meta.asOf = "2026-07-20"; });
vary("as_of_missing", D => { delete D.meta.asOf; });
vary("pnl_short_8_months", D => { D.pnl.months = D.pnl.months.slice(-8); D.pnl.lines.forEach(l => { l.values = l.values.slice(-8); }); });

/* edges a first review found unpinned */
for (const ye of [1, 2, 5, 7, 11]) vary("year_end_" + ye, (D, P) => { P.yearEndMonth.v = ye; });
vary("as_of_on_an_installment_date", D => { D.meta.asOf = "2026-12-15"; });
vary("as_of_on_a_weekend_installment", (D, P) => { P.yearEndMonth.v = 3; D.meta.asOf = "2026-03-15"; });
vary("accounts_without_text_names", D => { D.pnl.lines.push({ group: "expense", tax: "nondeductible", values: D.pnl.months.map(() => 40) },
  { account: 6100, group: "expense", tax: "nondeductible", values: D.pnl.months.map(() => 25) }, { group: "expense", values: D.pnl.months.map(() => 15) }); });
vary("confirmed_and_src_as_lists", (D, P) => { P.priorYearTax.confirmed = []; P.taxDepreciation.confirmed = []; P.taxDepreciation.src = []; P.state.src = []; });

/* the rules table overridden: a later rate, a rate with an end year, a date or balance-due row missing */
const rulesVariants = {}, R = FIS.TAX_RULES;
const withRules = (name, rows) => { const rules = Object.assign({}, R, { rows }), M = FIS.build(DATA, BRAIN, {}), T = FIS.evaluate(M, null);
  rulesVariants[name] = { rules: plain(rules), today: safe(() => FIS.tax(M, T, { base: T, rules })) }; };
withRules("later_rate_from_2027", R.rows.concat([Object.assign({}, R.rows[0], { id: "rate-2027", value: 0.25, fromYear: 2027 })]));
withRules("rate_ends_2025", R.rows.map(r => r.kind === "rate" ? Object.assign({}, r, { toYear: 2025 }) : r));
withRules("no_installment_dates", R.rows.filter(r => r.kind !== "estDates"));
withRules("no_balance_due_row", R.rows.filter(r => r.kind !== "balanceDue"));
withRules("no_meals_row", R.rows.filter(r => r.kind !== "mealsDeductible"));

/* the brain's asset life, written into the bridge and the notes as the skeleton writes it */
const brainVariants = {};
for (const life of [2.5, 1500]) { const B = Object.assign({}, BRAIN, { tax: Object.assign({}, BRAIN.tax, { assetLifeYears: life }) }), M = FIS.build(DATA, B, {}), T = FIS.evaluate(M, null);
  const I = FIS.blankIdea(M); I.assets.push({ l: "Van", cost: 38000, wk: 4 });
  brainVariants["asset_life_" + life] = { assetLifeYears: life, idea: plain(I), tax: safe(() => FIS.tax(M, FIS.evaluate(M, I), { base: T })) }; }

fs.writeFileSync(path.join(d, "tax.json"), JSON.stringify({ engine_sha256: sha, example: ex, runs, variants, rulesVariants, brainVariants }) + "\n");
process.stderr.write(ex + ": " + Object.keys(variants).length + " variants\n");
