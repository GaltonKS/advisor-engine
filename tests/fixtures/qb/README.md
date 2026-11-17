# QuickBooks CSV fixtures

Test data for `advisor_engine.books`, the Python port of the FIS pilot skeleton's
`app/upload.js`, used by `tests/test_books.py`.

- `<id>.csv`: QuickBooks Online (`qbo`) and Desktop (`qbd`) style exports. Most are made
  by the skeleton's `test/qb-fixtures.js` from the example snapshots; the `refuse-*`,
  `warn-*` and `edge-*` files are short hand-written ones that hit each refusal and
  warning in the reader. They are generated layouts, not real companies' files. The
  names in them (customers, suppliers) are made up and only there to check that no name
  comes out.
- `expected/<id>.json`: what the JS reader returns for each file (`U.read(text, name, ctx)`).
- `expected/apply-<id>.json`: what `U.apply(base, slots, ctx)` returns for the
  combinations listed under `applies` in `manifest.json`.
- `expected/helpers.json`: `csv`, `num`, `monthKey`, `dayKey`, `periodOf` and `shares` on
  fixed inputs (`NaN` written as `null`).
- `manifest.json`: for each case, the file, the name it was read under and the unit words
  (the brain's `vocab.unit`, from the skeleton's `FIS.vocab`); and the fixed upload time.
- `../skeleton/<example>/data.json`: the example snapshots the exports were made from.

The CSVs keep the CRLF line ends the generator writes (`.gitattributes` stops git from
changing them), and the tests read them as bytes.

## Regenerating

From any directory other than the skeleton, with Node 20 or later:

```bash
node tests/fixtures/qb/capture.js <path to the FIS pilot skeleton>
```

It overwrites every file listed above. Then run `pytest tests/test_books.py`: the parity
tests must pass with the new expectations. When `upload.js` changes, port the change to
`src/advisor_engine/books.py`; when a real export is refused, add its layout here (and to
the skeleton's `test/qb-fixtures.js`) rather than loosening a test.
