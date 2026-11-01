"""Multi-factor sign-in: settings, token claims, the API's gates, authenticator routes,
the firm owner's view, the operator commands, and the database's own enforcement.

API tests sign HS256 tokens with a test secret (as the token tests in test_api.py do)
and run against both stores. Supabase Auth's admin API is replaced by MemoryAuthAdmin,
or by httpx.MockTransport for the real adapter. Nothing here reaches the network.
"""

import time

import httpx
import pytest

pytest.importorskip("fastapi")
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from advisor_engine.api import admin  # noqa: E402
from advisor_engine.api.auth import FRESH_MFA_SECONDS, User, dev_user_id  # noqa: E402
from advisor_engine.api.auth_admin import (  # noqa: E402
    AuthAdminError,
    MemoryAuthAdmin,
    SupabaseAuthAdmin,
)
from advisor_engine.api.main import create_app  # noqa: E402
from advisor_engine.api.settings import Settings, load_settings  # noqa: E402
from advisor_engine.api.store import LocalStore  # noqa: E402

SUPABASE_URL = "https://example.supabase.co"
SECRET = "test-secret-that-is-long-enough-for-hs256"


def token(user: str, aal: str = "aal2", code_age: float | None = 0) -> str:
    """A Supabase-shaped access token for a dev user. `code_age`: seconds since the
    authenticator code was entered (None: never, a password-only session)."""
    now = int(time.time())
    amr = [{"method": "password", "timestamp": now - 3600}]
    if code_age is not None:
        amr.append({"method": "totp", "timestamp": int(now - code_age)})
    claims = {"sub": dev_user_id(user), "email": f"{user}@dev.local", "aud": "authenticated",
              "iss": f"{SUPABASE_URL}/auth/v1", "exp": now + 600, "aal": aal, "amr": amr,
              "role": "authenticated"}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def bearer(user: str, **kw) -> dict:
    return {"Authorization": f"Bearer {token(user, **kw)}"}


PASSWORD_ONLY = {"aal": "aal1", "code_age": None}
STALE = {"code_age": FRESH_MFA_SECONDS + 60}


@pytest.fixture(params=["local", "supabase"])
def make_api(request, tmp_path):
    """make_api(mode) -> (TestClient, MemoryAuthAdmin, approve). With the Supabase store
    the database's own mode is set to match, as the operator would."""
    def make(mode: str, require_approval: bool = False):
        if request.param == "local":
            store = LocalStore(tmp_path, require_approval=require_approval)

            def approve(email, firm=None):
                try:
                    store.approve_adviser(email, firm)
                except LookupError:
                    pass  # already active
        else:
            from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

            db = request.getfixturevalue("supabase_db")
            admin.set_mfa_mode(db, mode)
            store = SupabaseStore(db, MemoryStorage(), cache_dir=tmp_path / "cache")

            def approve(email, firm=None):
                if email.startswith("dave"):  # the fixture's never-approved sign-up
                    admin.approve(db, email, firm)
        auth_admin = MemoryAuthAdmin()
        settings = Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                            supabase_jwt_secret=SECRET, require_mfa=mode,
                            mfa_deadline="2026-11-08")
        return TestClient(create_app(settings, store=store, auth_admin=auth_admin)), auth_admin, approve
    return make


def new_client(client, user="alice", **kw):
    r = client.post("/clients", json={"name": "Household A"}, headers=bearer(user, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def audit_events(client, user="alice"):
    return client.get("/audit", headers=bearer(user)).json()


# --- settings and tokens -----------------------------------------------------------------

def test_mfa_settings_are_read_and_validated(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    for k in ("RENDER", "SUPABASE_DB_URL", "ADVISOR_AUTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("ADVISOR_REQUIRE_MFA", raising=False)
    assert load_settings().mfa == "off"
    monkeypatch.setenv("ADVISOR_REQUIRE_MFA", "Required")
    monkeypatch.setenv("ADVISOR_MFA_DEADLINE", "2026-11-08")
    s = load_settings()
    assert (s.mfa, s.mfa_deadline) == ("required", "2026-11-08")
    monkeypatch.setenv("ADVISOR_REQUIRE_MFA", "sometimes")
    with pytest.raises(ValueError, match="ADVISOR_REQUIRE_MFA"):
        load_settings()
    monkeypatch.setenv("ADVISOR_REQUIRE_MFA", "enrol")
    monkeypatch.setenv("ADVISOR_MFA_DEADLINE", "next week")
    with pytest.raises(ValueError, match="ADVISOR_MFA_DEADLINE"):
        load_settings()


def test_dev_auth_never_asks_for_a_second_factor(tmp_path):
    assert Settings(auth="dev", store_dir=tmp_path, require_mfa="required").mfa == "off"


def test_code_freshness_comes_from_the_amr_claim():
    now = 1_000_000
    assert User("u", "e").mfa_age(now) is None and not User("u", "e").mfa_fresh(now)
    u = User("u", "e", aal="aal2", amr=(("password", now - 5000), ("totp", now - 30)))
    assert u.mfa_age(now) == 30 and u.mfa_fresh(now)
    assert not User("u", "e", amr=(("totp", now - FRESH_MFA_SECONDS - 1),)).mfa_fresh(now)
    assert not User("u", "e", amr=(("password", now),)).mfa_fresh(now)  # a password isn't a code


def test_token_aal_and_amr_are_parsed_and_malformed_values_ignored(tmp_path):
    from advisor_engine.api.auth import verify_supabase_token

    s = Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                 supabase_jwt_secret=SECRET)
    u = verify_supabase_token(token("alice", code_age=10), s)
    assert u.aal == "aal2" and u.mfa_fresh()
    claims = jwt.decode(token("alice"), SECRET, algorithms=["HS256"], audience="authenticated")
    claims.update(aal="aal3", amr="totp")
    odd = verify_supabase_token(jwt.encode(claims, SECRET, algorithm="HS256"), s)
    assert odd.aal == "aal1" and odd.amr == ()


# --- the API's gates ---------------------------------------------------------------------

def test_required_mode_refuses_password_only_sessions_except_me(make_api):
    client, _, _ = make_api("required")
    me = client.get("/me", headers=bearer("alice", **PASSWORD_ONLY))
    assert me.status_code == 200
    assert me.json()["mfa"] == {"mode": "required", "deadline": "2026-11-08", "aal": "aal1",
                                "fresh_seconds": FRESH_MFA_SECONDS}
    r = client.get("/clients", headers=bearer("alice", **PASSWORD_ONLY))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_required"
    assert client.post("/clients", json={"name": "X"},
                       headers=bearer("alice", **PASSWORD_ONLY)).status_code == 403
    assert client.get("/clients", headers=bearer("alice")).status_code == 200


def test_enrol_and_off_modes_still_accept_password_only_sessions(make_api):
    for mode in ("enrol", "off"):
        client, _, _ = make_api(mode)
        assert client.get("/clients", headers=bearer("alice", **PASSWORD_ONLY)).status_code == 200


def test_mfa_is_checked_before_approval_and_registration_needs_it(make_api):
    client, _, _ = make_api("required", require_approval=True)
    r = client.post("/me/registration", headers=bearer("dave", **PASSWORD_ONLY),
                    json={"full_name": "Dave Adams", "crd": "1234567", "firm_name": "Dave Wealth"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_required"
    r = client.get("/clients", headers=bearer("dave"))  # code entered, but not yet approved
    assert r.status_code == 403 and r.json()["detail"] == "Your account is awaiting approval."
    ok = client.post("/me/registration", headers=bearer("dave"),
                     json={"full_name": "Dave Adams", "crd": "1234567", "firm_name": "Dave Wealth"})
    assert ok.status_code == 200, ok.text


# --- fresh code for sensitive actions ----------------------------------------------------

def test_deleting_a_client_needs_a_fresh_code(make_api):
    client, _, _ = make_api("required")
    c = new_client(client)
    r = client.delete(f"/clients/{c['id']}", headers=bearer("alice", **STALE))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_fresh_required"
    assert client.get(f"/clients/{c['id']}", headers=bearer("alice")).status_code == 200
    assert client.delete(f"/clients/{c['id']}", headers=bearer("alice", code_age=5)).status_code == 204
    assert client.get(f"/clients/{c['id']}", headers=bearer("alice")).status_code == 404


def test_client_deletion_audit_records_the_fresh_code_and_session_level(make_api):
    client, _, _ = make_api("required")
    c = new_client(client)
    client.delete(f"/clients/{c['id']}", headers=bearer("alice", code_age=5))
    store = client.app.state.store
    me = client.get("/me", headers=bearer("alice")).json()
    rows = store.as_user(dev_user_id("alice"), "alice@dev.local", aal="aal2").read_audit(
        me["adviser"]["firm_id"])
    deleted = [r for r in rows if r["event"] == "client_deleted"][-1]
    assert deleted["fresh_code"] is True and deleted["aal"] == "aal2"


def test_enrol_mode_asks_for_a_fresh_code_only_from_sessions_that_have_one(make_api):
    client, _, _ = make_api("enrol")
    c1, c2 = new_client(client), new_client(client)
    # Not enrolled yet: no code to give, so the grace period lets it through.
    assert client.delete(f"/clients/{c1['id']}",
                         headers=bearer("alice", **PASSWORD_ONLY)).status_code == 204
    # Enrolled and verified: the same rule as `required`.
    assert client.delete(f"/clients/{c2['id']}", headers=bearer("alice", **STALE)).status_code == 403


def test_off_mode_never_asks_for_a_code(make_api):
    client, _, _ = make_api("off")
    c = new_client(client)
    assert client.delete(f"/clients/{c['id']}",
                         headers=bearer("alice", **PASSWORD_ONLY)).status_code == 204


# --- authenticators ----------------------------------------------------------------------

def test_enrolment_is_audited_only_once_supabase_confirms_it(make_api):
    client, auth_admin, _ = make_api("required")
    uid = dev_user_id("alice")
    auth_admin.add(uid, "f-pending", status="unverified")
    for fid in ("nope", "f-pending"):
        assert client.post("/me/mfa/enrolled", json={"factor_id": fid},
                           headers=bearer("alice")).status_code == 404
    auth_admin.add(uid, "f-1", friendly_name="Work phone")
    r = client.post("/me/mfa/enrolled", json={"factor_id": "f-1"}, headers=bearer("alice"))
    assert r.status_code == 200 and r.json()["friendly_name"] == "Work phone"
    assert "secret" not in r.json()
    assert [e["event"] for e in audit_events(client)][0] == "mfa_enrolled"


def test_removing_an_authenticator_needs_a_fresh_code_and_never_the_last(make_api):
    client, auth_admin, _ = make_api("required")
    uid = dev_user_id("alice")
    auth_admin.add(uid, "f-1", friendly_name="Phone")
    assert client.delete("/me/mfa/factors/f-1", headers=bearer("alice", **STALE)).status_code == 403
    r = client.delete("/me/mfa/factors/f-1", headers=bearer("alice", code_age=5))
    assert r.status_code == 409 and "only authenticator" in r.json()["detail"]
    auth_admin.add(uid, "f-2", friendly_name="Backup")
    assert client.delete("/me/mfa/factors/f-1",
                         headers=bearer("alice", code_age=5)).status_code == 204
    assert [f["id"] for f in auth_admin.list_factors(uid)] == ["f-2"]
    assert client.delete("/me/mfa/factors/f-1",
                         headers=bearer("alice", code_age=5)).status_code == 404
    assert audit_events(client)[0]["event"] == "mfa_factor_removed"


def test_an_adviser_cannot_remove_someone_elses_authenticator(make_api):
    client, auth_admin, _ = make_api("required")
    auth_admin.add(dev_user_id("bob"), "bob-f", friendly_name="Bob's phone")
    auth_admin.add(dev_user_id("bob"), "bob-f2")
    assert client.delete("/me/mfa/factors/bob-f",
                         headers=bearer("alice", code_age=5)).status_code == 404
    assert len(auth_admin.list_factors(dev_user_id("bob"))) == 2


# --- the firm owner's view ---------------------------------------------------------------

def test_owner_sees_which_advisers_have_enrolled_and_members_cannot(make_api):
    client, auth_admin, approve = make_api("enrol", require_approval=True)
    client.get("/me", headers=bearer("alice"))
    approve("alice@dev.local")
    firm_id = client.get("/me", headers=bearer("alice")).json()["adviser"]["firm_id"]
    client.get("/me", headers=bearer("dave"))
    approve("dave@dev.local", firm_id)  # dave joins alice's firm as a member
    auth_admin.add(dev_user_id("alice"), "a1", created_at="2026-11-01T09:00:00+00:00")
    auth_admin.add(dev_user_id("alice"), "a2", created_at="2026-11-02T09:00:00+00:00")
    auth_admin.add(dev_user_id("alice"), "a3", status="unverified")
    auth_admin.add(dev_user_id("bob"), "b1")  # another firm: never listed

    view = client.get("/firm/mfa", headers=bearer("alice"))
    assert view.status_code == 200, view.text
    body = view.json()
    assert (body["mode"], body["deadline"]) == ("enrol", "2026-11-08")
    rows = {r["email"]: r for r in body["advisers"]}
    assert set(rows) == {"alice@dev.local", "dave@dev.local"}
    assert rows["alice@dev.local"]["enrolled"] and rows["alice@dev.local"]["factor_count"] == 2
    assert rows["alice@dev.local"]["latest_factor_at"].startswith("2026-11-02")
    assert not rows["dave@dev.local"]["enrolled"] and rows["dave@dev.local"]["factor_count"] == 0
    assert rows["dave@dev.local"]["role"] == "member"

    r = client.get("/firm/mfa", headers=bearer("dave"))
    assert r.status_code == 403 and r.json()["detail"] == "Only the firm's owner can see this."


def test_sign_in_service_errors_are_plain(make_api):
    client, _, _ = make_api("required")

    class Down(MemoryAuthAdmin):
        def list_factors(self, user_id):
            raise AuthAdminError("Couldn't reach the sign-in service. Please try again.")

    store = client.app.state.store
    settings = client.app.state.settings
    down = TestClient(create_app(settings, store=store, auth_admin=Down()))
    r = down.get("/firm/mfa", headers=bearer("alice"))
    assert r.status_code == 502 and "sign-in service" in r.json()["detail"]


# --- the Supabase Auth admin adapter -----------------------------------------------------

def admin_with(handler, key="sb_secret_abc"):
    return SupabaseAuthAdmin(SUPABASE_URL, key, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_admin_adapter_lists_and_deletes_with_the_secret_key():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers.get("apikey"),
                     req.headers.get("authorization")))
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": "f1", "friendly_name": "Phone",
                                              "factor_type": "totp", "status": "verified",
                                              "secret": "never-kept", "created_at": "x"}])
        return httpx.Response(200, json={})

    a = admin_with(handler)
    assert a.list_factors("u1") == [{"id": "f1", "friendly_name": "Phone", "factor_type": "totp",
                                     "status": "verified", "created_at": "x", "updated_at": None}]
    a.delete_factor("u1", "f1")
    assert seen == [("GET", "/auth/v1/admin/users/u1/factors", "sb_secret_abc", None),
                    ("DELETE", "/auth/v1/admin/users/u1/factors/f1", "sb_secret_abc", None)]
    legacy = []
    admin_with(lambda r: legacy.append(r.headers.get("authorization")) or httpx.Response(
        200, json={"factors": []}), key="eyJ.legacy").list_factors("u1")
    assert legacy == ["Bearer eyJ.legacy"]


@pytest.mark.parametrize("response, error", [
    (httpx.Response(404), LookupError),
    (httpx.Response(500), AuthAdminError),
])
def test_admin_adapter_errors_never_show_the_key_or_url(response, error):
    with pytest.raises(error) as e:
        admin_with(lambda r: response).list_factors("u1")
    assert "sb_secret" not in str(e.value) and "supabase.co" not in str(e.value)


def test_admin_adapter_network_failure_is_plain():
    def boom(req):
        raise httpx.ConnectError("connect failed to https://example.supabase.co?apikey=sb_secret_abc")

    with pytest.raises(AuthAdminError) as e:
        admin_with(boom).list_factors("u1")
    assert "sb_secret" not in str(e.value) and e.value.__cause__ is None


# --- the database's own enforcement ------------------------------------------------------

psycopg = pytest.importorskip("psycopg")


def raw(url, user, sql, params=(), aal=None, code_age=None):
    """Run SQL as `user` the way Supabase's REST API would. `code_age`: seconds since an
    authenticator code (None: no code in the token's `amr`)."""
    claims = {"sub": dev_user_id(user), "email": f"{user}@dev.local", "role": "authenticated"}
    if aal:
        claims["aal"] = aal
    if code_age is not None:
        claims["amr"] = [{"method": "password", "timestamp": int(time.time()) - 3600},
                         {"method": "totp", "timestamp": int(time.time() - code_age)}]
    import json

    with psycopg.connect(url) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", [json.dumps(claims)])
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


TABLES = ["firms", "advisers", "clients", "portfolios", "conversations", "drafts",
          "audit_events", "usage"]


@pytest.fixture
def alice_client(supabase_db, tmp_path):
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    store = SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")
    db = store.as_user(dev_user_id("alice"), "alice@dev.local", aal="aal2")
    adv = db.get_or_create_adviser(None, None)
    import hashlib

    from advisor_engine.assistant._paths import DEFAULT_HOLDINGS

    fid, csv = adv["firm_id"], DEFAULT_HOLDINGS.read_bytes()
    c = db.create_client(fid, adv["id"], {"name": "Household A"})
    p = db.add_portfolio(fid, c["id"], adv["id"], "h.csv", csv, hashlib.sha256(csv).hexdigest())
    db.create_draft(fid, {"portfolio_id": p["id"], "created_by": adv["id"], "original": "x",
                          "tool_calls": []})
    db.save_conversation(fid, p["id"], {"history": [], "messages": []})
    db.write_audit(fid, {"event": "turn"})
    db.bump_turns(adv["id"], 5)
    return adv


@pytest.mark.parametrize("table", TABLES)
def test_required_mode_hides_every_table_from_password_only_sessions(supabase_db, alice_client, table):
    sql = f"select count(*) from public.{table}"
    assert raw(supabase_db, "alice", sql)[0][0] > 0  # mode off: unchanged
    admin.set_mfa_mode(supabase_db, "required")
    assert raw(supabase_db, "alice", sql, aal="aal1")[0][0] == 0
    assert raw(supabase_db, "alice", sql)[0][0] == 0  # no claim at all counts as aal1
    assert raw(supabase_db, "alice", sql, aal="aal2")[0][0] > 0


def test_required_mode_refuses_writes_and_definer_functions_below_aal2(supabase_db, alice_client):
    admin.set_mfa_mode(supabase_db, "required")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "insert into public.clients (firm_id, name, created_by) "
            "values (%s, 'X', %s)", [alice_client["firm_id"], alice_client["id"]], aal="aal1")
    assert raw(supabase_db, "alice", "select app.bump_turns(5)", aal="aal1")[0][0] is False
    assert raw(supabase_db, "alice", "select app.bump_turns(5)", aal="aal2")[0][0] is True
    with pytest.raises(psycopg.errors.RaiseException, match="multi-factor"):
        raw(supabase_db, "dave", "select app.submit_registration('Dave', '123', 'F')", aal="aal1")
    # Signing in still works at aal1: it only reads the caller's own row.
    assert raw(supabase_db, "alice", "select (app.ensure_adviser()).email", aal="aal1")[0][0]


def test_advisers_cannot_read_or_change_the_mode(supabase_db, alice_client):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "select * from app.security_settings", aal="aal2")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "select app.set_mfa_mode('off')", aal="aal2")


def test_admin_mfa_mode_command(supabase_db, monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_DB_URL", supabase_db)
    assert admin.main(["mfa-mode"]) == 0
    assert "mode: off" in capsys.readouterr().out
    assert admin.main(["mfa-mode", "required"]) == 0
    assert admin.get_mfa_mode(supabase_db) == "required"
    with pytest.raises(SystemExit):
        admin.main(["mfa-mode", "sometimes"])


def test_operator_reset_removes_every_factor_and_is_audited(supabase_db, alice_client):
    auth_admin = MemoryAuthAdmin()
    uid = dev_user_id("alice")
    auth_admin.add(uid, "f1", friendly_name="Old phone")
    auth_admin.add(uid, "f2", status="unverified")
    record = admin.reset_mfa(supabase_db, auth_admin, "ALICE@dev.local", requested_by="Alice by phone",
                             verified_by="ops: Sam", check="called firm number on file")
    assert auth_admin.list_factors(uid) == []
    assert [r["factor_id"] for r in record["inputs"]["removed"]] == ["f1", "f2"]
    events = raw(supabase_db, "alice", "select event, record from public.audit_events "
                 "order by id desc limit 1", aal="aal2")
    assert events[0][0] == "mfa_reset_by_operator"
    assert events[0][1]["inputs"]["verified_by"] == "ops: Sam"
    with pytest.raises(LookupError):
        admin.reset_mfa(supabase_db, auth_admin, "nobody@x.com", requested_by="a",
                        verified_by="b", check="c")


def test_api_and_database_modes_that_differ_are_logged(supabase_db, tmp_path, caplog):
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    admin.set_mfa_mode(supabase_db, "required")
    store = SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")
    with caplog.at_level("WARNING", logger="advisor_engine.api"):
        create_app(Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                            supabase_jwt_secret=SECRET, require_mfa="enrol"), store=store,
                   auth_admin=MemoryAuthAdmin())
    assert "API enrol, database required" in caplog.text


# --- the direct-to-Supabase paths (20261107000000_mfa_direct_paths.sql) -------------------

def owner(url, sql, params=()):
    """As the owner role, as Supabase Auth's own connection would."""
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


def add_factor(url, user, status="verified") -> str:
    import uuid

    fid = str(uuid.uuid4())
    owner(url, "insert into auth.mfa_factors (id, user_id, friendly_name, status) "
               "values (%s, %s, 'Phone', %s)", [fid, dev_user_id(user), status])
    return fid


def unenroll(url, factor_id):
    """What Supabase Auth runs for `supabase.auth.mfa.unenroll` (and for the admin API)."""
    owner(url, "delete from auth.mfa_factors where id = %s", [factor_id])


def factor_ids(url, user):
    return {str(r[0]) for r in owner(url, "select id from auth.mfa_factors where user_id = %s",
                                     [dev_user_id(user)])}


class DbAuthAdmin(MemoryAuthAdmin):
    """Supabase Auth's admin API over the test database: deletes go through the trigger."""

    def __init__(self, url):
        super().__init__()
        self.url = url

    def list_factors(self, user_id):
        return [{"id": str(r[0]), "friendly_name": r[1], "factor_type": "totp", "status": r[2],
                 "created_at": None, "updated_at": None}
                for r in owner(self.url, "select id, friendly_name, status from auth.mfa_factors "
                                         "where user_id = %s", [user_id])]

    def delete_factor(self, user_id, factor_id):
        try:
            unenroll(self.url, factor_id)
        except psycopg.Error:
            raise AuthAdminError("The sign-in service refused the request (HTTP 500).") from None


def test_a_verified_authenticator_cannot_be_removed_by_calling_supabase_directly(supabase_db):
    fid = add_factor(supabase_db, "alice")
    with pytest.raises(psycopg.Error) as e:
        unenroll(supabase_db, fid)
    assert e.value.sqlstate == "PT403" and "Security page" in str(e.value)
    assert fid in factor_ids(supabase_db, "alice")
    # Abandoned set-ups (never verified) can still be tidied away by the browser.
    unverified = add_factor(supabase_db, "alice", status="unverified")
    unenroll(supabase_db, unverified)
    assert unverified not in factor_ids(supabase_db, "alice")


def test_a_permit_from_the_api_allows_one_removal_and_expires(supabase_db, tmp_path):
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    store = SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")
    uid = dev_user_id("alice")
    a, b, c = (add_factor(supabase_db, "alice") for _ in range(3))
    store.allow_factor_removal(uid, a)
    unenroll(supabase_db, a)
    # Used up: the same id again is refused, and a permit covers only its own factor.
    owner(supabase_db, "insert into auth.mfa_factors (id, user_id, status) values (%s, %s, 'verified')",
          [a, uid])
    with pytest.raises(psycopg.Error):
        unenroll(supabase_db, a)
    with pytest.raises(psycopg.Error):
        unenroll(supabase_db, b)
    # Another user's permit doesn't cover it either.
    store.allow_factor_removal(dev_user_id("bob"), c)
    with pytest.raises(psycopg.Error):
        unenroll(supabase_db, c)
    store.allow_factor_removal(uid, b)
    owner(supabase_db, "update app.mfa_factor_removals set allowed_until = now() - interval '1 second'")
    with pytest.raises(psycopg.Error):
        unenroll(supabase_db, b)


def test_advisers_cannot_issue_or_see_permits(supabase_db, alice_client):
    fid = add_factor(supabase_db, "alice")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "select app.allow_factor_removal(%s, %s)",
            [dev_user_id("alice"), fid], aal="aal2", code_age=0)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(supabase_db, "alice", "select * from app.mfa_factor_removals", aal="aal2")


def test_deleting_a_user_still_removes_their_authenticators(supabase_db):
    import uuid

    uid = str(uuid.uuid4())
    owner(supabase_db, "insert into auth.users (id, email) values (%s, 'gone@x.com')", [uid])
    owner(supabase_db, "insert into auth.mfa_factors (id, user_id, status) values (%s, %s, 'verified')",
          [str(uuid.uuid4()), uid])
    owner(supabase_db, "delete from auth.users where id = %s", [uid])
    assert owner(supabase_db, "select count(*) from auth.mfa_factors where user_id = %s", [uid])[0][0] == 0


@pytest.mark.parametrize("mode", ["off", "enrol", "required"])
def test_removal_through_the_api_works_and_direct_removal_does_not(supabase_db, tmp_path, mode):
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    admin.set_mfa_mode(supabase_db, mode)
    store = SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")
    settings = Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                        supabase_jwt_secret=SECRET, require_mfa=mode)
    client = TestClient(create_app(settings, store=store, auth_admin=DbAuthAdmin(supabase_db)))
    keep, remove = add_factor(supabase_db, "alice"), add_factor(supabase_db, "alice")
    with pytest.raises(psycopg.Error):
        unenroll(supabase_db, remove)  # the browser calling Supabase itself
    r = client.delete(f"/me/mfa/factors/{remove}", headers=bearer("alice"))
    assert r.status_code == 204, r.text
    assert factor_ids(supabase_db, "alice") == {keep}
    assert "mfa_factor_removed" in [e["event"] for e in audit_events(client)]


def test_operator_reset_removes_verified_factors_from_supabase(supabase_db):
    fids = {add_factor(supabase_db, "alice"), add_factor(supabase_db, "alice", status="unverified")}
    record = admin.reset_mfa(supabase_db, DbAuthAdmin(supabase_db), "alice@dev.local",
                             requested_by="Alice", verified_by="ops", check="call-back")
    assert {r["factor_id"] for r in record["inputs"]["removed"]} == fids
    assert factor_ids(supabase_db, "alice") == set()


def client_id_of(alice_client, url):
    return str(raw(url, "alice", "select id from public.clients limit 1", aal="aal2")[0][0])


SOFT_DELETE = "update public.clients set deleted_at = now() where id = %s"


@pytest.mark.parametrize("mode,session,allowed", [
    ("off", {}, True),                                        # no code needed
    ("enrol", {"aal": "aal1"}, True),                         # nothing to give yet
    ("enrol", {"aal": "aal2", "code_age": FRESH_MFA_SECONDS + 60}, False),
    ("enrol", {"aal": "aal2", "code_age": 0}, True),
    ("required", {"aal": "aal2"}, False),                     # verified, but no code time
    ("required", {"aal": "aal2", "code_age": FRESH_MFA_SECONDS + 60}, False),
    ("required", {"aal": "aal2", "code_age": FRESH_MFA_SECONDS - 30}, True),
])
def test_soft_deleting_a_client_directly_needs_a_fresh_code(supabase_db, alice_client, mode,
                                                            session, allowed):
    cid = client_id_of(alice_client, supabase_db)
    admin.set_mfa_mode(supabase_db, mode)
    if allowed:
        raw(supabase_db, "alice", SOFT_DELETE, [cid], **session)
        assert owner(supabase_db, "select deleted_at is not null from public.clients where id = %s",
                     [cid])[0][0]
    else:
        with pytest.raises(psycopg.Error) as e:
            raw(supabase_db, "alice", SOFT_DELETE, [cid], **session)
        assert e.value.sqlstate == "PT403"
        assert owner(supabase_db, "select deleted_at from public.clients where id = %s", [cid])[0][0] is None


def test_other_client_updates_and_the_owner_need_no_code(supabase_db, alice_client):
    cid = client_id_of(alice_client, supabase_db)
    admin.set_mfa_mode(supabase_db, "required")
    raw(supabase_db, "alice", "update public.clients set name = 'Renamed' where id = %s", [cid],
        aal="aal2", code_age=FRESH_MFA_SECONDS + 60)
    with pytest.raises(psycopg.Error):  # nor can deleted_by be set on its own
        raw(supabase_db, "alice", "update public.clients set deleted_by = %s where id = %s",
            [alice_client["id"], cid], aal="aal2")
    owner(supabase_db, SOFT_DELETE, [cid])
    with pytest.raises(psycopg.Error):  # undeleting is a change to deleted_at too
        raw(supabase_db, "alice", "update public.clients set deleted_at = null where id = %s",
            [cid], aal="aal2")


def test_the_api_forwards_the_code_time_and_answers_the_databases_refusal(supabase_db, tmp_path):
    from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

    store = SupabaseStore(supabase_db, MemoryStorage(), cache_dir=tmp_path / "cache")
    admin.set_mfa_mode(supabase_db, "required")

    def api(mode):
        settings = Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                            supabase_jwt_secret=SECRET, require_mfa=mode)
        return TestClient(create_app(settings, store=store, auth_admin=MemoryAuthAdmin()))

    # The API's own check passes and the database agrees: the amr claim reached it.
    client = api("required")
    cid = new_client(client)["id"]
    assert client.delete(f"/clients/{cid}", headers=bearer("alice")).status_code == 204
    # An API in `off` mode skips its check; the stricter database still asks for a code.
    client = api("off")
    cid = new_client(client)["id"]
    r = client.delete(f"/clients/{cid}", headers=bearer("alice", **STALE))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_fresh_required"
    assert any(c["id"] == cid for c in client.get("/clients", headers=bearer("alice")).json())
