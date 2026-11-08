"""Household risk profile routes (routers/household_profile.py), against both stores."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from advisor_engine import profile  # noqa: E402
from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.main import create_app  # noqa: E402
from advisor_engine.api.settings import Settings  # noqa: E402
from advisor_engine.api.store import LocalStore  # noqa: E402
from advisor_engine.profile_config import METHOD_STATUS, METHOD_VERSION  # noqa: E402

ALICE = {"X-Dev-User": "alice"}
BOB = {"X-Dev-User": "bob"}


@pytest.fixture(params=["local", "supabase"])
def api(request, tmp_path):
    if request.param == "local":
        store = LocalStore(tmp_path)
    else:
        from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

        store = SupabaseStore(request.getfixturevalue("supabase_db"), MemoryStorage(),
                              cache_dir=tmp_path / "cache")
    return TestClient(create_app(Settings(auth="dev", store_dir=tmp_path), store=store)), store


def session(store, user="alice"):
    """The user's store session and firm id."""
    uid, email = dev_user_id(user), f"{user}@dev.local"
    db = store.as_user(uid, email)
    return db, db.get_or_create_adviser(uid, email)["firm_id"]


def audit(store, user="alice"):
    db, fid = session(store, user)
    return db.read_audit(fid)


@pytest.fixture
def household(api):
    client, store = api
    c = client.post("/clients", json={"name": "Dana and Marcus"}, headers=ALICE).json()
    dana = client.post(f"/clients/{c['id']}/people", json={"name": "Dana"}, headers=ALICE)
    marcus = client.post(f"/clients/{c['id']}/people", json={"name": "Marcus"}, headers=ALICE)
    assert dana.status_code == marcus.status_code == 201, (dana.text, marcus.text)
    return client, store, c["id"], dana.json(), marcus.json()


def post(client, cid, question_id, answer, person=None, headers=ALICE):
    return client.post(f"/clients/{cid}/profile/inputs", headers=headers, json={
        "question_id": question_id, "answer": answer,
        "person_id": person["id"] if person else None})


def test_question_bank(api):
    client, _ = api
    bank = client.get("/profile/questions", headers=ALICE).json()
    assert bank == profile.question_bank()
    assert bank["method_status"] == METHOD_STATUS


def test_people_roles_and_the_two_person_cap(household):
    client, store, cid, dana, marcus = household
    assert (dana["role"], marcus["role"]) == ("primary", "partner")
    r = client.post(f"/clients/{cid}/people", json={"name": "Third"}, headers=ALICE)
    assert r.status_code == 409 and "one or two" in r.json()["detail"]
    assert [p["name"] for p in client.get(f"/clients/{cid}/people", headers=ALICE).json()] == [
        "Dana", "Marcus"]
    assert client.post(f"/clients/{cid}/people", json={"name": " "}, headers=ALICE).status_code == 422
    assert [e["event"] for e in audit(store)].count("person_added") == 2


def test_explicit_role_conflict(api):
    client, _ = api
    c = client.post("/clients", json={"name": "Solo"}, headers=ALICE).json()
    client.post(f"/clients/{c['id']}/people", json={"name": "P", "role": "partner"}, headers=ALICE)
    r = client.post(f"/clients/{c['id']}/people", json={"name": "Q", "role": "partner"}, headers=ALICE)
    assert r.status_code == 409
    r = client.post(f"/clients/{c['id']}/people", json={"name": "Q"}, headers=ALICE)
    assert r.status_code == 201 and r.json()["role"] == "primary"


def test_answers_drive_the_engine_profile(household):
    client, store, cid, dana, marcus = household
    for args in [("w_reaction", "hold", dana),
                 ("w_loss_limits", {"uncomfortable_pct": 0.1, "act_pct": 0.2}, dana),
                 ("w_reaction", "sell_some", marcus), ("w_experience", "good", marcus),
                 ("a_income_share", "some"), ("a_horizon_years", 12),
                 ("n_required_return", 0.058)]:
        r = post(client, cid, *args)
        assert r.status_code == 201, r.text
    got = client.get(f"/clients/{cid}/profile", headers=ALICE).json()
    db, fid = session(store)
    inputs, people = db.list_profile_inputs(fid, cid), db.list_people(fid, cid)
    expected = profile.compute_profile(inputs, people)
    assert {k: got[k] for k in expected} == expected
    assert got["inputs_hash"] == profile.inputs_hash(inputs)
    assert round(got["need"]["mean"], 1) == 6.1
    assert got["provisional"] is False and got["method_version"] == METHOD_VERSION
    assert {(a["question_id"], a["person_id"]) for a in got["answers"]} >= {
        ("w_reaction", dana["id"]), ("w_reaction", marcus["id"]), ("n_required_return", None)}
    events = [e for e in audit(store) if e["event"] == "profile_answer_recorded"]
    assert len(events) == 7 and events[0]["inputs"]["question_id"] == "w_reaction"
    assert events[0]["client_id"] == cid and events[0]["adviser_id"] == dev_user_id("alice")


@pytest.mark.parametrize("question_id,answer,person", [
    ("w_reaction", "panic", "dana"),
    ("w_loss_limits", {"uncomfortable_pct": 0.3, "act_pct": 0.2}, "dana"),
    ("a_horizon_years", -3, None),
    ("n_required_return", "0.05", None),
    ("no_such_question", 1, None),
    ("w_reaction", "hold", None),        # willingness needs a person
    ("a_cash_months", "3_6", "dana"),    # ability is per household
])
def test_invalid_answers_are_rejected(household, question_id, answer, person):
    client, store, cid, dana, _ = household
    r = post(client, cid, question_id, answer, dana if person else None)
    assert r.status_code == 422, r.text
    assert client.get(f"/clients/{cid}/profile", headers=ALICE).json()["answers"] == []
    assert not [e for e in audit(store) if e["event"].startswith("profile_answer")]


def test_person_from_another_household_is_rejected(household):
    client, _, cid, _, _ = household
    other = client.post("/clients", json={"name": "Other"}, headers=ALICE).json()
    stranger = client.post(f"/clients/{other['id']}/people", json={"name": "S"}, headers=ALICE).json()
    assert post(client, cid, "w_reaction", "hold", stranger).status_code == 422


def test_retraction(household):
    client, store, cid, dana, _ = household
    post(client, cid, "w_reaction", "buy_lot", dana)
    assert client.get(f"/clients/{cid}/profile", headers=ALICE).json()["people"][0][
        "willingness"]["mean"] == 9.5
    assert post(client, cid, "w_reaction", None, dana).status_code == 201
    got = client.get(f"/clients/{cid}/profile", headers=ALICE).json()
    assert got["people"][0]["willingness"] is None and got["answers"] == []
    assert "profile_answer_retracted" in [e["event"] for e in audit(store)]


def test_snapshot_freezes_the_result_and_hash(household):
    client, store, cid, dana, _ = household
    post(client, cid, "w_reaction", "hold", dana)
    live = client.get(f"/clients/{cid}/profile", headers=ALICE).json()
    r = client.post(f"/clients/{cid}/profile/snapshots", headers=ALICE)
    assert r.status_code == 201, r.text
    snap = r.json()
    assert snap["inputs_hash"] == live["inputs_hash"] == snap["result"]["inputs_hash"]
    assert snap["method_version"] == METHOD_VERSION
    assert snap["result"]["ceiling"] == live["ceiling"]
    # Later answers change the live profile, not the saved version.
    post(client, cid, "w_reaction", "sell_all", dana)
    again = client.get(f"/clients/{cid}/profile/snapshots/{snap['id']}", headers=ALICE).json()
    assert again["result"]["ceiling"] == live["ceiling"]
    assert client.get(f"/clients/{cid}/profile", headers=ALICE).json()["inputs_hash"] != snap["inputs_hash"]
    client.post(f"/clients/{cid}/profile/snapshots", headers=ALICE)
    listed = client.get(f"/clients/{cid}/profile/snapshots", headers=ALICE).json()
    assert len(listed) == 2 and listed[1]["id"] == snap["id"]  # newest first
    saved = [e for e in audit(store) if e["event"] == "profile_snapshot_saved"]
    assert saved[0]["inputs"]["snapshot_id"] == snap["id"]


def test_snapshot_of_another_client_is_404(household):
    client, _, cid, _, _ = household
    snap = client.post(f"/clients/{cid}/profile/snapshots", headers=ALICE).json()
    other = client.post("/clients", json={"name": "Other"}, headers=ALICE).json()
    assert client.get(f"/clients/{other['id']}/profile/snapshots/{snap['id']}",
                      headers=ALICE).status_code == 404


def test_another_firms_client_is_404(household):
    client, _, cid, dana, _ = household
    snap = client.post(f"/clients/{cid}/profile/snapshots", headers=ALICE).json()
    for method, path, body in [
        ("get", f"/clients/{cid}/people", None),
        ("post", f"/clients/{cid}/people", {"name": "Planted"}),
        ("post", f"/clients/{cid}/profile/inputs",
         {"question_id": "w_reaction", "answer": "hold", "person_id": dana["id"]}),
        ("get", f"/clients/{cid}/profile", None),
        ("post", f"/clients/{cid}/profile/snapshots", None),
        ("get", f"/clients/{cid}/profile/snapshots", None),
        ("get", f"/clients/{cid}/profile/snapshots/{snap['id']}", None),
    ]:
        r = client.request(method, path, json=body, headers=BOB)
        assert r.status_code == 404, (path, r.text)
    assert len(client.get(f"/clients/{cid}/people", headers=ALICE).json()) == 2


def test_deleted_client_household_is_404(household):
    client, _, cid, _, _ = household
    assert client.delete(f"/clients/{cid}", headers=ALICE).status_code == 204
    assert client.get(f"/clients/{cid}/profile", headers=ALICE).status_code == 404
    assert client.get(f"/clients/{cid}/people", headers=ALICE).status_code == 404


def test_empty_household_profile(api):
    client, _ = api
    c = client.post("/clients", json={"name": "New"}, headers=ALICE).json()
    got = client.get(f"/clients/{c['id']}/profile", headers=ALICE).json()
    assert got["people"] == [] and got["ceiling"] is None and got["provisional"] is True
    assert got["plain_summary"].endswith(METHOD_STATUS)
