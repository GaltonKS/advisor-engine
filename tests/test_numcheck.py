import pytest

from advisor_engine.assistant.numcheck import (
    collect_allowed_numbers,
    extract_numbers,
    verify,
)

OUT = {
    "returns_source": "synthetic",
    "n_periods": 120,
    "concentration": {"effective_bets": 4.63, "top_component_share": 0.2113},
    "drawdown": {"historical_max_drawdown": -0.3412, "stress_loss_1_in_100": -0.0987},
    "positions": [
        {"ticker": "AAA", "risk_share": 0.1534, "risk_to_weight": 1.19},
        {"ticker": "BBB", "risk_share": 0.05, "risk_to_weight": 0.62},
    ],
}


def ok(text, outputs=(OUT,), **kw):
    return verify(text, list(outputs), **kw)


# --- extract_numbers -------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("There are 12 positions", [12]),
        ("beta of 1.19", [1.19]),
        ("21.1% of risk", [21.1]),
        ("21.1 percent of risk", [21.1]),
        ("about 1,250,000 total", [1250000]),
        ("a loss of -34.1%", [-34.1]),
        ("a drop of −34.1%", [-34.1]),
        ("risk is 1.19x its weight", [1.19]),
        ("5-10 positions", [5, 10]),
        ("(4.6)", [4.6]),
        ("**21.1%**", [21.1]),
        ("it is 21.1%.", [21.1]),
    ],
)
def test_extract_numbers(text, expected):
    assert extract_numbers(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "In 2024 the portfolio",
        "since 1999",
        "the 1st and 2nd and 3rd positions",
        "Ticker T1 and BRK.B2",
        "see stress_loss_1_in_100",
        "the 1-in-100 stress loss",
        "Q3 results",
        "",
    ],
)
def test_extract_ignores(text):
    assert extract_numbers(text) == []


def test_extract_ignores_list_numbering():
    text = "Top drivers:\n1. AAA at 15.3%\n2) BBB at 5%\n  3. CCC\n- 4. DDD\n**5.** EEE"
    assert extract_numbers(text) == [15.3, 5]


def test_extract_keeps_year_like_numbers_that_are_not_bare():
    assert extract_numbers("1,999 shares") == [1999]
    assert extract_numbers("2000%") == [2000]
    assert extract_numbers("2000.5") == [2000.5]
    assert extract_numbers("1899 and 2101") == [1899, 2101]


def test_extract_does_not_split_decimals_glued_to_text():
    assert extract_numbers("21.1abc") == []


# --- collect_allowed_numbers ------------------------------------------------

def test_collect_flattens_nested_and_adds_percent_forms():
    allowed = collect_allowed_numbers([OUT])
    assert 4.63 in allowed and 120 in allowed
    assert pytest.approx(21.13) in allowed
    assert pytest.approx(-34.12) in allowed
    # 1.19 is outside [-1, 1]: no percent form
    assert not any(pytest.approx(119.0) == a for a in allowed)


def test_collect_accepts_json_strings_and_error_text():
    allowed = collect_allowed_numbers(['{"a": [0.5, {"b": 7}]}', "Need at least 24 periods"])
    assert 7 in allowed and 0.5 in allowed and 50 in allowed and 24 in allowed


def test_collect_ignores_bools_and_none():
    assert collect_allowed_numbers([{"a": True, "b": None, "c": float("nan")}]) == []


# --- verify: matches --------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Effective bets: 4.63",
        "Effective bets: 4.6",
        "about 5 effective bets",
        "top component share 0.2113",
        "top component share 0.21",
        "top component carries 21.13% of risk",
        "top component carries 21.1% of risk",
        "top component carries 21% of risk",
        "top component carries 21.1 percent of risk",
        "max drawdown of -34.1%",
        "max drawdown of 34.1%",
        "max drawdown of −34.12%",
        "AAA adds 1.19x its weight",
        "AAA adds 1.2x its weight",
        "AAA risk share is 15.3%",
        "BBB risk share is 5%",
        "BBB risk share is 5.0%",
        "120 periods of data",
        "across 2 positions",  # list length
        "stress loss of 9.9%",
        "The 1-in-100 stress loss is 9.87%",
        "No figures at all here.",
    ],
)
def test_verify_accepts(text):
    r = ok(text)
    assert r["ok"], r
    assert r["unmatched"] == []


def test_verify_thousands_separators_match_tool_value():
    r = verify("over 1,250 periods", [{"n": 1250}])
    assert r["ok"]


# --- verify: rejections ------------------------------------------------------

@pytest.mark.parametrize(
    "text, bad",
    [
        ("Effective bets: 7.2", "7.2"),
        ("top component carries 25% of risk", "25%"),
        ("top component carries 21.5% of risk", "21.5%"),
        ("top component carries 21.2% of risk", "21.2%"),
        ("top component share 0.25", "0.25"),
        ("AAA adds 1.5x its weight", "1.5x"),
        ("AAA risk share is 119%", "119%"),  # percent form only exists for [-1, 1]
        ("max drawdown of 40%", "40%"),
        ("drawdown 0.3412%", "0.3412%"),  # a fraction mislabelled as a percent
        ("a 1.19% risk share", "1.19%"),  # a raw value mislabelled as a percent
        ("21.1x the weight", "21.1x"),  # a percent form is not a multiplier
        ("portfolio is worth $1,000,000", "1,000,000"),
    ],
)
def test_verify_rejects_invented_numbers(text, bad):
    r = ok(text)
    assert not r["ok"]
    assert bad in r["unmatched"]


def test_verify_reports_each_unmatched_number_once():
    r = ok("Bets 6.6, again 6.6, and 8.8; real: 4.63")
    assert r["unmatched"] == ["6.6", "8.8"]


def test_verify_mixed_reply_flags_only_the_invented_number():
    r = ok("Top share 21.1% and a made-up 77.7% figure")
    assert r["unmatched"] == ["77.7%"]


def test_verify_with_no_tool_outputs_rejects_any_number():
    assert not verify("risk is 5%", [])["ok"]
    assert verify("no numbers here", [])["ok"]


def test_verify_ignores_years_and_numbering_when_checking():
    r = ok("As of 2024:\n1. Effective bets 4.6\n2. Top share 21.1%")
    assert r["ok"], r


def test_verify_counts_numbers_checked():
    assert ok("4.6 and 21.1%")["checked"] == 2


def test_verify_numbers_from_any_tool_output_in_conversation():
    other = {"effective_bets": 9.1}
    assert ok("Earlier 9.1, now 4.6", outputs=[OUT, other])["ok"]
    assert not ok("Earlier 9.1", outputs=[OUT])["ok"]


def test_verify_float_noise_in_tool_output():
    assert verify("share 21.1%", [{"s": 0.21100000000000002}])["ok"]
    assert verify("share 33.33%", [{"s": 1 / 3}])["ok"]


def test_verify_tolerance_floor_is_configurable():
    # A reply more precise than rounding allows only passes via the tolerance floor.
    out = [{"s": 0.2113}]
    assert not verify("share 0.2115", out, rel_tol=0.0, abs_tol=0.0)["ok"]
    assert verify("share 0.2115", out, rel_tol=0.005, abs_tol=0.0005)["ok"]


# --- sign ----------------------------------------------------------------------

NEG = [{"d": -0.12}]
POS = [{"d": 0.12}]


@pytest.mark.parametrize(
    "text, outputs, ok_",
    [
        ("a 12% gain", NEG, False),
        ("a 12% loss", NEG, True),
        ("a 12% loss", POS, False),
        ("a 12% gain", POS, True),
        ("-10.9%", [{"d": -0.109}], True),
        ("−10.9%", [{"d": -0.109}], True),
        ("-10.9%", [{"d": 0.109}], False),
        ("shares fell by about 12.0%", NEG, True),
        ("shares fell by about 12.0%", POS, False),
        ("up 12.0%", NEG, False),
        ("12.0% drawdown", NEG, True),
        ("12.0% drawdown", POS, False),
        ("12.0%", NEG, True),  # no direction stated
        ("12.0%", POS, True),
        # direction word too far away (more than 4 words) is ignored
        ("a gain that was seen across all of the 12.0%", NEG, True),
        # each clause gets its own direction
        ("gain of 12.0% and loss of 12.0%", [{"a": 0.12}, {"b": -0.12}], True),
        ("gain of 12.0% and loss of 12.0%", POS, False),
    ],
)
def test_sign_agreement(text, outputs, ok_):
    r = verify(text, outputs)
    assert r["ok"] is ok_, r
    if not ok_:
        assert r["reasons"][r["unmatched"][0]] == "sign mismatch"


def test_sign_mismatch_when_only_other_sign_exists_but_other_value_matches():
    # +12 exists elsewhere in the tool output, so "a 12% gain" is backed.
    assert verify("a 12% gain", [{"a": -0.12, "b": 0.12}])["ok"]


# --- coarse figures ------------------------------------------------------------

def test_coarse_integer_not_matched_by_near_value():
    for v in (5.4, 4.6):
        r = verify("5 bets", [{"effective_bets": v}])
        assert not r["ok"]
        assert r["reasons"]["5"] == "coarse match, unverified"


def test_coarse_integer_exact_value_passes():
    assert verify("5 bets", [{"effective_bets": 5.0, "other": 5.4}])["ok"]
    assert verify("5 bets", [{"effective_bets": 5}])["ok"]


def test_coarse_integer_list_length_passes():
    assert verify("12 positions", [{"positions": [{}] * 12}])["ok"]


def test_coarse_integer_closest_tool_number_passes():
    assert verify("about 21% of risk", [{"s": 0.2113}])["ok"]
    # 5.1 is the nearest tool number and within a quarter unit
    assert verify("5 bets", [{"a": 5.2, "b": 5.1}])["ok"]
    # 5.3 rounds to 5 but is not close enough
    assert not verify("5 bets", [{"a": 5.3}])["ok"]


def test_coarse_with_no_nearby_value_is_plain_no_match():
    r = verify("40 bets", [{"a": 4.63}])
    assert r["reasons"]["40"] == "no matching tool figure"


def test_precise_figure_still_passes():
    assert verify("1.29", [{"x": 1.2929}])["ok"]
    assert verify("10.9%", [{"x": 0.109}])["ok"]


# --- K / M / B suffixes (business units) ------------------------------------

BOOKS = {"kept_per_month": 13912.4, "year_sales": 553174}


@pytest.mark.parametrize(
    "reply, outputs, good, reason",
    [
        ("The shop keeps $13.9K a month.", BOOKS, True, None),
        ("The shop keeps $99.9K a month.", BOOKS, False, "no matching tool figure"),
        ("Sales were $1.2M for the year.", BOOKS, False, "no matching tool figure"),
        ("Sales were $553,174 for the year.", BOOKS, True, None),
        ("$13.9K", {"x": 13949.0}, True, None),  # rounds to the nearest 100
        ("$13.9K", {"x": 13951.0}, False, "no matching tool figure"),
        # A scaled figure never matches the percent pool (0.2113 -> 21.13%).
        ("$21.1K of risk", {"top_component_share": 0.2113}, False, "no matching tool figure"),
    ],
)
def test_scaled_figures_are_checked(reply, outputs, good, reason):
    r = verify(reply, [outputs])
    assert r["checked"] == 1
    assert r["ok"] is good
    if reason:
        assert list(r["reasons"].values()) == [reason]


@pytest.mark.parametrize(
    "text, expected",
    [("$13.9K", [13900.0]), ("1.5B", [1500000000.0]), ("-2.5M", [-2500000.0]),
     ("2m and 3k", [2000000.0, 3000.0])],
)
def test_extract_numbers_scales_suffixes(text, expected):
    assert extract_numbers(text) == expected


def test_suffix_glued_to_a_word_is_not_a_figure():
    assert extract_numbers("13.9Kg and 5Mbps") == []


# --- labels: account types and dates -----------------------------------------

@pytest.mark.parametrize(
    "text",
    ["401(k)", "401k", "403(b)", "403b", "401 (k)", "Nov 10", "10 November", "Sept. 3",
     "March 3rd", "on Dec. 31, the books close", "Your 401(k) and 403b plans"],
)
def test_account_types_and_dates_are_labels(text):
    assert verify(text, [{}])["checked"] == 0


def test_month_does_not_swallow_a_year():
    r = verify("In Nov 2026 sales were $553,174.", [BOOKS])
    assert r["ok"] and r["checked"] == 1


def test_labels_do_not_hide_real_figures():
    # Money written as $401k is a figure; so is a percent after a month name, and a
    # count before the verb "may".
    assert extract_numbers("$401k") == [401000.0]
    assert extract_numbers("Nov 10.5%") == [10.5]
    assert extract_numbers("Of the 12, 10 may be sold") == [12, 10]
