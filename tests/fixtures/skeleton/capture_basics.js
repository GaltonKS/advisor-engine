/* The skeleton's small building blocks, for tests/test_rand.py and tests/test_jsmath.py:
   known-seed draws from rng (mulberry32) and gauss (Box-Muller), and what n2, Math.round,
   mean, sd, quant and clamp give on awkward inputs.

   Usage:  node capture_basics.js <skeleton dir> > basics.json
   The skeleton is not vendored; the sha256 of its app/engine.js is recorded in the output. */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]); globalThis.window = globalThis;
const FIS = require(path.join(dir, "app/engine.js"));
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/engine.js"))).digest("hex");
/* NaN, Infinity and -0 cannot go through JSON: write them as strings */
const num = v => (typeof v === "number" && (!isFinite(v) || Object.is(v, -0)) ? (Object.is(v, -0) ? "-0" : String(v)) : v);
const out = { engine_sha256: sha, uniform: {}, gauss: {} };

for (const seed of [77, 91, 0, 1, -5, 4294967295, 123456789]) {
  const r = FIS.rng(seed); out.uniform[seed] = Array.from({ length: 20 }, () => r());
  const g = FIS.gauss(FIS.rng(seed)); out.gauss[seed] = Array.from({ length: 20 }, () => g());
}
/* a long run, to catch drift: the 2000-path cash simulation draws ~28,000 values from seed 77 */
{ const g = FIS.gauss(FIS.rng(77)); let s = 0, last; for (let i = 0; i < 28000; i++) { last = g(); s += last; } out.gauss_77_28000 = { sum: s, last }; }

/* n2: loose input -> a number or NaN. Inputs are tagged so Python can rebuild them exactly. */
const N2_INPUTS = [["null", null], ["undefined", undefined], ["str", ""], ["str", "  "], ["str", "2600"], ["str", " 4.5 "], ["str", "0x10"],
  ["str", "-0x10"], ["str", "1_000"], ["str", "1e5"], ["str", ".5"], ["str", "5."], ["str", "+5"], ["str", "Infinity"], ["str", "inf"],
  ["str", "nan"], ["str", "abc"], ["str", "12abc"], ["str", "1,000"], ["str", "\u00a07\u00a0"], ["bool", true], ["bool", false],
  ["num", 7], ["num", -0.25], ["num", 1e308], ["list", []], ["list", ["3"]], ["list", [1, 2]],
  /* the corners a first review found: other radixes, non-ASCII digits, what counts as whitespace, arrays */
  ["str", "0b101"], ["str", "0o17"], ["str", "0X1F"], ["str", "\u0663"], ["str", "\uff11\uff12"], ["str", "\u001c5"], ["str", "5\u0085"],
  ["str", "\ufeff \ufeff5"], ["str", "\u20285\u3000"], ["str", "-Infinity"], ["str", "1e999"], ["str", "1e"], ["str", "--5"],
  ["list", [null]], ["list", [""]], ["list", [true]], ["list", [false]], ["list", [[" 8 "]]], ["list", [1.5]], ["obj", {}]];
out.n2 = N2_INPUTS.map(([tag, v]) => ({ tag, v: v === undefined ? null : v, out: num(FIS.n2(v)) }));

out.round = [0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 0.49999999999999994, 1e15 + 0.5, -0.0001, -0.2, -0.6, 2.675 * 100, 4503599627370495.5]
  .map(v => ({ v, out: num(Math.round(v)) }));
const QP = [0, 0.1, 0.25, 0.5, 0.9, 1, -0.1, 1.5, NaN, 2];
out.quantP = QP.map(num);
const ARR = [[], [5], [4, 1, 3, 2], [0.1, 0.2, 0.3], [1e16, 1, -1e16, 0.1, 0.1, 0.1], [3, 3, 3, 3], [-2.5, 7, 1e-9, 12.75, 0]];
out.stats = ARR.map(a => ({ a, sum: num(FIS.sum(a)), mean: num(FIS.mean(a)), sd: num(FIS.sd(a)),
  q: QP.map(p => num(FIS.quant(a, p))) }));
out.clamp = [[5, 0, 10], [-1, 0, 10], [11, 0, 10], [NaN, 0, 10], [5, 0, NaN], [5, NaN, 10]]
  .map(([v, a, b]) => ({ v: num(v), a: num(a), b: num(b), out: num(FIS.clamp(v, a, b)) }));

/* what arithmetic does to an operand (ToNumber, via unary plus), division, Math.max/min, String() */
out.toNumber = N2_INPUTS.filter(([t]) => t !== "undefined").map(([tag, v]) => ({ tag, v, out: num(+v) }));
out.div = [[1, 0], [-1, 0], [0, 0], [6, 3], [1, -0], [0, 5]].map(([a, b]) => ({ a, b: num(b), out: num(a / b) }));
out.max = [[0, 5], [0, -5], [0, NaN], [NaN, 3], [-0, 0]].map(xs => ({ xs: xs.map(num), max: num(Math.max(...xs)), min: num(Math.min(...xs)) }));
out.string = [[5.0, "num"], [5.5, "num"], [-0.25, "num"], [1e21, "num"], [1e20, "num"], [123456789012345680000, "num"], [0.00001, "num"],
  [0.000001, "num"], [1.5e-7, "num"], [-2e-7, "num"], [0.1 + 0.2, "num"], [-0, "num"], [null, "null"], [true, "bool"], ["x", "str"], [[1, [2, 3]], "list"], [[null], "list"]]
  .map(([v, tag]) => ({ tag, v, out: String(v) }));

/* dates: local calendar days (the skeleton builds every date with new Date(y, m, d)) */
const day = d => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
/* the Python port refuses everything after "2026-1-5" on purpose (see jsmath.parse_day); recorded to show what JS does */
out.parseDay = ["2026-11-03", "2026-11", "2024-02-29", "2026-1-5", "2026-02-30", "2026-11-", "0099-01-05", "2026-1_1-03", "2026-13-01"]
  .map(s => { const d = FIS.parseDay(s); return { s, out: isNaN(d) ? "Invalid Date" : day(d) }; });
out.addDays = [["2026-03-07", 1], ["2026-03-07", 7], ["2026-10-31", 1], ["2024-02-28", 1], ["2026-12-29", 7], ["2026-01-05", -7]]
  .map(([s, n]) => ({ s, n, out: day(FIS.addDays(FIS.parseDay(s), n)) }));
out.fmtMonth = ["2026-01", "2026-11", "2025-12", "2026-00", "2026-13"].map(k => ({ k, out: FIS.fmtMonth(k) }));

process.stdout.write(JSON.stringify(out, null, 1) + "\n");
