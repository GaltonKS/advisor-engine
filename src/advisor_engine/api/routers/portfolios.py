"""Portfolios: upload, the holdings editor, risk profile, what-if and compare.

Every figure comes from the engine (`deps.profile_of`, `analysis.engine_profile`,
`compare.compare_profiles`); these routes only move it around and audit the runs.
"""

from __future__ import annotations

import hashlib
import re

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...assistant.tools import engine_profile
from ...compare import compare_profiles
from ...holdings import COLUMNS, load_holdings, validate_holdings
from ..deps import Ctx, ctx, frame_of, log, own_client, own_portfolio, profile_of
from ..schemas import HoldingRow, HoldingsIn

router = APIRouter()

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _frame(rows: list[HoldingRow]) -> pd.DataFrame:
    try:
        return validate_holdings(pd.DataFrame([r.model_dump() for r in rows]))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _engine_profile(holdings: pd.DataFrame) -> dict:
    """The same payload get_risk_profile returns, for holdings that aren't a stored file."""
    try:
        return engine_profile(holdings)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _holdings_rows(df: pd.DataFrame) -> list[dict]:
    return [{k: (float(v) if k in ("shares", "price", "value", "weight") else v)
             for k, v in row.items()} for row in df.to_dict("records")]


@router.post("/clients/{client_id}/portfolios", status_code=201)
async def upload_portfolio(client_id: str, file: UploadFile = File(...),
                           c: Ctx = Depends(ctx)) -> dict:
    own_client(c, client_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Holdings file is larger than 2 MB.")
    name = re.sub(r"[^\w.-]", "_", file.filename or "holdings.csv")
    if not name.lower().endswith(".csv"):
        raise HTTPException(422, "The file must be a .csv.")
    try:
        load_holdings(data)
    except Exception as e:  # any load failure means the file is not usable
        raise HTTPException(422, f"{name} was rejected: {e}") from e
    return c.db.add_portfolio(c.adv["firm_id"], client_id, c.adv["id"], name, data,
                              hashlib.sha256(data).hexdigest())


@router.get("/portfolios/{portfolio_id}/profile")
def profile(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    p = own_portfolio(c, portfolio_id)
    return {"holdings_sha256": p["sha256"], "profile": profile_of(c, portfolio_id)}


@router.get("/portfolios/{portfolio_id}/holdings")
def holdings(portfolio_id: str, c: Ctx = Depends(ctx)) -> dict:
    own_portfolio(c, portfolio_id)
    return {"holdings": _holdings_rows(frame_of(c, portfolio_id))}


@router.post("/clients/{client_id}/portfolios/holdings", status_code=201)
def save_holdings(client_id: str, body: HoldingsIn, c: Ctx = Depends(ctx)) -> dict:
    """Save edited holdings as a new portfolio version; the original is never changed."""
    own_client(c, client_id)
    data = _frame(body.holdings)[COLUMNS].to_csv(index=False).encode()
    p = c.db.add_portfolio(c.adv["firm_id"], client_id, c.adv["id"], "edited-holdings.csv",
                           data, hashlib.sha256(data).hexdigest(),
                           label=body.label.strip(), source="editor")
    log(c, "portfolio_saved", client_id=client_id, portfolio_id=p["id"],
        holdings_sha256=p["sha256"])
    return p


@router.post("/portfolios/{portfolio_id}/what-if")
def what_if(portfolio_id: str, body: HoldingsIn, c: Ctx = Depends(ctx)) -> dict:
    p = own_portfolio(c, portfolio_id)
    before = profile_of(c, portfolio_id)
    after = _engine_profile(_frame(body.holdings))
    comparison = compare_profiles(before, after)
    log(c, "what_if", client_id=p["client_id"], portfolio_id=portfolio_id,
        holdings_sha256=p["sha256"],
        inputs={"holdings": [r.model_dump() for r in body.holdings]}, result=comparison)
    return {"before": before, "after": after, "comparison": comparison}


@router.get("/portfolios/{portfolio_id}/compare/{other_id}")
def compare(portfolio_id: str, other_id: str, c: Ctx = Depends(ctx)) -> dict:
    p, o = own_portfolio(c, portfolio_id), own_portfolio(c, other_id)
    before, after = profile_of(c, portfolio_id), profile_of(c, other_id)
    comparison = compare_profiles(before, after)
    log(c, "compare", client_id=p["client_id"], portfolio_id=portfolio_id,
        inputs={"other_portfolio_id": other_id, "other_client_id": o["client_id"]},
        result=comparison)
    return {"before": before, "after": after, "comparison": comparison}
