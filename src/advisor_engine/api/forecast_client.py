"""Calls to the forecast engine service (GaltonKS/forecast-engine) for one firm.

The forecast engine is our own service, deployed separately; this API calls it on the
firm's behalf so its key never reaches the browser. It computes every forecast figure;
this module only moves requests and answers.

Tenancy: each firm is one forecast tenant, with an id derived from the firm id
(`tenant_id`), created on first use with the service's idempotent `PUT /tenants/{id}`.
Nothing is stored here, so there is no lookup table to keep in step.
"""

from __future__ import annotations

import uuid

import httpx

# Fixed namespace: changing it would point every firm at a new, empty tenant.
TENANT_NAMESPACE = uuid.UUID("3f6c1d2a-7b8e-5c4f-9a1d-0e2b4c6d8f10")
TIMEOUT = httpx.Timeout(60.0, connect=5.0)  # an `auto` forecast backtests several models


class ForecastUnavailable(Exception):
    """The service isn't configured, can't be reached, or failed (shown as 503 / 502)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ForecastRefused(Exception):
    """The service answered 4xx: bad input, no data yet, unknown forecast."""

    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def tenant_id(firm_id: str) -> str:
    return str(uuid.uuid5(TENANT_NAMESPACE, f"galtonks-firm:{firm_id}"))


class ForecastClient:
    def __init__(self, base_url: str, api_key: str = "",
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _call(self, method: str, path: str, **kw):
        if not self.configured:
            raise ForecastUnavailable(503, "The forecast engine isn't connected. "
                                           "Set FORECAST_API_URL on the API.")
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            with httpx.Client(base_url=self.base_url, headers=headers, timeout=TIMEOUT,
                              transport=self.transport) as http:
                r = http.request(method, path, **kw)
        except httpx.HTTPError:
            # No URL or key in the message: it is shown to the user.
            raise ForecastUnavailable(502, "Couldn't reach the forecast engine. "
                                           "Please try again in a moment.") from None
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 500 or r.status_code in (401, 403):
            # 401/403 means our key is wrong: a server problem, not the user's.
            raise ForecastUnavailable(502, "The forecast engine returned an error.")
        if r.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise ForecastRefused(r.status_code, detail or "The forecast engine refused the request.")
        return data

    def health(self) -> dict:
        return self._call("GET", "/health")

    def ensure_tenant(self, firm_id: str) -> str:
        tid = tenant_id(firm_id)
        self._call("PUT", f"/tenants/{tid}", json={"name": f"GaltonKS firm {firm_id}"})
        return tid

    def upload(self, firm_id: str, filename: str, content: bytes) -> dict:
        tid = self.ensure_tenant(firm_id)
        return self._call("POST", f"/tenants/{tid}/actuals/upload",
                          files={"file": (filename or "actuals.csv", content, "text/csv")})

    def monthly(self, firm_id: str) -> list[dict]:
        tid = self.ensure_tenant(firm_id)
        return self._call("GET", f"/tenants/{tid}/actuals/monthly")

    def run(self, firm_id: str, request: dict) -> dict:
        tid = self.ensure_tenant(firm_id)
        return self._call("POST", f"/tenants/{tid}/forecasts", json=request)

    def list_runs(self, firm_id: str, limit: int = 20) -> list[dict]:
        tid = self.ensure_tenant(firm_id)
        return self._call("GET", f"/tenants/{tid}/forecasts", params={"limit": limit})

    def get_run(self, firm_id: str, run_id: str) -> dict:
        tid = self.ensure_tenant(firm_id)
        return self._call("GET", f"/tenants/{tid}/forecasts/{run_id}")
