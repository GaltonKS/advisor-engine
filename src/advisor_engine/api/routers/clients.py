"""Clients: list, create, read, rename, soft delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ... import questionnaire as qn
from ..deps import MFA_FRESH_REQUIRED, Ctx, ctx, log, own_client, require_fresh_code
from ..schemas import ClientIn, ClientRenameIn

router = APIRouter()


@router.get("/clients")
def list_clients(c: Ctx = Depends(ctx)) -> list[dict]:
    return c.db.list_clients(c.adv["firm_id"])


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, c: Ctx = Depends(ctx)) -> dict:
    return c.db.create_client(c.adv["firm_id"], c.adv["id"], body.model_dump())


@router.get("/clients/{client_id}")
def get_client(client_id: str, c: Ctx = Depends(ctx)) -> dict:
    client = own_client(c, client_id)
    answers = client.get("questionnaire")
    return {**client, "portfolios": c.db.list_portfolios(c.adv["firm_id"], client_id),
            # Scored on read, so a method change never leaves a stale stored score.
            "questionnaire_result": qn.score(answers) if answers else None}


@router.patch("/clients/{client_id}")
def rename_client(client_id: str, body: ClientRenameIn, c: Ctx = Depends(ctx)) -> dict:
    old = own_client(c, client_id)["name"]
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "The client name can't be blank.")
    client = c.db.rename_client(c.adv["firm_id"], client_id, name)
    if old != name:
        log(c, "client_renamed", client_id=client_id, inputs={"from": old, "to": name})
    return client


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(client_id: str, c: Ctx = Depends(ctx)) -> None:
    """Remove a client from the firm's list. A soft delete: their portfolios, drafts
    and the audit trail are kept on record, but the app no longer shows or uses them."""
    client = own_client(c, client_id)
    fresh = require_fresh_code(c)
    try:
        c.db.delete_client(c.adv["firm_id"], client_id, c.adv["id"])
    except PermissionError:
        # The database checks the code too (it can differ by a clock tick, or by mode).
        raise HTTPException(403, MFA_FRESH_REQUIRED) from None
    log(c, "client_deleted", client_id=client_id, inputs={"name": client["name"]},
        fresh_code=fresh)
