"""Supabase persistence: Postgres for records, Storage for holdings CSVs.

Each call runs in its own transaction as the `authenticated` role with the caller's
claims in `request.jwt.claims`, the same way Supabase's own REST layer does. Row-level
security in supabase/migrations/ therefore applies to the API's queries: even a bug
that passed the wrong `firm_id` here could not read or write another firm's rows.
The `firm_id` filters below are kept anyway, so both stores behave the same.

CSVs are written to the private `holdings` bucket with the project's secret key, under
`<firm_id>/<portfolio_id>.csv`, and cached locally under `<cache>/firms/<firm_id>/` so
the engine tool's path guard confines the model to that firm, as with LocalStore. A
cached file is only used if its SHA-256 matches the database row.

Connections are opened per call. That is fine at prototype traffic; add a pool
(psycopg_pool) before it isn't. Use the transaction pooler URL, so prepared
statements are disabled.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..assistant.audit import build_record

BUCKET = "holdings"
# Portfolio columns the API returns (storage_path stays server-side).
_PORTFOLIO_COLS = "id, firm_id, client_id, filename, sha256, uploaded_by, created_at, label, source"


def _uuid(value: str) -> str | None:
    """`value` as a UUID string, or None for anything that isn't one (unknown id → 404)."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _plain(row: dict | None) -> dict | None:
    """Database row → JSON-ready dict, matching what LocalStore returns."""
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    return out


class SupabaseStorage:
    """The few Storage REST calls the API needs, authorised with the secret key."""

    def __init__(self, supabase_url: str, secret_key: str, *, client: httpx.Client | None = None):
        self.base = f"{supabase_url.rstrip('/')}/storage/v1/object/{BUCKET}"
        headers = {"apikey": secret_key}
        if not secret_key.startswith("sb_"):  # legacy service_role keys are JWTs
            headers["Authorization"] = f"Bearer {secret_key}"
        self.http = client or httpx.Client(timeout=30)
        self.headers = headers

    def put(self, path: str, data: bytes) -> None:
        r = self.http.post(f"{self.base}/{path}", content=data,
                           headers={**self.headers, "Content-Type": "text/csv"})
        r.raise_for_status()

    def get(self, path: str) -> bytes:
        r = self.http.get(f"{self.base}/{path}", headers=self.headers)
        r.raise_for_status()
        return r.content


class MemoryStorage:
    """In-process stand-in for SupabaseStorage, for tests."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, path: str, data: bytes) -> None:
        if path in self.objects:
            raise FileExistsError(path)
        self.objects[path] = data

    def get(self, path: str) -> bytes:
        return self.objects[path]


class SupabaseStore:
    def __init__(self, db_url: str, storage, cache_dir: Path):
        self.db_url = db_url
        self.storage = storage
        self.cache_dir = Path(cache_dir)

    def as_user(self, user_id: str, email: str) -> "SupabaseSession":
        return SupabaseSession(self, user_id, email)


class SupabaseSession:
    def __init__(self, store: SupabaseStore, user_id: str, email: str):
        self.store = store
        self.claims = json.dumps({"sub": user_id, "email": email, "role": "authenticated"})

    def _run(self, sql: str, params=(), *, one: bool = False):
        with psycopg.connect(self.store.db_url, prepare_threshold=None, row_factory=dict_row) as conn:
            with conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute("select set_config('request.jwt.claims', %s, true)", [self.claims])
                cur = conn.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
        rows = [_plain(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    # --- advisers and firms ----------------------------------------------------------

    def get_or_create_adviser(self, user_id: str, email: str) -> dict:
        return self._run("select * from app.ensure_adviser()", one=True)

    def submit_registration(self, full_name: str, crd: str, firm_name: str, *,
                            user_id: str) -> dict:
        """The caller's own registration (app.submit_registration checks it is pending)."""
        try:
            return self._run("select * from app.submit_registration(%s, %s, %s)",
                             [full_name, crd, firm_name], one=True)
        except psycopg.errors.RaiseException as e:
            raise PermissionError(str(e).splitlines()[0]) from None

    def get_firm(self, firm_id: str) -> dict:
        return self._run("select * from public.firms where id = %s", [firm_id], one=True) or {
            "id": firm_id}

    def bump_turns(self, adviser_id: str, limit: int) -> bool:
        return self._run("select app.bump_turns(%s) as ok", [limit], one=True)["ok"]

    # --- clients ---------------------------------------------------------------------

    def create_client(self, firm_id: str, adviser_id: str, fields: dict) -> dict:
        return self._run(
            "insert into public.clients (firm_id, created_by, name, objective, tone) "
            "values (%s, %s, %s, %s, %s) returning *",
            [firm_id, adviser_id, fields["name"], fields.get("objective", ""),
             fields.get("tone", "")], one=True)

    def list_clients(self, firm_id: str) -> list[dict]:
        return self._run("select * from public.clients where firm_id = %s and deleted_at is null "
                         "order by created_at", [firm_id])

    def get_client(self, firm_id: str, client_id: str) -> dict | None:
        if not (cid := _uuid(client_id)):
            return None
        return self._run("select * from public.clients where firm_id = %s and id = %s "
                         "and deleted_at is null", [firm_id, cid], one=True)

    def rename_client(self, firm_id: str, client_id: str, name: str) -> dict:
        return self._run("update public.clients set name = %s where firm_id = %s and id = %s "
                         "and deleted_at is null returning *", [name, firm_id, client_id], one=True)

    def delete_client(self, firm_id: str, client_id: str, adviser_id: str) -> None:
        """Soft delete: portfolios, drafts and audit rows stay on record (see migration)."""
        self._run("update public.clients set deleted_at = now(), deleted_by = %s "
                  "where firm_id = %s and id = %s and deleted_at is null",
                  [adviser_id, firm_id, client_id])

    def save_questionnaire(self, firm_id: str, client_id: str, answers: dict) -> dict:
        return self._run(
            "update public.clients set questionnaire = %s, questionnaire_updated_at = now() "
            "where firm_id = %s and id = %s returning *",
            [Jsonb(answers), firm_id, client_id], one=True)

    # --- portfolios ------------------------------------------------------------------

    def firm_dir(self, firm_id: str) -> Path:
        return self.store.cache_dir / "firms" / firm_id

    def add_portfolio(self, firm_id: str, client_id: str, adviser_id: str, filename: str,
                      csv_bytes: bytes, sha256: str, label: str = "", source: str = "upload") -> dict:
        pid = str(uuid.uuid4())
        path = f"{firm_id}/{pid}.csv"
        self.store.storage.put(path, csv_bytes)
        return self._run(
            "insert into public.portfolios (id, firm_id, client_id, filename, sha256, storage_path,"
            " uploaded_by, label, source) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"returning {_PORTFOLIO_COLS}",
            [pid, firm_id, client_id, filename, sha256, path, adviser_id, label, source], one=True)

    def list_portfolios(self, firm_id: str, client_id: str) -> list[dict]:
        if not (cid := _uuid(client_id)):
            return []
        return self._run(
            f"select {_PORTFOLIO_COLS} from public.portfolios "
            "where firm_id = %s and client_id = %s order by created_at",
            [firm_id, cid])

    def get_portfolio(self, firm_id: str, portfolio_id: str) -> dict | None:
        if not (pid := _uuid(portfolio_id)):
            return None
        return self._run("select * from public.portfolios where firm_id = %s and id = %s",
                         [firm_id, pid], one=True)

    def holdings_file(self, firm_id: str, portfolio_id: str) -> Path:
        """The portfolio's CSV in the local cache, downloaded and hash-checked if needed."""
        row = self.get_portfolio(firm_id, portfolio_id)
        if row is None:
            raise FileNotFoundError(portfolio_id)
        target = self.firm_dir(firm_id) / "portfolios" / f"{row['id']}.csv"
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]:
            return target
        data = self.store.storage.get(row["storage_path"])
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Stored holdings file does not match its recorded hash.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    # --- conversations and drafts ----------------------------------------------------

    def get_conversation(self, firm_id: str, portfolio_id: str) -> dict:
        row = self._run("select history, messages from public.conversations "
                        "where firm_id = %s and portfolio_id = %s",
                        [firm_id, portfolio_id], one=True)
        return row or {"history": [], "messages": []}

    def save_conversation(self, firm_id: str, portfolio_id: str, conversation: dict) -> None:
        self._run(
            "insert into public.conversations (portfolio_id, firm_id, history, messages) "
            "values (%s, %s, %s, %s) on conflict (portfolio_id) do update "
            "set history = excluded.history, messages = excluded.messages, updated_at = now()",
            [portfolio_id, firm_id, Jsonb(conversation["history"]),
             Jsonb(conversation["messages"])])

    def create_draft(self, firm_id: str, fields: dict) -> dict:
        return self._run(
            "insert into public.drafts (firm_id, portfolio_id, created_by, original, tool_calls) "
            "values (%s, %s, %s, %s, %s) returning *",
            [firm_id, fields["portfolio_id"], fields["created_by"], fields["original"],
             Jsonb(fields["tool_calls"])], one=True)

    def get_draft(self, firm_id: str, draft_id: str) -> dict | None:
        if not (did := _uuid(draft_id)):
            return None
        return self._run("select * from public.drafts where firm_id = %s and id = %s",
                         [firm_id, did], one=True)

    def list_drafts(self, firm_id: str, portfolio_id: str) -> list[dict]:
        return self._run("select * from public.drafts where firm_id = %s and portfolio_id = %s "
                         "order by created_at desc", [firm_id, portfolio_id])

    def save_draft(self, firm_id: str, draft: dict) -> None:
        self._run(
            "update public.drafts set approved = %s, approved_text = %s, approved_by = %s, "
            "approved_at = %s where firm_id = %s and id = %s",
            [draft["approved"], draft.get("approved_text"), draft.get("approved_by"),
             draft.get("approved_at"), firm_id, draft["id"]])

    # --- audit -----------------------------------------------------------------------

    def write_audit(self, firm_id: str, record: dict) -> dict:
        record = build_record(record)
        self._run(
            "insert into public.audit_events (firm_id, portfolio_id, event, record) "
            "values (%s, %s, %s, %s)",
            [firm_id, _uuid(record.get("portfolio_id") or ""), record.get("event"),
             Jsonb(record)])
        return record

    def read_audit(self, firm_id: str) -> list[dict]:
        rows = self._run("select record from public.audit_events where firm_id = %s order by id",
                         [firm_id])
        return [r["record"] for r in rows]
