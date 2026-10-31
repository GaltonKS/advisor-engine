"""AI-suggested stress scenarios: the AI suggests shocks (assumptions), the adviser
reviews them, and the engine computes the result. Nothing here calls the real API."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from advisor_engine.assistant.scenario import (WITHHELD, MockSuggester, output_schema,
                                               portfolio_targets, suggest_shocks)
from advisor_engine.assistant.tools import execute_tool
from advisor_engine.holdings import load_holdings

REPO = Path(__file__).resolve().parents[1]
MIXED = load_holdings(b"ticker,shares,price,sector,asset class\n"
                      b"AAA,100,50,Technology,Equity\nBBB,100,50,Energy,Equity\n"
                      b"LQD,100,100,Corporate Bonds,Fixed Income\nCASH,500,1,Cash,Cash\n"
                      b"ZZZ,10,10,,Equity\n")


class FakeAnthropic:
    """Returns one scripted structured-output reply and records the request."""

    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, model=kw["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def payload(**over):
    base = {"label": "Chip shock",
            "sector_shocks": [{"sector": "Technology", "shock": -0.25, "reason": "Chip supply falls; -25% for Technology."}],
            "asset_class_shocks": [{"asset_class": "fixed_income", "shock": 0.02, "reason": "Flight to safety, +2%."}],
            "default_shock": -0.05, "default_reason": "Everything else -5%.",
            "rationale": "An illustrative supply shock to review, not a forecast."}
    return {**base, **over}


def test_targets_are_the_portfolios_own_sectors_and_classes():
    sectors, classes = portfolio_targets(MIXED)
    assert sectors == ["Cash", "Corporate Bonds", "Energy", "Technology"]  # no Unclassified
    assert classes == ["cash", "fixed_income"]


def test_schema_limits_targets_and_needs_no_open_objects():
    schema = output_schema(["Energy"], ["cash"])
    items = schema["properties"]["sector_shocks"]["items"]
    assert items["properties"]["sector"]["enum"] == ["Energy"]
    assert schema["additionalProperties"] is False and items["additionalProperties"] is False


def test_live_request_uses_structured_output_and_fallbacks():
    fake = FakeAnthropic(payload())
    s = suggest_shocks("China blockades Taiwan", MIXED, fake, model="m")
    kw = fake.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and "tool_choice" not in kw
    assert "China blockades Taiwan" in kw["messages"][0]["content"]
    assert s["sector_shocks"] == {"Technology": -0.25} and s["asset_class_shocks"] == {"fixed_income": 0.02}
    assert s["default_shock"] == -0.05 and s["source"] == "ai" and not s["explanation_withheld"]


def test_explanation_quoting_other_figures_is_withheld_but_shocks_kept():
    fake = FakeAnthropic(payload(rationale="This would cost the portfolio about 13.7%."))
    s = suggest_shocks("chips", MIXED, fake, model="m")
    assert s["explanation_withheld"] and s["rationale"] == WITHHELD
    assert all(r["reason"] == "" for r in s["reasons"])
    assert s["sector_shocks"] == {"Technology": -0.25} and "13.7%" in s["unmatched"]


def test_off_list_targets_are_dropped_and_out_of_range_rejected():
    s = suggest_shocks("x", MIXED, FakeAnthropic(payload(sector_shocks=[
        {"sector": "Utilities", "shock": -0.1, "reason": "-10%."}])), model="m")
    assert s["sector_shocks"] == {}
    with pytest.raises(ValueError, match="outside"):
        suggest_shocks("x", MIXED, FakeAnthropic(payload(default_shock=-1.5)), model="m")


def test_refusal_and_empty_description():
    with pytest.raises(ValueError, match="declined"):
        suggest_shocks("x", MIXED, FakeAnthropic(payload(), stop_reason="refusal"), model="m")
    with pytest.raises(ValueError, match="Describe"):
        suggest_shocks("   ", MIXED, MockSuggester())


@pytest.mark.parametrize("text,label", [("The Fed hikes rates again", "Rising rates"),
                                        ("A deep recession", "Recession"),
                                        ("Taiwan chip blockade", "Technology sell-off"),
                                        ("OPEC cuts oil supply", "Oil price spike"),
                                        ("Something unrelated", "Generic market fall")])
def test_mock_suggester_shapes_pass_the_explanation_check(text, label):
    s = suggest_shocks(text, MIXED, MockSuggester())
    assert s["label"] == label and s["source"] == "mock" and not s["explanation_withheld"]
    assert set(s["sector_shocks"]) <= {"Technology", "Energy", "Corporate Bonds", "Cash"}


def test_referral_tool_returns_no_figures_and_needs_no_file():
    res = execute_tool("refer_to_stress_test", {"closest_preset": "recession"})
    d = json.loads(res.text)
    assert not res.is_error and d["closest_preset_label"] == "Recession"
    assert not any(ch.isdigit() for ch in res.text)
    assert json.loads(execute_tool("refer_to_stress_test", {"closest_preset": "none"}).text)["closest_preset"] is None
