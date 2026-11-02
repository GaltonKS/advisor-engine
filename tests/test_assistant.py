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


def test_retry_message_steers_a_worked_out_remainder_back_to_the_parts():
    # "The other 19%" is 100% minus the cash share: the engine never returns it, and the
    # retry has to say why, or the model repeats it and the whole answer is withheld.
    from advisor_engine.assistant.numcheck import verify
    tool = json.dumps({"asset_class_exposure": {"cash": 0.81, "fixed_income": 0.088, "equity": 0.102}})
    v = verify("81.0% is in cash and the other 19% is invested.", [tool])
    assert v["unmatched"] == ["19%"]
    msg = chat._retry_message(v)
    assert "19% is not in any tool result" in msg
    assert "what is left of 100%" in msg and "each part" in msg
    assert "Don't add any figure" in msg
    # The hint carries no example figures the model could copy into another portfolio:
    # apart from the flagged figure, its only number is the "100%" in its wording.
    from advisor_engine.assistant.numcheck import extract_numbers
    assert extract_numbers(msg.replace("19%", "")) == [100.0]
    # Quoting the parts instead passes.
    assert verify("81.0% is in cash, 8.8% in bonds and 10.2% in stocks.", [tool])["ok"]


def test_prompt_forbids_totals_and_remainders():
    p = prompts.SYSTEM_PROMPT
    assert "totals and remainders" in p and "each part's own figure" in p


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


# --- prompt caching and token usage -------------------------------------------------

class UsageClient(FakeClient):
    """FakeClient whose responses report token usage, like the real API."""

    def __init__(self, script, usages):
        super().__init__(script)
        self.usages = list(usages)

    def create(self, **kw):
        resp = super().create(**kw)
        resp.usage = SimpleNamespace(**self.usages.pop(0))
        return resp


def test_every_call_asks_for_prompt_caching(tmp_path):
    client = FakeClient([[tool_block()], [text_block(good_reply())]])
    run(client, tmp_path)
    assert len(client.calls) == 2
    assert all(c["cache_control"] == {"type": "ephemeral"} for c in client.calls)


def test_cached_prefix_is_identical_across_turns(tmp_path):
    # Caching only pays if tools and system prompt are byte-identical call to call.
    client = FakeClient([[tool_block()], [text_block(good_reply())]])
    first = run(client, tmp_path, default_holdings=SAMPLE)
    chat.run_turn("And the second largest?", first.history, client, model="test-model",
                  default_holdings=SAMPLE, audit_path=tmp_path / "audit.jsonl")
    assert len({json.dumps(c["tools"]) for c in client.calls}) == 1
    assert len({c["system"] for c in client.calls}) == 1


def test_token_usage_is_summed_over_the_turn_and_audited(tmp_path):
    client = UsageClient(
        [[tool_block()], [text_block(good_reply())]],
        [{"input_tokens": 3500, "output_tokens": 120, "cache_read_input_tokens": 0,
          "cache_creation_input_tokens": 3400},
         {"input_tokens": 1800, "output_tokens": 300, "cache_read_input_tokens": 3400,
          "cache_creation_input_tokens": 1750}])
    run(client, tmp_path)
    (rec,) = audit_lines(tmp_path)
    assert rec["usage"] == {"api_calls": 2, "input_tokens": 5300, "output_tokens": 420,
                            "cache_read_input_tokens": 3400,
                            "cache_creation_input_tokens": 5150}


def test_missing_cache_fields_count_as_zero(tmp_path):
    client = UsageClient([[text_block("Hello.")]],
                         [{"input_tokens": 10, "output_tokens": 5,
                           "cache_read_input_tokens": None}])
    run(client, tmp_path)
    assert audit_lines(tmp_path)[0]["usage"] == {
        "api_calls": 1, "input_tokens": 10, "output_tokens": 5,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def test_clients_without_usage_record_none_not_zeros(tmp_path):
    run(FakeClient([[text_block("Hello.")]]), tmp_path)
    assert audit_lines(tmp_path)[0]["usage"] is None


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


# --- older tool results are not resent ---------------------------------------------------

def _results(history):
    return [b for m in history if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result"]


def _turn(question, history, client, tmp_path):
    return chat.run_turn(question, history, client, model="m", audit_path=tmp_path / "a.jsonl")


def test_only_the_newest_result_of_each_tool_is_resent(tmp_path):
    first = _turn("Risk?", [], FakeClient([[tool_block(1)], [text_block(good_reply())]]), tmp_path)
    assert "_full" not in json.dumps(first.history)  # one profile so far: kept in full
    client = FakeClient([[tool_block(2)], [text_block(good_reply())]])
    second = _turn("And now?", first.history, client, tmp_path)
    old, new = _results(second.history)
    assert old["content"].startswith("[Earlier get_risk_profile result")
    assert json.loads(old["_full"])["concentration"]  # full text kept for the number check
    assert "_full" not in new and json.loads(new["content"])["concentration"]
    # The private copy never reaches the API.
    third = FakeClient([[text_block("Fine.")]])
    _turn("Thanks", second.history, third, tmp_path)
    assert "_full" not in json.dumps(third.calls[0]["messages"])
    # One full profile sent instead of two.
    assert len(json.dumps(third.calls[0]["messages"])) < 0.6 * len(json.dumps(second.history))


def test_figures_from_a_left_out_result_still_pass_the_number_check(tmp_path):
    full = json.dumps({"effective_bets": 7.77})
    history = [
        {"role": "user", "content": "Risk?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_risk_profile", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                       "content": chat._compacted_note("get_risk_profile"), "_full": full}]},
        {"role": "assistant", "content": [{"type": "text", "text": "About 7.77 bets."}]},
    ]
    ok = _turn("Remind me?", history, FakeClient([[text_block("It was 7.77.")]]), tmp_path)
    assert not ok.blocked
    bad = _turn("Remind me?", history, FakeClient([[text_block("It was 8.42.")]]), tmp_path)
    assert bad.blocked


def test_short_and_error_results_are_kept(tmp_path):
    history = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "x", "input": {}},
                                          {"type": "tool_use", "id": "b", "name": "y", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "short"},
                                     {"type": "tool_result", "tool_use_id": "b", "content": "E" * 999,
                                      "is_error": True}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "x", "input": {}},
                                          {"type": "tool_use", "id": "d", "name": "y", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": "short"},
                                     {"type": "tool_result", "tool_use_id": "d", "content": "ok"}]},
    ]
    assert chat.compact_history(history) == history


def test_requests_only_append_within_a_turn_and_across_turns(tmp_path):
    # The prompt cache only pays if each request starts with the previous one.
    c1 = FakeClient([[tool_block(1)], [text_block(good_reply())]])
    first = _turn("Risk?", [], c1, tmp_path)
    c2 = FakeClient([[text_block("Fine.")]])
    _turn("Thanks", first.history, c2, tmp_path)
    calls = [c["messages"] for c in c1.calls + c2.calls]
    for before, after in zip(calls, calls[1:]):
        assert after[:len(before)] == before


# --- output cap ---------------------------------------------------------------------------

def test_output_cap_leaves_room_for_thinking(tmp_path):
    client = FakeClient([[text_block("Hello.")]])
    run(client, tmp_path)
    assert client.calls[0]["max_tokens"] == chat.MAX_TOKENS >= 16000


class CutOffClient(FakeClient):
    def create(self, **kw):
        resp = super().create(**kw)
        resp.stop_reason = "max_tokens"
        return resp


@pytest.mark.parametrize("blocks", [
    [text_block("Effective bets are")],                    # reply cut off mid-sentence
    [tool_block()],                                        # tool call cut off: never run
])
def test_a_reply_cut_off_at_the_cap_is_withheld_and_not_kept(tmp_path, blocks):
    client = CutOffClient([blocks])
    r = run(client, tmp_path)
    assert r.blocked and r.block_reason == "max_tokens"
    assert "length limit" in r.reply and r.history == [] and r.tool_calls == []
    assert len(client.calls) == 1  # no retry
    rec = audit_lines(tmp_path)[-1]
    assert rec["blocked"] and rec["block_reason"] == "max_tokens"
