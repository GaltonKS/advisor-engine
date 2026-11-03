"""Operator commands: `python -m advisor_engine.api.admin ...`.

    pending                      list advisers awaiting approval, with their registration
    approve EMAIL                activate them, in the firm they signed up with
    approve EMAIL --firm FIRM_ID activate them as a member of an existing firm
    mfa-mode                     show the database's multi-factor mode
    mfa-mode MODE                set it: off | enrol | required
    reset-mfa EMAIL --requested-by WHO --verified-by WHO --check HOW
                                 remove every authenticator of an adviser who lost them all

Multi-factor mode lives in two places: the API's ADVISOR_REQUIRE_MFA and the database's
own setting (which row-level security uses). Switch the API first and the database last,
so the database is never stricter than the API.

Reset only after verifying the adviser's identity out of band (a call to the number the
firm has on file, their CRD on BrokerCheck, and the firm owner's confirmation for
multi-adviser firms); never on an in-app or email request alone. Every reset is written
to the firm's audit trail with who asked, who verified and how. Needs SUPABASE_URL and
SUPABASE_SECRET_KEY as well as SUPABASE_DB_URL.

Verify the CRD number on FINRA BrokerCheck (https://brokercheck.finra.org) before
approving. Uses SUPABASE_DB_URL, the API's own (owner) connection: advisers cannot run
app.approve_adviser themselves. The same can be done in the Supabase SQL Editor:

    select * from app.approve_adviser('name@firm.com');
    select * from app.approve_adviser('name@firm.com', '<firm id>');
"""

from __future__ import annotations

import argparse
import sys

from ..config import supabase_admin
from .settings import MFA_MODES

PENDING_SQL = """
    select a.email, a.full_name, a.crd, f.requested_name as firm_name,
           a.registration_submitted_at, a.created_at
      from public.advisers a join public.firms f on f.id = a.firm_id
     where a.status = 'pending'
     order by a.registration_submitted_at nulls last, a.created_at
"""


def _connect(db_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(db_url, prepare_threshold=None, row_factory=dict_row, autocommit=True)


def list_pending(db_url: str) -> list[dict]:
    with _connect(db_url) as conn:
        return conn.execute(PENDING_SQL).fetchall()


def approve(db_url: str, email: str, firm_id: str | None = None) -> dict:
    with _connect(db_url) as conn:
        return conn.execute("select * from app.approve_adviser(%s, %s)",
                            [email, firm_id]).fetchone()


def get_mfa_mode(db_url: str) -> str:
    with _connect(db_url) as conn:
        return conn.execute("select mfa_mode from app.security_settings").fetchone()["mfa_mode"]


def set_mfa_mode(db_url: str, mode: str) -> str:
    if mode not in MFA_MODES:
        raise ValueError(f"mode must be one of {', '.join(MFA_MODES)}")
    with _connect(db_url) as conn:
        return conn.execute("select app.set_mfa_mode(%s) as mode", [mode]).fetchone()["mode"]


def reset_mfa(db_url: str, auth_admin, email: str, *, requested_by: str, verified_by: str,
              check: str) -> dict:
    """Remove all of an adviser's authenticators and audit it in their firm's trail."""
    from ..assistant.audit import build_record
    from psycopg.types.json import Jsonb

    with _connect(db_url) as conn:
        adv = conn.execute("select id, email, firm_id from public.advisers "
                           "where lower(email) = lower(%s)", [email]).fetchone()
        if adv is None:
            raise LookupError(f"no adviser with email {email}")
        uid = str(adv["id"])
        removed = []
        try:
            factors = auth_admin.list_factors(uid)
        except LookupError:
            factors = []
        for f in factors:
            # Supabase refuses to delete a verified factor without a permit (see
            # 20261107000000_mfa_direct_paths.sql); autocommit, so Supabase Auth sees it.
            conn.execute("select app.allow_factor_removal(%s, %s)", [uid, f["id"]])
            auth_admin.delete_factor(uid, f["id"])
            removed.append({"factor_id": f["id"], "friendly_name": f["friendly_name"]})
        record = build_record({"event": "mfa_reset_by_operator", "model": None,
                               "adviser_id": uid, "firm_id": str(adv["firm_id"]),
                               "inputs": {"requested_by": requested_by, "verified_by": verified_by,
                                          "identity_check": check, "removed": removed}})
        conn.execute("insert into public.audit_events (firm_id, event, record) values (%s, %s, %s)",
                     [adv["firm_id"], record["event"], Jsonb(record)])
        return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m advisor_engine.api.admin",
                                     description="Approve adviser sign-ups.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="list advisers awaiting approval")
    ap = sub.add_parser("approve", help="activate a pending adviser")
    ap.add_argument("email")
    ap.add_argument("--firm", help="add them to this existing firm (id) as a member")
    mp = sub.add_parser("mfa-mode", help="show or set the database's multi-factor mode")
    mp.add_argument("mode", nargs="?", choices=MFA_MODES)
    rp = sub.add_parser("reset-mfa", help="remove all of an adviser's authenticators")
    rp.add_argument("email")
    rp.add_argument("--requested-by", required=True, help="who asked for the reset")
    rp.add_argument("--verified-by", required=True, help="who checked their identity")
    rp.add_argument("--check", required=True, help="how identity was checked")
    args = parser.parse_args(argv)

    cfg = supabase_admin()
    db_url = cfg.db_url
    if not db_url:
        print("Set SUPABASE_DB_URL (the API's database connection) first.", file=sys.stderr)
        return 2
    if args.command == "pending":
        rows = list_pending(db_url)
        if not rows:
            print("No advisers are awaiting approval.")
        for r in rows:
            submitted = r["registration_submitted_at"] or "registration not yet submitted"
            print(f"{r['email']}  name={r['full_name'] or '-'}  CRD={r['crd'] or '-'}  "
                  f"firm={r['firm_name'] or '-'}  submitted={submitted}")
        return 0
    if args.command == "mfa-mode":
        if args.mode:
            set_mfa_mode(db_url, args.mode)
            print(f"Database multi-factor mode set to {args.mode}. The API's "
                  "ADVISOR_REQUIRE_MFA should already be at this mode: the database must "
                  "never be stricter than the API.")
        print(f"Database multi-factor mode: {get_mfa_mode(db_url)}")
        return 0
    if args.command == "reset-mfa":
        from .auth_admin import SupabaseAuthAdmin

        url, key = cfg.url, cfg.secret_key
        if not (url and key):
            print("Set SUPABASE_URL and SUPABASE_SECRET_KEY as well.", file=sys.stderr)
            return 2
        record = reset_mfa(db_url, SupabaseAuthAdmin(url, key), args.email,
                           requested_by=args.requested_by, verified_by=args.verified_by,
                           check=args.check)
        n = len(record["inputs"]["removed"])
        print(f"Removed {n} authenticator(s) for {args.email}; recorded in their firm's audit "
              "trail. They can sign in with their password and set up a new one.")
        return 0
    row = approve(db_url, args.email, args.firm)
    print(f"Approved {row['email']} (firm {row['firm_id']}, role {row['role']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
