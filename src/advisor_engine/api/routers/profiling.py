"""Client risk profiling: the questionnaire, a client's saved answers, and the
suitability check of a portfolio against them (draft method, pending compliance review)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ... import questionnaire as qn
from ..deps import Ctx, ctx, log, own_client, own_portfolio, profile_of
from ..schemas import QuestionnaireIn

router = APIRouter()


@router.get("/questionnaire")
def questionnaire(c: Ctx = Depends(ctx)) -> dict:
    return {"questions": qn.QUESTIONS, "method_version": qn.METHOD_VERSION,
            "method_status": qn.METHOD_STATUS}


@router.put("/clients/{client_id}/questionnaire")
def save_questionnaire(client_id: str, body: QuestionnaireIn, c: Ctx = Depends(ctx)) -> dict:
    own_client(c, client_id)
    try:
        result = qn.score(body.answers)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    c.db.save_questionnaire(c.adv["firm_id"], client_id, body.answers)
    log(c, "questionnaire_saved", client_id=client_id, inputs=body.answers, result=result)
    return result


@router.get("/portfolios/{portfolio_id}/suitability")
def suitability(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    p = own_portfolio(c, portfolio_id)
    client = own_client(c, p["client_id"])
    if not client.get("questionnaire"):
        raise HTTPException(409, "Complete the client's risk questionnaire first.")
    result = qn.score(client["questionnaire"])
    return {"questionnaire": result,
            "checks": qn.check_portfolio(result, profile_of(c, portfolio_id))}
