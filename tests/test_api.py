"""API tests: sign-in, firm isolation, the assistant loop and draft approval.

Dev auth (an X-Dev-User header) stands in for Supabase except in the token tests, which
sign HS256 tokens with a test secret. Nothing here reaches the network or the real API.

The `api` fixture runs each test against both stores: LocalStore, and SupabaseStore on a
fresh Postgres with the real migrations (row-level security on), using in-memory Storage.
"""

import json
import time

import pytest

pytest.importorskip("fastapi")
import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.main import create_app  # noqa: E402
from advisor_engine.api.settings import Settings, load_settings  # noqa: E402
from advisor_engine.api.store import LocalStore  # noqa: E402
from advisor_engine.assistant._paths import DEFAULT_HOLDINGS  # noqa: E402
from advisor_engine.assistant.tools import execute_tool  # noqa: E402

SUPABASE_URL = "https://example.supabase.co"
SECRET = "test-secret-that-is-long-enough-for-hs256"


@pytest.fixture(params=["local", "supabase"])
def api(request, tmp_path):
    if request.param == "local":
        store = LocalStore(tmp_path)
    else:
        from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

        store = SupabaseStore(request.getfixturevalue("supabase_db"), MemoryStorage(),
                              cache_dir=tmp_path / "cache")
    app = create_app(Settings(auth="dev", store_dir=tmp_path, daily_turn_limit=5), store=store)
    return TestClient(app), store


def as_(user):
    return {"X-Dev-User": user}


def session(store, user):
    return store.as_user(dev_user_id(user), f"{user}@dev.local")


def audit_records(store, user, firm_id):
    return session(store, user).read_audit(firm_id)


def setup_portfolio(client, user="alice"):
    c = client.post("/clients", json={"name": "Sample Household A", "objective": "retirement income"},
                    headers=as_(user)).json()
    p = client.post(f"/clients/{c['id']}/portfolios", headers=as_(user),
                    files={"file": ("holdings.csv", DEFAULT_HOLDINGS.read_bytes(), "text/csv")})
    assert p.status_code == 201, p.text
    return c, p.json()


# --- sign-in -----------------------------------------------------------------------------

def test_requests_without_sign_in_are_refused(api):
    client, _ = api
    assert client.get("/health").status_code == 200
    assert client.get("/clients").status_code == 401


def test_first_sign_in_creates_a_firm_and_dev_auth_is_never_live(api, monkeypatch):
    client, _ = api
    monkeypatch.setenv("ADVISOR_LLM", "anthropic")
    me = client.get("/me", headers=as_("alice")).json()
    assert me["adviser"]["role"] == "owner" and me["firm"]["id"] == me["adviser"]["firm_id"]
    assert me["llm"] == "mock"


def test_dev_auth_refuses_to_start_on_render(monkeypatch):
    for k in ("ADVISOR_AUTH", "SUPABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="Dev auth"):
        load_settings()


def token(**claims):
    base = {"sub": "user-1", "email": "a@firm.com", "aud": "authenticated",
            "iss": f"{SUPABASE_URL}/auth/v1", "exp": int(time.time()) + 600}
    return jwt.encode({**base, **claims}, SECRET, algorithm="HS256")


@pytest.mark.parametrize("claims, ok", [
    ({}, True),
    ({"aud": "anon"}, False),                        # anon / service keys are not sign-ins
    ({"iss": "https://other.supabase.co/auth/v1"}, False),
    ({"exp": int(time.time()) - 10}, False),
    ({"is_anonymous": True}, False),
])
def test_supabase_tokens_are_verified(tmp_path, claims, ok):
    app = create_app(Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                              supabase_jwt_secret=SECRET))
    r = TestClient(app).get("/me", headers={"Authorization": f"Bearer {token(**claims)}"})
    assert r.status_code == (200 if ok else 401), r.text


def test_token_signed_with_another_secret_is_refused(tmp_path):
    app = create_app(Settings(auth="supabase", store_dir=tmp_path, supabase_url=SUPABASE_URL,
                              supabase_jwt_secret=SECRET))
    forged = jwt.encode(jwt.decode(token(), options={"verify_signature": False}),
                        "x" * 40, algorithm="HS256")
    assert TestClient(app).get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


# --- engine and assistant ------------------------------------------------------------------

def test_profile_is_the_engine_output(api):
    client, _ = api
    _, p = setup_portfolio(client)
    got = client.get(f"/portfolios/{p['id']}/profile", headers=as_("alice")).json()
    expected = json.loads(execute_tool("get_risk_profile", {"holdings_path": str(DEFAULT_HOLDINGS)}).text)
    assert got["profile"] == expected


def test_turn_is_verified_saved_and_audited_with_the_adviser(api):
    client, store = api
    c, p = setup_portfolio(client)
    r = client.post(f"/portfolios/{p['id']}/turns", json={"message": "How risky is this portfolio?"},
                    headers=as_("alice")).json()
    assert not r["blocked"] and r["verification"]["ok"]
    assert r["tool_calls"][0]["label"] == "Risk profile"
    assert "holdings_path" not in json.dumps(r["tool_calls"])  # no server paths leak
    assert len(client.get(f"/portfolios/{p['id']}/messages", headers=as_("alice")).json()) == 2
    rec = audit_records(store, "alice", p["firm_id"])[-1]
    assert rec["adviser_id"] == dev_user_id("alice") and rec["portfolio_id"] == p["id"]
    assert rec["client_id"] == c["id"] and rec["model"] == "mock"


def test_bad_csv_is_rejected(api):
    client, _ = api
    c = client.post("/clients", json={"name": "X"}, headers=as_("alice")).json()
    r = client.post(f"/clients/{c['id']}/portfolios", headers=as_("alice"),
                    files={"file": ("h.csv", b"ticker,shares\nAAA,-1\n", "text/csv")})
    assert r.status_code == 422


def test_daily_turn_limit(api):
    client, _ = api
    _, p = setup_portfolio(client)
    codes = [client.post(f"/portfolios/{p['id']}/turns", json={"message": "risk?"},
                         headers=as_("alice")).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


# --- drafts --------------------------------------------------------------------------------

def make_draft(client):
    _, p = setup_portfolio(client)
    r = client.post(f"/portfolios/{p['id']}/turns", json={"draft": True}, headers=as_("alice")).json()
    assert r["draft_id"], r
    return r


def test_approving_a_draft_records_who_approved(api):
    client, store = api
    r = make_draft(client)
    ok = client.post(f"/drafts/{r['draft_id']}/approve", json={"text": r["content"]}, headers=as_("alice"))
    assert ok.status_code == 200 and ok.json()["approved_by"] == dev_user_id("alice")
    rec = audit_records(store, "alice", ok.json()["firm_id"])[-1]
    assert rec["event"] == "draft_approved" and rec["approved_by_email"] == "alice@dev.local"
    again = client.post(f"/drafts/{r['draft_id']}/approve", json={"text": r["content"]}, headers=as_("alice"))
    assert again.status_code == 409


def test_edited_draft_with_invented_figure_is_not_approved(api):
    client, _ = api
    r = make_draft(client)
    bad = client.post(f"/drafts/{r['draft_id']}/approve",
                      json={"text": r["content"] + " Expect 91.2% next year."}, headers=as_("alice"))
    assert bad.status_code == 422 and "91.2%" in bad.json()["detail"]["unmatched"]


# --- firm isolation ------------------------------------------------------------------------

def test_another_firm_cannot_see_or_use_anything(api):
    client, _ = api
    c, p = setup_portfolio(client, "alice")
    draft = client.post(f"/portfolios/{p['id']}/turns", json={"draft": True}, headers=as_("alice")).json()
    bob = as_("bob")
    assert client.get("/clients", headers=bob).json() == []
    assert client.get(f"/clients/{c['id']}", headers=bob).status_code == 404
    assert client.get(f"/portfolios/{p['id']}/profile", headers=bob).status_code == 404
    assert client.get(f"/portfolios/{p['id']}/messages", headers=bob).status_code == 404
    assert client.post(f"/portfolios/{p['id']}/turns", json={"message": "risk?"}, headers=bob).status_code == 404
    assert client.post(f"/drafts/{draft['draft_id']}/approve", json={"text": draft["content"]},
                       headers=bob).status_code == 404
    up = client.post(f"/clients/{c['id']}/portfolios", headers=bob,
                     files={"file": ("h.csv", DEFAULT_HOLDINGS.read_bytes(), "text/csv")})
    assert up.status_code == 404
    assert client.get("/audit", headers=bob).json() == []


def test_engine_tool_cannot_read_another_firms_file(api):
    """Even if the model is handed another firm's path, the tool's path guard refuses it."""
    client, store = api
    _, p = setup_portfolio(client, "alice")
    bob_firm = client.get("/me", headers=as_("bob")).json()["firm"]["id"]
    alice_csv = session(store, "alice").holdings_file(p["firm_id"], p["id"])
    bob_dir = session(store, "bob").firm_dir(bob_firm)
    res = execute_tool("get_risk_profile", {"holdings_path": str(alice_csv)}, data_dir=bob_dir)
    assert res.is_error and "must be inside" in res.text
    rel = f"{p['firm_id']}/portfolios/{p['id']}.csv"  # relative to the firms/ folder
    assert execute_tool("get_risk_profile", {"holdings_path": rel}, data_dir=bob_dir).is_error


# --- CFP tools -----------------------------------------------------------------------------

EDITED = [{"ticker": "AAA", "shares": 100, "price": 50, "sector": "Technology"},
          {"ticker": "BBB", "shares": 200, "price": 30, "sector": "Health Care"},
          {"ticker": "ZZZ", "shares": 100, "price": 40, "sector": "Utilities"}]


def test_holdings_round_trip_and_edits_save_as_a_new_version(api):
    client, store = api
    c, p = setup_portfolio(client)
    rows = client.get(f"/portfolios/{p['id']}/holdings", headers=as_("alice")).json()["holdings"]
    assert {r["ticker"] for r in rows} >= {"AAA", "BBB"} and abs(sum(r["weight"] for r in rows) - 1) < 1e-9
    new = client.post(f"/clients/{c['id']}/portfolios/holdings", headers=as_("alice"),
                      json={"label": "Trim tech", "holdings": EDITED})
    assert new.status_code == 201 and new.json()["source"] == "editor"
    assert new.json()["id"] != p["id"] and new.json()["label"] == "Trim tech"
    versions = client.get(f"/clients/{c['id']}", headers=as_("alice")).json()["portfolios"]
    assert [v["source"] for v in versions] == ["upload", "editor"]
    assert audit_records(store, "alice", p["firm_id"])[-1]["event"] == "portfolio_saved"


def test_upload_without_sectors_then_fill_them_in(api):
    client, _ = api
    c = client.post("/clients", json={"name": "X"}, headers=as_("alice")).json()
    up = client.post(f"/clients/{c['id']}/portfolios", headers=as_("alice"),
                     files={"file": ("h.csv", b"Symbol,Quantity,Price\nAAA,100,50\nBBB,200,25\n",
                                     "text/csv")})
    assert up.status_code == 201, up.text
    prof = client.get(f"/portfolios/{up.json()['id']}/profile", headers=as_("alice")).json()["profile"]
    assert prof["sector_coverage"]["n_unclassified"] == 2
    assert "withheld" in prof["concentration"]["sector_level"]
    # The editor sends a blank sector for any still unfilled; the engine labels it.
    rows = [{"ticker": "AAA", "shares": 100, "price": 50, "sector": "Technology"},
            {"ticker": "BBB", "shares": 200, "price": 25, "sector": ""}]
    v2 = client.post(f"/clients/{c['id']}/portfolios/holdings", headers=as_("alice"), json={"holdings": rows})
    assert v2.status_code == 201, v2.text
    prof2 = client.get(f"/portfolios/{v2.json()['id']}/profile", headers=as_("alice")).json()["profile"]
    assert prof2["sector_coverage"]["unclassified_tickers"] == ["BBB"]


def test_asset_classes_survive_editing_and_drive_custom_stress(api):
    client, _ = api
    c = client.post("/clients", json={"name": "X"}, headers=as_("alice")).json()
    csv = b"Ticker,Shares,Share Price,Sector,Asset Class\nAAA,100,50,Energy,US Equity\nLQD,100,100,Bonds,Fixed Income ETF\n"
    up = client.post(f"/clients/{c['id']}/portfolios", headers=as_("alice"),
                     files={"file": ("h.csv", csv, "text/csv")}).json()
    rows = client.get(f"/portfolios/{up['id']}/holdings", headers=as_("alice")).json()["holdings"]
    assert {r["ticker"]: r["asset_class"] for r in rows} == {"AAA": "equity", "LQD": "fixed_income"}
    v2 = client.post(f"/clients/{c['id']}/portfolios/holdings", headers=as_("alice"),
                     json={"holdings": [{k: r[k] for k in ("ticker", "shares", "price", "sector", "asset_class")}
                                        for r in rows]}).json()
    prof = client.get(f"/portfolios/{v2['id']}/profile", headers=as_("alice")).json()["profile"]
    assert set(prof["asset_class_exposure"]) == {"equity", "fixed_income"}
    r = client.post(f"/portfolios/{v2['id']}/stress", headers=as_("alice"),
                    json={"default_shock": -0.2, "asset_class_shocks": {"fixed_income": 0.01}})
    assert r.status_code == 200, r.text
    assert {p["ticker"]: p["shock"] for p in r.json()["positions"]} == {"AAA": -0.2, "LQD": 0.01}


def test_invalid_edited_holdings_are_rejected(api):
    client, _ = api
    c, _ = setup_portfolio(client)
    bad = [{**EDITED[0], "shares": -5}]
    r = client.post(f"/clients/{c['id']}/portfolios/holdings", headers=as_("alice"), json={"holdings": bad})
    assert r.status_code == 422


def test_what_if_returns_engine_before_after_and_is_audited(api):
    client, store = api
    _, p = setup_portfolio(client)
    r = client.post(f"/portfolios/{p['id']}/what-if", headers=as_("alice"), json={"holdings": EDITED})
    assert r.status_code == 200, r.text
    body = r.json()
    expected_before = json.loads(execute_tool("get_risk_profile", {"holdings_path": str(DEFAULT_HOLDINGS)}).text)
    assert body["before"] == expected_before
    bets = next(h for h in body["comparison"]["headline"] if h["key"] == "effective_bets")
    assert bets["after"] == body["after"]["concentration"]["effective_bets"]
    assert "ZZZ" in body["comparison"]["added"]
    rec = audit_records(store, "alice", p["firm_id"])[-1]
    assert rec["event"] == "what_if" and rec["inputs"]["holdings"][2]["ticker"] == "ZZZ"


def test_compare_two_versions(api):
    client, _ = api
    c, p = setup_portfolio(client)
    v2 = client.post(f"/clients/{c['id']}/portfolios/holdings", headers=as_("alice"),
                     json={"holdings": EDITED}).json()
    r = client.get(f"/portfolios/{p['id']}/compare/{v2['id']}", headers=as_("alice")).json()
    assert r["comparison"]["added"] == ["ZZZ"]


def test_stress_preset_and_custom(api):
    client, _ = api
    _, p = setup_portfolio(client)
    ids = [s["id"] for s in client.get("/stress/scenarios", headers=as_("alice")).json()]
    assert "broad_selloff" in ids
    r = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"),
                    json={"scenario_id": "broad_selloff"}).json()
    assert abs(r["portfolio_return"] + 0.20) < 1e-9
    custom = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"),
                         json={"sector_shocks": {"Technology": -0.5}, "default_shock": 0}).json()
    tech = sum(s["weight"] for s in custom["sectors"] if s["sector"] == "Technology")
    assert abs(custom["portfolio_return"] + 0.5 * tech) < 1e-9
    assert client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"),
                       json={"scenario_id": "meteor"}).status_code == 422


def test_questionnaire_scores_on_save_and_checks_the_portfolio(api):
    client, _ = api
    c, p = setup_portfolio(client)
    qs = client.get("/questionnaire", headers=as_("alice")).json()
    assert len(qs["questions"]) == 6 and qs["method_status"]
    assert client.get(f"/portfolios/{p['id']}/suitability", headers=as_("alice")).status_code == 409
    answers = {q["id"]: 2 for q in qs["questions"]}
    saved = client.put(f"/clients/{c['id']}/questionnaire", headers=as_("alice"), json={"answers": answers})
    assert saved.status_code == 200 and saved.json()["risk_level"] == 6
    detail = client.get(f"/clients/{c['id']}", headers=as_("alice")).json()
    assert detail["questionnaire_result"]["risk_level"] == 6
    s = client.get(f"/portfolios/{p['id']}/suitability", headers=as_("alice")).json()
    assert s["checks"][0]["client_limit"] == 0.15
    bad = client.put(f"/clients/{c['id']}/questionnaire", headers=as_("alice"),
                     json={"answers": {"time_horizon": 9}})
    assert bad.status_code == 422


def test_drafts_list_for_a_portfolio(api):
    client, _ = api
    r = make_draft(client)
    pid = client.get("/clients", headers=as_("alice")).json()[0]["id"]
    portfolio = client.get(f"/clients/{pid}", headers=as_("alice")).json()["portfolios"][0]["id"]
    drafts = client.get(f"/portfolios/{portfolio}/drafts", headers=as_("alice")).json()
    assert [d["id"] for d in drafts] == [r["draft_id"]] and "tool_calls" not in drafts[0]


def test_cfp_tools_respect_firm_isolation(api):
    client, _ = api
    c, p = setup_portfolio(client, "alice")
    _, bob_p = setup_portfolio(client, "bob")
    bob = as_("bob")
    assert client.get(f"/portfolios/{p['id']}/holdings", headers=bob).status_code == 404
    assert client.post(f"/portfolios/{p['id']}/what-if", headers=bob, json={"holdings": EDITED}).status_code == 404
    assert client.post(f"/portfolios/{p['id']}/stress", headers=bob,
                       json={"scenario_id": "broad_selloff"}).status_code == 404
    assert client.get(f"/portfolios/{bob_p['id']}/compare/{p['id']}", headers=bob).status_code == 404
    assert client.put(f"/clients/{c['id']}/questionnaire", headers=bob,
                      json={"answers": {"time_horizon": 1}}).status_code == 404
    assert client.post(f"/clients/{c['id']}/portfolios/holdings", headers=bob,
                       json={"holdings": EDITED}).status_code == 404
    assert client.get(f"/portfolios/{p['id']}/drafts", headers=bob).status_code == 404


def test_server_errors_reach_the_browser_with_cors_headers(tmp_path):
    """An unexpected error must still carry CORS headers, or the browser reports a
    misleading "Failed to fetch" / access-control error instead of the real problem."""

    class Broken(LocalStore):
        def list_clients(self, firm_id):
            raise RuntimeError("database unavailable")

    app = create_app(Settings(auth="dev", store_dir=tmp_path,
                              allowed_origins=["https://app.example.com"]),
                     store=Broken(tmp_path))
    r = TestClient(app, raise_server_exceptions=False).get(
        "/clients", headers={**as_("alice"), "Origin": "https://app.example.com"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "database unavailable" not in r.text  # internals stay in the server log


def test_turn_reports_each_tool_used(api):
    client, _ = api
    _, p = setup_portfolio(client)
    r = client.post(f"/portfolios/{p['id']}/turns", headers=as_("alice"),
                    json={"message": "What if we sold AAA?"})
    assert r.status_code == 200, r.text
    calls = r.json()["tool_calls"]
    assert [c["label"] for c in calls] == ["Risk profile", "What-if"]
    assert "comparison" in calls[1]["output"]
    assert "holdings_path" not in json.dumps(calls)  # no server paths leak


def test_allowed_origins_ignore_trailing_slashes_and_spaces(monkeypatch):
    for k in ("ADVISOR_AUTH", "SUPABASE_URL", "SUPABASE_DB_URL", "RENDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://galtonks.onrender.com/ , http://localhost:5173")
    assert load_settings().allowed_origins == ["https://galtonks.onrender.com", "http://localhost:5173"]
