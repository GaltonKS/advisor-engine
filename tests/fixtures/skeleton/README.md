# Skeleton parity fixtures

What the FIS pilot skeleton's own engine (`app/engine.js`) returns, captured by Node from
the skeleton itself, so the Python port is tested against the skeleton's answers rather
than against what we expect them to be. Never edit the JSON by hand: recapture.

| File | Written by | Holds | Tested in |
| --- | --- | --- | --- |
| `basics.json` | `capture_basics.js` | known-seed `rng` / `gauss` draws; `n2`, `Math.round`, `sum` / `mean` / `sd` / `quant`, `clamp`, `parseDay`, `addDays`, `fmtMonth` on awkward inputs | `test_rand.py`, `test_jsmath.py` |

Every file records the sha256 of the skeleton's `app/engine.js` it came from.

To recapture (Node 20+, with the skeleton downloaded or checked out somewhere):

```bash
cd tests/fixtures/skeleton
node capture_basics.js /path/to/fis-pilot-skeleton > basics.json
```

The skeleton builds dates as local calendar days; the capture gives the same output in UTC
and in America/New_York (checked when it was written).
