"""Three-part risk profile engine (profile.py; draft method, profile_config.py)."""

import copy

import pytest

from advisor_engine import profile as pf
from advisor_engine import profile_config as cfg

DANA = {"id": "p-dana", "name": "Dana", "role": "primary"}
MARCUS = {"id": "p-marcus", "name": "Marcus", "role": "partner"}


def row(question_id, answer, person=None, t=None):
    r = {"person_id": person["id"] if person else None, "question_id": question_id,
         "answer": answer}
    if t is not None:
        r["recorded_at"] = f"2026-11-03T10:00:{t:02d}Z"
    return r


def full_household():
    return [
        row("w_reaction", 2, DANA), row("w_loss_limits", {"uncomfortable_pct": 0.1,
                                                          "act_pct": 0.2}, DANA),
        row("w_reaction", 3, MARCUS), row("w_experience", 3, MARCUS),
        row("a_income_share", 2), row("a_horizon_years", 12),
        row("n_required_return", 0.058),
    ]


# --- empty and determinism -----------------------------------------------------------


def test_no_answers_gives_none_not_zero_and_is_provisional():
    out = pf.compute_profile([], [DANA, MARCUS])
    assert [p["willingness"] for p in out["people"]] == [None, None]
    assert out["household_willingness"] is None
    assert out["ability"] is None and out["need"] is None and out["ceiling"] is None
    assert out["provisional"] is True
    assert out["flags"] == []
    assert out["method_version"] == cfg.METHOD_VERSION == "draft-2"
    assert out["plain_summary"].endswith(cfg.METHOD_STATUS)


def test_no_people_is_provisional():
    out = pf.compute_profile([row("a_income_share", 2)], [])
    assert out["provisional"] and out["people"] == [] and out["ceiling"] is None


def test_deterministic_and_input_unchanged():
    inputs = full_household()
    before = copy.deepcopy(inputs)
    a = pf.compute_profile(inputs, [DANA, MARCUS], portfolio_risk=7.0)
    b = pf.compute_profile(inputs, [DANA, MARCUS], portfolio_risk=7.0)
    assert a == b
    assert inputs == before


def test_complete_household_is_not_provisional():
    out = pf.compute_profile(full_household(), [DANA, MARCUS])
    assert out["provisional"] is False and out["provisional_reasons"] == []


def test_method_is_labelled_draft():
    assert "draft" in cfg.METHOD_STATUS.lower() and "compliance" in cfg.METHOD_STATUS.lower()
    assert "DRAFT" in (pf.__doc__ or "") or "draft" in (pf.__doc__ or "").lower()


# --- mappings ------------------------------------------------------------------------


def test_mockup_anchor_required_return_5_8_pct_maps_to_6_1():
    assert pf.reading("n_required_return", 0.058) == pytest.approx(6.1)
    out = pf.compute_profile([row("n_required_return", 0.058)], [DANA])
    assert out["need"]["mean"] == 6.1


def test_tables_are_monotone_and_on_the_scale():
    for q in cfg.QUESTIONS:
        values = ([o["value"] for o in q["options"]] if q["kind"] == "choice"
                  else [y for _, y in q["table"]])
        assert values == sorted(values), q["id"]
        assert all(cfg.SCALE_MIN <= v <= cfg.SCALE_MAX for v in values), q["id"]
        if "table" in q:
            xs = [x for x, _ in q["table"]]
            assert xs == sorted(set(xs)), q["id"]


def test_interpolation_clamps_at_the_ends():
    table = [(0.0, 1.0), (1.0, 3.0)]
    assert pf.interpolate(table, -5) == 1.0
    assert pf.interpolate(table, 0.5) == 2.0
    assert pf.interpolate(table, 9) == 3.0


def test_loss_limits_reading_uses_act_pct():
    assert pf.reading("w_loss_limits", {"uncomfortable_pct": 0.05, "act_pct": 0.2}) == 5.5
    assert pf.reading("w_loss_limits", {"uncomfortable_pct": 0.2, "act_pct": 0.2}) == 5.5


# --- validation ----------------------------------------------------------------------


def test_act_below_uncomfortable_is_rejected():
    with pytest.raises(ValueError, match="cannot be smaller"):
        pf.validate_answer("w_loss_limits", {"uncomfortable_pct": 0.2, "act_pct": 0.1})


@pytest.mark.parametrize("qid,answer", [
    ("w_reaction", 5), ("w_reaction", -1), ("w_reaction", True), ("w_reaction", "2"),
    ("w_loss_limits", {"uncomfortable_pct": 0.1, "act_pct": 0.6}),
    ("w_loss_limits", {"act_pct": 0.2}),
    ("w_loss_limits", {"uncomfortable_pct": 0.1, "act_pct": 0.2, "extra": 1}),
    ("a_horizon_years", -1), ("a_horizon_years", 61), ("a_horizon_years", float("nan")),
    ("n_required_return", 0.5), ("n_required_return", "6%"),
    ("unknown", 1),
])
def test_bad_answers_are_rejected(qid, answer):
    with pytest.raises(ValueError):
        pf.validate_answer(qid, answer)


def test_retraction_is_always_a_valid_answer():
    assert pf.validate_answer("w_reaction", None) is None


def test_willingness_needs_a_person_in_the_household_and_ability_takes_none():
    with pytest.raises(ValueError, match="person"):
        pf.compute_profile([row("w_reaction", 2)], [DANA])
    with pytest.raises(ValueError, match="person"):
        pf.compute_profile([row("w_reaction", 2, MARCUS)], [DANA])
    with pytest.raises(ValueError, match="household question"):
        pf.compute_profile([row("a_income_share", 2, DANA)], [DANA])


def test_at_most_two_people():
    with pytest.raises(ValueError, match="at most two"):
        pf.compute_profile([], [DANA, MARCUS, {"id": "x", "name": "X", "role": "partner"}])


# --- estimator -----------------------------------------------------------------------


def test_one_answer_band_is_its_noise():
    est = pf.estimate([(5.5, 1.5)])
    assert est == {"mean": 5.5, "band": 1.5, "low": 4.0, "high": 7.0, "n_answers": 1}


def test_more_agreeing_answers_narrow_the_band():
    one = pf.estimate([(5.0, 1.5)])
    two = pf.estimate([(5.0, 1.5), (5.0, 1.5)])
    three = pf.estimate([(5.0, 1.5), (5.0, 1.5), (5.0, 1.0)])
    assert one["band"] > two["band"] > three["band"]
    assert one["mean"] == two["mean"] == three["mean"] == 5.0


def test_disagreeing_answers_widen_the_band():
    agree = pf.estimate([(5.0, 1.5), (5.0, 1.0)])
    disagree = pf.estimate([(2.0, 1.5), (8.0, 1.0)])
    assert disagree["band"] > agree["band"]


def test_trusted_answers_weigh_more():
    est = pf.estimate([(2.0, 3.0), (8.0, 1.0)])  # weights 1/9 and 1
    assert est["mean"] == pytest.approx(7.4)


def test_band_is_clipped_to_the_scale():
    est = pf.estimate([(1.5, 3.0)])
    assert est["low"] == 1.0 and est["high"] == 4.5


def test_rounding_is_half_up():
    assert pf.r1(6.25) == 6.3 and pf.r1(6.15) == 6.2 and pf.r1(6.05) == 6.1


# --- latest wins, retractions --------------------------------------------------------


def test_latest_answer_wins_by_recorded_at_not_list_order():
    inputs = [row("w_reaction", 4, DANA, t=5), row("w_reaction", 0, DANA, t=1)]
    out = pf.compute_profile(inputs, [DANA])
    assert out["people"][0]["willingness"]["mean"] == 9.5
    assert out["people"][0]["willingness"]["n_answers"] == 1


def test_latest_answer_wins_in_list_order_without_timestamps():
    out = pf.compute_profile([row("w_reaction", 0, DANA), row("w_reaction", 4, DANA)], [DANA])
    assert out["people"][0]["willingness"]["mean"] == 9.5


def test_retracted_answers_are_ignored():
    inputs = [row("w_reaction", 4, DANA, t=1), row("w_experience", 0, DANA, t=2),
              row("w_reaction", None, DANA, t=3)]
    out = pf.compute_profile(inputs, [DANA])
    assert out["people"][0]["willingness"]["n_answers"] == 1
    assert out["people"][0]["willingness"]["mean"] == 3.0
    # Retract the last one too: back to no answers.
    out = pf.compute_profile(inputs + [row("w_experience", None, DANA, t=4)], [DANA])
    assert out["people"][0]["willingness"] is None and out["ceiling"] is None


def test_answers_are_per_person():
    inputs = [row("w_reaction", 0, DANA, t=1), row("w_reaction", 4, MARCUS, t=2)]
    out = pf.compute_profile(inputs, [DANA, MARCUS])
    assert [p["willingness"]["mean"] for p in out["people"]] == [1.5, 9.5]


def test_inputs_hash_is_stable_and_follows_answers_in_force():
    a = [row("w_reaction", 2, DANA, t=1), row("a_income_share", 1, t=2)]
    b = list(reversed(a))
    assert pf.inputs_hash(a) == pf.inputs_hash(b)
    retracted = a + [row("w_experience", 1, DANA, t=3), row("w_experience", None, DANA, t=4)]
    assert pf.inputs_hash(retracted) == pf.inputs_hash(a)
    assert pf.inputs_hash(a + [row("a_income_share", 2, t=5)]) != pf.inputs_hash(a)


# --- household rules -----------------------------------------------------------------


def test_household_willingness_is_the_lower_partner_not_the_average():
    inputs = [row("w_reaction", 1, DANA), row("w_reaction", 3, MARCUS)]
    out = pf.compute_profile(inputs, [DANA, MARCUS])
    assert out["household_willingness"]["mean"] == 3.5
    assert out["household_willingness"]["person_id"] == DANA["id"]
    assert "uses the lower, 3.5" in out["plain_summary"]


def test_household_willingness_skips_a_person_without_answers():
    out = pf.compute_profile([row("w_reaction", 3, MARCUS)], [DANA, MARCUS])
    assert out["household_willingness"]["mean"] == 7.5
    assert out["provisional"]


def test_ceiling_is_the_min_of_willingness_and_ability():
    inputs = [row("w_reaction", 3, DANA), row("a_income_share", 1)]  # 7.5 vs 3.5
    out = pf.compute_profile(inputs, [DANA])
    assert out["ceiling"] == {"value": 3.5, "limited_by": "ability"}
    inputs = [row("w_reaction", 1, DANA), row("a_income_share", 4)]  # 3.5 vs 9.0
    out = pf.compute_profile(inputs, [DANA])
    assert out["ceiling"] == {"value": 3.5, "limited_by": "willingness"}


def test_ceiling_waits_for_willingness_but_not_for_ability():
    assert pf.compute_profile([row("a_income_share", 1)], [DANA])["ceiling"] is None
    out = pf.compute_profile([row("w_reaction", 2, DANA)], [DANA])
    assert out["ceiling"] == {"value": 5.5, "limited_by": "willingness"}


def test_need_is_compared_not_folded_into_the_ceiling():
    out = pf.compute_profile([row("w_reaction", 1, DANA), row("n_required_return", 0.12)],
                             [DANA])
    assert out["ceiling"]["value"] == 3.5 and out["need"]["mean"] == 10.0


def test_answers_carry_their_readings():
    out = pf.compute_profile([row("n_required_return", 0.058)], [DANA])
    assert out["answers"] == [{"person_id": None, "question_id": "n_required_return",
                               "dimension": "need", "answer": 0.058, "reading": 6.1}]


# --- flags at and just below their thresholds ----------------------------------------


def est(mean, band=1.0, n=2):
    return {"mean": mean, "band": band, "low": mean - band, "high": mean + band, "n_answers": n}


def person(pid, name, mean, band=1.0):
    return {"person_id": pid, "name": name, "willingness": est(mean, band)}


def codes(flags):
    return [f["code"] for f in flags]


@pytest.mark.parametrize("gap,fires", [(cfg.PARTNER_GAP, True), (cfg.PARTNER_GAP - 0.1, False)])
def test_partner_gap(gap, fires):
    people = [person("a", "Dana", 4.0), person("b", "Marcus", 4.0 + gap)]
    flags = pf.derive_flags(people, people[0]["willingness"], None, None, None)
    assert ("partner_gap" in codes(flags)) is fires
    if fires:
        f = flags[0]
        assert f["severity"] == "warn" and f["refs"]["people"] == ["a", "b"]
        assert "Dana" in f["message"] and "Marcus" in f["message"]


@pytest.mark.parametrize("gap,fires", [(cfg.NEED_OVER_CEILING, True),
                                       (cfg.NEED_OVER_CEILING - 0.1, False)])
def test_need_exceeds_ceiling(gap, fires):
    flags = pf.derive_flags([], None, None, est(5.0 + gap), {"value": 5.0, "limited_by": "x"})
    assert ("need_exceeds_ceiling" in codes(flags)) is fires


@pytest.mark.parametrize("gap,fires", [(cfg.PORTFOLIO_OVER_CEILING, True),
                                       (cfg.PORTFOLIO_OVER_CEILING - 0.1, False)])
def test_portfolio_exceeds_ceiling(gap, fires):
    flags = pf.derive_flags([], None, None, None, {"value": 5.0, "limited_by": "x"},
                            portfolio_risk=5.0 + gap)
    assert ("portfolio_exceeds_ceiling" in codes(flags)) is fires


def test_portfolio_flag_needs_a_reading():
    flags = pf.derive_flags([], None, None, None, {"value": 1.0, "limited_by": "x"})
    assert flags == []


@pytest.mark.parametrize("gap,fires", [(cfg.ABILITY_BELOW_WILLINGNESS, True),
                                       (cfg.ABILITY_BELOW_WILLINGNESS - 0.1, False)])
def test_ability_below_willingness(gap, fires):
    flags = pf.derive_flags([], est(6.0), est(6.0 - gap), None, None)
    assert ("ability_below_willingness" in codes(flags)) is fires


@pytest.mark.parametrize("band,fires", [(cfg.WIDE_BAND, True), (cfg.WIDE_BAND - 0.1, False)])
@pytest.mark.parametrize("where", ["willingness", "ability", "need"])
def test_wide_band(band, fires, where):
    people = [person("a", "Dana", 5.0, band if where == "willingness" else 1.0)]
    ability = est(5.0, band) if where == "ability" else None
    need = est(5.0, band) if where == "need" else None
    flags = pf.derive_flags(people, None, ability, need, None)
    assert ("wide_band" in codes(flags)) is fires
    if fires:
        assert flags[0]["refs"]["dimensions"] == [where]


def test_flags_come_through_compute_profile():
    inputs = [row("w_reaction", 0, DANA), row("w_reaction", 4, MARCUS),
              row("n_required_return", 0.10)]
    out = pf.compute_profile(inputs, [DANA, MARCUS], portfolio_risk=8)
    assert set(codes(out["flags"])) >= {"partner_gap", "need_exceeds_ceiling",
                                        "portfolio_exceeds_ceiling"}
    assert out["portfolio_risk"] == 8.0
    assert "flags to review" in out["plain_summary"]


def test_portfolio_risk_must_be_on_the_scale():
    with pytest.raises(ValueError):
        pf.compute_profile([], [DANA], portfolio_risk=11)
