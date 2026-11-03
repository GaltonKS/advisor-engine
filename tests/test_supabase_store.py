"""Database-level guarantees from supabase/migrations, checked on real Postgres.

The API tests cover behaviour through HTTP; these check what holds even if the API code
is wrong: row-level security, the append-only audit log, and onboarding rules.
"""

import hashlib

import httpx
import pytest

psycopg = pytest.importorskip("psycopg")

from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.supabase_store import (  # noqa: E402
    MemoryStorage,
    SupabaseStorage,
    SupabaseStore,
)
from advisor_engine.paths import DEFAULT_HOLDINGS  # noqa: E402

CSV = DEFAULT_HOLDINGS.read_bytes()


@pytest.fixture
def store(supabase_db, tmp_path):
    return SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")


def as_(store, user):
    return store.as_user(dev_user_id(user), f"{user}@dev.local")


@pytest.fixture
def alice_world(store):
    """Alice's firm with a client, a portfolio, a draft, a conversation and an audit row."""
    db = as_(store, "alice")
    adv = db.get_or_create_adviser(None, None)
    fid = adv["firm_id"]
    client = db.create_client(fid, adv["id"], {"name": "Household A"})
    p = db.add_portfolio(fid, client["id"], adv["id"], "h.csv", CSV, hashlib.sha256(CSV).hexdigest())
    db.create_draft(fid, {"portfolio_id": p["id"], "created_by": adv["id"], "original": "x",
                          "tool_calls": []})
    db.save_conversation(fid, p["id"], {"history": [], "messages": [{"role": "user"}]})
    db.write_audit(fid, {"event": "turn", "portfolio_id": p["id"]})
    return {"firm_id": fid, "client": client, "portfolio": p, "adviser": adv}


def raw(url, user, sql, params=()):
    """Run SQL as `user` the way SupabaseSession does, with no firm filter of its own."""
    with psycopg.connect(url) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)",
                     [f'{{"sub": "{dev_user_id(user)}", "role": "authenticated"}}'])
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


TABLES = ["firms", "advisers", "clients", "portfolios", "conversations", "drafts", "audit_events"]


@pytest.mark.parametrize("table", TABLES)
def test_another_firm_sees_no_rows_even_without_a_filter(supabase_db, store, alice_world, table):
    as_(store, "bob").get_or_create_adviser(None, None)
    assert raw(supabase_db, "alice", f"select count(*) from public.{table}")[0][0] >= 1
    assert raw(supabase_db, "bob", f"select count(*) from public.{table}")[0][0] == (
        1 if table in ("firms", "advisers") else 0)  # only Bob's own firm and adviser row


def test_wrong_firm_id_from_the_api_still_cannot_cross_firms(store, alice_world):
    """If a route bug handed Bob's session Alice's firm id, the database still says no."""
    bob = as_(store, "bob")
    bob_adv = bob.get_or_create_adviser(None, None)
    fid = alice_world["firm_id"]
    assert bob.list_clients(fid) == []
    assert bob.get_portfolio(fid, alice_world["portfolio"]["id"]) is None
    assert bob.read_audit(fid) == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        bob.create_client(fid, bob_adv["id"], {"name": "planted"})
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        bob.write_audit(fid, {"event": "forged"})


@pytest.mark.parametrize("sql", [
    "update public.audit_events set event = 'edited'",
    "delete from public.audit_events",
])
def test_audit_log_is_append_only(supabase_db, alice_world, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", sql)


def test_another_firm_cannot_rename_or_delete_a_client_without_a_filter(supabase_db, store,
                                                                       alice_world):
    as_(store, "bob").get_or_create_adviser(None, None)
    raw(supabase_db, "bob", "update public.clients set name = 'x', deleted_at = now()")
    row = raw(supabase_db, "alice", "select name, deleted_at from public.clients")[0]
    assert row == ("Household A", None)


def test_clients_cannot_be_hard_deleted(supabase_db, alice_world):
    """Deleting a client is a soft delete; the row (and what refers to it) stays."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "delete from public.clients")


@pytest.mark.parametrize("sql", [
    "select count(*) from market.prices",
    "select count(*) from market.fetches",
    "insert into market.prices values ('FMP', 'AAA', '2026-10-31', 1.0)",
])
def test_licensed_prices_are_not_readable_or_writable_by_advisers(supabase_db, alice_world, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", sql)


def test_anon_cannot_reach_the_price_cache(supabase_db):
    with psycopg.connect(supabase_db) as conn:
        conn.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from market.prices")


def test_advisers_cannot_change_their_firm_role_or_status(supabase_db, alice_world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "update public.advisers set role = 'owner', status = 'active'")


def test_anon_role_has_no_table_access(supabase_db, alice_world):
    with psycopg.connect(supabase_db) as conn:
        conn.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from public.clients")


def test_pending_adviser_sees_nothing_and_cannot_use_the_assistant(supabase_db, store, alice_world):
    """Ready for CRD checks: parking an adviser as pending cuts off their firm's data."""
    with psycopg.connect(supabase_db, autocommit=True) as conn:
        conn.execute("update public.advisers set status = 'pending' where id = %s",
                     [dev_user_id("alice")])
    alice = as_(store, "alice")
    assert alice.list_clients(alice_world["firm_id"]) == []
    assert alice.bump_turns(alice_world["adviser"]["id"], 100) is False


def test_daily_turn_limit_is_enforced_in_the_database(store):
    db = as_(store, "carol")
    adv = db.get_or_create_adviser(None, None)
    assert [db.bump_turns(adv["id"], 2) for _ in range(3)] == [True, True, False]


def test_first_sign_in_is_idempotent(store):
    db = as_(store, "carol")
    assert db.get_or_create_adviser(None, None) == db.get_or_create_adviser(None, None)


# --- holdings files ------------------------------------------------------------------------

def test_cached_holdings_file_is_checked_against_its_hash(store, alice_world):
    db = as_(store, "alice")
    fid, pid = alice_world["firm_id"], alice_world["portfolio"]["id"]
    path = db.holdings_file(fid, pid)
    assert path.read_bytes() == CSV and path.is_relative_to(db.firm_dir(fid))
    path.write_bytes(b"tampered")  # a stale or altered cache copy is replaced
    assert db.holdings_file(fid, pid).read_bytes() == CSV
    store.storage.objects[f"{fid}/{pid}.csv"] = b"ticker,shares,price,sector\n"
    path.unlink()
    with pytest.raises(ValueError, match="hash"):
        db.holdings_file(fid, pid)


@pytest.mark.parametrize("key, bearer", [("sb_secret_abc", False), ("eyJhbGciOi.legacy.jwt", True)])
def test_storage_requests_use_the_secret_key(key, bearer):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    s = SupabaseStorage("https://x.supabase.co", key,
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    s.put("f/p.csv", b"data")
    assert s.get("f/p.csv") == b"ok"
    assert str(seen[0].url) == "https://x.supabase.co/storage/v1/object/holdings/f/p.csv"
    assert all(r.headers["apikey"] == key for r in seen)
    assert ("authorization" in seen[0].headers) == bearer


# --- adviser approval ---------------------------------------------------------------------

def test_new_sign_up_is_pending_and_cut_off(supabase_db, store, alice_world):
    dave = as_(store, "dave")
    adv = dave.get_or_create_adviser(None, None)
    assert adv["status"] == "pending"
    assert dave.list_clients(adv["firm_id"]) == []
    assert dave.list_clients(alice_world["firm_id"]) == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS refuses the write
        dave.create_client(adv["firm_id"], adv["id"], {"name": "Mine"})


def test_registration_is_stored_for_review_and_only_while_pending(supabase_db, store):
    dave = as_(store, "dave")
    dave.get_or_create_adviser(None, None)
    with pytest.raises(PermissionError):  # the database checks the details too
        dave.submit_registration("Dave", "12-34", "Dave Wealth", user_id=dev_user_id("dave"))
    adv = dave.submit_registration("Dave Adams", "1234567", "Dave Wealth LLC",
                                   user_id=dev_user_id("dave"))
    assert adv["crd"] == "1234567" and adv["full_name"] == "Dave Adams"
    assert adv["registration_submitted_at"] is not None and adv["status"] == "pending"
    alice = as_(store, "alice")  # already active: registration is closed to her
    with pytest.raises(PermissionError):
        alice.submit_registration("A", "1", "F", user_id=dev_user_id("alice"))


def test_advisers_cannot_approve_themselves(supabase_db, store):
    as_(store, "dave").get_or_create_adviser(None, None)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "dave", "select app.approve_adviser('dave@dev.local')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "dave", "update public.advisers set status = 'active'")


def test_operator_approves_into_their_own_or_an_existing_firm(supabase_db, store, alice_world):
    from advisor_engine.api import admin

    dave = as_(store, "dave")
    dave.get_or_create_adviser(None, None)
    dave.submit_registration("Dave Adams", "1234567", "Dave Wealth LLC", user_id=dev_user_id("dave"))
    assert [r["email"] for r in admin.list_pending(supabase_db)] == ["dave@dev.local"]
    row = admin.approve(supabase_db, "dave@dev.local", alice_world["firm_id"])
    assert row["status"] == "active" and row["role"] == "member"
    assert [c["name"] for c in dave.list_clients(alice_world["firm_id"])] == ["Household A"]
    assert admin.list_pending(supabase_db) == []
    with pytest.raises(psycopg.errors.RaiseException):  # nothing left to approve
        admin.approve(supabase_db, "dave@dev.local")


def test_approval_names_the_new_firm_as_registered(supabase_db, store):
    from advisor_engine.api import admin

    dave = as_(store, "dave")
    adv = dave.get_or_create_adviser(None, None)
    dave.submit_registration("Dave Adams", "1234567", "Dave Wealth LLC", user_id=dev_user_id("dave"))
    admin.approve(supabase_db, "dave@dev.local")
    assert dave.get_firm(adv["firm_id"])["name"] == "Dave Wealth LLC"


def test_admin_command_lists_and_approves(supabase_db, store, monkeypatch, capsys):
    from advisor_engine.api import admin

    dave = as_(store, "dave")
    dave.get_or_create_adviser(None, None)
    dave.submit_registration("Dave Adams", "1234567", "Dave Wealth LLC", user_id=dev_user_id("dave"))
    monkeypatch.setenv("SUPABASE_DB_URL", supabase_db)
    assert admin.main(["pending"]) == 0
    assert "dave@dev.local  name=Dave Adams  CRD=1234567  firm=Dave Wealth LLC" in capsys.readouterr().out
    assert admin.main(["approve", "dave@dev.local"]) == 0
    assert "Approved dave@dev.local" in capsys.readouterr().out
    monkeypatch.delenv("SUPABASE_DB_URL")
    assert admin.main(["pending"]) == 2


# --- approval audit trail ---------------------------------------------------------------------

def test_approval_audit_trail_is_append_only_and_private(supabase_db, store):
    from advisor_engine.api import admin

    as_(store, "dave").get_or_create_adviser(None, None)
    admin.approve(supabase_db, "dave@dev.local")
    with psycopg.connect(supabase_db, autocommit=True) as conn:  # even the owner can't edit
        assert conn.execute("select reviewed_by from public.adviser_reviews").fetchone()[0].startswith("sql:")
        for sql in ("update public.adviser_reviews set decision = 'rejected'",
                    "delete from public.adviser_reviews", "truncate public.adviser_reviews"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(sql)
    for user in ("alice", "dave"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            raw(supabase_db, user, "select count(*) from public.adviser_reviews")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "select app.review_adviser('erin@dev.local', 'approved', 'me', '{}', null, 'x')")
