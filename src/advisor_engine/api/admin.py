"""Operator commands for adviser approval: `python -m advisor_engine.api.admin ...`.

    pending                      list advisers awaiting approval, with their registration
    approve EMAIL                activate them, in the firm they signed up with
    approve EMAIL --firm FIRM_ID activate them as a member of an existing firm

Verify the CRD number on FINRA BrokerCheck (https://brokercheck.finra.org) before
approving. Uses SUPABASE_DB_URL, the API's own (owner) connection: advisers cannot run
app.approve_adviser themselves. The same can be done in the Supabase SQL Editor:

    select * from app.approve_adviser('name@firm.com');
    select * from app.approve_adviser('name@firm.com', '<firm id>');
"""

from __future__ import annotations

import argparse
import os
import sys

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m advisor_engine.api.admin",
                                     description="Approve adviser sign-ups.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="list advisers awaiting approval")
    ap = sub.add_parser("approve", help="activate a pending adviser")
    ap.add_argument("email")
    ap.add_argument("--firm", help="add them to this existing firm (id) as a member")
    args = parser.parse_args(argv)

    db_url = os.environ.get("SUPABASE_DB_URL", "")
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
    row = approve(db_url, args.email, args.firm)
    print(f"Approved {row['email']} (firm {row['firm_id']}, role {row['role']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
