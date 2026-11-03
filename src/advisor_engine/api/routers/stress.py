"""Stress tests: presets, custom and AI-suggested scenarios, and the logged runs.

The AI suggests assumptions only (shocks); the engine computes every result. Each run
gets a `run_id`, on the result and on its `stress` audit record, and the audit log is
the record the Stress test tab reopens runs from.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from ...assistant.chat import _make_client as make_llm_client
from ...assistant.scenario import MockSuggester, suggest_shocks
from ...stress import run_scenario, scenario_list, stress_test
from ..deps import (Ctx, assistant_error_message, ctx, frame_of, log, log_,
                    own_portfolio)
from ..schemas import StressIn, SuggestIn

router = APIRouter()


def _stress_summary(record: dict) -> dict:
    """What a logged stress run assumed, for the audit trail: the preset's name, or a
    custom scenario's description and shocks (the adviser's inputs, not results)."""
    inputs = record.get("inputs") or {}
    result = record.get("result") or {}
    scenario = result.get("scenario") or {}
    out = {"id": inputs.get("scenario_id") or "custom", "label": scenario.get("label"),
           "source": (result.get("assumptions") or {}).get("source")}
    if not inputs.get("scenario_id"):
        out.update(description=inputs.get("description") or "",
                   sector_shocks=inputs.get("sector_shocks") or {},
                   asset_class_shocks=inputs.get("asset_class_shocks") or {},
                   default_shock=inputs.get("default_shock", 0.0))
    return out


def _assumptions(body: StressIn) -> dict:
    """Where a stress run's assumptions came from: a preset, the adviser, or an AI
    suggestion (kept as suggested, or edited by the adviser before running)."""
    if body.scenario_id:
        return {"source": "preset"}
    if not body.suggested:
        return {"source": "adviser", "description": body.description}

    def norm(d: dict | None) -> dict:
        return {k: round(float(v), 4) for k, v in (d or {}).items()}

    s = body.suggested
    same = (norm(s.get("sector_shocks")) == norm(body.sector_shocks)
            and norm(s.get("asset_class_shocks")) == norm(body.asset_class_shocks)
            and round(float(s.get("default_shock", 0.0)), 4) == round(body.default_shock, 4))
    return {"source": "ai_suggested" if same else "ai_suggested_edited",
            "description": body.description or s.get("description", ""),
            "label": s.get("label", ""), "model": s.get("model", "")}


def stress_runs(c: Ctx, portfolio_id: str) -> list[dict]:
    """This portfolio's stress runs and resets from the audit log, oldest first.
    The log is the record of what was run, so reopening a run shows exactly what the
    engine returned then (holdings versions never change)."""
    return [r for r in c.db.read_audit(c.adv["firm_id"])
            if r.get("portfolio_id") == portfolio_id
            and r.get("event") in ("stress", "stress_reset")]


def run_view(r: dict) -> dict:
    return {"run_id": r.get("run_id"), "timestamp": r.get("timestamp"),
            "inputs": r.get("inputs") or {}, "result": {**(r.get("result") or {}),
                                                        "run_id": r.get("run_id")}}


@router.get("/stress/scenarios")
def scenarios(c: Ctx = Depends(ctx)) -> list[dict]:
    return scenario_list()


@router.post("/portfolios/{portfolio_id}/stress")
def stress(portfolio_id: str, body: StressIn, c: Ctx = Depends(ctx)) -> dict:
    p = own_portfolio(c, portfolio_id)
    df = frame_of(c, portfolio_id)
    try:
        if body.scenario_id:
            result = run_scenario(df, body.scenario_id)
        else:
            label = (body.suggested or {}).get("label") or "Custom scenario"
            result = {"scenario": {"id": "custom", "label": label,
                                   "sectors": body.sector_shocks,
                                   "default": body.default_shock,
                                   "asset_classes": body.asset_class_shocks},
                      **stress_test(df, body.sector_shocks, body.default_shock,
                                    body.asset_class_shocks)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    result["assumptions"] = _assumptions(body)
    # One id per run, on the audit record and the result, so the audit trail can
    # link back to it and the Stress test tab can reopen it.
    result["run_id"] = uuid.uuid4().hex
    log(c, "stress", client_id=p["client_id"], portfolio_id=portfolio_id,
        holdings_sha256=p["sha256"], run_id=result["run_id"], inputs=body.model_dump(),
        result=result)
    return result


@router.get("/portfolios/{portfolio_id}/stress/current")
def stress_current(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    """The scenario the Stress test tab should show: the latest run, unless the
    adviser has reset the scenario since."""
    own_portfolio(c, portfolio_id)
    runs = stress_runs(c, portfolio_id)
    last = runs[-1] if runs else None
    return {"run": run_view(last) if last and last["event"] == "stress" else None}


@router.get("/portfolios/{portfolio_id}/stress/runs/{run_id}")
def stress_run(portfolio_id: str, run_id: str, c: Ctx = Depends(ctx)) -> dict:
    own_portfolio(c, portfolio_id)
    for r in stress_runs(c, portfolio_id):
        if r["event"] == "stress" and r.get("run_id") == run_id:
            return {"run": run_view(r)}
    raise HTTPException(404, "Stress test not found.")


@router.post("/portfolios/{portfolio_id}/stress/reset")
def stress_reset(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    """Clear the Stress test tab's scenario. Earlier runs stay in the audit log."""
    p = own_portfolio(c, portfolio_id)
    runs = stress_runs(c, portfolio_id)
    if runs and runs[-1]["event"] == "stress":
        log(c, "stress_reset", client_id=p["client_id"], portfolio_id=portfolio_id)
    return {"run": None}


@router.post("/portfolios/{portfolio_id}/stress/suggest")
def suggest(portfolio_id: str, body: SuggestIn, c: Ctx = Depends(ctx)) -> dict:
    """AI-suggested shocks for a described scenario. Assumptions only: the adviser
    reviews them, and the engine computes the result when they run the scenario."""
    p = own_portfolio(c, portfolio_id)
    df = frame_of(c, portfolio_id)
    if not c.db.bump_turns(c.adv["id"], c.settings.daily_turn_limit):
        raise HTTPException(429, "Daily assistant limit reached; try again tomorrow.")
    try:
        client = MockSuggester() if c.settings.llm == "mock" else make_llm_client()
        suggestion = suggest_shocks(body.description, df, client)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # model API error, missing package, ...
        log_.exception("Scenario suggestion failed for portfolio %s", portfolio_id)
        raise HTTPException(502, assistant_error_message(e)) from e
    log(c, "scenario_suggested", client_id=p["client_id"], portfolio_id=portfolio_id,
        holdings_sha256=p["sha256"], model=suggestion["model"],
        inputs={"description": body.description}, result=suggestion)
    return suggestion
