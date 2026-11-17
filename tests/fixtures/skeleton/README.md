# Skeleton parity fixtures

What the FIS pilot skeleton's own engine (`app/engine.js`) returns, captured by Node from
the skeleton itself, so the Python port is tested against the skeleton's answers rather
than against what we expect them to be. Never edit the JSON by hand: recapture.

| File | Written by | Holds | Tested in |
| --- | --- | --- | --- |
| `basics.json` | `capture_basics.js` | known-seed `rng` / `gauss` draws; `n2`, `Math.round`, `sum` / `mean` / `sd` / `quant`, `clamp`, `parseDay`, `addDays`, `fmtMonth` on awkward inputs | `test_rand.py`, `test_jsmath.py` |
| `<example>/brain.json`, `<example>/data.json` | `capture_build.js` | the three worked examples' brain and snapshot as JSON (a brain's metric closures become parameterised measures: `METRIC_PORTS`) | every step |
| `<example>/build.json` | `capture_build.js` | the model base (`FIS.build`) for each example built five ways, plus nine edge runs on the barbershop | `test_model.py` |
| `<example>/ideas.json` | `capture_ideas.js` | `FIS.evaluate` and `FIS.bridge` on about twenty ideas per example (as shipped, and corrected on the 13-week basis), plus loose and malformed ideas on the barbershop, with the error where the skeleton throws | `test_evaluate.py` |
| `<example>/cash.json` | `capture_cash.js` | `FIS.cash` for ideas that move cash, a path-count override and synthetic tax payments, as shipped and corrected; on the barbershop, brain and data variants (11 to 70 weeks, a closed month, a 26-week horizon, the brain's path count, a floor above cash) | `test_cash.py` |

Every file records the sha256 of the skeleton's `app/engine.js` it came from.

To recapture (Node 20+, with the skeleton downloaded or checked out somewhere):

```bash
cd tests/fixtures/skeleton
node capture_basics.js /path/to/fis-pilot-skeleton > basics.json
node capture_build.js /path/to/fis-pilot-skeleton .
node capture_ideas.js /path/to/fis-pilot-skeleton .     # reads the brain.json / data.json written above
node capture_cash.js /path/to/fis-pilot-skeleton .
```

The skeleton builds dates as local calendar days; the capture gives the same output in UTC
and in America/New_York (checked when it was written).
