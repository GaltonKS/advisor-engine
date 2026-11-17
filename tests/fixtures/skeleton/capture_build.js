/* The skeleton's model base (FIS.build) on its three worked examples, for tests/test_model.py.
   Also writes each example's brain and data as JSON, which every later step reads.

   Usage:  node capture_build.js <skeleton dir> <out dir>
   Writes <out>/<example>/{brain,data,build}.json. The skeleton is not vendored; the sha256 of
   its app/engine.js is recorded in every build.json.

   Each example is built five ways: as shipped; with the owner's corrections and the 13-week
   basis; without customers, A/R and A/P; without a Profit and Loss; with only the last 30
   weeks of sales. Dates are written as YYYY-MM-DD (the skeleton builds local dates), NaN as
   null (as JSON.stringify does). */
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
  if (v instanceof Map || typeof v === "function") return undefined;
  if (typeof v === "number") return isFinite(v) ? v : null;
  /* defineProperty, so a key named __proto__ is kept as data rather than setting the prototype */
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v)) { const x = plain(v[k]); if (x !== undefined) Object.defineProperty(o, k, { value: x, enumerable: true, writable: true, configurable: true }); } return o; }
  return v;
};

/* A brain may hold closures for company-specific metrics. A Python brain is config and holds no
   code: each closure becomes a library measure with parameters. This table is the port of every
   closure in the shipped examples; the measures step checks each renders what the closure did. */
const METRIC_PORTS = { top4_share: { metric: "top_n_share", n: 4 } };
const brainJSON = B => { const b = plain(B); if (B.metrics) { b.metrics = {};
  for (const k of Object.keys(B.metrics)) { if (!METRIC_PORTS[k]) throw new Error("No Python port for brain metric " + k);
    b.metrics[k] = Object.assign(typeof B.metrics[k].l === "string" ? { l: B.metrics[k].l } : {}, METRIC_PORTS[k]); } }
  return b; };

const load = ex => { const b = path.join(dir, "examples", ex); for (const f of ["brain.js", "data.js"]) delete require.cache[require.resolve(path.join(b, f))];
  return { BRAIN: require(path.join(b, "brain.js")), DATA: require(path.join(b, "data.js")) }; };
/* the model base without the snapshot and brain it was built from, and without fit.sf (a function) */
const modelOut = M => plain(Object.assign({}, M, { DATA: undefined, BRAIN: undefined, _T0: undefined, fit: Object.assign({}, M.fit, { sf: undefined }) }));

/* Edge runs, on the barbershop only. Each stores the exact snapshot and brain it was built from
   (`data`, `brain`), so the Python test builds the same thing without re-deriving it. */
function edgeRuns(BRAIN, DATA) {
  const out = {}, run = (name, D, B) => { out[name] = { opts: {}, data: plain(D), brain: brainJSON(B), model: modelOut(FIS.build(D, B, {})) }; };
  /* a shop closed for February: no rows in the weeks whose Thursday falls in February */
  { const D = FIS.clone(DATA), feb = new Set(D.sales.weeks.map((w, i) => [FIS.addDays(FIS.parseDay(w), 3).getMonth(), i]).filter(x => x[0] === 1).map(x => x[1]));
    D.sales.rows = D.sales.rows.filter(r => !feb.has(r[0])); run("closed_february", D, BRAIN); }
  /* 70 weeks: the first 18 weeks repeated a year earlier, so the year-on-year trend and a window that starts after week 0 run */
  { const D = FIS.clone(DATA), first = D.sales.weeks.slice(0, 18), shift = s => { const d = FIS.addDays(FIS.parseDay(s), -364); return day(d); };
    D.sales.weeks = first.map(shift).concat(D.sales.weeks);
    D.sales.rows = D.sales.rows.filter(r => r[0] < 18).map(r => [r[0], r[1], r[2], r[3], Math.round(r[4] * 0.9)]).concat(D.sales.rows.map(r => [r[0] + 18].concat(r.slice(1))));
    run("weeks_70", D, BRAIN); }
  /* loose inputs a hand-made or older snapshot can hold */
  { const D = FIS.clone(DATA); D.sales.offerings[0].costPct = "40"; D.sales.offerings[1].costPct = "abc"; D.sales.offerings[2].name = "";
    D.sales.rows[0][4] = null; D.sales.rows[1][3] = null; D.sales.byMonth[0].amount = null; D.balance = 0; D.ar = null;
    D.pnl.lines.unshift({ account: "Income" }, { account: "Memo", group: "memo", values: ["n/a"] });
    run("loose_inputs", D, BRAIN); }
  /* brain.terms.types written in other shapes */
  const terms = (types, name) => { const B = FIS.clone(BRAIN); B.terms = Object.assign({}, B.terms, { types }); run(name, DATA, B); };
  terms("", "terms_empty_string"); terms(false, "terms_false"); terms(["share", "wage"], "terms_list"); terms({ share: true, wage: true }, "terms_true");
  terms({ share: "x" }, "terms_string_value");
  { const B = FIS.clone(BRAIN); delete B.terms; run("terms_missing", DATA, B); }
  return out;
}

for (const ex of ["barbershop", "home-services", "home-services-tax"]) {
  const { BRAIN, DATA } = load(ex), d = path.join(out, ex); fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, "brain.json"), JSON.stringify(brainJSON(BRAIN), null, 1) + "\n");
  fs.writeFileSync(path.join(d, "data.json"), JSON.stringify(plain(DATA)) + "\n");
  const runs = {};
  runs.base = { opts: {}, model: modelOut(FIS.build(DATA, BRAIN, {})) };
  { const M0 = FIS.build(DATA, BRAIN, {}), f0 = M0.rates[0], x0 = M0.fixed[0];
    const opts = { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 };
    runs.corrected = { opts, model: modelOut(FIS.build(DATA, BRAIN, opts)) }; }
  { const D = FIS.clone(DATA); delete D.customers; delete D.ar; delete D.ap; runs.no_customers_ar_ap = { opts: {}, model: modelOut(FIS.build(D, BRAIN, {})) }; }
  { const D = FIS.clone(DATA); D.pnl = { months: [], lines: [] }; runs.no_books = { opts: {}, model: modelOut(FIS.build(D, BRAIN, {})) }; }
  { const D = FIS.clone(DATA), n = DATA.sales.weeks.length; D.sales.weeks = D.sales.weeks.slice(-30);
    D.sales.rows = D.sales.rows.filter(r => r[0] >= n - 30).map(r => [r[0] - (n - 30)].concat(r.slice(1)));
    runs.weeks_30 = { opts: {}, model: modelOut(FIS.build(D, BRAIN, {})) }; }
  if (ex === "barbershop") Object.assign(runs, edgeRuns(BRAIN, DATA));
  fs.writeFileSync(path.join(d, "build.json"), JSON.stringify({ engine_sha256: sha, example: ex, runs }) + "\n");
  process.stderr.write(ex + ": " + Object.keys(runs).length + " runs\n");
}
