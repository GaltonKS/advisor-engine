"""jsmath.py against the skeleton's own helpers (tests/fixtures/skeleton/basics.json,
written by capture_basics.js from app/engine.js).

The engine port leans on these to add, round and read numbers exactly as the skeleton
does, so each is checked against what JavaScript actually returned, not against what we
expect it to return. NaN and -0 come through the fixture as the strings "NaN" and "-0".

Three deliberate differences, each asserted next to what JavaScript does: parse_day refuses
what is not YYYY-MM[-DD] with a four-digit year, and impossible days (JavaScript rolls them
over or misreads them); month_label refuses a month outside 1 to 12 (JavaScript writes
"undefined 2026"). A bad date in a snapshot is a bad export, not a different week.
"""

import json
import math
from datetime import date
from pathlib import Path

import pytest

from advisor_engine.jsmath import (
    add_days, clamp, fin, js_div, js_max, js_min, js_or, js_round, js_string, mean, month_label, n2, or_zero, parse_day,
    plain, quant, sd, to_number, total,
)

FIX = json.loads((Path(__file__).parent / "fixtures" / "skeleton" / "basics.json").read_text())


def num(v):
    return {"NaN": float("nan"), "-0": -0.0}.get(v, v) if isinstance(v, str) else v


def same(got, want):
    if want == "NaN":
        return isinstance(got, float) and math.isnan(got)
    if want == "-0":
        return got == 0 and math.copysign(1, got) == -1
    if isinstance(want, (int, float)) and want == 0:
        return got == 0 and math.copysign(1, got) == 1  # +0 stays +0
    return got == want


@pytest.mark.parametrize("case", FIX["n2"], ids=lambda c: f"{c['tag']}:{c['v']!r}")
def test_n2_reads_loose_input_like_the_skeleton(case):
    assert same(n2(case["v"]), case["out"])


@pytest.mark.parametrize("case", FIX["round"], ids=lambda c: repr(c["v"]))
def test_js_round_is_math_round_including_negative_zero(case):
    assert same(js_round(case["v"]), case["out"])


@pytest.mark.parametrize("case", FIX["stats"], ids=lambda c: repr(c["a"]))
def test_total_mean_sd_and_quantiles_match(case):
    a = case["a"]
    assert same(total(a), case["sum"]), "left-to-right addition, not Python 3.12+'s compensated sum()"
    assert same(mean(a), case["mean"])
    assert same(sd(a), case["sd"])
    for p, want in zip(FIX["quantP"], case["q"]):
        assert same(quant(a, num(p)), want), p


@pytest.mark.parametrize("case", FIX["clamp"])
def test_clamp_matches_and_keeps_nan(case):
    assert same(clamp(num(case["v"]), num(case["a"]), num(case["b"])), case["out"])


@pytest.mark.parametrize("case", FIX["parseDay"], ids=lambda c: c["s"])
def test_parse_day_matches_or_refuses_a_bad_date(case):
    refused = {"2026-02-30": "2026-03-02", "2026-11-": "2026-11-01", "0099-01-05": "1999-01-05",
               "2026-1_1-03": "Invalid Date", "2026-13-01": "2027-01-01"}
    if case["s"] in refused:
        assert case["out"] == refused[case["s"]], "what JavaScript does with it"
        with pytest.raises(ValueError, match="not a date"):
            parse_day(case["s"])
    else:
        assert parse_day(case["s"]).isoformat() == case["out"]


@pytest.mark.parametrize("case", FIX["addDays"], ids=lambda c: f"{c['s']}{c['n']:+d}")
def test_add_days_matches_across_month_year_and_leap_day(case):
    assert add_days(parse_day(case["s"]), case["n"]).isoformat() == case["out"]


@pytest.mark.parametrize("case", FIX["fmtMonth"], ids=lambda c: c["k"])
def test_month_label_matches_fmt_month_or_refuses_a_bad_month(case):
    if case["out"].startswith("undefined"):
        with pytest.raises(ValueError, match="not a month"):
            month_label(case["k"])
    else:
        assert month_label(case["k"]) == case["out"]


def test_js_or_is_javascripts_falsy_or():
    # Python's `or` treats NaN as true; JavaScript's || does not
    assert js_or(float("nan"), 1) == 1 and (float("nan") or 1) != 1
    for falsy in (0, 0.0, "", None, False):
        assert js_or(falsy, "d") == "d"
    assert js_or(3, 1) == 3 and js_or("x", 1) == "x" and js_or(-1, 5) == -1
    assert or_zero(float("nan")) == 0 and or_zero(None) == 0 and or_zero(2.5) == 2.5


def test_fin_is_a_finite_number_and_not_a_boolean():
    assert fin(3) and fin(-0.5)
    assert not any(fin(x) for x in (True, False, float("nan"), float("inf"), "3", None))


def test_plain_turns_dates_and_nan_into_json():
    assert plain({"d": date(2026, 11, 3), "x": float("nan"), "t": (1, float("inf")), "f": len}) == \
        {"d": "2026-11-03", "x": None, "t": [1, None]}


@pytest.mark.parametrize("case", FIX["toNumber"], ids=lambda c: f"{c['tag']}:{c['v']!r}")
def test_to_number_is_what_arithmetic_does_to_an_operand(case):
    assert same(to_number(case["v"]), case["out"]) or (case["out"] in ("Infinity", "-Infinity")
                                                        and to_number(case["v"]) == float(case["out"].replace("Infinity", "inf")))


@pytest.mark.parametrize("case", FIX["div"], ids=lambda c: f"{c['a']}/{c['b']}")
def test_js_div_never_raises_and_matches(case):
    got, want = js_div(case["a"], num(case["b"])), case["out"]
    if want in ("Infinity", "-Infinity"):
        assert got == float(want.replace("Infinity", "inf"))
    else:
        assert same(got, want)


@pytest.mark.parametrize("case", FIX["max"], ids=lambda c: repr(c["xs"]))
def test_js_max_and_min_keep_nan_and_signed_zero(case):
    xs = [num(x) for x in case["xs"]]
    assert same(js_max(*xs), case["max"]) and same(js_min(*xs), case["min"])


@pytest.mark.parametrize("case", FIX["string"], ids=lambda c: repr(c["v"]))
def test_js_string_is_string(case):
    assert js_string(case["v"]) == case["out"]
