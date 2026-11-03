"""Both stores implement the interface the API uses (api/store_base.py), by method name
and signature. Behaviour is covered by the shared tests that run against both stores."""

import inspect

import pytest

pytest.importorskip("fastapi")

from advisor_engine.api.store import LocalStore  # noqa: E402
from advisor_engine.api.store_base import Store, StoreSession  # noqa: E402
from advisor_engine.api.supabase_store import SupabaseSession, SupabaseStore  # noqa: E402


def protocol_methods(proto) -> dict[str, inspect.Signature]:
    return {name: inspect.signature(fn) for name, fn in vars(proto).items()
            if inspect.isfunction(fn) and not name.startswith("_")}


def assert_conforms(cls, proto):
    for name, want in protocol_methods(proto).items():
        assert hasattr(cls, name), f"{cls.__name__} has no {name}()"
        have = inspect.signature(getattr(cls, name))
        want_params = list(want.parameters.values())
        have_params = list(have.parameters.values())
        # Same parameters, in order, with the same kinds and defaults ...
        for w, h in zip(want_params, have_params):
            assert (h.name, h.kind, h.default) == (w.name, w.kind, w.default), \
                f"{cls.__name__}.{name}: {have} does not match {want}"
        # ... and anything extra must be optional, so a protocol-shaped call still works.
        for extra in have_params[len(want_params):]:
            assert extra.default is not inspect.Parameter.empty, \
                f"{cls.__name__}.{name}: extra required parameter {extra.name}"
        assert len(have_params) >= len(want_params), f"{cls.__name__}.{name}: {have}"


@pytest.mark.parametrize("cls", [LocalStore, SupabaseStore])
def test_store_conforms(cls):
    assert_conforms(cls, Store)


@pytest.mark.parametrize("cls", [LocalStore, SupabaseSession])
def test_session_conforms(cls):
    assert_conforms(cls, StoreSession)


def test_local_store_instance_is_both(tmp_path):
    store = LocalStore(tmp_path)
    assert isinstance(store, Store)
    session = store.as_user("u1", "u1@dev.local")
    assert isinstance(session, StoreSession)


def test_protocol_covers_every_store_call_in_the_routes():
    # Every `c.db.<method>` / `store.<method>` the API makes is declared in a protocol, so
    # a new store method can't be used by a route without being added to the interface.
    import re
    from pathlib import Path

    api = Path(__file__).resolve().parents[1] / "src" / "advisor_engine" / "api"
    text = "".join(p.read_text() for p in [api / "deps.py", api / "main.py",
                                           *sorted((api / "routers").glob("*.py"))])
    used_session = set(re.findall(r"\bc\.db\.(\w+)\(", text)) | set(re.findall(r"\bdb\.(\w+)\(", text))
    used_store = set(re.findall(r"\bstore\.(\w+)\(", text)) - {"mfa_mode"}  # optional
    assert used_session and used_store
    assert used_session <= set(protocol_methods(StoreSession)), used_session - set(protocol_methods(StoreSession))
    assert used_store <= set(protocol_methods(Store)), used_store - set(protocol_methods(Store))
