"""rand.py against draws taken from the skeleton's own rng and gauss in Node.

tests/fixtures/skeleton/basics.json comes from capture_basics.js, run on the skeleton itself.
Uniform draws must be identical. Normal draws go through log/sqrt/sin/cos, which V8 and
the C library compute independently, so they are held to 1e-15 (a last-bit difference);
the 28,000-draw run checks that no difference grows along a long stream.
"""

import json
from pathlib import Path

import pytest

from advisor_engine.rand import gauss, rng

FIX = json.loads((Path(__file__).parent / "fixtures" / "skeleton" / "basics.json").read_text())


@pytest.mark.parametrize("seed", list(FIX["uniform"]))
def test_first_twenty_uniform_draws_match_javascript_exactly(seed):
    r = rng(int(seed))
    assert [r() for _ in range(20)] == FIX["uniform"][seed]


@pytest.mark.parametrize("seed", list(FIX["gauss"]))
def test_first_twenty_normal_draws_match_javascript(seed):
    g = gauss(rng(int(seed)))
    got = [g() for _ in range(20)]
    assert got == pytest.approx(FIX["gauss"][seed], rel=0, abs=1e-15)


def test_a_long_stream_does_not_drift():
    g = gauss(rng(77))
    s, last = 0.0, 0.0
    for _ in range(28000):
        last = g()
        s += last
    assert last == pytest.approx(FIX["gauss_77_28000"]["last"], abs=1e-15)
    assert s == pytest.approx(FIX["gauss_77_28000"]["sum"], abs=1e-9)


def test_same_seed_same_stream_and_a_fresh_generator_restarts():
    a, b = rng(77), rng(77)
    assert [a() for _ in range(5)] == [b() for _ in range(5)]
    assert rng(77)() == FIX["uniform"]["77"][0]


def test_uniform_draws_stay_in_unit_interval():
    r = rng(91)
    assert all(0 <= r() < 1 for _ in range(10000))
