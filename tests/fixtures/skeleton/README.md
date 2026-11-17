# Skeleton parity fixtures

What the FIS pilot skeleton's own engine computes on its three worked examples, for
`tests/test_parity.py` (gate layer 2) and `tests/test_rand.py`. Written by Node from the
skeleton itself; never edit the JSON by hand.

| File | Written by | Holds |
| --- | --- | --- |
| `<example>/brain.json`, `<example>/data.json` | `capture.js` | the example's brain and snapshot as JSON (brain closures replaced by parameterised measures, see `METRIC_PORTS`) |
| `<example>/expected.json` | `capture.js` | five runs × ~20 ideas: model, evaluate, cash, bridge, chartFor, measures, views, panels, cost lines, check, tax |
| `check_broken.json` | `capture.js` | the brain check on three deliberately broken brains |
| `rand.json` | `capture_rand.js` | known-seed draws from `rng` and `gauss` |

Every file records the sha256 of the skeleton's `app/engine.js`; `test_parity.py` pins it.

To recapture (Node 20+, the skeleton checked out or downloaded somewhere):

```bash
cd tests/fixtures/skeleton
node capture.js /path/to/fis-pilot-skeleton .
node capture_rand.js /path/to/fis-pilot-skeleton > rand.json
```

A new skeleton version changes the sha256: update `SKELETON_SHA` in `test_parity.py` and
read the diff of the expected files before trusting it. If Python then disagrees, fix the
port, not the tolerance.

Gate layer 3 (Elite's real data, tied to DaySmart's totals) is not here yet: Elite's
`data.js` and the skeleton's output for it have not been provided.
