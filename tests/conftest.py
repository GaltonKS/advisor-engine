"""Shared fixtures. `supabase_db` gives each test a fresh Postgres database with the
Supabase shim and every migration applied, or skips when no Postgres is reachable
(set TEST_DATABASE_URL to an admin connection string to point it elsewhere)."""

import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DEV_USERS = ("alice", "bob", "carol")  # approved advisers, as most tests need
NEW_USERS = ("dave",)                  # signed up but never approved (stays pending)


def _with_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def _apply_schema(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute((ROOT / "tests" / "supabase_shim.sql").read_text())
        for migration in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            conn.execute(migration.read_text())


def _seed_dev_users(url: str) -> None:
    import psycopg

    from advisor_engine.api.auth import dev_user_id

    with psycopg.connect(url, autocommit=True) as conn:
        # Supabase creates auth.users rows at sign-up; the dev users stand in for that.
        for user in DEV_USERS + NEW_USERS:
            conn.execute("insert into auth.users (id, email) values (%s, %s)",
                         [dev_user_id(user), f"{user}@dev.local"])
        # New sign-ups wait as pending; sign the dev users up and approve them, as the
        # operator would, so tests start from active advisers.
        for user in DEV_USERS:
            with conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute("select set_config('request.jwt.claims', %s, true)",
                             [f'{{"sub": "{dev_user_id(user)}", "email": "{user}@dev.local",'
                              ' "role": "authenticated"}'])
                conn.execute("select app.ensure_adviser()")
            conn.execute("select app.approve_adviser(%s)", [f"{user}@dev.local"])


@pytest.fixture
def make_supabase_db():
    """Factory for fresh databases: `make_supabase_db(seed=False)` gives the migrations
    and no data (a restore target); the default also signs up the dev users."""
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("No Postgres for Supabase store tests (set TEST_DATABASE_URL).")
    names = []

    def make(seed: bool = True, migrations: bool = True) -> str:
        name = f"t_{uuid.uuid4().hex[:12]}"
        admin.execute(f'create database "{name}"')
        names.append(name)
        url = _with_db(ADMIN_URL, name)
        if migrations:
            _apply_schema(url)
            if seed:
                _seed_dev_users(url)
        return url

    try:
        yield make
    finally:
        for name in names:
            admin.execute(f'drop database if exists "{name}" with (force)')
        admin.close()


@pytest.fixture
def supabase_db(make_supabase_db):
    return make_supabase_db()
