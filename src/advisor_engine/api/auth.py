"""Who is calling: a verified Supabase access token, or a dev header locally.

The token also says how strongly the caller signed in: `aal` is "aal1" after a password
alone and "aal2" once they have entered a code from an enrolled authenticator, and `amr`
lists each method used with the time it was used. The API enforces multi-factor sign-in
from these (see main.py and docs/mfa-plan.md); the browser only guides the user.

Supabase signs access tokens either with the project's asymmetric signing keys
(published at /auth/v1/.well-known/jwks.json) or, on older projects, with a shared
HS256 secret. Both are accepted; the audience must be "authenticated", so anon and
service-role keys are refused. Keep "Confirm email" on in Supabase: it stops a session
being issued before the address is verified, which this server relies on.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, Request

from .settings import Settings


# Sign-in methods that count as a second factor in a token's `amr` claim. Supabase
# records an authenticator code as "totp"; passkeys / security keys are listed so they
# satisfy the same checks once they are added as a factor type.
MFA_METHODS = frozenset({"totp", "mfa/totp", "webauthn", "mfa/webauthn"})

# How recently a code must have been entered for a sensitive action (deleting a client,
# removing an authenticator). Older than this, the API asks for a fresh code.
FRESH_MFA_SECONDS = 300


@dataclass(frozen=True)
class User:
    id: str
    email: str
    aal: str = "aal1"
    # (method, unix time) pairs from the token's `amr` claim.
    amr: tuple[tuple[str, int], ...] = ()

    def mfa_age(self, now: float | None = None) -> float | None:
        """Seconds since a second factor was last used in this session, or None if never."""
        times = [t for method, t in self.amr if method in MFA_METHODS]
        if not times:
            return None
        return (time.time() if now is None else now) - max(times)

    def mfa_fresh(self, now: float | None = None) -> bool:
        age = self.mfa_age(now)
        return age is not None and age <= FRESH_MFA_SECONDS


def _amr(claim) -> tuple[tuple[str, int], ...]:
    """The `amr` claim as (method, timestamp) pairs; anything malformed is ignored."""
    out = []
    for entry in claim if isinstance(claim, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("method"), str):
            try:
                out.append((entry["method"], int(entry.get("timestamp", 0))))
            except (TypeError, ValueError):
                continue
    return tuple(out)


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_supabase_token(token: str, settings: Settings) -> User:
    try:
        if settings.supabase_jwt_secret:
            key, algorithms = settings.supabase_jwt_secret, ["HS256"]
        else:
            key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token).key
            algorithms = ["ES256", "RS256"]
        claims = jwt.decode(
            token, key, algorithms=algorithms, audience="authenticated",
            issuer=f"{settings.supabase_url}/auth/v1",
            options={"require": ["sub", "exp", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid sign-in token: {e}") from e
    if claims.get("is_anonymous"):
        raise HTTPException(401, "Anonymous sessions cannot use the engine.")
    aal = claims.get("aal") if claims.get("aal") in ("aal1", "aal2") else "aal1"
    return User(id=claims["sub"], email=claims.get("email", ""), aal=aal,
                amr=_amr(claims.get("amr")))


def dev_user_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"advisor-engine-dev:{name}"))


def current_user(
    request: Request,
    authorization: str = Header(default=""),
    x_dev_user: str = Header(default=""),
) -> User:
    settings: Settings = request.app.state.settings
    if settings.auth == "dev":
        if not x_dev_user:
            raise HTTPException(401, "Dev auth: send an X-Dev-User header.")
        # A stable UUID per name, the same shape as a Supabase user id.
        return User(id=dev_user_id(x_dev_user), email=f"{x_dev_user}@dev.local")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in required.")
    return verify_supabase_token(token, settings)
