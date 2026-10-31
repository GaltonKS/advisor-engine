"""Mock LLM tests: offline, but through the real tool -> number check -> audit pipeline."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from advisor_engine.assistant import chat
from advisor_engine.assistant.mock_client import MockClient

REPO = Path(__file__).resolve().parents[1]
SAMPLE = str(REPO / "data" / "sample" / "sample_holdings.csv")

QUESTIONS = {
    "overview": "How risky is this portfolio?",
    "position": "Why is BBB a concern?",
    "position_no_ticker": "Why does it matter?",
    "sectors": "What is my sector exposure?",
    "drawdown": "What is the worst drawdown?",
    "loss": "How big could a loss get?",
    "client": "Explain this for my client",
}


def turn(question, tmp_path, client=None, **kw):
    return chat.run_turn(question, [], client or MockClient(),
                         audit_path=tmp_path / "audit.jsonl", **kw)


def audit_lines(tmp_path):
    return [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("question", QUESTIONS.values(), ids=QUESTIONS.keys())
def test_template_passes_verification_and_is_audited(question, tmp_path):
    r = turn(question, tmp_path)
    assert not r.blocked, r.reply
    assert r.verification["ok"] and r.verification["checked"] > 0
    assert r.tool_calls[0]["name"] == "get_risk_profile"
    (rec,) = audit_lines(tmp_path)
    assert rec["model"] == "mock" and rec["blocked"] is False
    assert rec["tool_calls"][0]["output"] and rec["holdings_sha256"]


def test_fallback_lists_what_it_can_answer(tmp_path):
    r = turn("What's the weather?", tmp_path)
    assert not r.blocked
    for topic in ("overview", "sector", "drawdown", "client-ready"):
        assert topic in r.reply.lower()


def test_keyword_routing(tmp_path):
    reply = {k: turn(q, tmp_path).reply for k, q in QUESTIONS.items()}
    assert "BBB (Technology)" in reply["position"]
    assert "didn't name a position" in reply["position_no_ticker"]
    assert "largest sector" in reply["sectors"]
    assert "peak-to-trough" in reply["drawdown"] and "monthly" in reply["drawdown"]
    assert reply["loss"] == reply["drawdown"]
    assert reply["client"].startswith("DRAFT for advisor review")
    assert "more detail" in reply["overview"]


def test_no_internal_field_names(tmp_path):
    names = ("risk_share", "risk_to_weight", "avg_corr_to_rest", "top_component_share",
             "historical_max_drawdown", "effective_bets", "returns_source")
    for q in QUESTIONS.values():
        reply = turn(q, tmp_path).reply
        assert not [n for n in names if n in reply]


def test_tone_and_objective_come_from_the_system_prompt(tmp_path):
    plain = turn(QUESTIONS["overview"], tmp_path).reply
    prof = {"objective": "retirement income", "tone": "plain and concise"}
    shaped = turn(QUESTIONS["overview"], tmp_path, firm_profile=prof).reply
    assert "retirement income" in shaped and "illustrative" not in shaped
    assert "illustrative" in plain


def test_tool_error_reply_is_verified(tmp_path):
    r = turn("risk?", tmp_path, default_holdings=str(tmp_path / "missing.csv"))
    assert not r.blocked and "couldn't get a risk profile" in r.reply


def test_templates_verify_on_varied_portfolios(tmp_path):
    """Formatting must survive the number check whatever the figures turn out to be."""
    data = tmp_path / "data"
    data.mkdir()
    rng = np.random.default_rng(7)
    sectors = ["Tech", "Energy", "Health Care", "Utilities", "Financials"]
    for k in range(8):
        n = int(rng.integers(3, 16))
        rows = ["ticker,shares,price,sector"]
        for i in range(n):
            rows.append(f"T{k}X{i:02d},{int(rng.integers(10, 3000))},"
                        f"{rng.uniform(5, 900):.2f},{sectors[int(rng.integers(0, 5))]}")
        path = data / f"p{k}.csv"
        path.write_text("\n".join(rows))
        for q in QUESTIONS.values():
            r = chat.run_turn(q, [], MockClient(), default_holdings=str(path), data_dir=data,
                              audit_path=tmp_path / "a.jsonl", max_retries=0)
            assert not r.blocked, (path.name, q, r.reply)


@pytest.mark.parametrize("csv", [
    "ticker,shares,price,sector\nAAA,100,50,Technology\nBBB,100,50,Energy\nCCC,300,40,\n",
    "ticker,shares,price\nAAA,100,50\nBBB,100,50\nCCC,300,40\n",
], ids=["some_missing", "no_sector_column"])
def test_templates_handle_holdings_without_sector(csv, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "h.csv"
    path.write_text(csv)
    for k, q in QUESTIONS.items():
        r = chat.run_turn(q, [], MockClient(), default_holdings=str(path), data_dir=data,
                          audit_path=tmp_path / "a.jsonl", max_retries=0)
        assert not r.blocked, (k, r.reply)
        assert "Unclassified is the largest" not in r.reply and "money is in Unclassified" not in r.reply
        if k in ("overview", "sectors"):
            assert "no sector" in r.reply and "CCC" in r.reply, r.reply


def test_overview_states_the_asset_class_mix(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "h.csv"
    path.write_text("ticker,shares,price,sector,asset class\nAAA,100,50,Energy,Equity\n"
                    "BBB,100,50,Bonds,Fixed Income\nCCC,500,1,Cash,Cash\n")
    r = chat.run_turn("How risky is this portfolio?", [], MockClient(), default_holdings=str(path),
                      data_dir=data, audit_path=tmp_path / "a.jsonl", max_retries=0)
    assert not r.blocked, r.reply
    assert "By asset class it is" in r.reply and "bonds" in r.reply and "cash" in r.reply


@pytest.mark.parametrize("question,tools,phrase", [
    ("How correlated is this portfolio with the market?", ["get_risk_profile"], "correlation with the market"),
    ("What if we sold AAA and BBB?", ["get_risk_profile", "run_what_if", "show_chart"], "taken out"),
    ("What happens in a recession?", ["get_risk_profile", "run_stress_test", "refer_to_stress_test"],
     "recession scenario"),
    ("How much is the portfolio worth?", ["get_risk_profile", "get_holdings"], "worth"),
    ("What is the stress loss in a severe period?", ["get_risk_profile"], "1-in-100"),
])
def test_new_tools_in_mock_mode_pass_verification(question, tools, phrase, tmp_path):
    r = turn(question, tmp_path)
    assert not r.blocked, r.reply
    assert [c["name"] for c in r.tool_calls] == tools
    assert phrase in r.reply and r.verification["checked"] > 0


@pytest.mark.parametrize("question,kind", [
    ("What is my sector exposure?", "risk_by_group"),
    ("How much risk is in bonds?", "risk_by_group"),
    ("Show me what drives the risk", "risk_vs_size"),
    ("What if we sold AAA?", "what_if"),
])
def test_mock_shows_a_chart_and_points_to_it(question, kind, tmp_path):
    r = turn(question, tmp_path)
    assert not r.blocked, r.reply
    chart = json.loads(r.tool_calls[-1]["output"])["chart"]
    assert r.tool_calls[-1]["name"] == "show_chart" and chart["kind"] == kind
    assert "The chart below shows" in r.reply


def test_hypothetical_event_is_referred_to_the_stress_tab(tmp_path):
    r = turn("What if China invades Taiwan?", tmp_path)
    assert not r.blocked, r.reply
    assert [c["name"] for c in r.tool_calls] == ["get_risk_profile", "refer_to_stress_test"]
    assert "doesn't forecast" in r.reply and "Stress test tab" in r.reply


def test_force_bad_number_is_blocked_and_logged(tmp_path):
    r = turn("How risky is this?", tmp_path, MockClient(force_bad_number=True))
    assert r.blocked and r.block_reason == "unmatched_numbers"
    assert r.unmatched and r.unmatched[0] in r.reply
    (rec,) = audit_lines(tmp_path)
    assert rec["blocked"] is True and rec["verification"]["unmatched"] == r.unmatched
    assert len(rec["retries"]) == 1  # the one automatic retry also failed
    assert r.unmatched[0] in rec["reply"]


def test_mock_mode_needs_no_network_or_anthropic(tmp_path):
    code = textwrap.dedent(f"""
        import socket, sys
        sys.modules["anthropic"] = None            # any import of it now raises
        def no_net(*a, **k): raise AssertionError("network used")
        socket.socket.connect = no_net
        from advisor_engine.assistant import chat
        r = chat.run_turn("How risky is this?", [], audit_path={str(tmp_path / "a.jsonl")!r})
        assert not r.blocked and r.verification["ok"]
        print("OK")
    """)
    env = {"PATH": "", "ADVISOR_LLM": "mock", "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=tmp_path)
    assert out.stdout.strip() == "OK", out.stderr


# --- mode selection ----------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(chat, "_load_env", lambda: None)  # ignore the repo's .env
    monkeypatch.delenv("ADVISOR_LLM", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return monkeypatch


def test_mode_defaults_to_mock_without_key(env):
    assert chat.llm_mode() == "mock"
    assert isinstance(chat._make_client(), MockClient)


def test_mode_defaults_to_anthropic_with_key(env):
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert chat.llm_mode() == "anthropic"


def test_adviser_llm_overrides_key(env):
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    env.setenv("ADVISOR_LLM", "MOCK")
    assert chat.llm_mode() == "mock"
    env.setenv("ADVISOR_LLM", "anthropic")
    env.delenv("ANTHROPIC_API_KEY")
    assert chat.llm_mode() == "anthropic"


def test_bad_mode_value_is_rejected(env):
    env.setenv("ADVISOR_LLM", "gpt")
    with pytest.raises(ValueError):
        chat.llm_mode()
