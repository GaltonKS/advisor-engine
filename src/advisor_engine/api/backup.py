"""Our own backups of the Supabase data, and restore into an empty database.

    python -m advisor_engine.api.backup create OUT_DIR [--skip-files]
    python -m advisor_engine.api.backup restore BACKUP_DIR [--skip-files | --files-only] [--auth-partial]
    python -m advisor_engine.api.backup verify BACKUP_DIR [--skip-files] [--auth-partial]

`create` reads SUPABASE_DB_URL (the API's owner connection) and, for the holdings files,
SUPABASE_URL and SUPABASE_SECRET_KEY. `restore` and `verify` write to / read from
RESTORE_DB_URL, RESTORE_SUPABASE_URL and RESTORE_SUPABASE_SECRET_KEY, so a restore never
picks up the production settings by accident. Connection strings come from the
environment, never the command line, so they don't show in `ps` or shell history.

Why this exists next to Supabase's own daily backups (see docs/backups.md):
- Supabase's database backups don't include the files in Storage: they hold only the
  objects' metadata. Our holdings CSVs live in the `holdings` bucket.
- A copy we hold ourselves, encrypted, survives a problem with the Supabase project or
  account, and can be kept longer than the plan's 7 days.

What a backup holds:
- Every table in the `public` and `app` schemas, plus Supabase's `auth.users`,
  `auth.identities` and `auth.mfa_factors` (advisers' ids reference `auth.users`), as
  PostgreSQL COPY text, read in one repeatable-read snapshot so the tables agree.
  New tables in `public` / `app` are included automatically.
- Every holdings file a portfolio row points to, checked against the row's sha256.
- `manifest.json`: columns, row counts and sha256 of every file, plus the migrations in
  the repo at backup time. No connection strings or keys.

Not included, on purpose: `market` (licensed vendor prices; a rebuildable cache that must
not leave the API's database) and Supabase's own `storage` schema (re-created when the
files are uploaded again).

The schema is not in the backup: it is `supabase/migrations/`. A restore goes into a
database with the migrations already applied and no data, and refuses anything else.
Generated columns are skipped (Postgres recomputes them) and identity sequences are moved
past the restored ids. The contents are sensitive (client holdings, conversations,
password hashes): the nightly job encrypts the archive before it leaves the runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
SCHEMAS = ("public", "app")
AUTH_TABLES = ("users", "identities", "mfa_factors")
MANIFEST = "manifest.json"
# Fixed output settings, so the same rows always export to the same bytes (verify relies
# on it) whatever the server's or role's defaults are.
SESSION_SETTINGS = {
    "TimeZone": "UTC",
    "DateStyle": "ISO, YMD",
    "IntervalStyle": "postgres",
    "extra_float_digits": "1",
    "bytea_output": "hex",
}
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$")


class BackupError(Exception):
    """A backup, restore or verify that cannot go ahead; the message says why."""


@dataclass
class Report:
    """What a restore or verify did. `problems` make it fail; `notes` are information."""

    tables: dict[str, int] = field(default_factory=dict)  # "schema.table" -> rows
    files: int = 0
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


# --- database helpers ----------------------------------------------------------------

def _connect(url: str):
    import psycopg

    return psycopg.connect(url, prepare_threshold=None)


def _settings(conn) -> None:
    """Fixed output settings for the current transaction. Local to the transaction, not
    the session: through Supabase's transaction pooler each transaction may run on a
    different server connection."""
    for name, value in SESSION_SETTINGS.items():
        conn.execute("select set_config(%s, %s, true)", [name, value])


def _snapshot(conn) -> None:
    """Make the connection's next transaction a read-only, repeatable-read snapshot, so
    every table is read as of the same moment."""
    from psycopg import IsolationLevel

    conn.isolation_level = IsolationLevel.REPEATABLE_READ
    conn.read_only = True


def _q(schema: str, table: str) -> str:
    from psycopg import sql

    return sql.Identifier(schema, table).as_string(None)


def _cols(names) -> str:
    from psycopg import sql

    return sql.SQL(", ").join(sql.Identifier(n) for n in names).as_string(None)


def _tables(conn) -> list[tuple[str, str]]:
    """The tables a backup covers, parents before children (foreign keys)."""
    rows = conn.execute(
        """select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where c.relkind in ('r', 'p') and not c.relispartition
              and (n.nspname = any(%s) or (n.nspname = 'auth' and c.relname = any(%s)))
            order by n.nspname, c.relname""",
        [list(SCHEMAS), list(AUTH_TABLES)]).fetchall()
    tables = [(s, t) for s, t in rows]
    deps = {t: set() for t in tables}
    for cs, ct, ps, pt in conn.execute(
            """select cn.nspname, cc.relname, pn.nspname, pc.relname
                 from pg_constraint k
                 join pg_class cc on cc.oid = k.conrelid join pg_namespace cn on cn.oid = cc.relnamespace
                 join pg_class pc on pc.oid = k.confrelid join pg_namespace pn on pn.oid = pc.relnamespace
                where k.contype = 'f'""").fetchall():
        child, parent = (cs, ct), (ps, pt)
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    ordered: list[tuple[str, str]] = []
    done: set[tuple[str, str]] = set()

    def visit(t, path=()):
        if t in done:
            return
        if t in path:
            raise BackupError(f"Foreign keys form a cycle through {_q(*t)}; restore order is ambiguous.")
        for p in sorted(deps[t]):
            visit(p, path + (t,))
        done.add(t)
        ordered.append(t)

    for t in tables:
        visit(t)
    return ordered


def _columns(conn, schema: str, table: str) -> list[str]:
    """Stored columns in table order; generated columns are left out (Postgres recomputes them)."""
    return [r[0] for r in conn.execute(
        """select a.attname from pg_attribute a
            where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped
              and a.attgenerated = '' order by a.attnum""", [_q(schema, table)]).fetchall()]


def _order(conn, schema: str, table: str, cols: list[str]) -> list[str]:
    pk = [r[0] for r in conn.execute(
        """select a.attname from pg_index i
             join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
            where i.indrelid = %s::regclass and i.indisprimary
            order by array_position(i.indkey, a.attnum)""", [_q(schema, table)]).fetchall()]
    return [c for c in pk if c in cols] or cols


def _export(conn, schema: str, table: str, cols: list[str]) -> bytes:
    order = _order(conn, schema, table, cols)
    query = f"copy (select {_cols(cols)} from {_q(schema, table)} order by {_cols(order)}) to stdout"
    out = bytearray()
    with conn.cursor().copy(query) as copy:
        for chunk in copy:
            out += chunk
    return bytes(out)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _host(url: str) -> str:
    """Where a connection string points, without the user or password."""
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(url)
    return f"{info.get('host', 'localhost')}:{info.get('port', '5432')}/{info.get('dbname', '')}"


def _migrations() -> list[str]:
    from ..assistant._paths import REPO_ROOT

    return sorted(p.name for p in (REPO_ROOT / "supabase" / "migrations").glob("*.sql"))


def _project(data: bytes, cols: list[str], keep: list[str]) -> bytes:
    """COPY text rows cut down to `keep`. Tabs and newlines inside values are escaped in
    this format, so splitting on them is exact."""
    if keep == cols:
        return data
    idx = [cols.index(c) for c in keep]
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return b"".join(b"\t".join(line.split(b"\t")[i] for i in idx) + b"\n" for line in lines)


def _rows(data: bytes) -> list[bytes]:
    return sorted(line for line in data.split(b"\n") if line)


def _safe_file(path: str) -> str:
    if not _SAFE_PATH.match(path) or ".." in path.split("/"):
        raise BackupError(f"Refusing an unexpected storage path: {path!r}")
    return path


# --- create ----------------------------------------------------------------------------

def create_backup(db_url: str, out_dir: Path, storage=None) -> dict:
    """Write a backup of `db_url` (and, with `storage`, the holdings files) to `out_dir`.

    `out_dir` must be new or empty. Returns the manifest. Raises BackupError if a holdings
    file is missing or doesn't match its portfolio's recorded hash: a backup that can't
    restore every portfolio is reported, not quietly written.
    """
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BackupError(f"{out_dir} is not empty; back up into a new folder.")
    (out_dir / "tables").mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "format": FORMAT_VERSION,
        "source": _host(db_url),
        "migrations": _migrations(),
        "excluded": ["market (licensed price cache, rebuildable)",
                     "storage schema (Supabase-managed; files are under files/)"],
        "tables": [],
        "files_included": storage is not None,
        "files": [],
    }
    from .. import __version__

    manifest["engine_version"] = __version__
    with _connect(db_url) as conn:
        _snapshot(conn)
        _settings(conn)
        manifest["created_at"] = conn.execute("select now()").fetchone()[0].astimezone(
            timezone.utc).isoformat()
        manifest["server_version"] = conn.execute("show server_version").fetchone()[0]
        for schema, table in _tables(conn):
            cols = _columns(conn, schema, table)
            data = _export(conn, schema, table, cols)
            name = f"tables/{schema}.{table}.copy"
            (out_dir / name).write_bytes(data)
            manifest["tables"].append({
                "schema": schema, "table": table, "columns": cols, "file": name,
                "rows": len(_rows(data)), "sha256": _sha(data)})
        portfolios = conn.execute(
            "select storage_path, sha256 from public.portfolios order by storage_path").fetchall()
        conn.rollback()
    if storage is not None:
        problems = []
        for path, sha in portfolios:
            _safe_file(path)
            try:
                data = storage.get(path)
            except Exception as exc:  # noqa: BLE001 - reported below, by path only
                problems.append(f"{path}: could not be downloaded ({type(exc).__name__})")
                continue
            if _sha(data) != sha:
                problems.append(f"{path}: does not match the portfolio's recorded sha256")
                continue
            target = out_dir / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest["files"].append({"path": path, "sha256": sha, "bytes": len(data)})
        if problems:
            raise BackupError("Holdings files failed:\n  " + "\n  ".join(problems))
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# --- reading a backup -------------------------------------------------------------------

def load_backup(backup_dir: Path) -> dict:
    """The manifest, after checking every file in the backup against its sha256."""
    backup_dir = Path(backup_dir)
    try:
        manifest = json.loads((backup_dir / MANIFEST).read_text())
    except FileNotFoundError:
        raise BackupError(f"No {MANIFEST} in {backup_dir}: not a backup folder, or incomplete.") from None
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError(f"Backup format {manifest.get('format')!r} is not supported "
                          f"(this version reads {FORMAT_VERSION}).")
    bad = []
    for t in manifest["tables"]:
        path = backup_dir / t["file"]
        if not path.is_file() or _sha(path.read_bytes()) != t["sha256"]:
            bad.append(t["file"])
    for f in manifest["files"]:
        path = backup_dir / "files" / _safe_file(f["path"])
        if not path.is_file() or _sha(path.read_bytes()) != f["sha256"]:
            bad.append(f"files/{f['path']}")
    if bad:
        raise BackupError("Backup is damaged; these files are missing or changed:\n  "
                          + "\n  ".join(bad))
    return manifest


def _plan(conn, manifest: dict, auth_partial: bool, report: Report) -> list[tuple[dict, list[str]]]:
    """For each backed-up table, the columns to restore into the target, checking the
    target's schema matches. Schema problems go into `report.problems`."""
    present = set(_tables(conn))
    plan = []
    for t in manifest["tables"]:
        key, name = (t["schema"], t["table"]), f"{t['schema']}.{t['table']}"
        auth = t["schema"] == "auth"
        if key not in present:
            if auth and auth_partial:
                report.notes.append(f"{name}: not in the target, skipped (--auth-partial)")
            else:
                report.problems.append(f"{name}: table missing in the target "
                                       "(apply every migration in supabase/migrations first)")
            continue
        target_cols = _columns(conn, *key)
        missing = [c for c in t["columns"] if c not in target_cols]
        if missing and not (auth and auth_partial):
            report.problems.append(f"{name}: target lacks column(s) {', '.join(missing)} "
                                   "(schema differs from the backup's; apply the missing migrations)")
            continue
        if missing:
            report.notes.append(f"{name}: {len(missing)} column(s) not in the target, "
                                "left out (--auth-partial)")
        plan.append((t, [c for c in t["columns"] if c in target_cols]))
    return plan


# --- restore ----------------------------------------------------------------------------

def restore_backup(backup_dir: Path, db_url: str, storage=None, *, files_only: bool = False,
                   auth_partial: bool = False) -> Report:
    """Restore a backup into `db_url`, which must have the migrations applied and no data.

    The tables load in one transaction: either all of them or none. Holdings files are
    uploaded afterwards (with `storage`); a file already there with the same content is
    accepted, so `files_only=True` can finish a restore whose uploads were interrupted.
    `auth_partial` lets a plain Postgres stand-in (the nightly drill) take the backup
    without Supabase's full auth schema; never use it for a real restore.
    """
    backup_dir = Path(backup_dir)
    manifest = load_backup(backup_dir)
    report = Report()
    if not files_only:
        with _connect(db_url) as conn:
            _settings(conn)
            plan = _plan(conn, manifest, auth_partial, report)
            if report.problems:
                return report
            busy = [f"{s}.{t}" for s, t in _tables(conn)
                    if conn.execute(f"select exists (select from {_q(s, t)})").fetchone()[0]]
            if busy:
                report.problems.append("The target already has data in " + ", ".join(busy)
                                       + ". Restore only into a database with no data.")
                return report
            for t, cols in plan:
                data = _project((backup_dir / t["file"]).read_bytes(), t["columns"], cols)
                with conn.cursor().copy(
                        f"copy {_q(t['schema'], t['table'])} ({_cols(cols)}) from stdin") as copy:
                    copy.write(data)
                report.tables[f"{t['schema']}.{t['table']}"] = t["rows"]
            _reset_sequences(conn, [(t["schema"], t["table"]) for t, _ in plan])
            conn.commit()
    if not manifest["files_included"]:
        report.notes.append("This backup has no holdings files (made with --skip-files).")
    elif storage is None:
        report.notes.append(f"Holdings files not uploaded ({len(manifest['files'])} in the backup).")
    else:
        for f in manifest["files"]:
            data = (backup_dir / "files" / f["path"]).read_bytes()
            try:
                storage.put(f["path"], data)
            except Exception as exc:  # noqa: BLE001 - accept an identical file already there
                try:
                    same = _sha(storage.get(f["path"])) == f["sha256"]
                except Exception:  # noqa: BLE001
                    same = False
                if not same:
                    report.problems.append(f"{f['path']}: upload failed ({type(exc).__name__})")
                    continue
            report.files += 1
    return report


def _reset_sequences(conn, tables) -> None:
    """Move identity / serial sequences past the restored ids, so new rows don't collide."""
    for schema, table in tables:
        for (col,) in conn.execute(
                """select a.attname from pg_attribute a
                    where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped
                      and pg_get_serial_sequence(%s, a.attname) is not null""",
                [_q(schema, table), _q(schema, table)]).fetchall():
            seq = conn.execute("select pg_get_serial_sequence(%s, %s)",
                               [_q(schema, table), col]).fetchone()[0]
            top = conn.execute(f"select max({_cols([col])}) from {_q(schema, table)}").fetchone()[0]
            if top is None:
                conn.execute("select setval(%s, 1, false)", [seq])
            else:
                conn.execute("select setval(%s, %s, true)", [seq, top])


# --- verify -----------------------------------------------------------------------------

def verify_restore(backup_dir: Path, db_url: str, storage=None, *, auth_partial: bool = False) -> Report:
    """Check `db_url` (and, with `storage`, the bucket) holds exactly what the backup does:
    the same rows in every table, compared value for value, and every holdings file with
    its recorded sha256."""
    backup_dir = Path(backup_dir)
    manifest = load_backup(backup_dir)
    report = Report()
    with _connect(db_url) as conn:
        _snapshot(conn)
        _settings(conn)
        for t, cols in _plan(conn, manifest, auth_partial, report):
            name = f"{t['schema']}.{t['table']}"
            expected = _rows(_project((backup_dir / t["file"]).read_bytes(), t["columns"], cols))
            actual = _rows(_export(conn, t["schema"], t["table"], cols))
            if actual != expected:
                report.problems.append(f"{name}: {len(actual)} rows in the database, "
                                       f"{len(expected)} in the backup, or values differ")
            report.tables[name] = len(actual)
        conn.rollback()
    if storage is not None and manifest["files_included"]:
        for f in manifest["files"]:
            try:
                ok = _sha(storage.get(f["path"])) == f["sha256"]
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                report.files += 1
            else:
                report.problems.append(f"{f['path']}: missing from storage or changed")
    elif manifest["files_included"]:
        report.notes.append("Holdings files not checked in storage (--skip-files).")
    return report


# --- command line -----------------------------------------------------------------------

def _storage(url_var: str, key_var: str):
    from .supabase_store import SupabaseStorage

    url, key = os.environ.get(url_var, ""), os.environ.get(key_var, "")
    if not (url and key):
        raise BackupError(f"Set {url_var} and {key_var} for the holdings files, "
                          "or pass --skip-files.")
    return SupabaseStorage(url, key)


def _env(var: str) -> str:
    value = os.environ.get(var, "")
    if not value:
        raise BackupError(f"Set {var} first.")
    return value


def _print_report(action: str, report: Report) -> int:
    rows = sum(report.tables.values())
    print(f"{action}: {len(report.tables)} tables, {rows} rows, {report.files} holdings files.")
    for note in report.notes:
        print(f"  note: {note}")
    for problem in report.problems:
        print(f"  PROBLEM: {problem}", file=sys.stderr)
    print("OK" if report.ok else "FAILED", file=sys.stdout if report.ok else sys.stderr)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m advisor_engine.api.backup",
                                     description="Back up the Supabase data, restore it, verify a restore.")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="write a backup folder (SUPABASE_DB_URL, SUPABASE_URL, SUPABASE_SECRET_KEY)")
    c.add_argument("out_dir", type=Path)
    c.add_argument("--skip-files", action="store_true", help="database only, no holdings files")
    for name, text in (("restore", "restore into RESTORE_DB_URL (migrations applied, no data)"),
                       ("verify", "check RESTORE_DB_URL holds exactly the backup")):
        p = sub.add_parser(name, help=text)
        p.add_argument("backup_dir", type=Path)
        p.add_argument("--skip-files", action="store_true", help="leave the holdings files out")
        p.add_argument("--auth-partial", action="store_true",
                       help="target is a plain-Postgres stand-in without Supabase's full auth "
                            "schema (restore drills only)")
        if name == "restore":
            p.add_argument("--files-only", action="store_true",
                           help="only upload the holdings files (finish an interrupted restore)")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            db_url = _env("SUPABASE_DB_URL")
            storage = None if args.skip_files else _storage("SUPABASE_URL", "SUPABASE_SECRET_KEY")
            m = create_backup(db_url, args.out_dir, storage)
            rows = sum(t["rows"] for t in m["tables"])
            print(f"Backed up {m['source']} at {m['created_at']}: {len(m['tables'])} tables, "
                  f"{rows} rows, {len(m['files'])} holdings files -> {args.out_dir}")
            return 0
        files_only = getattr(args, "files_only", False)
        if files_only and args.skip_files:
            raise BackupError("--files-only and --skip-files together would do nothing.")
        db_url = "" if files_only else _env("RESTORE_DB_URL")
        storage = None if args.skip_files else _storage("RESTORE_SUPABASE_URL",
                                                        "RESTORE_SUPABASE_SECRET_KEY")
        if args.command == "restore":
            report = restore_backup(args.backup_dir, db_url, storage, files_only=files_only,
                                    auth_partial=args.auth_partial)
            where = "storage" if files_only else _host(db_url)
            return _print_report(f"Restored into {where}", report)
        report = verify_restore(args.backup_dir, db_url, storage, auth_partial=args.auth_partial)
        return _print_report(f"Verified {_host(db_url)}", report)
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
