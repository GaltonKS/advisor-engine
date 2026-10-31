"""Tool-use loop with a number check on the final reply.

The model may only explain what the engine returned. After it answers, every
figure in the reply is checked against all tool outputs in the conversation; a
reply with a figure the engine did not produce is withheld and logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .audit import append_audit, build_record
from .numcheck import verify
from .tools import TOOLS, execute_tool

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 5
MAX_RETRIES = 1
MAX_TOKENS = 2048


@dataclass
class TurnResult:
    reply: str  # what to show the advisor (the withheld notice if blocked)
    blocked: bool
    verification: dict
    history: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    block_reason: str | None = None

    @property
    def unmatched(self) -> list:
        return self.verification.get("unmatched", [])


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def llm_mode() -> str:
    """"mock" or "anthropic". ADVISOR_LLM wins; otherwise live only if an API key is set."""
    _load_env()
    mode = os.environ.get("ADVISOR_LLM", "").strip().lower()
    if mode in ("mock", "anthropic"):
        return mode
    if mode:
        raise ValueError(f"ADVISOR_LLM must be 'mock' or 'anthropic', not {mode!r}.")
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "mock"


def _make_client():
    if llm_mode() == "mock":
        from .mock_client import MockClient  # no network, no anthropic import

        return MockClient()
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError('The anthropic package is missing: pip install -e ".[llm]"') from e
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


def _blocks(content) -> list[dict]:
    """SDK content blocks -> plain dicts, so history is JSON-safe and reusable."""
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})
    return out


def _tool_outputs(messages: list[dict]) -> list[str]:
    outs = []
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            outs.extend(b["content"] for b in m["content"] if b.get("type") == "tool_result")
    return outs


# What to tell the model about each kind of number-check failure, so its one retry can
# fix the figure rather than repeat it. Keys are numcheck.verify reasons.
_RETRY_HINTS = {
    "coarse match, unverified": (
        "is rounded too far to check; quote it with the precision the tool gives, at least "
        "two significant digits (for example -0.58% rather than -0.6% or -0.006)"),
    "sign mismatch": (
        "has the wrong direction; check whether the tool value is a fall or a rise, and "
        "quote change values with the sign the tool gives"),
    "no matching tool figure": (
        "is not in any tool result; remove it, or call a tool that returns it"),
}


def _retry_message(verification: dict) -> str:
    reasons = verification.get("reasons", {})
    lines = [f"- {fig} {_RETRY_HINTS.get(reasons.get(fig, ''), 'is not in any tool result')}"
             for fig in verification["unmatched"]]
    return (
        "Your answer contained figures that no tool returned:\n" + "\n".join(lines)
        + "\nRestate it using only figures from the tool results, quoted as precisely as the "
        "tool gives them, and say so where a figure is not available."
    )


def _withheld_message(unmatched: list[str]) -> str:
    return (
        "I withheld this answer because it contained figures that were not produced by the "
        f"engine: {', '.join(unmatched)}. Try rephrasing the question."
    )


def run_turn(
    user_message: str,
    history: list | None = None,
    client=None,
    *,
    model: str | None = None,
    firm_profile: dict | None = None,
    default_holdings: str | None = None,
    max_retries: int = MAX_RETRIES,
    audit_path: str | Path | None = None,
    data_dir: Path | None = None,
    returns_fn=None,
    audit_extra: dict | None = None,
    audit_sink=None,
) -> TurnResult:
    """Answer one user message. `history` is the `.history` of the previous result.

    `audit_extra` is merged into the audit record (e.g. who asked, for which firm); it
    cannot overwrite the turn's own fields. `audit_sink(record)` replaces the JSONL file
    (e.g. a database insert); it receives the record already built by
    `audit.build_record`, so timestamps, engine version and key redaction are the same.
    """
    _load_env()
    client = client or _make_client()
    # A client may name itself (the mock does), so the audit log never claims a real model.
    model = model or getattr(client, "model_name", None) or os.environ.get("ADVISOR_MODEL", DEFAULT_MODEL)
    firm_profile = firm_profile if firm_profile is not None else prompts.FIRM_PROFILE

    system = prompts.build_system_prompt(firm_profile)
    if default_holdings:
        system += (
            f"\nThe portfolio under discussion is holdings_path {default_holdings!r}. Use it "
            f"for every tool call unless the user names another file.\n"
        )

    history = list(history or [])
    messages = history + [{"role": "user", "content": user_message}]
    tool_calls: list[dict] = []
    retries: list[dict] = []
    drop: set[int] = set()  # failed attempt + correction, kept out of saved history
    rounds = 0
    holdings_sha = None
    text, verification, block_reason = "", {"ok": True, "unmatched": [], "checked": 0}, None

    while True:
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system, tools=TOOLS, messages=list(messages)
        )
        blocks = _blocks(resp.content)
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b["type"] == "tool_use"]

        if uses:
            if rounds >= MAX_TOOL_ROUNDS:
                block_reason = "tool_round_cap"
                break
            rounds += 1
            results = []
            for u in uses:
                res = execute_tool(u["name"], u["input"], data_dir=data_dir, returns_fn=returns_fn)
                tool_calls.append(
                    {"name": u["name"], "input": u["input"], "output": res.text,
                     "is_error": res.is_error, "holdings_sha256": res.holdings_sha256}
                )
                holdings_sha = res.holdings_sha256 or holdings_sha
                block = {"type": "tool_result", "tool_use_id": u["id"], "content": res.text}
                if res.is_error:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})
            continue

        text = "".join(b["text"] for b in blocks if b["type"] == "text")
        verification = verify(text, _tool_outputs(messages))
        if verification["ok"]:
            break
        if len(retries) < max_retries:
            retries.append({"reply": text, "unmatched": verification["unmatched"]})
            drop.add(len(messages) - 1)
            messages.append({"role": "user", "content": _retry_message(verification)})
            drop.add(len(messages) - 1)
            continue
        block_reason = "unmatched_numbers"
        break

    blocked = block_reason is not None
    if block_reason == "tool_round_cap":
        shown = f"I stopped after {MAX_TOOL_ROUNDS} rounds of tool calls without reaching an answer."
        verification = {"ok": False, "unmatched": [], "checked": 0}
        new_history = history
    elif blocked:
        shown = _withheld_message(verification["unmatched"])
        new_history = history  # drop the turn so the rejected figures are not reused
    else:
        shown = text
        if firm_profile and firm_profile.get("disclaimer"):
            shown += "\n\n" + firm_profile["disclaimer"]
        new_history = [m for i, m in enumerate(messages) if i not in drop]

    record = {
        **(audit_extra or {}),
        "model": model,
        "holdings_sha256": holdings_sha,
        "user_message": user_message,
        "tool_calls": tool_calls,
        "reply": text,
        "retries": retries,
        "verification": verification,
        "blocked": blocked,
        "block_reason": block_reason,
    }
    if audit_sink:
        audit_sink(build_record(record))
    else:
        append_audit(record, audit_path)
    return TurnResult(shown, blocked, verification, new_history, tool_calls, block_reason)
