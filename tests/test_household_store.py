"""Household and risk-profile store methods, run against both stores.

LocalStore on disk, and SupabaseStore on a fresh Postgres with the real migrations (RLS
on). Database-only guarantees (grants, triggers) are in test_supabase_store.py.
"""

import pytest

pytest.importorskip("fastapi")

from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.store import LocalStore  # noqa: E402
from advisor_engine.api.store_base import StoreSession  # noqa: E402


@pytest.fixture(params=["local", "supabase"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalStore(tmp_path)
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    return SupabaseStore(request.getfixturevalue("supabase_db"), MemoryStorage(),
                         cache_dir=tmp_path / "cache")


def session(store, user):
    db = store.as_user(dev_user_id(user), f"{user}@dev.local")
    return db, db.get_or_create_adviser(dev_user_id(user), f"{user}@dev.local")


@pytest.fixture
def alice(store):
    db, adv = session(store, "alice")
    client = db.create_client(adv["firm_id"], adv["id"], {"name": "Dana and Marcus"})
    return db, adv, adv["firm_id"], client["id"]


def answer(db, adv, cid, question_id, value, dimension, person_id=None):
    return db.add_profile_input(adv["firm_id"], cid, adv["id"], {
        "person_id": person_id, "dimension": dimension, "question_id": question_id,
        "answer": value, "method_version": "draft-2"})


def test_people_one_primary_one_partner(alice):
    db, adv, fid, cid = alice
    dana = db.create_person(fid, cid, adv["id"], {"name": "Dana", "role": "primary"})
    marcus = db.create_person(fid, cid, adv["id"], {"name": "Marcus", "role": "partner"})
    assert [p["name"] for p in db.list_people(fid, cid)] == ["Dana", "Marcus"]
    assert {dana["role"], marcus["role"]} == {"primary", "partner"}
    assert dana["client_id"] == cid and dana["created_by"] == adv["id"]
    for role in ("primary", "partner"):
        with pytest.raises(ValueError, match="already has"):
            db.create_person(fid, cid, adv["id"], {"name": "Third", "role": role})
    with pytest.raises(ValueError, match="primary or partner"):
        db.create_person(fid, cid, adv["id"], {"name": "Kid", "role": "child"})
    assert len(db.list_people(fid, cid)) == 2


def test_inputs_are_kept_in_order_with_retractions(alice):
    db, adv, fid, cid = alice
    dana = db.create_person(fid, cid, adv["id"], {"name": "Dana", "role": "primary"})
    answer(db, adv, cid, "w_reaction", "hold", "willingness", dana["id"])
    answer(db, adv, cid, "w_reaction", None, "willingness", dana["id"])
    answer(db, adv, cid, "w_loss_limits", {"uncomfortable_pct": 0.1, "act_pct": 0.2},
           "willingness", dana["id"])
    answer(db, adv, cid, "n_required_return", 0.058, "need")
    rows = db.list_profile_inputs(fid, cid)
    assert [r["answer"] for r in rows] == [
        "hold", None, {"uncomfortable_pct": 0.1, "act_pct": 0.2}, 0.058]
    assert rows[0]["source"] == "advisor_entered" and rows[0]["entered_by"] == adv["id"]
    assert rows[0]["method_version"] == "draft-2" and rows[3]["person_id"] is None
    assert [r["recorded_at"] for r in rows] == sorted(r["recorded_at"] for r in rows)


def test_snapshots_save_list_and_get(alice):
    db, adv, fid, cid = alice
    a = db.save_profile_snapshot(fid, cid, adv["id"], {
        "method_version": "draft-2", "inputs_hash": "h1", "result": {"ceiling": None}})
    b = db.save_profile_snapshot(fid, cid, adv["id"], {
        "method_version": "draft-2", "inputs_hash": "h2", "result": {"ceiling": {"value": 4.0}}})
    assert [s["inputs_hash"] for s in db.list_profile_snapshots(fid, cid)] == ["h2", "h1"]
    got = db.get_profile_snapshot(fid, b["id"])
    assert got["result"] == {"ceiling": {"value": 4.0}} and got["created_by"] == adv["id"]
    assert db.get_profile_snapshot(fid, a["id"])["inputs_hash"] == "h1"
    assert db.get_profile_snapshot(fid, "not-an-id") is None


def test_store_offers_no_way_to_change_inputs_or_snapshots(alice):
    db, adv, fid, cid = alice
    names = [n for n in dir(StoreSession) if not n.startswith("_")]
    for n in names:
        if "profile" in n:
            assert n.startswith(("add_", "save_", "list_", "get_")), n
    for cls in (type(db),):
        assert not [n for n in dir(cls) if "profile" in n
                    and n.startswith(("update", "delete", "remove", "edit", "set"))]
    # What a caller gets back is a copy: changing it changes nothing stored.
    row = answer(db, adv, cid, "a_cash_months", "3_6", "ability")
    row["answer"] = "over_12"
    snap = db.save_profile_snapshot(fid, cid, adv["id"], {
        "method_version": "draft-2", "inputs_hash": "h", "result": {"x": 1}})
    snap["result"]["x"] = 2
    assert db.list_profile_inputs(fid, cid)[0]["answer"] == "3_6"
    assert db.get_profile_snapshot(fid, snap["id"])["result"] == {"x": 1}


def test_another_firm_sees_nothing(store, alice):
    db, adv, fid, cid = alice
    dana = db.create_person(fid, cid, adv["id"], {"name": "Dana", "role": "primary"})
    answer(db, adv, cid, "w_reaction", "hold", "willingness", dana["id"])
    snap = db.save_profile_snapshot(fid, cid, adv["id"], {
        "method_version": "draft-2", "inputs_hash": "h", "result": {}})
    bob, bob_adv = session(store, "bob")
    firms = [bob_adv["firm_id"]]
    if not isinstance(store, LocalStore):
        # With Supabase, RLS holds even if a route bug handed Bob's session Alice's firm id
        # (LocalStore is one trusted process: its sessions are scoped by the firm id alone).
        firms.append(fid)
    for firm in firms:
        assert bob.list_people(firm, cid) == []
        assert bob.list_profile_inputs(firm, cid) == []
        assert bob.list_profile_snapshots(firm, cid) == []
        assert bob.get_profile_snapshot(firm, snap["id"]) is None


def test_a_deleted_clients_household_is_hidden(alice):
    db, adv, fid, cid = alice
    db.create_person(fid, cid, adv["id"], {"name": "Dana", "role": "primary"})
    answer(db, adv, cid, "a_cash_months", "3_6", "ability")
    snap = db.save_profile_snapshot(fid, cid, adv["id"], {
        "method_version": "draft-2", "inputs_hash": "h", "result": {}})
    db.delete_client(fid, cid, adv["id"])
    assert db.list_people(fid, cid) == []
    assert db.list_profile_inputs(fid, cid) == []
    assert db.list_profile_snapshots(fid, cid) == []
    assert db.get_profile_snapshot(fid, snap["id"]) is None
