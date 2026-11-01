"""The few Supabase Auth admin calls the API makes for multi-factor sign-in.

The browser enrols and verifies authenticators directly with Supabase (that is how the
session reaches aal2). Three things go through the server instead, with the project's
secret key, so the server decides rather than the browser:

- confirming that a newly enrolled authenticator is real before it is audited;
- removing an authenticator (only after a fresh code, and never the last one while MFA
  is required);
- a firm owner's view of which of the firm's advisers have enrolled.

Errors shown to advisers never include the key or the request URL, and httpx errors are
not chained (same rule as the market-data adapters).
"""

from __future__ import annotations

import httpx


class AuthAdminError(RuntimeError):
    """The sign-in service could not be reached or refused the request."""


def secret_key_headers(secret_key: str) -> dict:
    """Headers for a Supabase secret key: new `sb_secret_...` keys go in `apikey` only;
    legacy service_role keys are JWTs and also go in Authorization."""
    headers = {"apikey": secret_key}
    if not secret_key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {secret_key}"
    return headers


def _factor(raw: dict) -> dict:
    """A factor as the API uses it: no secrets (Supabase doesn't return them anyway)."""
    return {k: raw.get(k) for k in ("id", "friendly_name", "factor_type", "status",
                                    "created_at", "updated_at")}


def verified(factors: list[dict]) -> list[dict]:
    return [f for f in factors if f.get("status") == "verified"]


class SupabaseAuthAdmin:
    def __init__(self, supabase_url: str, secret_key: str, *, client: httpx.Client | None = None):
        self.base = f"{supabase_url.rstrip('/')}/auth/v1/admin/users"
        self.headers = secret_key_headers(secret_key)
        self.http = client or httpx.Client(timeout=15)

    def _call(self, method: str, path: str) -> httpx.Response:
        try:
            r = self.http.request(method, f"{self.base}/{path}", headers=self.headers)
        except httpx.HTTPError:
            raise AuthAdminError("Couldn't reach the sign-in service. Please try again.") from None
        if r.status_code == 404:
            raise LookupError("not found")
        if r.status_code >= 400:
            raise AuthAdminError(
                f"The sign-in service refused the request (HTTP {r.status_code}).") from None
        return r

    def list_factors(self, user_id: str) -> list[dict]:
        """Every factor the user has, verified or not."""
        data = self._call("GET", f"{user_id}/factors").json()
        if isinstance(data, dict):  # tolerate {"factors": [...]} as well as a bare list
            data = data.get("factors") or []
        return [_factor(f) for f in data if isinstance(f, dict)]

    def delete_factor(self, user_id: str, factor_id: str) -> None:
        self._call("DELETE", f"{user_id}/factors/{factor_id}")


class MemoryAuthAdmin:
    """In-process stand-in, for dev auth and tests: factors are whatever tests put here."""

    def __init__(self):
        self.factors: dict[str, list[dict]] = {}

    def add(self, user_id: str, factor_id: str, *, status: str = "verified",
            friendly_name: str = "", created_at: str = "2026-11-01T00:00:00+00:00") -> dict:
        f = {"id": factor_id, "friendly_name": friendly_name, "factor_type": "totp",
             "status": status, "created_at": created_at, "updated_at": created_at}
        self.factors.setdefault(user_id, []).append(f)
        return f

    def list_factors(self, user_id: str) -> list[dict]:
        return [dict(f) for f in self.factors.get(user_id, [])]

    def delete_factor(self, user_id: str, factor_id: str) -> None:
        before = self.factors.get(user_id, [])
        after = [f for f in before if f["id"] != factor_id]
        if len(after) == len(before):
            raise LookupError("not found")
        self.factors[user_id] = after
