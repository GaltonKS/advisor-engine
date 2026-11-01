# Supabase setup

Project: `znkwkvcnxtahwinrxbvn` (https://znkwkvcnxtahwinrxbvn.supabase.co), Pro, East US
(North Virginia). It uses JWT signing keys, so the API verifies tokens through the
project's JWKS and needs no JWT secret.

## What the migrations create

`migrations/20261031000000_firm_scoped_schema.sql`:

- Tables: `firms`, `advisers`, `clients`, `portfolios`, `conversations`, `drafts`,
  `audit_events`, `usage`.
- Row-level security on every table, keyed on the caller's firm
  (`app.current_firm_id()`). The API runs each request as the signed-in adviser, so
  these rules apply to the API's own queries too.
- `audit_events` is append-only: advisers can insert and read their firm's rows, and
  nobody but the database owner can update or delete them.
- `app.ensure_adviser()` (first sign-in creates a firm) and `app.bump_turns()` (daily
  assistant limit).
- The private Storage bucket `holdings` (2 MB limit, CSV only).

The tests apply the same file to a plain Postgres (`tests/supabase_shim.sql` stands in
for Supabase's `auth` schema), so what runs in CI is what runs here.

## Apply the migrations

Each file in `migrations/` runs once per project, in name order. A second run of the
same file fails with `42P07 relation ... already exists`; that only means it was already
applied.

| File | What it adds |
|---|---|
| `20261031000000_firm_scoped_schema.sql` | Tables, row-level security, audit log, `holdings` bucket |
| `20261101000000_questionnaire_and_versions.sql` | `clients.questionnaire`, `portfolios.label` / `source` |
| `20261102000000_client_soft_delete.sql` | `clients.deleted_at` / `deleted_by` (deleting a client hides it; nothing is removed) |
| `20261103000000_market_price_cache.sql` | `market.prices` / `market.fetches`: shared month-end price cache, private to the API |
| `20261104000000_adviser_approval.sql` | New sign-ups start `pending`; `app.submit_registration` (name, CRD, firm) and operator-only `app.approve_adviser` |
| `20261105000000_adviser_reviews.sql` | `public.adviser_reviews` (append-only approval audit trail), `app.review_adviser`, `rejected` status; `approve_adviser` now records a review |

Apply a new migration **before** merging the code that uses it: Render redeploys the API
from `main`, and the new API reads the new columns.

Either option works.

**SQL Editor (no install):** in the Supabase dashboard, open **SQL Editor**, paste the
whole migration file, and click **Run**. It should finish without errors. Repeat for each
file you haven't applied yet.

**Supabase CLI:**

```bash
npx supabase login
```

```bash
npx supabase link --project-ref znkwkvcnxtahwinrxbvn
```

```bash
npx supabase db push
```

Check it worked: **Table Editor** lists the tables above, each marked "RLS enabled", and
**Storage** shows a private `holdings` bucket.

## Settings the API reads

Set these on Render (or in `.env` for a local run against the project). Never commit them.

| Variable | Where to find it | Secret |
|---|---|---|
| `SUPABASE_URL` | `https://znkwkvcnxtahwinrxbvn.supabase.co` | no |
| `SUPABASE_DB_URL` | **Connect → Transaction pooler** (port 6543), with the database password filled in | yes |
| `SUPABASE_SECRET_KEY` | **Project Settings → API Keys → Secret keys** (`sb_secret_…`); used only for the `holdings` bucket | yes |
| `ALLOWED_ORIGINS` | the React app's address, e.g. `http://localhost:5173` for now | no |
| `ADVISOR_OPERATORS` | your own sign-in email(s), comma-separated: they see the **Pilot metrics** page (usage across every firm, and the approval queue) | no |

With `SUPABASE_URL` set, sign-in is real and the assistant may go live
(`ANTHROPIC_API_KEY`); with `SUPABASE_DB_URL` set, data lives in Supabase.


## Approving advisers

**Preferred:** open **Pilot metrics** in the app (operators only, see `ADVISOR_OPERATORS`),
click **Review** on a pending sign-up, tick the BrokerCheck checks and approve, or pick a
reason and reject. Your email, the time, the checks and a summary go into the approval
audit trail (`public.adviser_reviews`), which can't be edited or deleted. The SQL below
still works, but is recorded as `sql:<database user>` with no checks.

New sign-ups wait as `pending`: they can sign in, but see only a registration form
(name, CRD number, firm name) until you approve them. Existing advisers are not affected.

1. List who is waiting, in the SQL Editor:

   ```sql
   select a.email, a.full_name, a.crd, f.requested_name as firm, a.registration_submitted_at
     from public.advisers a join public.firms f on f.id = a.firm_id
    where a.status = 'pending' order by a.registration_submitted_at nulls last;
   ```

2. Look the CRD number up on FINRA BrokerCheck (https://brokercheck.finra.org) and check
   the name and firm match.
3. Approve, either in their own new firm or as a member of an existing one:

   ```sql
   select * from app.approve_adviser('name@firm.com');
   ```

   ```sql
   select * from app.approve_adviser('name@firm.com', '<existing firm id>');
   ```

The same from a terminal with `SUPABASE_DB_URL` set: `python -m advisor_engine.api.admin
pending` and `python -m advisor_engine.api.admin approve name@firm.com [--firm <id>]`.
