/* The skeleton's evaluate and bridge on its three worked examples, for tests/test_evaluate.py.

   Usage:  node capture_ideas.js <skeleton dir> <out dir>      (after capture_build.js)
   Writes <out>/<example>/ideas.json. Reads brain.json / data.json from <out>/<example>/, the same
   files the Python tests read, so both sides build from identical input.

   Two runs per example: as shipped, and with the owner's corrections on the 13-week basis. Each
   run evaluates about twenty ideas (one per kind of change, then all at once) and bridges each
   from today's setup. The barbershop also gets loose and malformed ideas (text where a number
   goes, out-of-range percents, unknown names, wrong shapes) and a bridge from a non-blank
   starting idea. Dates as YYYY-MM-DD, NaN as null, the evaluation's `by` Map dropped. */
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
  if (v && typeof v === "object") { const o = {}; for (const k of Object.keys(v)) { const x = plain(v[k]); if (x !== undefined) o[k] = x; } return o; }
  return v;
};

function ideas(M) {
  const I = () => FIS.blankIdea(M), u = M.units, o = M.off, list = [];
  const add = (name, f) => { const x = I(); f(x); list.push([name, x]); };
  list.push(["today", I()]);
  add("price_up_10", x => { x.price[o[0].name] = o[0].price * 1.1; x.elast = -0.5; });
  add("price_down_20_brain_elasticity", x => { x.price[o[1].name] = o[1].price * 0.8; });
  add("price_to_zero", x => { x.price[o[2].name] = 0; });
  add("remove_unit", x => { x.terms[u[2].name] = { on: false }; });
  add("remove_unit_keep_50", x => { x.terms[u[2].name] = { on: false }; x.keep = 50; });
  add("remove_every_unit", x => { u.forEach(v => { x.terms[v.name] = { on: false }; }); x.keep = 80; });
  add("unit_to_fixed_rent", x => { x.terms[u[0].name] = { type: "fixedIn", rent: 300 }; });
  add("unit_to_wage", x => { x.terms[u[1].name] = { type: "wage", wage: 4200 }; });
  add("unit_to_share", x => { x.terms[u[1].name] = { type: "share", rate: 45 }; });
  add("unit_to_none", x => { x.terms[u[1].name] = { type: "none" }; });
  add("add_unit_wage", x => { x.added.push({ name: "New one", mo: 10000, type: "wage", wage: 5800 }); });
  add("add_unit_share", x => { x.added.push({ name: "Weekend", mo: 2500, type: "share", rate: 45 }); });
  add("add_two_units_one_unnamed", x => { x.added.push({ mo: 3000 }, { name: "Second", mo: 1500, type: "fixedIn", rent: 200 }); });
  add("sales_down_15", x => { x.shock = -15; });
  add("offering_up_25", x => { x.offer[o[o.length > 2 ? 2 : 0].name] = 25; });
  add("cost_rate_up_1", x => { x.rate[M.rates[0].k] = M.F[M.rates[0].k] + 1; });
  add("new_monthly_bill_and_saving", x => { x.fixed.push({ l: "New lease", mo: 2000 }, { l: "Cheaper software", mo: -300 }); });
  add("everything", x => { x.shock = -10; x.price[o[0].name] = o[0].price + 5; x.terms[u[0].name] = { type: "fixedIn", rent: 280 };
    x.terms[u[Math.min(6, u.length - 1)].name] = { on: false }; x.keep = 30; x.added.push({ name: "Weekend", mo: 2500, type: "share", rate: 45 });
    x.rate[M.rates[M.rates.length - 1].k] = 4; x.fixed.push({ l: "Rent increase", mo: 500 }); x.once.push({ l: "Chairs", amt: -6000, wk: 2 }); });
  return list;
}

/* loose and malformed ideas: what an assistant or a hand-edited saved idea could send */
function looseIdeas(M) {
  const u = M.units, o = M.off, list = [];
  const add = (name, idea) => list.push([name, idea]);
  add("null_idea", null);
  add("price_as_text", { price: { [o[0].name]: "50" } });
  add("price_negative_and_unknown", { price: { [o[0].name]: -5, "No such service": 99 } });
  add("price_not_an_object", { price: "cheaper" });
  add("elast_null_and_text", { price: { [o[0].name]: o[0].price * 1.2 }, elast: null });
  add("elast_text", { price: { [o[0].name]: o[0].price * 1.2 }, elast: "steep" });
  add("shock_text_and_below_minus_100", { shock: "abc", offer: { [o[1].name]: -250 } });
  add("shock_minus_150", { shock: -150 });
  add("keep_out_of_range", { terms: { [u[1].name]: { on: false } }, keep: 150 });
  add("keep_negative", { terms: { [u[1].name]: { on: false } }, keep: -20 });
  add("keep_text", { terms: { [u[1].name]: { on: false } }, keep: "30" });
  add("pay_kind_not_offered", { terms: { [u[0].name]: { type: "wage", wage: 4000 } } });
  add("pay_figures_as_text_and_out_of_range", { terms: { [u[0].name]: { type: "share", rate: "45" }, [u[1].name]: { type: "share", rate: 150 },
    [u[2].name]: { type: "fixedIn", rent: "abc" } } });
  add("terms_for_unknown_unit", { terms: { "Nobody": { on: false } } });
  add("terms_not_an_object", { terms: ["Marcus T."] });
  add("added_loose", { added: [{ mo: "2600", type: "barter" }, { name: "Neg", mo: -5 }, { name: "Text", mo: "lots" }] });
  add("added_not_a_list", { added: "two more" });
  add("offer_as_text", { offer: { [o[0].name]: "25" } });
  add("rate_override_text", { rate: { [M.rates[0].k]: "4" } });
  add("fixed_loose", { fixed: [{ mo: "500" }, { l: "Zero", mo: 0 }, { l: 5, mo: 100 }, { l: "Text", mo: "abc" }] });
  add("fixed_with_null", { fixed: [null, { l: "Lease", mo: 100 }] });
  add("added_item_not_an_object", { added: ["a weekend barber"] });
  add("term_entry_not_an_object", { terms: { [u[0].name]: "off" } });
  add("fixed_not_a_list", { fixed: "rent up" });
  return list;
}

for (const ex of ["barbershop", "home-services", "home-services-tax"]) {
  const d = path.join(out, ex), BRAIN = JSON.parse(fs.readFileSync(path.join(d, "brain.json"))), DATA = JSON.parse(fs.readFileSync(path.join(d, "data.json")));
  const runs = {};
  const run = (name, opts) => { const M = FIS.build(DATA, BRAIN, opts), r = { opts, ideas: {} };
    const all = ideas(M).concat(ex === "barbershop" && name === "base" ? looseIdeas(M) : []);
    for (const [n, idea] of all) {
      /* some malformed ideas make the skeleton throw; record what it said */
      let E, br;
      try { E = plain(Object.assign({}, FIS.evaluate(M, idea), { by: undefined })); } catch (e) { E = { error: e.constructor.name + ": " + e.message }; }
      try { br = plain(FIS.bridge(M, FIS.blankIdea(M), idea)); } catch (e) { br = { error: e.constructor.name + ": " + e.message }; }
      r.ideas[n] = { idea: idea === null ? null : plain(idea), evaluate: E, bridge: br }; }
    if (ex === "barbershop" && name === "base") {
      /* a bridge whose starting point is not today's setup: units switched on and off on both sides */
      const today = FIS.blankIdea(M); today.terms[M.units[1].name] = { on: false }; today.terms[M.units[2].name] = { type: "fixedIn", rent: 250 };
      const idea = FIS.blankIdea(M); idea.terms[M.units[3].name] = { on: false }; idea.terms[M.units[2].name] = { type: "share", rate: 40 }; idea.keep = 25; idea.shock = 5;
      r.bridgeFromIdea = { today: plain(today), idea: plain(idea), bridge: plain(FIS.bridge(M, today, idea)) }; }
    runs[name] = r; };
  run("base", {});
  { const M0 = FIS.build(DATA, BRAIN, {}), f0 = M0.rates[0], x0 = M0.fixed[0];
    run("corrected", { facts: { [x0.k]: M0.F[x0.k] + 1000, [f0.k]: M0.F[f0.k] + 1.4 }, confirmed: { [x0.k]: true }, basis: 3 }); }
  fs.writeFileSync(path.join(d, "ideas.json"), JSON.stringify({ engine_sha256: sha, example: ex, runs }) + "\n");
  process.stderr.write(ex + ": " + Object.values(runs).reduce((n, r) => n + Object.keys(r.ideas).length, 0) + " ideas\n");
}
