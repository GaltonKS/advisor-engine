/* The skeleton's cash range (FIS.cash) on its worked examples, for tests/test_cash.py.

   Usage:  node capture_cash.js <skeleton dir> <out dir>      (after capture_build.js)
   Writes <out>/<example>/cash.json, reading brain.json / data.json from <out>/<example>/.

   Per example, as shipped and corrected on the 13-week basis: ideas that move cash (a slow
   stretch, one-time amounts in and out at loose weeks, a purchase, a new bill, all at once,
   every unit removed), a path-count override, and synthetic tax payments (the cash range
   takes the tax picture's payments; step 7 builds the real ones). On the barbershop, brain
   and data variants each stored with their exact brain and data. A case where the skeleton
   throws records the error. Dates as YYYY-MM-DD, NaN as null. */
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
  /* defineProperty, so a key named __proto__ is kept as data rather than setting the prototype */
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v)) { const x = plain(v[k]); if (x !== undefined) Object.defineProperty(o, k, { value: x, enumerable: true, writable: true, configurable: true }); } return o; }
  return v;
};
const clone = x => JSON.parse(JSON.stringify(x));
const safe = f => { try { return plain(f()); } catch (e) { return { error: e.constructor.name + ": " + e.message }; } };

function ideas(M) {
  const I = () => FIS.blankIdea(M), list = [], add = (n, f) => { const x = I(); f(x); list.push([n, x]); };
  list.push(["today", I()]);
  add("sales_down_15", x => { x.shock = -15; });
  add("one_time_out_week_4", x => { x.once.push({ l: "Van", amt: -38000, wk: 4 }); });
  add("one_time_loose_weeks", x => { x.once.push({ l: "Late", amt: 5000, wk: 13.6 }, { l: "Text", amt: "2500", wk: "3" }, { l: "Zero", amt: 1000, wk: 0 },
    { l: "Far", amt: -700, wk: 99 }, { l: "No week", amt: 400 }, { l: "Not a number", amt: "lots", wk: 2 }); });
  add("buy_asset", x => { x.assets.push({ l: "Van", cost: 38000, wk: 4 }, { l: "Free", cost: 0, wk: 2 }, { l: "Text", cost: "1200", wk: 6.5 }); });
  add("new_monthly_bill", x => { x.fixed.push({ l: "New lease", mo: 2000 }); });
  add("remove_every_unit", x => { M.units.forEach(u => { x.terms[u.name] = { on: false }; }); });
  add("everything", x => { x.shock = -10; x.terms[M.units[0].name] = { type: "fixedIn", rent: 280 }; x.fixed.push({ l: "Rent increase", mo: 500 });
    x.once.push({ l: "Chairs", amt: -6000, wk: 2 }); x.added.push({ name: "Weekend", mo: 2500, type: "share", rate: 45 }); });
  return list;
}

/* payments around the horizon: before it starts, inside it, on a week's first and last day, after it */
function payments(M) {
  const last = M.weeks[M.weeks.length - 1].dt, at = n => FIS.addDays(last, n);
  return [{ date: at(3), amount: 1000 }, { date: at(7), amount: 4400 }, { date: at(13), amount: 250 }, { date: at(14), amount: 300 },
    { date: at(40), amount: 5000 }, { date: at(7 * 14), amount: 9000 }, { date: at(7 * 14 - 1), amount: 77 }];
}

function cases(B, D) {
  const out = {};
  for (const [name, opts] of [["base", {}], ["corrected", null]]) {
    let o = opts; if (!o) { const M0 = FIS.build(D, B, {}), f0 = M0.rates[0], x0 = M0.fixed[0];
      o = { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 }; }
    const M = FIS.build(D, B, o), r = { opts: o, ideas: {} };
    for (const [n, idea] of ideas(M)) r.ideas[n] = { idea: plain(idea), cash: safe(() => FIS.cash(M, FIS.evaluate(M, idea))) };
    const T = FIS.evaluate(M, null);
    r.paths500 = safe(() => FIS.cash(M, T, { paths: 500 }));
    const pay = payments(M);
    r.tax = { payments: plain(pay), cash: safe(() => FIS.cash(M, T, { tax: { ok: true, payments: pay } })),
      notOk: safe(() => FIS.cash(M, T, { tax: { ok: false, payments: pay } })) };
    out[name] = r;
  }
  return out;
}

function variants(BRAIN, DATA) {
  const out = {}, run = (name, B, D) => { const M = FIS.build(D, B, {}), T = FIS.evaluate(M, null);
    const I = FIS.blankIdea(M); I.shock = -20; I.once.push({ l: "Out", amt: -9000, wk: 5 });
    out[name] = { brain: plain(B), data: plain(D), today: safe(() => FIS.cash(M, T)), idea: plain(I), ideaCash: safe(() => FIS.cash(M, FIS.evaluate(M, I))) }; };
  const cut = (D, n) => { const k = D.sales.weeks.length; D.sales.weeks = D.sales.weeks.slice(-n); D.sales.rows = D.sales.rows.filter(r => r[0] >= k - n).map(r => [r[0] - (k - n)].concat(r.slice(1))); return D; };
  run("weeks_11", BRAIN, cut(clone(DATA), 11));
  run("weeks_12", BRAIN, cut(clone(DATA), 12));
  run("weeks_30", BRAIN, cut(clone(DATA), 30));
  { const D = clone(DATA), feb = new Set(D.sales.weeks.map((w, i) => [FIS.addDays(FIS.parseDay(w), 3).getMonth(), i]).filter(x => x[0] === 1).map(x => x[1]));
    D.sales.rows = D.sales.rows.filter(r => !feb.has(r[0])); run("closed_february", BRAIN, D); }
  { const D = clone(DATA), first = D.sales.weeks.slice(0, 18);
    D.sales.weeks = first.map(s => day(FIS.addDays(FIS.parseDay(s), -364))).concat(D.sales.weeks);
    D.sales.rows = D.sales.rows.filter(r => r[0] < 18).map(r => [r[0], r[1], r[2], r[3], Math.round(r[4] * 0.9)]).concat(D.sales.rows.map(r => [r[0] + 18].concat(r.slice(1))));
    run("weeks_70", BRAIN, D); }
  { const B = clone(BRAIN); B.assumptions = Object.assign({}, B.assumptions, { horizonWeeks: 26 }); run("horizon_26", B, DATA); }
  { const B = clone(BRAIN); B.assumptions = Object.assign({}, B.assumptions, { paths: 300 }); run("brain_paths_300", B, DATA); }
  { const D = clone(DATA); D.facts.forEach(f => { if (f.k === "floor") f.v = 40000; }); run("floor_above_cash", BRAIN, D); }
  return out;
}

for (const ex of ["barbershop", "home-services", "home-services-tax"]) {
  const d = path.join(out, ex), BRAIN = JSON.parse(fs.readFileSync(path.join(d, "brain.json"))), DATA = JSON.parse(fs.readFileSync(path.join(d, "data.json")));
  const res = { engine_sha256: sha, example: ex, runs: cases(BRAIN, DATA) };
  if (ex === "barbershop") res.variants = variants(BRAIN, DATA);
  fs.writeFileSync(path.join(d, "cash.json"), JSON.stringify(res) + "\n");
  process.stderr.write(ex + ": done\n");
}
