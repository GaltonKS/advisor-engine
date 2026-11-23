"""The Forecast routes: the API passes requests to the forecast engine service for the
signed-in firm and returns its answers unchanged. The service is faked with
httpx.MockTransport, so nothing reaches the network."""

import json

import httpx
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.forecast_client import ForecastClient, tenant_id  # noqa: E402
from advisor_engine.api.main import create_app  # noqa: E402
from advisor_engine.api.settings import Settings  # noqa: E402
from advisor_engine.api.store import LocalStore  # noqa: E402

KEY = "forecast-test-key"
RESULT = {"id": "11111111-1111-1111-1111-111111111111", "engine_version": "0.2.0",
          "last_actual_period": "2025-12-01", "history_months": 24, "drivers": {},
          "formulas": {}, "months": [{"period": "2026-01-01", "revenue": 198940.38}],
          "models": {}, "warnings": []}


class FakeService:
    """Just enough of the forecast engine: tenants, upload, monthly, runs."""

    def __init__(self, fail: int | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.tenants: set[str] = set()
        self.fail = fail

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = req.content.decode(errors="replace") if req.content else ""
        self.calls.append((req.method, req.url.path, dict(req.headers)))
        if self.fail:
            return httpx.Response(self.fail, json={"detail": "boom"})
        if req.headers.get("x-api-key") != KEY and req.url.path != "/health":
            return httpx.Response(401, json={"detail": "Invalid or missing API key"})
        parts = req.url.path.strip("/").split("/")
        if parts == ["health"]:
            return httpx.Response(200, json={"status": "ok", "engine_version": "0.2.0"})
        tid = parts[1]
        if req.method == "PUT":
            self.tenants.add(tid)
            return httpx.Response(200, json={"id": tid, "name": json.loads(body)["name"],
                                             "created_at": "2026-11-23T00:00:00Z"})
        assert tid in self.tenants, "tenant used before it was created"
        if parts[2:] == ["actuals", "upload"]:
            if "nonsense" in body:
                return httpx.Response(422, json={"detail": "CSV is missing columns: period"})
            return httpx.Response(200, json={"rows_received": 3, "months_in_history": 24})
        if parts[2:] == ["actuals", "monthly"]:
            return httpx.Response(200, json=[{"period": "2025-12-01", "revenue": 1.0}])
        if parts[2:] == ["forecasts"] and req.method == "POST":
            return httpx.Response(201, json={**RESULT, "request": json.loads(body)})
        if parts[2:] == ["forecasts"]:
            return httpx.Response(200, json=[{"id": RESULT["id"]}])
        if parts[2] == "forecasts":
            return httpx.Response(200, json=RESULT) if parts[3] == RESULT["id"] else \
                httpx.Response(404, json={"detail": "Forecast not found"})
        return httpx.Response(404, json={"detail": "Not Found"})


def make(tmp_path, service=None, url="http://forecast.test"):
    service = service or FakeService()
    store = LocalStore(tmp_path)
    fc = ForecastClient(url, KEY, transport=httpx.MockTransport(service))
    app = create_app(Settings(auth="dev", store_dir=tmp_path), store=store, forecast=fc)
    return TestClient(app), store, service


H = {"X-Dev-User": "alice"}


def firm_of(client, user="alice"):
    return client.get("/me", headers={"X-Dev-User": user}).json()["adviser"]["firm_id"]


def test_status_when_not_configured(tmp_path):
    client, _, _ = make(tmp_path, url="")
    assert client.get("/forecast", headers=H).json() == {
        "configured": False, "reachable": False, "engine_version": None}
    r = client.post("/forecast/runs", json={}, headers=H)
    assert r.status_code == 503 and "isn't connected" in r.json()["detail"]


def test_requires_sign_in(tmp_path):
    client, _, _ = make(tmp_path)
    assert client.get("/forecast").status_code == 401


def test_upload_run_and_read_back_for_the_firm_tenant(tmp_path):
    client, store, svc = make(tmp_path)
    assert client.get("/forecast", headers=H).json()["engine_version"] == "0.2.0"
    up = client.post("/forecast/actuals", headers=H,
                     files={"file": ("a.csv", b"period,account,category,amount\n", "text/csv")})
    assert up.status_code == 200 and up.json()["months_in_history"] == 24
    run = client.post("/forecast/runs", headers=H,
                      json={"horizon_months": 6, "overrides": {"revenue_growth": 0.02}})
    assert run.status_code == 201, run.text
    out = run.json()
    assert out["months"] == RESULT["months"]  # the service's figures, unchanged
    assert out["request"] == {"horizon_months": 6, "overrides": {"revenue_growth": 0.02},
                              "method": "auto", "seasonal": None}
    assert client.get("/forecast/runs", headers=H).json() == [{"id": RESULT["id"]}]
    assert client.get(f"/forecast/runs/{RESULT['id']}", headers=H).json()["id"] == RESULT["id"]
    assert client.get("/forecast/runs/other", headers=H).status_code == 404
    # Every call went to this firm's tenant, with the key, which never reaches the browser.
    firm = firm_of(client)
    tid = tenant_id(firm)
    assert all(p == "/health" or p.startswith(f"/tenants/{tid}") for _, p, _ in svc.calls)
    assert KEY not in run.text
    events = [r["event"] for r in store.as_user(dev_user_id("alice"), "alice@dev.local")
              .read_audit(firm)]
    assert events == ["forecast_upload", "forecast_run"]


def test_firms_get_separate_tenants(tmp_path):
    client, _, svc = make(tmp_path)
    client.get("/forecast/runs", headers=H)
    client.get("/forecast/runs", headers={"X-Dev-User": "bob"})
    assert tenant_id(firm_of(client)) != tenant_id(firm_of(client, "bob"))
    assert len(svc.tenants) == 2
    assert tenant_id("abc") == tenant_id("abc")  # stable across restarts


def test_service_errors_are_passed_on_plainly(tmp_path):
    client, _, _ = make(tmp_path)
    r = client.post("/forecast/actuals", headers=H, files={"file": ("a.csv", b"nonsense", "text/csv")})
    assert r.status_code == 422 and r.json()["detail"] == "CSV is missing columns: period"
    assert client.post("/forecast/runs", json={"horizon_months": 0}, headers=H).status_code == 422
    for status in (500, 401):
        bad, _, _ = make(tmp_path / str(status), FakeService(fail=status))
        r = bad.post("/forecast/runs", json={}, headers=H)
        assert r.status_code == 502 and "forecast.test" not in r.text and KEY not in r.text


def test_unreachable_service(tmp_path):
    def down(req):
        raise httpx.ConnectError("refused")
    store = LocalStore(tmp_path)
    fc = ForecastClient("http://forecast.test", KEY, transport=httpx.MockTransport(down))
    client = TestClient(create_app(Settings(auth="dev", store_dir=tmp_path), store=store, forecast=fc))
    assert client.get("/forecast", headers=H).json()["reachable"] is False
    r = client.get("/forecast/runs", headers=H)
    assert r.status_code == 502 and "Couldn't reach" in r.json()["detail"]
