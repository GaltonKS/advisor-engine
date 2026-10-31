"""Assistant tests. None of these call the real API: the client is a scripted fake."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from advisor_engine import __version__
from advisor_engine.assistant import audit, chat, prompts
from advisor_engine.assistant.tools import TOOLS, execute_tool, resolve_holdings_path

REPO = Path(__file__).resolve().parents[1]
SAMPLE = str(REPO / "data" / "sample" / "sample_holdings.csv")


# --- fake client ---------------------------------------------------------------

def text_block(t):
    return SimpleNamespace(type="text", text=t)


def tool_block(i=1, path=SAMPLE):
    return SimpleNamespace(type="tool_use", id=f"tu_{i}", name="get_risk_profile",
                           input={"holdings_path": path})


class FakeClient:
    """Plays back scripted responses; `script` items are lists of blocks or callables."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self  # client.messages.create(...)

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        content = item(kw) if callable(item) else item
        return SimpleNamespace(content=content)


def profile():
    return json.loads(execute_tool("get_risk_profile", {"holdings_path": SAMPLE}).text)


def good_reply():
    p = profile()
    top = p["positions"][0]
    return (
        f"Effective bets: {p['concentration']['effective_bets']:.1f}. "
        f"{top['ticker']} contributes {top['risk_share']:.1%} of variance "
        f"({top['risk_to_weight']:.2f}x its weight)."
    )


def run(client, tmp_path, **kw):
    return chat.run_turn("Which positions add the most risk?", [], client, model="test-model",
                         audit_path=tmp_path / "audit.jsonl", **kw)


def audit_lines(tmp_path):
    return [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]


# --- tools -----------------------------------------------------------------------

def test_tool_definition_shape():
    t = TOOLS[0]
    assert t["name"] == "get_risk_profile"
    assert t["input_schema"]["required"] == ["holdings_path"]


def test_tool_returns_engine_profile_as_json():
    res = execute_tool("get_risk_profile", {"holdings_path": SAMPLE})
    assert not res.is_error
    data = json.loads(res.text)
    assert data["engine_version"] == __version__
    assert data["returns_source"] == "synthetic"
    assert res.holdings_sha256 == audit.file_sha256(SAMPLE)


def test_tool_accepts_repo_relative_path():
    assert not execute_tool("get_risk_profile",
                            {"holdings_path": "data/sample/sample_holdings.csv"}).is_error


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../pyproject.toml",
        "data/../pyproject.toml",
        "data/sample/../../pyproject.toml",
        "pyproject.toml",
        "",
    ],
)
def test_tool_rejects_paths_outside_data(path):
    res = execute_tool("get_risk_profile", {"holdings_path": path})
    assert res.is_error and res.text.startswith("Error")


def test_tool_rejects_symlink_escaping_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "secret.csv"
    outside.write_text("ticker,shares,price,sector\nA,1,1,X\n")
    (data / "link.csv").symlink_to(outside)
    with pytest.raises(ValueError, match="inside"):
        resolve_holdings_path(str(data / "link.csv"), data_dir=data)
    assert execute_tool("get_risk_profile", {"holdings_path": str(data / "link.csv")},
                        data_dir=data).is_error


def test_tool_rejects_audit_dir(tmp_path):
    data = tmp_path / "data"
    (data / "audit").mkdir(parents=True)
    (data / "audit" / "audit.csv").write_text("x")
    assert execute_tool("get_risk_profile", {"holdings_path": str(data / "audit" / "audit.csv")},
                        data_dir=data).is_error


def test_tool_bad_csv_returns_error_string(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cases = {
        "missing_cols.csv": "ticker,shares\nA,1\n",
        "negative.csv": "ticker,shares,price,sector\nA,-1,10,X\nB,1,10,X\n",
        "dupes.csv": "ticker,shares,price,sector\nA,1,10,X\nA,1,10,X\n",
        "empty.csv": "",
        "garbage.csv": "ticker,shares,price,sector\nA,lots,10,X\n",
    }
    for name, body in cases.items():
        (data / name).write_text(body)
        res = execute_tool("get_risk_profile", {"holdings_path": str(data / name)}, data_dir=data)
        assert res.is_error and isinstance(res.text, str) and res.text.startswith("Error"), name


def test_tool_missing_file_and_unknown_tool():
    assert execute_tool("get_risk_profile", {"holdings_path": "data/nope.csv"}).is_error
    assert execute_tool("delete_everything", {}).is_error
    assert execute_tool("get_risk_profile", None).is_error


def test_tool_engine_error_becomes_text():
    def short(h):
        from advisor_engine.synthetic import synthetic_returns
        return synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=10)

    res = execute_tool("get_risk_profile", {"holdings_path": SAMPLE}, returns_fn=short)
    assert res.is_error and "24 periods" in res.text


# --- chat -------------------------------------------------------------------------

def test_reply_quoting_only_tool_numbers_passes(tmp_path):
    reply = good_reply()
    client = FakeClient([[tool_block()], [text_block(reply)]])
    r = run(client, tmp_path)
    assert not r.blocked and r.reply == reply
    assert r.verification["ok"]
    (rec,) = audit_lines(tmp_path)
    assert rec["blocked"] is False and rec["reply"] == reply
    assert rec["tool_calls"][0]["name"] == "get_risk_profile"
    assert rec["holdings_sha256"] == audit.file_sha256(SAMPLE)
    # history carries the tool exchange and the answer, ending on the assistant
    assert r.history[-1]["role"] == "assistant"
    assert any(b.get("type") == "tool_result" for m in r.history if isinstance(m["content"], list)
               for b in m["content"])


def test_tool_result_is_sent_back_to_the_model(tmp_path):
    client = FakeClient([[tool_block()], [text_block("No figures.")]])
    run(client, tmp_path)
    last_user = client.calls[1]["messages"][-1]
    assert last_user["role"] == "user"
    assert last_user["content"][0]["type"] == "tool_result"
    assert last_user["content"][0]["tool_use_id"] == "tu_1"
    assert json.loads(last_user["content"][0]["content"])["engine_version"] == __version__
    assert client.calls[0]["tools"] == TOOLS


def test_invented_number_is_blocked_and_logged(tmp_path):
    bad = "Effective bets: 99.9, so you are very diversified."
    client = FakeClient([[tool_block()], [text_block(bad)]])
    r = run(client, tmp_path, max_retries=0)
    assert r.blocked and r.block_reason == "unmatched_numbers"
    assert "99.9" in r.reply and "withheld" in r.reply
    assert "very diversified" not in r.reply
    assert r.unmatched == ["99.9"]
    (rec,) = audit_lines(tmp_path)
    assert rec["blocked"] is True
    assert rec["reply"] == bad  # the audit keeps what the model said
    assert rec["verification"]["unmatched"] == ["99.9"]


def test_blocked_turn_is_not_kept_in_history(tmp_path):
    client = FakeClient([[tool_block()], [text_block("Bets: 99.9")]])
    r = run(client, tmp_path, max_retries=0)
    assert r.history == []


def test_retry_recovers_and_drops_the_bad_attempt_from_history(tmp_path):
    good = good_reply()
    client = FakeClient([[tool_block()], [text_block("Bets: 99.9")], [text_block(good)]])
    r = run(client, tmp_path)
    assert not r.blocked and r.reply == good
    # the retry message told the model which figure was bad
    assert "99.9" in client.calls[2]["messages"][-1]["content"]
    assert "99.9" not in json.dumps(r.history)
    assert [m["role"] for m in r.history] == ["user", "assistant", "user", "assistant"]
    (rec,) = audit_lines(tmp_path)
    assert rec["blocked"] is False
    assert rec["retries"][0]["unmatched"] == ["99.9"]


def test_retry_message_says_why_each_figure_failed():
    from advisor_engine.assistant.numcheck import verify
    tool = json.dumps({"change": -0.0064, "loss": -0.12})
    v = verify("Volatility changes by -0.006, a 12% gain, and 47.3% elsewhere.", [tool])
    msg = chat._retry_message(v)
    assert msg.startswith("Your answer contained figures")  # the mock relies on this prefix
    assert "-0.006 is rounded too far" in msg and "two significant digits" in msg
    assert "12% has the wrong direction" in msg
    assert "47.3% is not in any tool result" in msg


def test_prompt_formatting_rules_keep_detail():
    p = prompts.SYSTEM_PROMPT
    assert "short bold title on its own line" in p and "split long bullets rather than dropping detail" in p


def test_prompt_asks_for_two_significant_digits():
    assert "at least two significant digits" in prompts.SYSTEM_PROMPT


def test_retry_that_fails_again_is_blocked(tmp_path):
    client = FakeClient([[tool_block()], [text_block("Bets: 99.9")], [text_block("Bets: 88.8")]])
    r = run(client, tmp_path)
    assert r.blocked and r.unmatched == ["88.8"]
    assert len(client.calls) == 3


def test_tool_round_cap(tmp_path):
    client = FakeClient([[tool_block()]])  # asks for a tool forever
    r = run(client, tmp_path)
    assert r.blocked and r.block_reason == "tool_round_cap"
    (rec,) = audit_lines(tmp_path)
    assert len(rec["tool_calls"]) == chat.MAX_TOOL_ROUNDS == 5
    assert rec["blocked"] is True and rec["block_reason"] == "tool_round_cap"
    assert len(client.calls) == chat.MAX_TOOL_ROUNDS + 1


def test_numbers_from_earlier_turns_are_allowed(tmp_path):
    first = FakeClient([[tool_block()], [text_block(good_reply())]])
    r1 = run(first, tmp_path)
    p = profile()
    second = FakeClient([[text_block(f"As before, {p['concentration']['effective_bets']:.1f}.")]])
    r2 = chat.run_turn("Remind me?", r1.history, second, model="m", audit_path=tmp_path / "a.jsonl")
    assert not r2.blocked


def test_no_tool_call_and_a_number_is_blocked(tmp_path):
    client = FakeClient([[text_block("Your risk is 12%.")]])
    assert run(client, tmp_path, max_retries=0).blocked


def test_tool_error_numbers_may_be_quoted(tmp_path):
    def short(h):
        from advisor_engine.synthetic import synthetic_returns
        return synthetic_returns(list(h["ticker"]), list(h["sector"]), n_periods=10)

    client = FakeClient([[tool_block()], [text_block("The engine needs at least 24 periods.")]])
    r = run(client, tmp_path, returns_fn=short)
    assert not r.blocked


def test_system_prompt_and_default_holdings_are_sent(tmp_path):
    client = FakeClient([[text_block("Hello.")]])
    run(client, tmp_path, default_holdings=SAMPLE)
    system = client.calls[0]["system"]
    assert "never compute" in system.lower() and SAMPLE in system


def test_model_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ADVISOR_MODEL", "env-model")
    client = FakeClient([[text_block("Hi.")]])
    chat.run_turn("hi", [], client, audit_path=tmp_path / "a.jsonl")
    assert client.calls[0]["model"] == "env-model"


# --- firm profile -----------------------------------------------------------------

def test_firm_profile_defaults_to_none_and_leaves_prompt_alone():
    assert prompts.FIRM_PROFILE is None
    assert prompts.build_system_prompt(None) == prompts.SYSTEM_PROMPT


def test_firm_profile_objective_and_tone_reach_prompt_but_disclaimer_does_not():
    fp = {"objective": "retirement", "tone": "plain", "disclaimer": "DISCLAIMER-TEXT 12"}
    s = prompts.build_system_prompt(fp)
    assert "retirement" in s and "plain" in s and "DISCLAIMER-TEXT" not in s


def test_disclaimer_is_appended_verbatim_to_verified_reply_only(tmp_path):
    fp = {"disclaimer": "APPROVED TEXT 99"}  # its number must not trip the number check
    ok_client = FakeClient([[text_block("No figures.")]])
    r = run(ok_client, tmp_path, firm_profile=fp)
    assert r.reply == "No figures.\n\nAPPROVED TEXT 99"
    bad_client = FakeClient([[text_block("Bets 99.9")]])
    r = run(bad_client, tmp_path, firm_profile=fp, max_retries=0)
    assert r.blocked and "APPROVED TEXT" not in r.reply


# --- audit ------------------------------------------------------------------------

def test_audit_record_fields(tmp_path):
    path = tmp_path / "sub" / "audit.jsonl"  # directory is created
    audit.append_audit({"model": "m", "holdings_sha256": audit.file_sha256(SAMPLE),
                        "reply": "x", "blocked": False}, path)
    audit.append_audit({"model": "m", "blocked": True}, path)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["engine_version"] == __version__
    assert len(rec["holdings_sha256"]) == 64
    assert rec["timestamp"].endswith("+00:00")


def test_audit_never_contains_api_key(monkeypatch, tmp_path):
    key = "sk-ant-api03-SECRETSECRETSECRET"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    path = tmp_path / "audit.jsonl"
    audit.append_audit({"reply": f"leaked {key}", "tool_calls": [{"output": key}],
                        "note": "another sk-ant-zzzzzzzzzz"}, path)
    text = path.read_text()
    assert key not in text and "SECRETSECRET" not in text and "sk-ant-zzzz" not in text
    assert "[REDACTED]" in text


def test_default_audit_path_is_under_gitignored_data_dir():
    from advisor_engine.assistant._paths import AUDIT_PATH, DATA_DIR
    assert AUDIT_PATH.is_relative_to(DATA_DIR) and DATA_DIR.name == "data"
    ignore = (DATA_DIR.parent / ".gitignore").read_text()
    assert "data/*" in ignore and "!data/audit" not in ignore


def test_system_prompt_style_rules():
    p = prompts.SYSTEM_PROMPT
    assert "1-2 sentence" in p and "Answer the question fully" in p
    assert "Never mention internal field names" in p
    assert "how much risk it adds relative to its size" in p
    assert "how closely it moves with the rest" in p
    assert "period_label" in p


def test_audit_extra_is_recorded_but_cannot_overwrite_turn_fields(tmp_path):
    client = FakeClient([[tool_block()], [text_block(good_reply())]])
    run(client, tmp_path, audit_extra={"adviser_id": "u1", "blocked": "spoofed"})
    rec = audit_lines(tmp_path)[-1]
    assert rec["adviser_id"] == "u1" and rec["blocked"] is False


def test_audit_sink_gets_the_built_redacted_record_instead_of_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value-123")
    got = []
    client = FakeClient([[tool_block()], [text_block(good_reply())]])
    chat.run_turn("Which positions add the most risk? sk-ant-secret-value-123", [], client,
                  model="test-model", audit_path=tmp_path / "audit.jsonl", audit_sink=got.append)
    assert not (tmp_path / "audit.jsonl").exists()
    (rec,) = got
    assert rec["engine_version"] and rec["timestamp"]
    assert "sk-ant-secret-value-123" not in json.dumps(rec)
