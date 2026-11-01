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
from .auth_admin import secret_key_headers

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
        self.http = client or httpx.Client(timeout=30)
        self.headers = secret_key_headers(secret_key)

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

    def as_user(self, user_id: str, email: str, aal: str = "aal1") -> "SupabaseSession":
        return SupabaseSession(self, user_id, email, aal)

    def mfa_mode(self) -> str:
        """The database's multi-factor mode (app.security_settings), read as the owner."""
        with psycopg.connect(self.db_url, prepare_threshold=None) as conn:
            return conn.execute("select mfa_mode from app.security_settings").fetchone()[0]

    def pilot_metrics(self) -> dict:
        """Usage across every firm, for the operator (see api/metrics.py).

        Runs on the owner connection, not as an adviser: row-level security would limit it
        to one firm. Only routes gated on Settings.is_operator may call this.
        """
        from .metrics import ANALYSIS_EVENTS, FEATURE_DAYS, WEEKS

        def q(conn, sql, params=()):
            return [_plain(r) for r in conn.execute(sql, params).fetchall()]

        with psycopg.connect(self.db_url, prepare_threshold=None, row_factory=dict_row) as conn:
            weekly = q(conn, """
                select w.week::date as week, count(distinct e.record->>'adviser_id') as advisers
                  from generate_series(date_trunc('week', now()) - make_interval(weeks => %s - 1),
                                       date_trunc('week', now()), interval '1 week') as w(week)
                  left join public.audit_events e
                    on e.recorded_at >= w.week and e.recorded_at < w.week + interval '1 week'
                   and e.record->>'adviser_id' is not null
                 group by w.week order by w.week""", [WEEKS])
            features = q(conn, """
                select event, count(*) as uses, count(distinct record->>'adviser_id') as advisers
                  from public.audit_events
                 where recorded_at > now() - make_interval(days => %s) and event is not null
                 group by event order by uses desc, event""", [FEATURE_DAYS])
            funnel = q(conn, """
                select count(*) as signed_up,
                       count(a.registration_submitted_at) as registered,
                       count(*) filter (where a.status = 'active') as approved,
                       count(*) filter (where exists (select 1 from public.clients c
                                                      where c.created_by = a.id)) as added_client,
                       count(*) filter (where exists (
                           select 1 from public.audit_events e
                            where e.record->>'adviser_id' = a.id::text
                              and e.event = any(%s))) as ran_analysis,
                       extract(epoch from avg(a.approved_at - a.registration_submitted_at))
                           / 3600 as avg_approval_hours
                  from public.advisers a""", [list(ANALYSIS_EVENTS)])[0]
            totals = q(conn, """
                select (select count(distinct firm_id) from public.advisers
                         where status = 'active') as firms,
                       (select count(*) from public.advisers where status = 'active') as active_advisers,
                       (select count(*) from public.advisers where status = 'pending') as pending_advisers,
                       (select count(*) from public.clients where deleted_at is null) as clients,
                       (select count(*) from public.audit_events where event = 'turn'
                         and recorded_at > now() - make_interval(days => %s)) as questions""",
                       [FEATURE_DAYS])[0]
            pending = q(conn, """
                select a.email, a.full_name, a.crd, f.requested_name as firm_name,
                       a.registration_submitted_at as submitted_at, a.created_at as signed_up_at
                  from public.advisers a join public.firms f on f.id = a.firm_id
                 where a.status = 'pending'
                 order by a.registration_submitted_at nulls last, a.created_at""")
            reviews = q(conn, """
                select reviewed_at, adviser_email, adviser_name, crd, decision, reviewed_by,
                       checks, reason, summary, note
                  from public.adviser_reviews order by reviewed_at desc, id desc limit 100""")
        hours = funnel.pop("avg_approval_hours")
        return {"weeks": WEEKS, "feature_days": FEATURE_DAYS, "totals": totals,
                "weekly_active": weekly, "features": features, "funnel": funnel,
                "avg_approval_hours": float(hours) if hours is not None else None,
                "pending": pending, "reviews": reviews}

    def review_adviser(self, email: str, decision: str, reviewed_by: str, checks: list[str],
                       reason: str | None, summary: str, note: str | None) -> dict:
        """Approve or reject a pending adviser and record it (app.review_adviser), on the
        owner connection: advisers can't run it. LookupError if nobody is pending."""
        try:
            with psycopg.connect(self.db_url, prepare_threshold=None, row_factory=dict_row) as conn:
                row = conn.execute(
                    "select * from app.review_adviser(%s, %s, %s, %s, %s, %s, %s)",
                    [email, decision, reviewed_by, checks, reason, summary, note]).fetchone()
        except psycopg.errors.RaiseException as e:
            raise LookupError(str(e).splitlines()[0]) from None
        return _plain(row)


class SupabaseSession:
    def __init__(self, store: SupabaseStore, user_id: str, email: str, aal: str = "aal1"):
        self.store = store
        # `aal` is the level the API verified on the caller's token, so the database's
        # multi-factor policies see the same thing the API did.
        self.claims = json.dumps({"sub": user_id, "email": email, "role": "authenticated",
                                  "aal": aal})

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

    def list_firm_advisers(self, firm_id: str) -> list[dict]:
        return self._run("select id, email, full_name, role, status, created_at "
                         "from public.advisers where firm_id = %s order by created_at", [firm_id])

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
