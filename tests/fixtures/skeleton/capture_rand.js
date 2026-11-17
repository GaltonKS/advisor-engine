/* Known-seed draws from the skeleton's rng (mulberry32) and gauss (Box-Muller), for tests/test_rand.py.
   Usage: node capture_rand.js <skeleton dir> > rand.json */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]); globalThis.window = globalThis;
const FIS = require(path.join(dir, "app/engine.js"));
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/engine.js"))).digest("hex");
const out = { engine_sha256: sha, uniform: {}, gauss: {} };
for (const seed of [77, 91, 0, 1, -5, 4294967295, 123456789]) {
  const r = FIS.rng(seed); out.uniform[seed] = Array.from({ length: 20 }, () => r());
  const g = FIS.gauss(FIS.rng(seed)); out.gauss[seed] = Array.from({ length: 20 }, () => g());
}
/* a long run, to catch drift: the 2000-path cash simulation draws ~28,000 values from seed 77 */
{ const g = FIS.gauss(FIS.rng(77)); let s = 0, last; for (let i = 0; i < 28000; i++) { last = g(); s += last; } out.gauss_77_28000 = { sum: s, last }; }
process.stdout.write(JSON.stringify(out, null, 1) + "\n");
