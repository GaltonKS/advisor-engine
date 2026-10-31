"""Chart specs (charts.py) and the show_chart tool: every plotted figure is copied from
engine output, and the model can choose a chart kind but never supply content."""

import json
from pathlib import Path

import numpy as np
import pytest

from advisor_engine import __version__, charts
from advisor_engine.assistant.tools import engine_profile, execute_tool
from advisor_engine.compare import compare_profiles
from advisor_engine.holdings import load_holdings
from advisor_engine.risk import RISK_SOURCE_NAMES, risk_sources

REPO = Path(__file__).resolve().parents[1]
SAMPLE = str(REPO / "data" / "sample" / "sample_holdings.csv")


@pytest.fixture(scope="module")
def profile():
    return engine_profile(load_holdings(SAMPLE))


def chart(**kw):
    res = execute_tool("show_chart", {"holdings_path": SAMPLE, **kw})
    return res, (None if res.is_error else json.loads(res.text)["chart"])


# --- risk sources -----------------------------------------------------------------------

def test_risk_sources_are_four_sorted_buckets_summing_to_one():
    out = risk_sources(np.array([0.05, 0.6, 0.0, 0.2, 0.1, 0.05]))
    assert [s["name"] for s in out] == list(RISK_SOURCE_NAMES)
    assert [s["share"] for s in out] == pytest.approx([0.6, 0.2, 0.1, 0.1])


def test_few_holdings_have_no_empty_bucket():
    out = risk_sources(np.array([0.7, 0.3]))
    assert [s["name"] for s in out] == list(RISK_SOURCE_NAMES[:2])


def test_profile_carries_risk_sources(profile):
    src = profile["concentration"]["risk_sources"]
    assert sum(s["share"] for s in src) == pytest.approx(1)
    assert [s["share"] for s in src][:3] == sorted([s["share"] for s in src][:3], reverse=True)


# --- specs ------------------------------------------------------------------------------

def test_risk_by_group_copies_engine_figures(profile):
    spec = charts.risk_by_group(profile, "sector", "abc")
    assert [r["label"] for r in spec["rows"]] == list(profile["sector_risk"])
    for r in spec["rows"]:
        g = profile["sector_risk"][r["label"]]
        assert (r["weight"], r["risk_share"], r["risk_to_weight"]) == (g["weight"], g["risk_share"], g["risk_to_weight"])
    top = max(max(r["weight"], r["risk_share"]) for r in spec["rows"])
    assert top <= spec["axis_max"] < top + 0.05 + 1e-9
    assert spec["source"] == {"label": "Risk decomposition", "n_periods": profile["n_periods"],
                              "period_label": "monthly", "returns_source": "synthetic",
                              "engine_version": __version__, "holdings_sha256": "abc"}


def test_risk_by_group_rejects_unknown_group(profile):
    with pytest.raises(ValueError):
        charts.risk_by_group(profile, "country")


def test_unclassified_sector_gets_a_note():
    df = load_holdings(b"ticker,shares,price,sector\nAAA,10,10,Energy\nBBB,10,10,\n")
    spec = charts.risk_by_group(engine_profile(df), "sector")
    assert "Unclassified" in spec["note"]


def test_risk_vs_size_highlight_and_label_cap(profile):
    spec = charts.risk_vs_size(profile)
    assert len(spec["points"]) == len(profile["positions"])
    for p in spec["points"]:
        assert p["highlight"] == (p["risk_to_weight"] >= charts.HIGHLIGHT_RATIO)
    assert sum(p["label"] for p in spec["points"]) <= charts.MAX_POINT_LABELS
    assert all(p["label"] for p in spec["points"] if p["highlight"])


def test_what_if_measures_are_the_comparison_rows(profile):
    from advisor_engine.assistant.tools import apply_changes
    df = load_holdings(SAMPLE)
    after = engine_profile(apply_changes(df, {"remove": [df["ticker"][0]]}))
    comp = compare_profiles(profile, after)
    spec = charts.what_if(profile, after, comp, "without X")
    rows = {r["key"]: r for r in comp["headline"]}
    for m in spec["measures"]:
        assert (m["current"], m["option"], m["kind"]) == (rows[m["key"]]["before"], rows[m["key"]]["after"], rows[m["key"]]["kind"])
    assert [m["key"] for m in spec["measures"]] == ["period_volatility", "effective_bets", "stress_loss_1_in_100"]
    assert spec["mix"][0]["sources"] == profile["concentration"]["risk_sources"]
    assert spec["mix"][1] == {"label": "Without X", "sources": after["concentration"]["risk_sources"]}


# --- the tool ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,expected", [("risk_by_sector", "risk_by_group"),
                                           ("risk_by_asset_class", "risk_by_group"),
                                           ("risk_vs_size", "risk_vs_size")])
def test_show_chart_kinds(kind, expected):
    res, spec = chart(kind=kind)
    assert spec["kind"] == expected and spec["source"]["holdings_sha256"] == res.holdings_sha256


def test_what_if_label_comes_from_the_changes_not_the_model():
    _, spec = chart(kind="what_if", remove=["aaa"], option_label="Returns 47% a year")
    assert spec["option_label"] == "Without AAA"
    assert all("47" not in spec[k] for k in ("title", "subtitle", "option_label"))


@pytest.mark.parametrize("kw,msg", [({"kind": "pie"}, "Chart kind"),
                                    ({"kind": "what_if"}, "needs a change"),
                                    ({"kind": "what_if", "remove": ["NOPE"]}, "Not in the holdings")])
def test_show_chart_errors(kw, msg):
    res, _ = chart(**kw)
    assert res.is_error and msg in res.text


def test_show_chart_keeps_the_path_guard():
    res = execute_tool("show_chart", {"holdings_path": "/etc/passwd", "kind": "risk_vs_size"})
    assert res.is_error and "inside" in res.text
