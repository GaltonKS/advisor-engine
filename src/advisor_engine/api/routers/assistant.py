"""Galton AI: the conversation, turns, drafts and their approval.

Model replies only come from `run_turn`, so the number check, retry and withholding
apply; the browser never talks to the model. Draft approvals re-run `verify` on the
edited text.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ...assistant.chat import run_turn
from ...assistant.mock_client import MockClient
from ...assistant.numcheck import verify
from ..deps import (Ctx, assistant_error_message, ctx, log, log_, own_portfolio,
                    session_fields)
from ..schemas import ApproveIn, TurnIn

router = APIRouter()

DRAFT_REQUEST = "Please write a client-ready explanation of this portfolio's risk, for my review."
TOOL_LABELS = {"get_risk_profile": "Risk profile", "get_holdings": "Holdings",
               "run_what_if": "What-if", "run_stress_test": "Stress test", "show_chart": "Chart",
               "refer_to_stress_test": "Stress test referral"}


def _tool_view(call: dict) -> dict:
    """A tool call as the browser sees it: engine output, but not server file paths."""
    try:
        output = json.loads(call["output"])
    except ValueError:
        output = call["output"]
    return {"name": call["name"], "label": TOOL_LABELS.get(call["name"], call["name"]),
            "is_error": call["is_error"], "holdings_sha256": call.get("holdings_sha256"),
            "output": output}


@router.get("/portfolios/{portfolio_id}/drafts")
def drafts(portfolio_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
    own_portfolio(c, portfolio_id)
    return [{k: v for k, v in d.items() if k != "tool_calls"}
            for d in c.db.list_drafts(c.adv["firm_id"], portfolio_id)]


@router.get("/portfolios/{portfolio_id}/messages")
def messages(portfolio_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
    own_portfolio(c, portfolio_id)
    return c.db.get_conversation(c.adv["firm_id"], portfolio_id)["messages"]


@router.delete("/portfolios/{portfolio_id}/messages")
def new_chat(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    """Start a new Galton AI chat: clear the conversation and the model's history.
    Nothing is lost: every turn's question, reply and tool calls are already in the
    audit log, and drafts stay in the Client report."""
    portfolio = own_portfolio(c, portfolio_id)
    conversation = c.db.get_conversation(c.adv["firm_id"], portfolio_id)
    n = len(conversation["messages"])
    if n or conversation["history"]:
        c.db.save_conversation(c.adv["firm_id"], portfolio_id, {"history": [], "messages": []})
        log(c, "chat_cleared", client_id=portfolio["client_id"], portfolio_id=portfolio_id,
            inputs={"messages": n})
    return {"cleared": n}


@router.post("/portfolios/{portfolio_id}/turns")
def ask(portfolio_id: str, body: TurnIn, c: Ctx = Depends(ctx)) -> dict:
    fid = c.adv["firm_id"]
    portfolio = own_portfolio(c, portfolio_id)
    question = DRAFT_REQUEST if body.draft else body.message.strip()
    if not question:
        raise HTTPException(422, "Ask a question or request a draft.")
    if not c.db.bump_turns(c.adv["id"], c.settings.daily_turn_limit):
        raise HTTPException(429, "Daily assistant limit reached; try again tomorrow.")
    client = c.db.get_client(fid, portfolio["client_id"]) or {}
    conversation = c.db.get_conversation(fid, portfolio_id)
    # One id per turn, on the audit record and on both chat messages, so an audit
    # entry can link back to the exact exchange in the conversation.
    turn_id = uuid.uuid4().hex
    try:
        result = run_turn(
            question, conversation["history"],
            MockClient() if c.settings.llm == "mock" else None,
            firm_profile={k: client[k] for k in ("objective", "tone") if client.get(k)},
            default_holdings=str(c.db.holdings_file(fid, portfolio_id)),
            data_dir=c.db.firm_dir(fid),
            audit_sink=lambda record: c.db.write_audit(fid, record),
            audit_extra={"event": "turn", "adviser_id": c.adv["id"], "firm_id": fid,
                         **session_fields(c),
                         "client_id": portfolio["client_id"], "portfolio_id": portfolio_id,
                         "turn_id": turn_id},
        )
    except Exception as e:  # missing package, model API error, ...
        # The raw error can carry request ids and account details; keep it server-side.
        log_.exception("Assistant turn failed for portfolio %s", portfolio_id)
        raise HTTPException(502, assistant_error_message(e)) from e

    draft_id = None
    if body.draft and not result.blocked:
        draft_id = c.db.create_draft(fid, {
            "portfolio_id": portfolio_id, "created_by": c.adv["id"],
            "original": result.reply, "tool_calls": result.tool_calls})["id"]
    reply = {"role": "assistant", "content": result.reply, "blocked": result.blocked,
             "verification": result.verification,
             "tool_calls": [_tool_view(call) for call in result.tool_calls], "draft_id": draft_id,
             "turn_id": turn_id}
    conversation["history"] = result.history
    conversation["messages"] += [{"role": "user", "content": question, "turn_id": turn_id}, reply]
    c.db.save_conversation(fid, portfolio_id, conversation)
    return reply


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: str, body: ApproveIn, c: Ctx = Depends(ctx)) -> dict:
    fid = c.adv["firm_id"]
    draft = c.db.get_draft(fid, draft_id)
    if draft is None:
        raise HTTPException(404, "Draft not found.")
    p = c.db.get_portfolio(fid, draft["portfolio_id"])
    if p is None or c.db.get_client(fid, p["client_id"]) is None:  # client deleted
        raise HTTPException(404, "Draft not found.")
    # An approved report can be edited again later (from Galton AI or the Client
    # report tab). Each revision is re-checked, re-approved and logged on its own as
    # `draft_revised`, with the text it replaced; nothing is overwritten silently.
    revising = bool(draft["approved"])
    if revising and body.text == draft.get("approved_text"):
        raise HTTPException(409, "This draft is already approved with this text.")
    # Edits are re-checked: an adviser can reword, but not add a figure the engine
    # never produced.
    check = verify(body.text, [call["output"] for call in draft["tool_calls"]])
    if not check["ok"]:
        raise HTTPException(422, {"message": "These figures are not from the engine.",
                                  "unmatched": check["unmatched"]})
    record = c.db.write_audit(fid, {
        "event": "draft_revised" if revising else "draft_approved", "model": None,
        "adviser_id": c.adv["id"],
        "approved_by_email": c.adv["email"], "firm_id": fid,
        "portfolio_id": draft["portfolio_id"], "draft_id": draft_id,
        "user_message": DRAFT_REQUEST, "tool_calls": draft["tool_calls"],
        "reply": draft["original"], "approved_text": body.text,
        **({"previous_text": draft.get("approved_text")} if revising else {}),
        "edited": body.text != draft["original"], "verification": check, "blocked": False,
        **session_fields(c),
    })
    draft.update(approved=True, approved_text=body.text, approved_by=c.adv["id"],
                 approved_at=record["timestamp"])
    c.db.save_draft(fid, draft)
    return draft
