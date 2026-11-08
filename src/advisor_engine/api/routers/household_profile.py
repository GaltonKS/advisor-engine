"""Households and the three-part risk profile (willingness, ability, need).

Draft method: every number comes from `profile.py` / `profile_config.py` and is a
placeholder pending compliance review. These routes store the adviser's answers and
return what the engine computes from them; they score nothing themselves. Every write is
audited. The legacy questionnaire (routers/profiling.py) is separate and unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ... import profile
from ...profile_config import METHOD_VERSION, PER_PERSON
from ..deps import Ctx, ctx, log, own_client
from ..schemas import PersonIn, ProfileInputIn

router = APIRouter()


def _household(c: Ctx, client_id: str) -> tuple[list[dict], list[dict]]:
    fid = c.adv["firm_id"]
    return c.db.list_people(fid, client_id), c.db.list_profile_inputs(fid, client_id)


def _result(people: list[dict], inputs: list[dict]) -> dict:
    try:
        out = profile.compute_profile(inputs, people)
    except ValueError as e:  # a stored answer the current method rejects
        raise HTTPException(422, str(e)) from e
    return {**out, "inputs_hash": profile.inputs_hash(inputs)}


@router.get("/profile/questions")
def questions(c: Ctx = Depends(ctx)) -> dict:
    return profile.question_bank()


@router.get("/clients/{client_id}/people")
def list_people(client_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
    own_client(c, client_id)
    return c.db.list_people(c.adv["firm_id"], client_id)


@router.post("/clients/{client_id}/people", status_code=201)
def add_person(client_id: str, body: PersonIn, c: Ctx = Depends(ctx)) -> dict:
    own_client(c, client_id)
    fid = c.adv["firm_id"]
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "The name can't be blank.")
    role = body.role
    if role is None:
        taken = {p["role"] for p in c.db.list_people(fid, client_id)}
        role = "primary" if "primary" not in taken else "partner"
    try:
        person = c.db.create_person(fid, client_id, c.adv["id"], {"name": name, "role": role})
    except ValueError as e:
        raise HTTPException(409, f"{e} A household holds one or two people.") from None
    log(c, "person_added", client_id=client_id,
        inputs={"person_id": person["id"], "name": name, "role": role})
    return person


@router.post("/clients/{client_id}/profile/inputs", status_code=201)
def add_input(client_id: str, body: ProfileInputIn, c: Ctx = Depends(ctx)) -> dict:
    """Record one answer (or, with a null answer, retract the question)."""
    own_client(c, client_id)
    fid = c.adv["firm_id"]
    try:
        q = profile.question(body.question_id)
        profile.validate_answer(body.question_id, body.answer)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if q["dimension"] in PER_PERSON:
        people = {p["id"] for p in c.db.list_people(fid, client_id)}
        if body.person_id not in people:
            raise HTTPException(422, "Willingness answers need one of the household's people.")
    elif body.person_id is not None:
        raise HTTPException(422, f"{q['dimension'].capitalize()} answers are for the household, "
                                 "not one person.")
    row = c.db.add_profile_input(fid, client_id, c.adv["id"], {
        "person_id": body.person_id, "dimension": q["dimension"],
        "question_id": body.question_id, "answer": body.answer,
        "source": "advisor_entered", "method_version": METHOD_VERSION})
    log(c, "profile_answer_retracted" if body.answer is None else "profile_answer_recorded",
        client_id=client_id,
        inputs={"question_id": body.question_id, "person_id": body.person_id,
                "answer": body.answer, "input_id": row["id"]})
    return row


@router.get("/clients/{client_id}/profile")
def get_profile(client_id: str, c: Ctx = Depends(ctx)) -> dict:
    """The engine's profile from the stored answers, plus the answers in effect (so the
    screen can show which option is chosen). Recomputed on every read."""
    own_client(c, client_id)
    people, inputs = _household(c, client_id)
    answers = [{k: r[k] for k in ("person_id", "dimension", "question_id", "answer",
                                  "recorded_at")} for r in profile.latest_answers(inputs)]
    return {**_result(people, inputs), "answers": answers}


@router.post("/clients/{client_id}/profile/snapshots", status_code=201)
def save_snapshot(client_id: str, c: Ctx = Depends(ctx)) -> dict:
    """Freeze the current profile as a saved version."""
    own_client(c, client_id)
    people, inputs = _household(c, client_id)
    result = _result(people, inputs)
    snap = c.db.save_profile_snapshot(c.adv["firm_id"], client_id, c.adv["id"], {
        "method_version": result["method_version"], "inputs_hash": result["inputs_hash"],
        "result": result})
    log(c, "profile_snapshot_saved", client_id=client_id,
        inputs={"snapshot_id": snap["id"], "inputs_hash": result["inputs_hash"]},
        result={"method_version": result["method_version"],
                "provisional": result["provisional"], "ceiling": result["ceiling"]})
    return snap


@router.get("/clients/{client_id}/profile/snapshots")
def list_snapshots(client_id: str, c: Ctx = Depends(ctx)) -> list[dict]:
    own_client(c, client_id)
    return c.db.list_profile_snapshots(c.adv["firm_id"], client_id)


@router.get("/clients/{client_id}/profile/snapshots/{snapshot_id}")
def get_snapshot(client_id: str, snapshot_id: str, c: Ctx = Depends(ctx)) -> dict:
    own_client(c, client_id)
    snap = c.db.get_profile_snapshot(c.adv["firm_id"], snapshot_id)
    if snap is None or snap["client_id"] != client_id:
        raise HTTPException(404, "Saved profile not found.")
    return snap
