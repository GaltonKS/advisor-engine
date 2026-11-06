"""Household risk profile engine (profile.py): estimator, household rules, flags.

The numbers come from profile_config and are DRAFT placeholders; these tests pin the
method's behaviour (and the mockup anchor), not the placeholders' exact outputs, except
where a test needs a known reading.
"""

import json
import math

import pytest

from advisor_engine import profile, profile_config as cfg

DANA = {"id": "p-dana", "name": "Dana", "role": "primary"}
MARCUS = {"id": "p-marcus", "name": "Marcus", "role": "partner"}

_t = iter(range(1, 10_000))


def row(question_id, answer, person=None, at=None):
    q = profile.question(question_id)
    return {"person_id": person["id"] if person else None, "dimension": q["dimension"],
            "question_id": question_id, "answer": answer,
            "recorded_at": at if at is not None else f"2026-11-06T10:{next(_t):05d}"}


def loss(act, unc=None):
    return {"uncomfortable_pct": act if unc is None else unc, "act_pct": act}


def full_household():
    return [
        row("w_reaction", "hold", DANA), row("w_loss_limits", loss(0.20, 0.10), DANA),
        row("w_reaction", "sell_some", MARCUS), row("w_loss_limits", loss(0.15, 0.10), MARCUS),
        row("a_income_share", "some"), row("a_horizon_years", 12),
        row("n_required_return", 0.05),
    ]


# --- basics -------------------------------------------------------------------------------

def test_no_answers_gives_nones_and_provisional():
    out = profile.compute_profile([], [DANA, MARCUS])
    assert out["people"][0]["willingness"] is None and out["people"][1]["willingness"] is None
    for k in ("household_willingness", "ability", "need", "ceiling"):
        assert out[k] is None
    assert out["provisional"] is True
    assert out["flags"] == []
    assert out["plain_summary"].endswith(cfg.METHOD_STATUS)
    assert out["method_version"] == "draft-2" == cfg.METHOD_VERSION
    assert "pending compliance review" in out["method_status"]


def test_no_people_is_provisional():
    out = profile.compute_profile([row("a_income_share", "some")], [])
    assert out["provisional"] is True and out["ceiling"] is None and out["ability"] is not None


def test_deterministic_and_json_serialisable():
    inputs = full_household()
    a = profile.compute_profile(inputs, [DANA, MARCUS], portfolio_risk=7.0)
    b = profile.compute_profile(list(reversed(inputs)), [MARCUS, DANA], portfolio_risk=7.0)
    assert a == b
    assert json.loads(json.dumps(a)) == a
    assert profile.inputs_hash(inputs) == profile.inputs_hash(list(reversed(inputs)))


def test_mockup_anchor_required_return():
    assert profile.score_answer("n_required_return", 0.058) == pytest.approx(6.1)
    out = profile.compute_profile([row("n_required_return", 0.058)], [DANA])
    assert round(out["need"]["mean"], 1) == 6.1


def test_mapping_tables_are_monotone_and_on_scale():
    for table in (cfg.ACT_PCT_TABLE, cfg.REQUIRED_RETURN_TABLE, cfg.HORIZON_TABLE):
        xs, ys = zip(*table)
        assert list(xs) == sorted(xs) and list(ys) == sorted(ys)
        assert cfg.SCALE_MIN <= min(ys) and max(ys) <= cfg.SCALE_MAX
    for q in cfg.QUESTIONS:
        assert q["dimension"] in cfg.DIMENSIONS and q["noise_sd"] > 0
        for o in q.get("options", []):
            assert cfg.SCALE_MIN <= o["value"] <= cfg.SCALE_MAX


def test_interpolation_clamps_and_is_linear_between_points():
    t = [(0.0, 1.0), (1.0, 3.0)]
    assert profile.interpolate(t, -5) == 1.0
    assert profile.interpolate(t, 5) == 3.0
    assert profile.interpolate(t, 0.25) == pytest.approx(1.5)


# --- the estimator ---------------------------------------------------------------------------

def test_formula_matches_the_docstring():
    est = profile.estimate([(4.0, 1.0), (6.0, 2.0)])
    w1, w2 = 1.0, 0.25
    mean = (w1 * 4 + w2 * 6) / (w1 + w2)
    spread = (w1 * (4 - mean) ** 2 + w2 * (6 - mean) ** 2) / (w1 + w2)
    assert est["mean"] == pytest.approx(mean)
    assert est["band"] == pytest.approx(math.sqrt(1 / (w1 + w2) + spread))
    assert profile.estimate([]) is None


def test_more_agreeing_answers_narrow_the_band():
    one = profile.compute_profile([row("w_reaction", "hold", DANA)], [DANA])
    two = profile.compute_profile([row("w_reaction", "hold", DANA),
                                   row("w_loss_limits", loss(0.175), DANA)], [DANA])
    # 0.175 maps to 5.5 on the draft table, the same reading as "hold".
    assert profile.score_answer("w_loss_limits", loss(0.175)) == pytest.approx(5.5)
    assert two["people"][0]["willingness"]["band"] < one["people"][0]["willingness"]["band"]
    assert two["people"][0]["willingness"]["n_answers"] == 2


def test_disagreeing_answers_widen_the_band():
    agree = profile.compute_profile([row("w_reaction", "hold", DANA),
                                     row("w_loss_limits", loss(0.175), DANA)], [DANA])
    disagree = profile.compute_profile([row("w_reaction", "buy_lot", DANA),
                                        row("w_loss_limits", loss(0.05), DANA)], [DANA])
    assert (disagree["people"][0]["willingness"]["band"]
            > agree["people"][0]["willingness"]["band"])


# --- resolving inputs ------------------------------------------------------------------------

def test_latest_answer_wins():
    inputs = [row("w_reaction", "buy_lot", DANA, at="2026-11-06T10:00:02"),
              row("w_reaction", "sell_all", DANA, at="2026-11-06T10:00:01")]
    out = profile.compute_profile(inputs, [DANA])
    assert out["people"][0]["willingness"]["mean"] == 9.5
    assert out["people"][0]["willingness"]["n_answers"] == 1


def test_retracted_answers_are_ignored():
    inputs = [row("w_reaction", "buy_lot", DANA), row("w_reaction", None, DANA),
              row("a_income_share", "none")]
    out = profile.compute_profile(inputs, [DANA])
    assert out["people"][0]["willingness"] is None and out["ceiling"] is None
    assert profile.inputs_hash(inputs) == profile.inputs_hash([row("a_income_share", "none")])
    # Re-answering after a retraction counts again.
    out = profile.compute_profile(inputs + [row("w_reaction", "hold", DANA)], [DANA])
    assert out["people"][0]["willingness"]["mean"] == 5.5


def test_same_question_is_separate_per_person():
    out = profile.compute_profile([row("w_reaction", "hold", DANA),
                                   row("w_reaction", "sell_all", MARCUS)], [DANA, MARCUS])
    assert [p["willingness"]["mean"] for p in out["people"]] == [5.5, 1.0]


def test_inputs_hash_changes_with_answers():
    base = full_household()
    assert profile.inputs_hash(base) != profile.inputs_hash(base + [row("a_cash_months", "3_6")])
    assert len(profile.inputs_hash(base)) == 64


# --- validation ------------------------------------------------------------------------------

def test_act_below_uncomfortable_is_rejected():
    with pytest.raises(ValueError, match="act_pct must be at least"):
        profile.validate_answer("w_loss_limits", {"uncomfortable_pct": 0.2, "act_pct": 0.1})
    profile.validate_answer("w_loss_limits", {"uncomfortable_pct": 0.2, "act_pct": 0.2})


@pytest.mark.parametrize("qid,answer", [
    ("w_reaction", "panic"), ("w_reaction", 2), ("a_horizon_years", -1),
    ("a_horizon_years", True), ("a_horizon_years", "10"), ("n_required_return", 0.5),
    ("n_required_return", float("nan")), ("w_loss_limits", {"act_pct": 0.1}),
    ("w_loss_limits", {"uncomfortable_pct": 0.1, "act_pct": 0.6}),
    ("w_loss_limits", [0.1, 0.2]), ("no_such_question", 1),
])
def test_invalid_answers_are_rejected(qid, answer):
    with pytest.raises(ValueError):
        profile.validate_answer(qid, answer)


def test_retraction_is_always_valid():
    for q in cfg.QUESTIONS:
        profile.validate_answer(q["id"], None)


def test_compute_rejects_bad_rows_and_bad_portfolio_reading():
    with pytest.raises(ValueError, match="unknown person"):
        profile.compute_profile([row("w_reaction", "hold", MARCUS)], [DANA])
    bad = {**row("a_income_share", "some"), "dimension": "need"}
    with pytest.raises(ValueError, match="ability question"):
        profile.compute_profile([bad], [DANA])
    with pytest.raises(ValueError, match="portfolio_risk"):
        profile.compute_profile([], [DANA], portfolio_risk=11)


# --- household rules -------------------------------------------------------------------------

def test_household_willingness_is_the_lower_partner():
    inputs = [row("w_reaction", "buy_little", DANA), row("w_reaction", "sell_some", MARCUS)]
    out = profile.compute_profile(inputs, [DANA, MARCUS])
    assert out["household_willingness"]["mean"] == 3.0
    assert out["household_willingness"]["person_id"] == MARCUS["id"]
    assert "Marcus's" in out["plain_summary"]


def test_household_willingness_ignores_unanswered_partner():
    out = profile.compute_profile([row("w_reaction", "buy_little", DANA)], [DANA, MARCUS])
    assert out["household_willingness"]["mean"] == 7.5
    assert out["provisional"] is True


def test_ceiling_is_the_min_of_willingness_and_ability():
    w = [row("w_reaction", "buy_little", DANA)]  # 7.5
    low_ability = profile.compute_profile(w + [row("a_income_share", "large")], [DANA])  # 3.5
    assert low_ability["ceiling"]["value"] == 3.5
    assert low_ability["ceiling"]["limited_by"] == "ability"
    high_ability = profile.compute_profile(w + [row("a_income_share", "none")], [DANA])  # 9.0
    assert high_ability["ceiling"]["value"] == 7.5
    assert high_ability["ceiling"]["limited_by"] == "willingness"
    no_ability = profile.compute_profile(w, [DANA])
    assert no_ability["ceiling"]["value"] == 7.5


def test_ceiling_needs_a_willingness_answer():
    out = profile.compute_profile([row("a_income_share", "none"), row("n_required_return", 0.05)],
                                  [DANA])
    assert out["ceiling"] is None and out["ability"]["mean"] == 9.0


def test_provisional_until_the_draft_minimums_are_met():
    full = full_household()
    assert profile.compute_profile(full, [DANA, MARCUS])["provisional"] is False
    # Each person needs two willingness answers; ability and need one each.
    for drop in ({0}, {1}, {2}, {3}, {4, 5}, {6}):
        partial = [r for i, r in enumerate(full) if i not in drop]
        assert profile.compute_profile(partial, [DANA, MARCUS])["provisional"] is True, drop
    assert profile.compute_profile(full[:5] + full[6:], [DANA, MARCUS])["provisional"] is False
    assert "Provisional" not in profile.compute_profile(full, [DANA, MARCUS])["plain_summary"]


# --- flags: each fires at its threshold and not just below -----------------------------------

def codes(out):
    return {f["code"] for f in out["flags"]}


def willingness_at(person, score):
    """One willingness answer per person reading exactly `score` (via the loss table)."""
    for (x0, y0), (x1, y1) in zip(cfg.ACT_PCT_TABLE, cfg.ACT_PCT_TABLE[1:]):
        if y0 <= score <= y1:
            act = x0 + (score - y0) * (x1 - x0) / (y1 - y0)
            return row("w_loss_limits", loss(act), person)
    raise AssertionError(score)


def need_at(score):
    t = cfg.REQUIRED_RETURN_TABLE
    for (x0, y0), (x1, y1) in zip(t, t[1:]):
        if y0 <= score <= y1:
            return row("n_required_return", x0 + (score - y0) * (x1 - x0) / (y1 - y0))
    raise AssertionError(score)


def ability_at(score):
    t = cfg.HORIZON_TABLE
    for (x0, y0), (x1, y1) in zip(t, t[1:]):
        if y0 <= score <= y1:
            return row("a_horizon_years", x0 + (score - y0) * (x1 - x0) / (y1 - y0))
    raise AssertionError(score)


@pytest.mark.parametrize("gap,fires", [(cfg.PARTNER_GAP, True), (cfg.PARTNER_GAP - 0.01, False)])
def test_partner_gap(gap, fires):
    out = profile.compute_profile([willingness_at(DANA, 7.0), willingness_at(MARCUS, 7.0 - gap)],
                                  [DANA, MARCUS])
    assert ("partner_gap" in codes(out)) is fires
    if fires:
        f = next(f for f in out["flags"] if f["code"] == "partner_gap")
        assert f["severity"] == "warn" and f["refs"]["person_ids"] == [DANA["id"], MARCUS["id"]]
        assert "Dana and Marcus" in f["message"] and "2.0" in f["message"]


@pytest.mark.parametrize("over,fires", [(cfg.NEED_OVER_CEILING, True),
                                        (cfg.NEED_OVER_CEILING - 0.01, False)])
def test_need_exceeds_ceiling(over, fires):
    out = profile.compute_profile([willingness_at(DANA, 5.0), need_at(5.0 + over)], [DANA])
    assert ("need_exceeds_ceiling" in codes(out)) is fires


@pytest.mark.parametrize("over,fires", [(cfg.PORTFOLIO_OVER_CEILING, True),
                                        (cfg.PORTFOLIO_OVER_CEILING - 0.01, False)])
def test_portfolio_exceeds_ceiling(over, fires):
    out = profile.compute_profile([willingness_at(DANA, 5.0)], [DANA], portfolio_risk=5.0 + over)
    assert ("portfolio_exceeds_ceiling" in codes(out)) is fires


def test_portfolio_flag_needs_a_reading():
    out = profile.compute_profile([willingness_at(DANA, 2.0)], [DANA])
    assert "portfolio_exceeds_ceiling" not in codes(out) and out["portfolio_risk"] is None


@pytest.mark.parametrize("below,fires", [(cfg.ABILITY_BELOW_WILLINGNESS, True),
                                         (cfg.ABILITY_BELOW_WILLINGNESS - 0.01, False)])
def test_ability_below_willingness(below, fires):
    out = profile.compute_profile([willingness_at(DANA, 7.0), ability_at(7.0 - below)], [DANA])
    assert ("ability_below_willingness" in codes(out)) is fires


def test_wide_band():
    # One answer's band is its noise_sd: experience alone (3.0) is wide, reaction alone
    # (2.0) sits exactly at the threshold, which is not "wider than".
    assert cfg.WIDE_BAND == 2.0
    wide = profile.compute_profile([row("w_experience", "good", DANA)], [DANA])
    assert "wide_band" in codes(wide)
    f = next(f for f in wide["flags"] if f["code"] == "wide_band")
    assert f["refs"] == {"dimensions": ["willingness"], "person_ids": [DANA["id"]]}
    assert "Dana's willingness (±3.0)" in f["message"]
    at = profile.compute_profile([row("w_reaction", "hold", DANA)], [DANA])
    assert at["people"][0]["willingness"]["band"] == cfg.WIDE_BAND
    assert "wide_band" not in codes(at)
    just_over = profile.estimate([(5.0, cfg.WIDE_BAND + 0.01)])
    assert just_over["band"] > cfg.WIDE_BAND


def test_summary_is_a_template_with_engine_figures():
    out = profile.compute_profile(full_household(), [DANA, MARCUS])
    s = out["plain_summary"]
    assert s.startswith("Willingness: Dana ") and "Ceiling:" in s
    assert s.endswith(cfg.METHOD_STATUS)


def test_question_bank_is_plain_data():
    bank = profile.question_bank()
    assert [q["id"] for q in bank["questions"]] == [
        "w_reaction", "w_experience", "w_loss_limits", "a_income_share", "a_cash_months",
        "a_horizon_years", "n_required_return"]
    assert bank["method_version"] == cfg.METHOD_VERSION
    bank["questions"][0]["options"].clear()  # a copy: the config is untouched
    assert cfg.QUESTIONS[0]["options"]
