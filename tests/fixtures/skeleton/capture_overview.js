/* The skeleton's Overview pieces on its worked examples, for tests/test_overview.py: every
   measure (label, text, small line, tone), the pick-list, the ready-made views and the four
   numbers shown for saved choices; the panel list, layouts and four panels shown; the cost
   lines; chartFor for each focus on a set of ideas; and the brain check.

   Usage:  node capture_overview.js <skeleton dir> <out dir>      (after capture_build.js)
   Writes <out>/<example>/overview.json and <out>/check_cases.json.

   The JS runs each example's real brain from the skeleton (so the barbershop's own top4_share
   is the closure); the Python test reads brain.json, where that closure is the parameterised
   measure top_n_share with n = 4. The tax example is run with levers.taxes switched off: the
   tax estimate is Phase 2, step 7. Each example is built five ways, cut as capture_build.js
   cuts them. */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]), out = path.resolve(process.argv[3]);
globalThis.window = globalThis;
const FIS = require(path.join(dir, "app/engine.js")); globalThis.FIS = FIS;
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/engine.js"))).digest("hex");
const clone = x => JSON.parse(JSON.stringify(x));
const load = ex => { const b = path.join(dir, "examples", ex); for (const f of ["brain.js", "data.js"]) delete require.cache[require.resolve(path.join(b, f))];
  return { BRAIN: require(path.join(b, "brain.js")), DATA: require(path.join(b, "data.js")) }; };
const taxesOff = B => { const b = Object.assign({}, B); b.levers = Object.assign({}, B.levers, { taxes: false }); return b; };

const SELECTIONS = [null, "garbage", { preset: "default" }, { preset: "cash" }, { preset: "carry" }, { preset: "owed" }, { preset: "no-such" },
  { preset: "custom", keys: ["profit_month", "profit_month", "bogus", "cash_on_hand"] }, { preset: "custom", keys: ["bogus"] },
  { preset: "custom", keys: ["ar_open", "ap_open", "net_income"] }, { preset: "custom", keys: ["a", "b", "c", "d", "e", "f"] }];
const PANEL_SELECTIONS = [null, { preset: "default" }, { preset: "monthly" }, { preset: "cashfirst" }, { preset: "no-such" },
  { preset: "custom", keys: ["net", "net", "nope", "<script>", "ar"] }, { preset: "custom", keys: ["ar", "ap", "net"] }, { preset: "custom" }];

function ideas(M) {
  const I = () => FIS.blankIdea(M), u = M.units, o = M.off, list = [], add = (n, f) => { const x = I(); f(x); list.push([n, x]); };
  list.push(["today", I()]);
  add("price_up", x => { x.price[o[0].name] = o[0].price * 1.1; });
  add("offering_up", x => { x.offer[o[1].name] = 20; });
  add("remove_unit", x => { x.terms[u[1].name] = { on: false }; });
  add("pay_change_small", x => { x.terms[u[0].name] = { type: "share", rate: 50.5 }; });
  add("pay_change", x => { x.terms[u[0].name] = { type: "fixedIn", rent: 300 }; });
  add("add_unit", x => { x.added.push({ name: "New", mo: 4000 }); });
  add("price_and_unit", x => { x.price[o[0].name] = o[0].price * 1.1; x.terms[u[1].name] = { on: false }; });
  add("sales_down", x => { x.shock = -15; });
  return list;
}

function run(B, D) {
  const M = FIS.build(D, B, {}), T = FIS.evaluate(M, null), C = FIS.cash(M, T), r = {};
  r.metrics = {}; Object.keys(FIS.METRICS).concat(Object.keys(B.metrics || {})).forEach(k => { r.metrics[k] = FIS.metric(M, k, T, C); });
  r.viewMetrics = FIS.viewMetrics(M, T, C); r.viewPresets = FIS.viewPresets(M, T, C);
  r.viewKeys = SELECTIONS.map(sel => ({ sel, keys: FIS.viewKeys(M, sel, T, C) }));
  r.panelList = FIS.panelList(M); r.panelPresets = FIS.panelPresets(M); r.panelDefault = FIS.panelDefault(M);
  r.panelKeys = PANEL_SELECTIONS.map(sel => ({ sel, keys: FIS.panelKeys(M, sel) }));
  r.costLines = FIS.costLines(M); r.costLines6 = FIS.costLines(M, 6);
  r.chartFor = {}; for (const [n, idea] of ideas(M)) { const E = FIS.evaluate(M, idea); r.chartFor[n] = { idea: clone(idea), focus: Object.fromEntries(FIS.FOCUS.map(f => [f, FIS.chartFor(M, { focus: f }, T, E)])) }; }
  r.check = FIS.check(B, D);
  return r;
}

const cut = (D, n) => { const k = D.sales.weeks.length; D.sales.weeks = D.sales.weeks.slice(-n); D.sales.rows = D.sales.rows.filter(r => r[0] >= k - n).map(r => [r[0] - (k - n)].concat(r.slice(1))); return D; };
for (const ex of ["barbershop", "home-services", "home-services-tax"]) {
  let { BRAIN, DATA } = load(ex); if (ex === "home-services-tax") BRAIN = taxesOff(BRAIN);
  const runs = {};
  runs.base = run(BRAIN, DATA);
  { const D = clone(DATA); delete D.customers; delete D.ar; delete D.ap; runs.no_customers_ar_ap = run(BRAIN, D); }
  { const D = clone(DATA); D.pnl = { months: [], lines: [] }; runs.no_books = run(BRAIN, D); }
  runs.weeks_30 = run(BRAIN, cut(clone(DATA), 30));
  runs.weeks_11 = run(BRAIN, cut(clone(DATA), 11));
  fs.writeFileSync(path.join(out, ex, "overview.json"), JSON.stringify({ engine_sha256: sha, example: ex, taxesOff: ex === "home-services-tax", runs }) + "\n");
  process.stderr.write(ex + ": " + Object.keys(runs).length + " runs\n");
}

/* the brain check on broken and odd brains; each case stores its brain and data (JSON, no closures) */
const cases = {};
{ const { BRAIN, DATA } = load("home-services"), B = clone(BRAIN), D = clone(DATA);
  B.views = [{ id: "x", name: "Three and a typo", keys: ["revenue_month", "nope", "cash_on_hand"] }, { id: "custom", name: "Clash", keys: ["a", "b", "c", "d"] }, { name: "No id", keys: [] }, { id: "x", name: "Same id", keys: ["revenue_month", "profit_month", "cash_on_hand", "ar_open"] }];
  B.panels = ["weekly", "weekly", "typo"];
  B.panelViews = [{ id: "a", name: "Short", keys: ["weekly", "cash"] }, { id: "default", name: "Clash", keys: ["weekly", "cash", "books", "net"] }, { name: "No id", keys: [] },
    { id: "b", name: "Typo", keys: ["weekly", "cash", "books", "nope"] }, { id: "c", name: "Twice", keys: ["weekly", "weekly", "books", "net"] }];
  B.headline = B.headline.concat(["tax_cash_90d", "not_a_metric"]); B.upload = "yes"; B.assistant = { starters: ["What if “Nobody” leaves?"] };
  B.levers = Object.assign({}, B.levers, { taxes: "no" });
  D.unitTerms = Object.assign({}, D.unitTerms, { Ghost: { type: "fixedIn", rent: 1 } });
  cases.broken_views_panels_and_switches = { brain: B, data: D }; }
{ const { BRAIN, DATA } = load("barbershop"), D = clone(DATA), B = clone(BRAIN); delete B.metrics;
  B.company = { name: "" }; B.systems = [{ key: "daysmart" }]; B.theme = {}; B.panels = ["weekly", "cash", "tax", "books"]; B.views = [];
  D.sales.byMonth = D.sales.byMonth.map(m => ({ month: m.month, amount: m.amount * 0.8 })); D.balance = null; D.pnl = { months: [], lines: [] };
  cases.no_name_tax_panel_partial_detail = { brain: B, data: D }; }
{ const { BRAIN, DATA } = load("barbershop"), D = cut(clone(DATA), 8), B = clone(BRAIN); delete B.metrics; B.coverage = "Booth renters' sales are not in the books.";
  D.sales.byMonth = D.sales.byMonth.map(m => ({ month: m.month, amount: m.amount * 0.8 }));
  cases.eight_weeks_with_coverage = { brain: B, data: D }; }
{ const { BRAIN, DATA } = load("home-services"), B = clone(BRAIN), D = clone(DATA); D.facts.forEach(f => { f.confirmed = true; }); D.meta.sample = false;
  cases.all_confirmed_real = { brain: B, data: D }; }
for (const k of Object.keys(cases)) cases[k].check = FIS.check(cases[k].brain, cases[k].data);
fs.writeFileSync(path.join(out, "check_cases.json"), JSON.stringify({ engine_sha256: sha, cases }) + "\n");
