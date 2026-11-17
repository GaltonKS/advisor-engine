/* Runs the FIS pilot skeleton's engine on its three worked examples and writes what it computes as JSON,
   for the parity tests (tests/test_parity.py). Also writes each example's brain and data as JSON for Python.

   Usage:  node capture.js <skeleton dir> <out dir>
   The skeleton is not vendored: this records the sha256 of app/engine.js in every file it writes, and the
   parity test says which skeleton version its fixtures came from.

   Dates are written as YYYY-MM-DD (the skeleton builds them as local dates). NaN and Infinity become null,
   as JSON.stringify does. Typed arrays become plain arrays. */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]), out = path.resolve(process.argv[3]);
globalThis.window = globalThis;
const FIS = require(path.join(dir, "app/engine.js")); globalThis.FIS = FIS;
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/engine.js"))).digest("hex");

const p2 = n => String(n).padStart(2, "0");
const day = d => d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate());
const plain = v => {
  if (v instanceof Date) return day(v);
  if (ArrayBuffer.isView(v)) return Array.from(v).map(plain);
  if (Array.isArray(v)) return v.map(plain);
  if (v instanceof Map) return undefined;
  if (typeof v === "function") return undefined;
  if (typeof v === "number") return isFinite(v) ? v : null;
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v)) { const x = plain(v[k]); if (x !== undefined) o[k] = x; } return o; }
  return v;
};

/* A brain holds closures for company-specific metrics. Python has no closures in config: each becomes a library
   metric with parameters. This table is the port of every closure in the shipped examples; the parity test checks
   that the parameterised metric renders the same text as the closure did. */
const METRIC_PORTS = { top4_share: { metric: "top_n_share", n: 4 } };
const brainJSON = B => { const b = plain(B); if (B.metrics) { b.metrics = {};
  for (const k of Object.keys(B.metrics)) { if (!METRIC_PORTS[k]) throw new Error("No Python port for brain metric " + k); b.metrics[k] = Object.assign({ l: typeof B.metrics[k].l === "string" ? B.metrics[k].l : undefined }, METRIC_PORTS[k]); } }
  return b; };

const load = ex => { const b = path.join(dir, "examples", ex); for (const f of ["brain.js", "data.js"]) delete require.cache[require.resolve(path.join(b, f))];
  return { BRAIN: require(path.join(b, "brain.js")), DATA: require(path.join(b, "data.js")) }; };

/* the ideas every example is run on: one per kind of change, then everything at once */
function ideas(M, ex) {
  const I = () => FIS.blankIdea(M), u = M.units, o = M.off, list = [];
  list.push(["today", I()]);
  { const x = I(); x.price[o[0].name] = o[0].price * 1.1; x.elast = -0.5; list.push(["price_up_10", x]); }
  { const x = I(); x.price[o[1].name] = o[1].price * 0.8; list.push(["price_down_20_brain_elasticity", x]); }
  { const x = I(); x.terms[u[2].name] = { on: false }; list.push(["remove_unit", x]); }
  { const x = I(); x.terms[u[2].name] = { on: false }; x.keep = 50; list.push(["remove_unit_keep_50", x]); }
  { const x = I(); x.terms[u[0].name] = { type: "fixedIn", rent: 300 }; list.push(["unit_to_fixed_rent", x]); }
  { const x = I(); x.terms[u[1].name] = { type: "wage", wage: 4200 }; list.push(["unit_to_wage", x]); }
  { const x = I(); x.terms[u[1].name] = { type: "share", rate: 45 }; list.push(["unit_to_share", x]); }
  { const x = I(); x.added.push({ name: "New one", mo: 10000, type: "wage", wage: 5800 }); list.push(["add_unit_wage", x]); }
  { const x = I(); x.added.push({ name: "Weekend", mo: 2500, type: "share", rate: 45 }); list.push(["add_unit_share", x]); }
  { const x = I(); x.shock = -15; list.push(["sales_down_15", x]); }
  { const x = I(); x.offer[o[2].name] = 25; list.push(["offering_up_25", x]); }
  { const x = I(); x.rate[M.rates[0].k] = M.F[M.rates[0].k] + 1; list.push(["cost_rate_up_1", x]); }
  { const x = I(); x.fixed.push({ l: "New lease", mo: 2000 }); list.push(["new_monthly_bill", x]); }
  { const x = I(); x.once.push({ l: "Van", amt: -38000, wk: 4 }); list.push(["one_time_out", x]); }
  { const x = I(); x.once.push({ l: "Refund", amt: 5000, wk: 13.6 }); list.push(["one_time_in_late", x]); }
  { const x = I(); x.shock = -10; x.price[o[0].name] = o[0].price + 5; x.terms[u[0].name] = { type: "fixedIn", rent: 280 };
    x.terms[u[Math.min(6, u.length - 1)].name] = { on: false }; x.keep = 30; x.added.push({ name: "Weekend", mo: 2500, type: "share", rate: 45 });
    x.rate[M.rates[M.rates.length - 1].k] = 4; x.fixed.push({ l: "Rent increase", mo: 500 }); x.once.push({ l: "Chairs", amt: -6000, wk: 2 }); list.push(["everything", x]); }
  if (FIS.leversOn(M.BRAIN).taxes) {
    { const x = I(); x.salary = 6000; list.push(["owner_salary", x]); }
    { const x = I(); x.assets.push({ l: "Van", cost: 38000, wk: 4 }); list.push(["buy_asset", x]); }
  }
  return list;
}

const evalOut = E => plain(Object.assign({}, E, { by: undefined }));
const metricsOut = (M, T, C) => { const keys = Object.keys(FIS.METRICS).concat(Object.keys(M.BRAIN.metrics || {}));
  const o = {}; keys.forEach(k => { o[k] = FIS.metric(M, k, T, C); }); return o; };
const SELECTIONS = [null, "garbage", { preset: "default" }, { preset: "cash" }, { preset: "carry" }, { preset: "owed" }, { preset: "no-such" },
  { preset: "custom", keys: ["profit_month", "profit_month", "bogus", "cash_on_hand"] }, { preset: "custom", keys: ["bogus"] },
  { preset: "custom", keys: ["ar_open", "ap_open", "net_income"] }];
const PANEL_SELECTIONS = [null, { preset: "default" }, { preset: "monthly" }, { preset: "cashfirst" }, { preset: "no-such" },
  { preset: "custom", keys: ["net", "net", "nope", "<script>", "ar"] }, { preset: "custom", keys: ["ar", "ap", "net"] }, { preset: "custom" }];

function run(ex, B, D, opts) {
  const M = FIS.build(D, B, opts || {}), T = FIS.evaluate(M, null), C = FIS.cash(M, T), res = { model: plain(Object.assign({}, M, { DATA: undefined, BRAIN: undefined, _T0: undefined, fit: Object.assign({}, M.fit, { sf: undefined }) })) };
  res.ideas = {};
  for (const [name, idea] of ideas(M, ex)) { const E = FIS.evaluate(M, idea), Ci = FIS.cash(M, E), br = FIS.bridge(M, FIS.blankIdea(M), idea);
    res.ideas[name] = { idea: plain(idea), evaluate: evalOut(E), cash: plain(Ci), bridge: plain(br),
      chartFor: Object.fromEntries(FIS.FOCUS.map(f => [f, FIS.chartFor(M, { focus: f }, T, E)])) };
    if (FIS.leversOn(B).taxes) { const X = FIS.tax(M, E, { base: T }), Ct = FIS.cash(M, E, { tax: X }); res.ideas[name].tax = plain(X); res.ideas[name].cashWithTax = plain(Ct); } }
  res.metrics = plain(metricsOut(M, T, C));
  res.viewMetrics = plain(FIS.viewMetrics(M, T, C)); res.viewPresets = plain(FIS.viewPresets(M, T, C));
  res.viewKeys = SELECTIONS.map(s => ({ sel: s, keys: FIS.viewKeys(M, s, T, C) }));
  res.panelList = plain(FIS.panelList(M)); res.panelPresets = plain(FIS.panelPresets(M)); res.panelDefault = FIS.panelDefault(M);
  res.panelKeys = PANEL_SELECTIONS.map(s => ({ sel: s, keys: FIS.panelKeys(M, s) }));
  res.costLines = plain(FIS.costLines(M)); res.costLines6 = plain(FIS.costLines(M, 6));
  res.check = FIS.check(B, D);
  res.taxOf = plain(FIS.taxOf(M, T));
  return res;
}

const EXAMPLES = ["barbershop", "home-services", "home-services-tax"];
for (const ex of EXAMPLES) {
  const { BRAIN, DATA } = load(ex), d = path.join(out, ex); fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, "brain.json"), JSON.stringify(brainJSON(BRAIN), null, 1) + "\n");
  fs.writeFileSync(path.join(d, "data.json"), JSON.stringify(plain(DATA)) + "\n");
  const res = { engine_sha256: sha, example: ex, runs: {} };
  res.runs.base = run(ex, BRAIN, DATA, {});
  /* the owner's corrections, the 13-week basis */
  const M0 = FIS.build(DATA, BRAIN, {}), f0 = M0.rates[0], x0 = M0.fixed[0];
  res.runs.corrected = run(ex, BRAIN, DATA, { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 });
  /* data the reports can lack: no customers, no A/R, no Profit and Loss */
  { const D = FIS.clone(DATA); delete D.customers; delete D.ar; delete D.ap; res.runs.no_customers_ar_ap = run(ex, BRAIN, D, {}); }
  { const D = FIS.clone(DATA); D.pnl = { months: [], lines: [] }; res.runs.no_books = run(ex, BRAIN, D, {}); }
  { const D = FIS.clone(DATA); D.sales.weeks = D.sales.weeks.slice(-30); D.sales.rows = D.sales.rows.filter(r => r[0] >= DATA.sales.weeks.length - 30).map(r => [r[0] - (DATA.sales.weeks.length - 30)].concat(r.slice(1))); res.runs.weeks_30 = run(ex, BRAIN, D, {}); }
  fs.writeFileSync(path.join(d, "expected.json"), JSON.stringify(res) + "\n");
  process.stderr.write(ex + ": " + Object.keys(res.runs).length + " runs\n");
}
/* the brain check on broken brains (engine.test.js "the brain check flags ...") */
{ const { BRAIN, DATA } = load("home-services"), B = FIS.clone(BRAIN);
  B.views = [{ id: "x", name: "Three and a typo", keys: ["revenue_month", "nope", "cash_on_hand"] }, { id: "custom", name: "Clash", keys: ["a", "b", "c", "d"] }, { name: "No id", keys: [] }];
  B.panels = ["weekly", "weekly", "typo"]; B.panelViews = [{ id: "a", name: "Short", keys: ["weekly", "cash"] }, { id: "default", name: "Clash", keys: ["weekly", "cash", "books", "net"] }, { name: "No id", keys: [] }, { id: "b", name: "Typo", keys: ["weekly", "cash", "books", "nope"] }, { id: "c", name: "Twice", keys: ["weekly", "weekly", "books", "net"] }];
  B.headline = B.headline.concat(["tax_cash_90d", "not_a_metric"]); B.upload = "yes"; B.assistant = { starters: ["What if “Nobody” leaves?"] };
  const D = FIS.clone(DATA); D.unitTerms = Object.assign({}, D.unitTerms, { Ghost: { type: "fixedIn", rent: 1 } });
  const B2 = FIS.clone(load("home-services-tax").BRAIN), D2 = FIS.clone(load("home-services-tax").DATA); D2.tax.profile.entity.v = "s_corp"; D2.tax.profile.state.v = "12-3456789";
  const B3 = FIS.clone(B2), D3 = FIS.clone(load("home-services-tax").DATA); delete D3.tax.profile.priorYearTax; D3.tax.profile.entity.v = "trust";
  fs.writeFileSync(path.join(out, "check_broken.json"), JSON.stringify({ engine_sha256: sha, cases: [
    { brain: brainJSON(B), data: plain(D), check: FIS.check(B, D) }, { brain: brainJSON(B2), data: plain(D2), check: FIS.check(B2, D2) }, { brain: brainJSON(B3), data: plain(D3), check: FIS.check(B3, D3) }] }) + "\n");
}
