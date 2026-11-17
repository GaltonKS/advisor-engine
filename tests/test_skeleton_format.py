"""tests/skeleton_format.py against the skeleton's own formatters.

The parity tests render the engine's figures with skeleton_format and compare the text with
the skeleton's. If the renderer rounded differently from JavaScript, those tests would prove
nothing on halfway values, so the renderer itself is held to what usd, usdk, num, pct, grp and
signed returned in Node (fixtures/skeleton/basics.json, capture_basics.js), on the halfway and
switch-over values where rounding goes wrong.
"""

import json
from pathlib import Path

import pytest

from skeleton_format import grp, num, pct, signed, usd, usdk

FIX = json.loads((Path(__file__).parent / "fixtures" / "skeleton" / "basics.json").read_text())


def _v(x):
    return {"NaN": float("nan"), "-0": -0.0}.get(x, x) if isinstance(x, str) else x


@pytest.mark.parametrize("case", FIX["formats"], ids=lambda c: repr(c["v"]))
def test_renderer_matches_the_skeletons_formatters(case):
    v = _v(case["v"])
    got = {"usd": usd(v), "usdk": usdk(v), "num0": num(v), "num1": num(v, 1), "num2": num(v, 2), "pct0": pct(v / 100),
           "pct1": pct(v / 100, 1), "grp2": grp(v, 2), "signed1": signed(v / 100, lambda x: pct(x, 1))}
    assert got == {k: case[k] for k in got}
