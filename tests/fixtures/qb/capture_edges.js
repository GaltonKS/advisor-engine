/* Edge cases for the CSV readers that the generated exports do not reach, for tests/test_books.py:
   apply() with empty pieces in the base and slots whose file name is missing or null, the same
   exports with LF-only and CR-only line ends, and the two cases where the skeleton lets a customer
   name out (which the Python port deliberately refuses; recorded to show what the skeleton does).

   Usage:  node tests/fixtures/qb/capture_edges.js <skeleton dir>      (after capture.js)
   Writes tests/fixtures/qb/expected/edges.json. */
"use strict";
const path = require("path"), fs = require("fs"), crypto = require("crypto");
const dir = path.resolve(process.argv[2]), HERE = __dirname, EXP = path.join(HERE, "expected");
globalThis.window = globalThis;
const U = require(path.join(dir, "app/upload.js"));
const sha = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, "app/upload.js"))).digest("hex");
const manifest = JSON.parse(fs.readFileSync(path.join(HERE, "manifest.json"), "utf8"));
const NOW = manifest.now;
const read = (file, words) => U.read(fs.readFileSync(path.join(HERE, file), "utf8"), file, { unitWords: words || [], system: "quickbooks", now: NOW });
const slot = r => ({ name: r.name, report: r.report, detail: r.detail, rows: r.rows, data: r.data });
const safe = f => { try { return f(); } catch (e) { return { error: e.constructor.name + ": " + e.message }; } };
const out = { upload_sha256: sha, lineEnds: {}, apply: {}, privacy: {} };

/* the same exports with other line ends: what Python reads is built the same way in the test */
for (const file of ["home-services-qbo-pnl.csv", "home-services-qbd-sales.csv", "barbershop-qbo-balance.csv", "home-services-qbo-ar.csv"]) {
  const words = (manifest.reads.find(r => r.file === file) || {}).unit_words || [];
  const text = fs.readFileSync(path.join(HERE, file), "utf8").replace(/\r\n/g, "\n");
  out.lineEnds[file] = { words, lf: U.read(text, file, { unitWords: words }), cr: U.read(text.replace(/\n/g, "\r"), file, { unitWords: words }) };
}

/* apply() on bases with empty pieces, and slots with odd names */
const pnl = slot(read("home-services-qbo-pnl.csv")), bal = slot(read("home-services-qbo-balance.csv"));
const cases = {
  base_customers_empty: [{ customers: {} }, { pnl }],
  base_balance_empty: [{ balance: {} }, { pnl }],
  base_ar_empty: [{ ar: {}, ap: {} }, { pnl }],
  base_sales_without_rows: [{ sales: { units: [] }, pnl: { months: [] } }, { pnl }],
  balance_name_missing: [{}, { balance: Object.assign({}, bal, { name: undefined }) }],
  balance_name_null: [{}, { balance: Object.assign({}, bal, { name: null }) }],
  balance_name_number: [{}, { balance: Object.assign({}, bal, { name: 5 }) }],
  null_base: [null, { pnl }],
};
for (const [k, [base, slots]] of Object.entries(cases)) {
  /* JSON drops undefined, so "missing" survives the trip to Python as a missing key */
  out.apply[k] = { base, slots: JSON.parse(JSON.stringify(slots)), result: safe(() => U.apply(base, slots, { system: "quickbooks", now: NOW })) };
}

/* where the skeleton lets a customer name out */
{ const text = ["Jane Q. Private,120.00", "John Roe,80.00", "Ann Example,60.00", "Bo Sample,40.00", "Cy Placeholder,20.00", "Di Madeup,10.00"].join("\n") + "\n";
  out.privacy.untitled_customer_list = { text, result: U.read(text, "c.csv", {}) }; }
{ /* 14 weeks of a Sales by Customer Detail: customers as group headings, some lines with no product */
  const rows = ["Sample Co", "Sales by Customer Detail", "November 3, 2025 - February 8, 2026", "",
    ",Date,Transaction Type,Num,Product/Service,Qty,Sales Price,Amount"];
  let num = 1000;
  for (const who of ["Jane Q. Private", "John Roe"]) { rows.push(who + ",,,,,,,");
    for (let w = 0; w < 14; w++) { const d = FIS_DATE(2025, 11, 3 + 7 * w); rows.push("," + d + ",Sales Receipt," + (num++) + "," + (w % 3 ? "Haircut" : "") + ",1,45.00,45.00"); } }
  rows.push("TOTAL,,,,,,,1260.00");
  const text = rows.join("\r\n") + "\r\n";
  out.privacy.sales_by_customer_detail = { text, result: U.read(text, "d.csv", {}) }; }
function FIS_DATE(y, m, d) { const t = new Date(Date.UTC(y, m - 1, d)); return (t.getUTCMonth() + 1) + "/" + t.getUTCDate() + "/" + t.getUTCFullYear(); }

fs.writeFileSync(path.join(EXP, "edges.json"), JSON.stringify(out, null, 1) + "\n");
process.stderr.write("edges: " + Object.keys(out.apply).length + " apply cases\n");
