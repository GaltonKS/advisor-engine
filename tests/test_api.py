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
from advisor_engine.paths import DEFAULT_HOLDINGS  # noqa: E402
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


def test_an_approved_draft_can_be_revised_and_each_revision_is_checked_and_logged(api):
    client, store = api
    r = make_draft(client)
    client.post(f"/drafts/{r['draft_id']}/approve", json={"text": r["content"]}, headers=as_("alice"))
    revised = r["content"] + "\n\nWe will go through this together at our next meeting."
    ok = client.post(f"/drafts/{r['draft_id']}/approve", json={"text": revised}, headers=as_("alice"))
    assert ok.status_code == 200 and ok.json()["approved_text"] == revised
    rec = audit_records(store, "alice", ok.json()["firm_id"])[-1]
    assert rec["event"] == "draft_revised" and rec["previous_text"] == r["content"] and rec["edited"]
    bad = client.post(f"/drafts/{r['draft_id']}/approve",
                      json={"text": revised + " Expect 91.2% next year."}, headers=as_("alice"))
    assert bad.status_code == 422
    portfolio = ok.json()["portfolio_id"]
    kept = client.get(f"/portfolios/{portfolio}/drafts", headers=as_("alice")).json()[0]
    assert kept["approved_text"] == revised  # the rejected edit changed nothing


def test_edited_draft_with_invented_figure_is_not_approved(api):
    client, _ = api
    r = make_draft(client)
    bad = client.post(f"/drafts/{r['draft_id']}/approve",
                      json={"text": r["content"] + " Expect 91.2% next year."}, headers=as_("alice"))
    assert bad.status_code == 422 and "91.2%" in bad.json()["detail"]["unmatched"]


def test_new_chat_clears_the_conversation_but_keeps_drafts_and_audit(api):
    client, store = api
    r = make_draft(client)
    portfolio = next(d for d in client.get("/clients", headers=as_("alice")).json())
    pid = client.get(f"/clients/{portfolio['id']}", headers=as_("alice")).json()["portfolios"][-1]["id"]
    client.post(f"/portfolios/{pid}/turns", json={"message": "risk?"}, headers=as_("alice"))
    assert len(client.get(f"/portfolios/{pid}/messages", headers=as_("alice")).json()) == 4
    out = client.delete(f"/portfolios/{pid}/messages", headers=as_("alice"))
    assert out.status_code == 200 and out.json() == {"cleared": 4}
    assert client.get(f"/portfolios/{pid}/messages", headers=as_("alice")).json() == []
    assert [d["id"] for d in client.get(f"/portfolios/{pid}/drafts", headers=as_("alice")).json()] == [r["draft_id"]]
    audit = client.get(f"/audit?portfolio_id={pid}", headers=as_("alice")).json()
    assert audit[0]["event"] == "chat_cleared"
    assert sum(a["event"] == "turn" for a in audit) == 2  # the cleared turns stay on record
    # The model starts fresh too, and an empty chat clears without another audit row.
    again = client.post(f"/portfolios/{pid}/turns", json={"message": "risk?"}, headers=as_("alice")).json()
    assert again["content"]
    client.delete(f"/portfolios/{pid}/messages", headers=as_("alice"))
    client.delete(f"/portfolios/{pid}/messages", headers=as_("alice"))
    audit = client.get(f"/audit?portfolio_id={pid}", headers=as_("alice")).json()
    assert sum(a["event"] == "chat_cleared" for a in audit) == 2
    assert client.delete(f"/portfolios/{pid}/messages", headers=as_("bob")).status_code == 404


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


def test_stress_scenario_stays_until_reset_and_runs_reopen_from_the_audit_trail(api):
    client, _ = api
    _, p = setup_portfolio(client)
    alice, url = as_("alice"), f"/portfolios/{p['id']}/stress"
    assert client.get(f"{url}/current", headers=alice).json() == {"run": None}
    preset = client.post(url, headers=alice, json={"scenario_id": "broad_selloff"}).json()
    body = {"sector_shocks": {"Technology": -0.3}, "default_shock": -0.05,
            "asset_class_shocks": {}, "description": "Chip supply shock"}
    custom = client.post(url, headers=alice, json=body).json()
    assert preset["run_id"] and custom["run_id"] and preset["run_id"] != custom["run_id"]

    # The latest run is the current scenario, with the inputs to refill the fields.
    current = client.get(f"{url}/current", headers=alice).json()["run"]
    assert current["run_id"] == custom["run_id"] and current["inputs"]["sector_shocks"] == {"Technology": -0.3}
    assert current["result"]["portfolio_return"] == custom["portfolio_return"]

    # The audit trail says what each scenario was and links to the run.
    rows = client.get(f"/audit?portfolio_id={p['id']}", headers=alice).json()
    stress_rows = [r for r in rows if r["event"] == "stress"]
    assert [r["run_id"] for r in stress_rows] == [custom["run_id"], preset["run_id"]]
    assert stress_rows[0]["scenario"] == {
        "id": "custom", "label": "Custom scenario", "source": "adviser",
        "description": "Chip supply shock", "sector_shocks": {"Technology": -0.3},
        "asset_class_shocks": {}, "default_shock": -0.05}
    assert stress_rows[1]["scenario"]["id"] == "broad_selloff" and stress_rows[1]["scenario"]["label"]
    assert "sector_shocks" not in stress_rows[1]["scenario"]
    opened = client.get(f"{url}/runs/{preset['run_id']}", headers=alice).json()["run"]
    assert opened["inputs"]["scenario_id"] == "broad_selloff"
    assert opened["result"]["portfolio_return"] == preset["portfolio_return"]
    assert client.get(f"{url}/runs/nope", headers=alice).status_code == 404

    # Reset clears the current scenario (logged once); runs stay reopenable.
    assert client.post(f"{url}/reset", headers=alice).json() == {"run": None}
    client.post(f"{url}/reset", headers=alice)
    assert client.get(f"{url}/current", headers=alice).json() == {"run": None}
    rows = client.get(f"/audit?portfolio_id={p['id']}", headers=alice).json()
    assert [r["event"] for r in rows].count("stress_reset") == 1
    assert "scenario" not in rows[0] and rows[0]["event"] == "stress_reset"
    assert client.get(f"{url}/runs/{custom['run_id']}", headers=alice).status_code == 200
    bob = as_("bob")
    assert client.get(f"{url}/current", headers=bob).status_code == 404
    assert client.get(f"{url}/runs/{custom['run_id']}", headers=bob).status_code == 404
    assert client.post(f"{url}/reset", headers=bob).status_code == 404


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
    assert [c["label"] for c in calls] == ["Risk profile", "What-if", "Chart"]
    assert calls[2]["output"]["chart"]["kind"] == "what_if"
    assert "comparison" in calls[1]["output"]
    assert "holdings_path" not in json.dumps(calls)  # no server paths leak


class _ApiError(Exception):
    """Shaped like the Anthropic SDK's errors: a message plus an HTTP status."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize("error,expected", [
    (_ApiError(400, "This API key is not scoped to a workspace ... req_011abc"), "isn't set up correctly"),
    (_ApiError(401, "invalid x-api-key"), "isn't set up correctly"),
    (RuntimeError('The anthropic package is missing: pip install -e ".[llm]"'), "isn't set up correctly"),
    (_ApiError(429, "rate limited"), "busy"),
    (_ApiError(529, "overloaded"), "busy"),
    (ValueError("something else"), "unavailable right now"),
])
def test_assistant_errors_are_plain_and_keep_details_server_side(api, monkeypatch, error, expected):
    from advisor_engine.api.routers import assistant as assistant_routes

    client, _ = api
    _, p = setup_portfolio(client)

    def failing_turn(*args, **kwargs):
        raise error

    monkeypatch.setattr(assistant_routes, "run_turn", failing_turn)
    r = client.post(f"/portfolios/{p['id']}/turns", headers=as_("alice"),
                    json={"message": "How risky is this?"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert expected in detail and str(error) not in detail and "req_" not in detail


def test_ai_suggested_scenario_then_engine_run_is_labelled_and_audited(api):
    client, store = api
    _, p = setup_portfolio(client)
    sug = client.post(f"/portfolios/{p['id']}/stress/suggest", headers=as_("alice"),
                      json={"description": "A deep recession"})
    assert sug.status_code == 200, sug.text
    s = sug.json()
    assert s["source"] == "mock" and s["label"] == "Recession"
    body = {"sector_shocks": s["sector_shocks"], "default_shock": s["default_shock"],
            "asset_class_shocks": s["asset_class_shocks"], "description": "A deep recession",
            "suggested": s}
    r = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"), json=body).json()
    assert r["assumptions"]["source"] == "ai_suggested" and r["scenario"]["label"] == "Recession"
    edited = {**body, "default_shock": -0.5}
    r2 = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"), json=edited).json()
    assert r2["assumptions"]["source"] == "ai_suggested_edited"
    assert r2["portfolio_return"] != r["portfolio_return"]  # the engine ran the edited shocks
    events = [a["event"] for a in audit_records(store, "alice", p["firm_id"])]
    assert "scenario_suggested" in events and events.count("stress") == 2


def test_stress_assumption_sources_for_preset_and_adviser(api):
    client, _ = api
    _, p = setup_portfolio(client)
    pre = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"), json={"scenario_id": "recession"}).json()
    own = client.post(f"/portfolios/{p['id']}/stress", headers=as_("alice"), json={"default_shock": -0.1}).json()
    assert pre["assumptions"]["source"] == "preset" and own["assumptions"]["source"] == "adviser"


def test_scenario_suggestion_is_firm_scoped_and_validated(api):
    client, _ = api
    _, p = setup_portfolio(client)
    assert client.post(f"/portfolios/{p['id']}/stress/suggest", headers=as_("bob"),
                       json={"description": "crash"}).status_code == 404
    assert client.post(f"/portfolios/{p['id']}/stress/suggest", headers=as_("alice"),
                       json={"description": ""}).status_code == 422


def test_allowed_origins_ignore_trailing_slashes_and_spaces(monkeypatch):
    for k in ("ADVISOR_AUTH", "SUPABASE_URL", "SUPABASE_DB_URL", "RENDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://galtonks.onrender.com/ , http://localhost:5173")
    assert load_settings().allowed_origins == ["https://galtonks.onrender.com", "http://localhost:5173"]


# --- renaming and deleting clients ---------------------------------------------------------

def test_rename_client_is_saved_and_audited(api):
    client, store = api
    c, _ = setup_portfolio(client)
    r = client.patch(f"/clients/{c['id']}", json={"name": "  Boswell Family  "}, headers=as_("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Boswell Family"
    assert [x["name"] for x in client.get("/clients", headers=as_("alice")).json()] == ["Boswell Family"]
    assert client.patch(f"/clients/{c['id']}", json={"name": "   "},
                        headers=as_("alice")).status_code == 422
    firm = client.get("/me", headers=as_("alice")).json()["adviser"]["firm_id"]
    renamed = [r for r in audit_records(store, "alice", firm) if r["event"] == "client_renamed"]
    assert renamed[-1]["inputs"] == {"from": "Sample Household A", "to": "Boswell Family"}


def test_delete_client_hides_it_and_its_portfolios_but_keeps_the_record(api):
    client, store = api
    alice = as_("alice")
    c, p = setup_portfolio(client)
    draft = client.post(f"/portfolios/{p['id']}/turns", json={"draft": True}, headers=alice).json()
    keep, _ = setup_portfolio(client)

    assert client.delete(f"/clients/{c['id']}", headers=alice).status_code == 204
    assert [x["id"] for x in client.get("/clients", headers=alice).json()] == [keep["id"]]
    assert client.get(f"/clients/{c['id']}", headers=alice).status_code == 404
    assert client.patch(f"/clients/{c['id']}", json={"name": "x"}, headers=alice).status_code == 404
    assert client.delete(f"/clients/{c['id']}", headers=alice).status_code == 404
    assert client.get(f"/portfolios/{p['id']}/profile", headers=alice).status_code == 404
    assert client.post(f"/portfolios/{p['id']}/turns", json={"message": "risk?"},
                       headers=alice).status_code == 404
    assert client.post(f"/drafts/{draft['draft_id']}/approve", json={"text": draft["content"]},
                       headers=alice).status_code == 404

    firm = client.get("/me", headers=alice).json()["adviser"]["firm_id"]
    records = audit_records(store, "alice", firm)
    assert any(r["event"] == "turn" and r.get("client_id") == c["id"] for r in records)
    deleted = [r for r in records if r["event"] == "client_deleted"]
    assert deleted[-1]["client_id"] == c["id"]
    assert deleted[-1]["inputs"] == {"name": "Sample Household A"}
    # Soft delete: the portfolio row is still on record.
    assert session(store, "alice").get_portfolio(firm, p["id"]) is not None


def test_another_firm_cannot_rename_or_delete_a_client(api):
    client, _ = api
    c, _ = setup_portfolio(client, "alice")
    bob = as_("bob")
    assert client.patch(f"/clients/{c['id']}", json={"name": "Mine now"}, headers=bob).status_code == 404
    assert client.delete(f"/clients/{c['id']}", headers=bob).status_code == 404
    assert client.get(f"/clients/{c['id']}", headers=as_("alice")).json()["name"] == "Sample Household A"


def test_audit_entries_link_back_to_their_exchange_in_the_conversation(api):
    client, _ = api
    alice = as_("alice")
    _, p = setup_portfolio(client)
    reply = client.post(f"/portfolios/{p['id']}/turns", json={"message": "How risky is this portfolio?"},
                        headers=alice).json()
    draft = client.post(f"/portfolios/{p['id']}/turns", json={"draft": True}, headers=alice).json()
    client.post(f"/drafts/{draft['draft_id']}/approve", json={"text": draft["content"]}, headers=alice)

    messages = client.get(f"/portfolios/{p['id']}/messages", headers=alice).json()
    audit = client.get(f"/audit?portfolio_id={p['id']}", headers=alice).json()
    turns = [r for r in reversed(audit) if r["event"] == "turn"]
    assert [r["turn_id"] for r in turns] == [reply["turn_id"], draft["turn_id"]]
    # Each turn id marks exactly the question and the reply it produced.
    for r in turns:
        exchange = [m for m in messages if m.get("turn_id") == r["turn_id"]]
        assert [m["role"] for m in exchange] == ["user", "assistant"]
        assert exchange[0]["content"] == r["user_message"]
    approved = next(r for r in audit if r["event"] == "draft_approved")
    assert any(m.get("draft_id") == approved["draft_id"] for m in messages)


# --- adviser approval ---------------------------------------------------------------------

@pytest.fixture(params=["local", "supabase"])
def approval_api(request, tmp_path):
    """An API where new sign-ups wait for approval, with a way to approve them."""
    if request.param == "local":
        store = LocalStore(tmp_path, require_approval=True)
        approve = store.approve_adviser
    else:
        from advisor_engine.api import admin
        from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore

        db = request.getfixturevalue("supabase_db")
        store = SupabaseStore(db, MemoryStorage(), cache_dir=tmp_path / "cache")
        approve = lambda email, firm=None: admin.approve(db, email, firm)  # noqa: E731
    app = create_app(Settings(auth="dev", store_dir=tmp_path), store=store)
    return TestClient(app), approve


def test_pending_adviser_registers_then_gets_in_once_approved(approval_api):
    client, approve = approval_api
    dave = as_("dave")
    me = client.get("/me", headers=dave).json()
    assert me["adviser"]["status"] == "pending"
    r = client.get("/clients", headers=dave)
    assert r.status_code == 403 and r.json()["detail"] == "Your account is awaiting approval."
    assert client.post("/clients", json={"name": "X"}, headers=dave).status_code == 403

    bad = client.post("/me/registration", headers=dave,
                      json={"full_name": "Dave Adams", "crd": "CRD-12", "firm_name": "Dave Wealth"})
    assert bad.status_code == 422
    ok = client.post("/me/registration", headers=dave,
                     json={"full_name": "Dave Adams", "crd": "1234567", "firm_name": "Dave Wealth"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["adviser"]["crd"] == "1234567"
    assert client.get("/me", headers=dave).json()["adviser"]["registration_submitted_at"]

    approve("dave@dev.local")
    assert client.get("/clients", headers=dave).status_code == 200
    again = client.post("/me/registration", headers=dave,
                        json={"full_name": "Dave", "crd": "1", "firm_name": "F"})
    assert again.status_code == 409


# --- pilot metrics (operator only) -----------------------------------------------------------

def operator_app(store, tmp_path, **kw):
    return TestClient(create_app(Settings(auth="dev", store_dir=tmp_path,
                                          operator_emails=("alice@dev.local",), **kw), store=store))


def test_pilot_metrics_are_for_operators_only(api, tmp_path):
    _, store = api
    client = operator_app(store, tmp_path)
    assert client.get("/me", headers=as_("alice")).json()["operator"] is True
    assert client.get("/me", headers=as_("bob")).json()["operator"] is False
    r = client.get("/operator/metrics", headers=as_("bob"))
    assert r.status_code == 404  # not 403: the route isn't revealed
    assert client.get("/operator/metrics", headers=as_("alice")).status_code == 200


def test_pilot_metrics_count_activity_across_firms_without_client_data(api, tmp_path):
    _, store = api
    client = operator_app(store, tmp_path)
    c, p = setup_portfolio(client, "bob")  # another firm's activity still counts
    client.post(f"/portfolios/{p['id']}/turns", json={"message": "How risky is it?"}, headers=as_("bob"))
    r = client.post(f"/portfolios/{p['id']}/stress", json={"scenario_id": "broad_selloff"}, headers=as_("bob"))
    assert r.status_code == 200
    m = client.get("/operator/metrics", headers=as_("alice")).json()
    assert len(m["weekly_active"]) == m["weeks"] == 12
    assert m["weekly_active"][-1]["advisers"] == 1  # bob, this week
    uses = {f["event"]: f for f in m["features"]}
    assert uses["turn"]["uses"] == 1 and uses["turn"]["advisers"] == 1
    assert uses["stress"]["uses"] == 1
    assert m["totals"]["questions"] == 1 and m["totals"]["clients"] == 1
    assert m["funnel"]["added_client"] == 1 and m["funnel"]["ran_analysis"] == 1
    text = json.dumps(m)
    assert "Sample Household A" not in text and "How risky" not in text and c["id"] not in text


def test_pilot_metrics_show_the_approval_queue_and_wait(approval_api, tmp_path):
    client, approve = approval_api
    store = client.app.state.store
    ops = operator_app(store, tmp_path)
    if isinstance(store, LocalStore):  # with Supabase, alice is approved by the fixture
        ops.get("/me", headers=as_("alice"))
        store.approve_adviser("alice@dev.local")
    ops.post("/me/registration", headers=as_("dave"),
             json={"full_name": "Dave Adams", "crd": "1234567", "firm_name": "Dave Wealth"})
    m = ops.get("/operator/metrics", headers=as_("alice")).json()
    assert [(p["email"], p["crd"], p["firm_name"]) for p in m["pending"]] == [
        ("dave@dev.local", "1234567", "Dave Wealth")]
    assert m["totals"]["pending_advisers"] == 1 and m["funnel"]["registered"] == 1
    approve("dave@dev.local")
    m = ops.get("/operator/metrics", headers=as_("alice")).json()
    assert m["pending"] == [] and m["avg_approval_hours"] is not None


# --- approval reviews (operator) --------------------------------------------------------------

ALL_CHECKS = ["name_matches", "currently_registered", "firm_matches", "email_verified",
              "disclosures_reviewed"]


def reviews_app(approval_api, tmp_path):
    client, _ = approval_api
    store = client.app.state.store
    ops = operator_app(store, tmp_path)
    if isinstance(store, LocalStore):  # with Supabase, alice and bob are approved by the fixture
        for user in ("alice", "bob"):
            ops.get("/me", headers=as_(user))
            store.approve_adviser(f"{user}@dev.local")
    for user, crd in (("dave", "1234567"), ("erin", "7654321")):
        ops.get("/me", headers=as_(user))
        ops.post("/me/registration", headers=as_(user),
                 json={"full_name": user.title(), "crd": crd, "firm_name": f"{user.title()} Wealth"})
    return ops


def test_approval_is_recorded_with_reviewer_checks_and_summary(approval_api, tmp_path):
    ops = reviews_app(approval_api, tmp_path)
    alice = as_("alice")
    assert ops.post("/operator/reviews", headers=as_("bob"),
                    json={"email": "dave@dev.local", "decision": "approved", "checks": ALL_CHECKS}
                    ).status_code == 404  # bob isn't an operator
    partial = ops.post("/operator/reviews", headers=alice,
                       json={"email": "dave@dev.local", "decision": "approved", "checks": ALL_CHECKS[:3]})
    assert partial.status_code == 422 and "every BrokerCheck check" in partial.json()["detail"]
    r = ops.post("/operator/reviews", headers=alice,
                 json={"email": "dave@dev.local", "decision": "approved", "checks": ALL_CHECKS,
                       "note": "IAR at Dave Wealth since 2019"})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["reviewed_by"] == "alice@dev.local" and rec["decision"] == "approved"
    assert rec["summary"] == ("Approved. BrokerCheck CRD 1234567: name matches; currently registered; "
                              "firm matches; email/employment verified; disclosures reviewed.")
    assert ops.get("/clients", headers=as_("dave")).status_code == 200
    again = ops.post("/operator/reviews", headers=alice,
                     json={"email": "dave@dev.local", "decision": "approved", "checks": ALL_CHECKS})
    assert again.status_code == 404  # no longer pending


def test_rejection_needs_a_reason_and_locks_the_account(approval_api, tmp_path):
    ops = reviews_app(approval_api, tmp_path)
    alice = as_("alice")
    base = {"email": "erin@dev.local", "decision": "rejected"}
    assert ops.post("/operator/reviews", headers=alice, json=base).status_code == 422
    assert ops.post("/operator/reviews", headers=alice, json={**base, "reason": "other"}).status_code == 422
    r = ops.post("/operator/reviews", headers=alice, json={**base, "reason": "not_registered"})
    assert r.status_code == 200 and r.json()["summary"] == (
        "Rejected. BrokerCheck CRD 7654321: Not currently registered.")
    erin = as_("erin")
    blocked = ops.get("/clients", headers=erin)
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Your registration wasn't approved."
    assert ops.get("/me", headers=erin).json()["adviser"]["status"] == "rejected"
    assert ops.post("/me/registration", headers=erin,
                    json={"full_name": "E", "crd": "1", "firm_name": "F"}).status_code == 409


def test_metrics_show_the_audit_trail_newest_first_including_sql_approvals(approval_api, tmp_path):
    client, approve = approval_api
    ops = reviews_app(approval_api, tmp_path)
    alice = as_("alice")
    ops.post("/operator/reviews", headers=alice,
             json={"email": "erin@dev.local", "decision": "rejected", "reason": "duplicate"})
    approve("dave@dev.local")  # the SQL Editor / admin command path
    m = ops.get("/operator/metrics", headers=alice).json()
    trail = [(r["adviser_email"], r["decision"], r["reviewed_by"].split(":")[0]) for r in m["reviews"]]
    assert trail[:2] == [("dave@dev.local", "approved", "sql"), ("erin@dev.local", "rejected", "alice@dev.local")]
    assert [c["key"] for c in m["review_presets"]["checks"]] == ALL_CHECKS
    assert "other" in [r["key"] for r in m["review_presets"]["reject_reasons"]]
