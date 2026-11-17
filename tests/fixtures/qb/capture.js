/* Captures the JS reader's output for the QuickBooks CSV fixtures, so tests/test_books.py can check
   the Python port (advisor_engine.books) against it exactly. See README.md in this folder.

   Usage: node tests/fixtures/qb/capture.js <path to the FIS pilot skeleton>

   Writes, next to this script:
     <id>.csv                    the generated QuickBooks-style export (CRLF kept as generated)
     expected/<id>.json          U.read(text, name, ctx) for each CSV
     expected/apply-<id>.json    U.apply(base, slots, ctx) for each combination in manifest.json
     expected/helpers.json       csv / num / monthKey / dayKey / periodOf / shares on fixed inputs
     manifest.json               which file, name and unit words each case was read with
   and tests/fixtures/skeleton/<example>/data.json (the example snapshots, as JSON). */
"use strict";
const fs = require("fs"), path = require("path");
const SK = path.resolve(process.argv[2] || "");
if (!process.argv[2] || !fs.existsSync(path.join(SK, "app", "upload.js"))) { console.error("usage: node capture.js <skeleton dir>"); process.exit(2); }
const HERE = __dirname, EXP = path.join(HERE, "expected"), SKEL = path.resolve(HERE, "..", "skeleton");
globalThis.window = globalThis;
const FIS = require(path.join(SK, "app", "engine.js")), U = require(path.join(SK, "app", "upload.js")), QB = require(path.join(SK, "test", "qb-fixtures.js"));
const EXAMPLES = ["home-services", "barbershop", "home-services-tax"];
const NOW = "2026-11-13T18:00:00.000Z";
const L = {};
EXAMPLES.forEach(ex => { const b = path.join(SK, "examples", ex); globalThis.FIS = FIS; L[ex] = { BRAIN: require(b + "/brain.js"), DATA: require(b + "/data.js") }; });
const clone = o => JSON.parse(JSON.stringify(o));
const unitWords = ex => { const v = FIS.vocab(L[ex].BRAIN); return [v.unit, v.units]; };

fs.mkdirSync(EXP, { recursive: true });
const cases = [], applies = [];
const write = (id, text, name, ex) => {
  fs.writeFileSync(path.join(HERE, id + ".csv"), text);
  const ctx = { unitWords: unitWords(ex), system: "quickbooks", now: NOW };
  fs.writeFileSync(path.join(EXP, id + ".json"), JSON.stringify(U.read(text, name, ctx)) + "\n");
  cases.push({ id, file: id + ".csv", name, example: ex, unit_words: ctx.unitWords });
};

/* the example snapshots, as JSON */
EXAMPLES.forEach(ex => { fs.mkdirSync(path.join(SKEL, ex), { recursive: true }); fs.writeFileSync(path.join(SKEL, ex, "data.json"), JSON.stringify(L[ex].DATA) + "\n"); });

/* the five reports, both layouts, every example (test/upload.test.js reads them the same way) */
EXAMPLES.forEach(ex => { const D = L[ex].DATA, co = L[ex].BRAIN.company.name;
  ["qbo", "qbd"].forEach(st => {
    write(`${ex}-${st}-pnl`, QB.pnl(D, st, co), "pnl.csv", ex);
    write(`${ex}-${st}-balance`, QB.balance(D, st, co), "bs.csv", ex);
    ["ar", "ap"].forEach(k => { if (D[k]) write(`${ex}-${st}-${k}`, QB.aging(D, k, st, co), k + ".csv", ex); });
    write(`${ex}-${st}-sales`, QB.sales(D, st, co), "sales.csv", ex); }); });

/* variants the tests build from a changed snapshot */
const HS = L["home-services"], hs = "home-services";
{ const D = clone(HS.DATA), n = D.pnl.months.length; D.pnl.lines.push({ account: "Interest Income", group: "other", values: new Array(n).fill(120) }, { account: "Loan Interest", group: "other", values: new Array(n).fill(-340) });
  ["qbo", "qbd"].forEach(st => write(`variant-${st}-pnl-other`, QB.pnl(D, st, "X"), "p.csv", hs)); }
{ const D = clone(HS.DATA); D.sales.weeks = D.sales.weeks.slice(0, 8); D.sales.rows = D.sales.rows.filter(r => r[0] < 8); write("variant-qbo-sales-short", QB.sales(D, "qbo", "X"), "s.csv", hs); }
write("variant-qbo-sales-nounit", QB.sales(HS.DATA, "qbo", "X", { noUnit: true }), "s.csv", hs);
write("variant-qbd-sales-nounit", QB.sales(HS.DATA, "qbd", "X", { noUnit: true }), "s.csv", hs);
write("variant-qbo-sales-customers", QB.sales(HS.DATA, "qbo", "X", { customers: (row, n) => 1 + (n * 7919) % 300 }), "s.csv", hs);
write("variant-qbd-sales-customers", QB.sales(HS.DATA, "qbd", "X", { customers: (row, n) => 1 + (n * 7919) % 300 }), "s.csv", hs);
{ const D = clone(HS.DATA); D.sales.units[0] = D.sales.units[0].toUpperCase(); D.sales.units[1] = "New Hire"; write("variant-qbo-sales-renamed", QB.sales(D, "qbo", "X"), "s.csv", hs); }
{ /* a period that starts two days into the first week (as in the partial-weeks test) */
  const mid = QB.sales(HS.DATA, "qbo", "X").split("\r\n"), p = U.periodOf(mid[2]), start = new Date(p.from + "T00:00:00Z"); start.setUTCDate(start.getUTCDate() + 2);
  const m = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][start.getUTCMonth()];
  mid[2] = m + " " + start.getUTCDate() + ", " + start.getUTCFullYear() + " - " + mid[2].split(" - ")[1]; write("variant-qbo-sales-midweek", mid.join("\r\n"), "s.csv", hs); }
{ /* no title period at all: the weeks come from the dates, and a late-week last date ends the last week */
  const t = QB.sales(HS.DATA, "qbd", "X").split("\r\n"); t[2] = ""; write("variant-qbd-sales-noperiod", t.join("\r\n"), "s.csv", hs); }

/* refusals and warnings (one per reason in upload.js) */
{ const ok = QB.pnl(HS.DATA, "qbo", "X");
  write("refuse-pnl-byclass", ok.replace("Profit and Loss", "Profit and Loss by Class"), "a.csv", hs);
  write("refuse-pnl-total-income", ok.replace(/Total Income,[^\r\n]*/, "Total Income,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,12.00"), "b.csv", hs);
  const bad = ok.split("\r\n"), i = bad.findIndex(l => /^Net Income,/.test(l)); bad[i] = bad[i].replace(/^Net Income,[^,]*/, "Net Income,9999999.00"); write("refuse-pnl-net-income", bad.join("\r\n"), "c.csv", hs);
  write("refuse-pnl-no-months", "Profit and Loss\nNov 2025\nIncome,5\nTotal Income,5\n", "d.csv", hs);
  const head = ok.split("\r\n"), hi = head.findIndex(l => /^,\w{3} \d{4}/.test(l)); head[hi] = head[hi] + ",Nov 2025"; write("refuse-pnl-dup-month", head.join("\r\n"), "e.csv", hs);
  write("refuse-pnl-no-accounts", "Profit and Loss\n,Jan 2025,Feb 2025\nFoo,1,2\n", "f.csv", hs);
  write("refuse-pnl-no-income", "Profit and Loss\n,Jan 2025,Feb 2025\nExpenses\nRent,1,2\n", "g.csv", hs);
  write("pnl-small-tied", "Profit and Loss\n,Jan 2025,Feb 2025,Total\nIncome\nSales,\"1,000.00\",(20.50),979.50\nOther Expenses\nLoan Interest,5,-5,0\nNet Income,995,-15.5,979.5\n", "h.csv", hs);
  write("warn-pnl-untied", "Profit and Loss\n,Jan-25,Feb-25,Mar-25\nNumbers first,1,2,3\nRevenue\nSales,10,20,30\nOther Income\nInterest,1,1,1\n", "i.csv", hs);
  write("edge-pnl-months-in-first-column", "Profit and Loss\nJan-25,Feb-25,Mar-25\nRevenue\n10,20,30\n", "j.csv", hs); }
write("refuse-balance-no-bank", "Co\nBalance Sheet\nAs of November 1, 2026\n,Total\nAccounts Receivable (A/R),5\nTotal Accounts Receivable,5\n", "x.csv", hs);
write("warn-balance-no-date", "Co\nBalance Sheet\n,Total\nTotal Bank Accounts,\"$1,234.56\"\n", "x2.csv", hs);
write("refuse-aging-no-buckets", "Co\nA/R Aging Summary\nAs of November 1, 2026\n,Foo,Bar\nA,1,2\nTOTAL,1,2\n", "y.csv", hs);
write("refuse-aging-no-amounts", "Co\nA/P Aging Summary\nAs of November 1, 2026\n,Current,1 - 30,31 - 60,Total\nA,,,,\n", "y2.csv", hs);
write("warn-aging-no-total", "Co\nA/R Aging Summary\n,Current,1-30 days,31 - 60,61 - 90,90+,Over 120,Total\nA,1,2,3,4,5,6,99\nB,1,2,3,4,5,6,21\n", "y3.csv", hs);
write("warn-aging-total-off", "Co\nA/P Aging Summary\nAs of Nov 1, 2026\n,Current,1 - 30,31 - 60,61 - 90,91 and over,Total\nA,1,2,3,4,5,15\nTOTAL,1,2,3,4,5,999\n", "y4.csv", hs);
write("refuse-unknown", "Co\nInventory Valuation\nItem,Qty\nA,1\nB,2\n", "z.csv", hs);
write("refuse-empty", "", "e.csv", hs);
{ const sum = ["Sales by Customer Summary", "As of Nov 1, 2026", ",Total"]; for (let i = 0; i < 40; i++) sum.push("Name " + i + "," + Math.round(10000 / (i + 1))); sum.push("TOTAL,99999"); write("customers-summary", sum.join("\n"), "c.csv", hs); }
write("refuse-customers-no-column", "Sales by Customer Summary\nAs of Nov 1, 2026\nCustomer,Foo\nA,1\n", "c2.csv", hs);
write("refuse-customers-few", "Sales by Customer Summary\nAs of Nov 1, 2026\n,Total\nA,1\nB,2\nTOTAL,3\n", "c3.csv", hs);
write("refuse-sales-no-columns", "Co\nSales by Product/Service Detail\nJanuary 1 - March 31, 2025\nItem,Qty\nA,1\n", "s2.csv", hs);
write("refuse-sales-no-dates", "Co\nSales by Product/Service Detail\nJanuary 1 - March 31, 2025\n,Date,Amount\nA,soon,5\n", "s3.csv", hs);
write("csv-bom-semicolon", "﻿Co;;\nBalance Sheet;;\nAs of 11/01/2026;;\n;Total;\nTotal Checking/Savings;1.234,00-;\n", "semi.csv", hs);

/* putting the pieces into the snapshot */
const slotsOf = ids => { const o = {}; ids.forEach(id => { const r = JSON.parse(fs.readFileSync(path.join(EXP, id + ".json"), "utf8")); o[r.kind] = { name: r.name, report: r.report, detail: r.detail, rows: r.rows, data: r.data }; }); return o; };
const app = (id, ex, ids) => { const ctx = { system: "quickbooks", now: NOW }; fs.writeFileSync(path.join(EXP, "apply-" + id + ".json"), JSON.stringify(U.apply(L[ex].DATA, slotsOf(ids), ctx)) + "\n"); applies.push({ id, example: ex, reads: ids, system: ctx.system, now: NOW }); };
EXAMPLES.forEach(ex => ["qbo", "qbd"].forEach(st => app(`${ex}-${st}-all`, ex, ["pnl", "balance", "ar", "ap", "sales"].filter(k => L[ex].DATA[k]).map(k => `${ex}-${st}-${k}`))));
app("home-services-pnl-only", hs, ["home-services-qbd-pnl"]);
app("home-services-pnl-balance", hs, ["home-services-qbd-pnl", "home-services-qbd-balance"]);
app("home-services-renamed", hs, ["variant-qbo-sales-renamed"]);
app("home-services-nounit", hs, ["variant-qbo-sales-nounit"]);
app("home-services-customers", hs, ["variant-qbo-sales-customers", "customers-summary"]);
app("barbershop-balance-nodate", "barbershop", ["warn-balance-no-date"]);

/* the helpers on fixed inputs (NaN is written as null) */
const nn = v => (typeof v === "number" && Number.isNaN(v) ? null : v);
const H = {
  num: ["1,234.50", "(1,234.50)", "-$12", "", "n/a", "12-", "(5)-", "−7.5", "1.", ".5", "1e3", "0x10", " 3 ", "$ 1 000", "--1", "1.2.3", "(1,000.00", " 12"],
  monthKey: ["Jan 2025", "Sep 25", "September 2025", "Total", "Mar 5", "2025-3", "2025-13", "3/2025", "13/2025", "Sept. 2025", "jun-26", "Juni 2025", "Dec 69", "Dec 70", "  Oct   2026 ", "May"],
  dayKey: ["3/5/2025", "2025-03-05", "Mar 5, 2025", "13/45/2025", "2/29/2024", "2/29/2025", "1/2/69", "1/2/70", "2025-03-05T10:00", "Sept. 9, 2025", "9 Sep 2025", "5 March, 2025", "0050-01-01", "Foo 5, 2025", ""],
  periodOf: ["January 1 - December 31, 2025", "January through December 2025", "November 2025 - October 2026", "As of November 1, 2026", "Nov 3 - Jan 4, 2026", "Dec 2025 to Mar 2026", "11/03/2025 - 11/01/2026", "October 1, 2026 through September 30, 2026", "Co | Sales | Jan 5 thru Feb 2, 2026", "no dates here", "As of 2/30/2026"],
};
const helpers = { csv: ['﻿a,"b,c","d ""x"" e"\r\n"line\nbreak",,z\n', "a;b;c\n1;2;3\n", "a\tb\n1\t2", 'x,"un"closed,y\n"open', "a,b\r\r\nc"].map(t => [t, U.csv(t)]) };
Object.keys(H).forEach(k => { helpers[k] = H[k].map(s => [s, nn(U[k](s))]); });
helpers.shares = [[[100, 100, 100, 100, 100], 0.2], [[1, 2], null], [[5, 0, -3, 7, 1, 1, 2, 9.5, 3], 0.5], [Array.from({ length: 137 }, (_, i) => 1000 / (i + 1)), null]].map(([a, o]) => [a, o, U.shares(a, o)]);
fs.writeFileSync(path.join(EXP, "helpers.json"), JSON.stringify(helpers, null, 1) + "\n");
fs.writeFileSync(path.join(HERE, "manifest.json"), JSON.stringify({ now: NOW, reads: cases, applies }, null, 1) + "\n");
console.log(cases.length + " reads, " + applies.length + " applies captured");
