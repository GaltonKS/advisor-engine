"""Backups restore: a backup of a populated database, restored into a fresh one with the
migrations applied, gives back the same rows and files, and the restored database keeps
its guarantees (row-level security, append-only audit, working inserts)."""

import hashlib
import json

import pytest

psycopg = pytest.importorskip("psycopg")

from advisor_engine.api import backup  # noqa: E402
from advisor_engine.api.auth import dev_user_id  # noqa: E402
from advisor_engine.api.supabase_store import MemoryStorage, SupabaseStore  # noqa: E402
from advisor_engine.assistant._paths import DEFAULT_HOLDINGS  # noqa: E402

CSV = DEFAULT_HOLDINGS.read_bytes()


def as_(store, user):
    return store.as_user(dev_user_id(user), f"{user}@dev.local")


def raw(url, user, sql, params=()):
    with psycopg.connect(url) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)",
                     [f'{{"sub": "{dev_user_id(user)}", "role": "authenticated"}}'])
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


def owner(url, sql, params=()):
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def source(supabase_db, tmp_path):
    """Two firms with clients, portfolios (files in storage), drafts, conversations,
    audit rows, usage and a soft-deleted client; awkward text included."""
    storage = MemoryStorage()
    store = SupabaseStore(supabase_db, storage, cache_dir=tmp_path / "src-cache")
    world = {}
    for user in ("alice", "bob"):
        db = as_(store, user)
        adv = db.get_or_create_adviser(None, None)
        fid = adv["firm_id"]
        client = db.create_client(fid, adv["id"], {"name": f"{user}'s household\ttab\nnewline \\ é"})
        p = db.add_portfolio(fid, client["id"], adv["id"], "h.csv", CSV, hashlib.sha256(CSV).hexdigest())
        db.create_draft(fid, {"portfolio_id": p["id"], "created_by": adv["id"], "original": "x",
                              "tool_calls": [{"name": "get_risk_profile"}]})
        db.save_conversation(fid, p["id"], {"history": [], "messages": [{"role": "user", "text": "Hi"}]})
        db.write_audit(fid, {"event": "turn", "portfolio_id": p["id"]})
        db.save_questionnaire(fid, client["id"], {"q1": 3})
        gone = db.create_client(fid, adv["id"], {"name": "Former client"})
        db.delete_client(fid, gone["id"], adv["id"])
        db.bump_turns(adv["id"], 200)
        world[user] = {"firm_id": fid, "client": client, "portfolio": p, "adviser": adv}
    owner(supabase_db, "insert into market.prices (source, symbol, month_end, close) "
                       "values ('fmp', 'SPY', '2026-09-30', 571.2)")
    return {"url": supabase_db, "storage": storage, "store": store, "world": world}


@pytest.fixture
def made(source, tmp_path):
    out = tmp_path / "backup"
    manifest = backup.create_backup(source["url"], out, source["storage"])
    return out, manifest


def test_backup_covers_every_app_table_and_files_but_not_licensed_prices(source, made):
    out, manifest = made
    names = {f"{t['schema']}.{t['table']}" for t in manifest["tables"]}
    public = {r[0] for r in owner(source["url"], "select 'public.' || tablename from pg_tables "
                                                 "where schemaname = 'public'")}
    assert public <= names
    assert "auth.users" in names
    assert not any(n.startswith("market.") for n in names)
    assert len(manifest["files"]) == 2 and manifest["files_included"]
    assert manifest["migrations"] and manifest["engine_version"]
    # Parents come before children, so a restore satisfies foreign keys as it goes.
    order = [f"{t['schema']}.{t['table']}" for t in manifest["tables"]]
    assert order.index("auth.users") < order.index("public.advisers") < order.index("public.clients")
    # No credentials in the manifest.
    assert "postgres:postgres" not in (out / "manifest.json").read_text()


def test_restore_gives_back_the_same_data_and_a_working_database(source, made, make_supabase_db,
                                                                 tmp_path):
    out, manifest = made
    target = make_supabase_db(seed=False)
    storage = MemoryStorage()
    report = backup.restore_backup(out, target, storage)
    assert report.ok, report.problems
    assert report.files == 2
    assert report.tables["public.clients"] == 4

    check = backup.verify_restore(out, target, storage)
    assert check.ok, check.problems
    # Independent of verify: the tables read the same from both databases.
    for t in ("public.clients", "public.audit_events", "public.conversations", "auth.users"):
        q = f"select * from {t} order by 1"
        assert owner(target, q) == owner(source["url"], q)

    # The app works on the restored database, with the restored files.
    store = SupabaseStore(target, storage, cache_dir=tmp_path / "dst-cache")
    alice, w = as_(store, "alice"), source["world"]["alice"]
    adv = alice.get_or_create_adviser(None, None)
    assert adv["firm_id"] == w["firm_id"] and adv["status"] == "active"
    assert [c["id"] for c in alice.list_clients(w["firm_id"])] == [w["client"]["id"]]
    assert alice.holdings_file(w["firm_id"], w["portfolio"]["id"]).read_bytes() == CSV
    # New audit rows don't collide with restored ids (identity sequence moved on).
    before = max(r[0] for r in owner(target, "select id from public.audit_events"))
    alice.write_audit(w["firm_id"], {"event": "after_restore"})
    assert owner(target, "select id from public.audit_events where event = 'after_restore'")[0][0] > before

    # Row-level security and the append-only audit log still hold.
    assert raw(target, "bob", "select count(*) from public.clients where firm_id = %s",
               [w["firm_id"]])[0][0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(target, "alice", "delete from public.audit_events")


def test_restore_refuses_a_database_that_already_has_data(source, made):
    out, _ = made
    report = backup.restore_backup(out, source["url"], MemoryStorage())
    assert not report.ok and "already has data" in report.problems[0]


def test_restore_refuses_a_target_missing_a_migration(made, make_supabase_db):
    out, _ = made
    target = make_supabase_db(seed=False)
    owner(target, "alter table public.clients drop column deleted_by")
    report = backup.restore_backup(out, target)
    assert not report.ok and "public.clients" in report.problems[0]
    assert owner(target, "select count(*) from public.firms")[0][0] == 0  # nothing loaded


def test_restore_is_all_or_nothing(made, make_supabase_db):
    out, _ = made
    target = make_supabase_db(seed=False)
    # A constraint the backup's data breaks, on a table loaded late.
    owner(target, "alter table public.audit_events add constraint no_turns check (event <> 'turn')")
    with pytest.raises(psycopg.errors.CheckViolation):
        backup.restore_backup(out, target)
    assert owner(target, "select count(*) from public.firms")[0][0] == 0


def test_damaged_backup_is_refused_before_anything_is_written(made, make_supabase_db):
    out, manifest = made
    (out / "tables" / "public.clients.copy").write_bytes(b"tampered\n")
    target = make_supabase_db(seed=False)
    with pytest.raises(backup.BackupError, match="public.clients.copy"):
        backup.restore_backup(out, target)
    f = out / "files" / manifest["files"][0]["path"]
    f.unlink()
    with pytest.raises(backup.BackupError, match="damaged"):
        backup.load_backup(out)


def test_backup_fails_when_a_holdings_file_is_missing_or_changed(source, tmp_path):
    path = next(iter(source["storage"].objects))
    source["storage"].objects[path] = b"changed"
    with pytest.raises(backup.BackupError, match="recorded sha256"):
        backup.create_backup(source["url"], tmp_path / "b1", source["storage"])
    del source["storage"].objects[path]
    with pytest.raises(backup.BackupError, match="could not be downloaded"):
        backup.create_backup(source["url"], tmp_path / "b2", source["storage"])


def test_verify_catches_a_difference(made, make_supabase_db):
    out, _ = made
    target = make_supabase_db(seed=False)
    assert backup.restore_backup(out, target, MemoryStorage()).ok
    owner(target, "update public.clients set name = 'changed' where deleted_at is null "
                  "and name like 'alice%%'")
    report = backup.verify_restore(out, target)
    assert [p.split(":")[0] for p in report.problems] == ["public.clients"]


def test_files_only_finishes_an_interrupted_restore(made, make_supabase_db):
    out, manifest = made
    target = make_supabase_db(seed=False)
    storage = MemoryStorage()
    first = manifest["files"][0]
    storage.put(first["path"], (out / "files" / first["path"]).read_bytes())  # already uploaded
    assert backup.restore_backup(out, target, None).ok
    report = backup.restore_backup(out, target, storage, files_only=True)
    assert report.ok and report.files == 2
    assert backup.verify_restore(out, target, storage).ok


def test_auth_partial_is_needed_for_a_target_without_supabase_auth(source, tmp_path,
                                                                    make_supabase_db):
    # Real auth.users has many more columns, some generated; the test shim has two.
    owner(source["url"], "alter table auth.users add column phone text, "
                         "add column email_lower text generated always as (lower(email)) stored")
    owner(source["url"], "update auth.users set phone = '555'")
    out = tmp_path / "b"
    manifest = backup.create_backup(source["url"], out, source["storage"])
    users = next(t for t in manifest["tables"] if t["table"] == "users")
    assert users["columns"] == ["id", "email", "phone"]  # generated column left out

    target = make_supabase_db(seed=False)
    strict = backup.restore_backup(out, target)
    assert not strict.ok and "auth.users" in strict.problems[0]
    partial = backup.restore_backup(out, target, auth_partial=True)
    assert partial.ok and any("auth.users" in n for n in partial.notes)
    assert backup.verify_restore(out, target, auth_partial=True).ok


def test_command_line_round_trip(source, make_supabase_db, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli"
    target = make_supabase_db(seed=False)
    monkeypatch.setenv("SUPABASE_DB_URL", source["url"])
    assert backup.main(["create", str(out), "--skip-files"]) == 0
    assert json.loads((out / "manifest.json").read_text())["files_included"] is False
    monkeypatch.setenv("RESTORE_DB_URL", target)
    assert backup.main(["restore", str(out), "--skip-files"]) == 0
    assert backup.main(["verify", str(out), "--skip-files"]) == 0
    assert backup.main(["restore", str(out), "--skip-files"]) == 1  # target has data now
    text = capsys.readouterr()
    assert "OK" in text.out and "already has data" in text.err
    assert "postgres:postgres" not in text.out + text.err
    # --files-only needs storage, not a database.
    monkeypatch.delenv("RESTORE_DB_URL")
    assert backup.main(["restore", str(out), "--files-only", "--skip-files"]) == 2
    monkeypatch.delenv("RESTORE_SUPABASE_URL", raising=False)
    assert backup.main(["restore", str(out), "--files-only"]) == 2
    assert "RESTORE_SUPABASE_URL" in capsys.readouterr().err


def test_command_line_needs_settings_and_an_empty_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert backup.main(["create", str(tmp_path / "x")]) == 2
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/none")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert backup.main(["create", str(tmp_path / "x")]) == 2
    assert "--skip-files" in capsys.readouterr().err
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("x")
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.create_backup("postgresql://localhost/none", tmp_path / "full")


def test_unexpected_storage_paths_are_refused():
    for bad in ("../etc/passwd", "/abs.csv", "a/../../b.csv", ""):
        with pytest.raises(backup.BackupError):
            backup._safe_file(bad)
    assert backup._safe_file("0b6c/1f2e-aa.csv") == "0b6c/1f2e-aa.csv"
