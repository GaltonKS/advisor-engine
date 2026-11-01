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

With `SUPABASE_URL` set, sign-in is real and the assistant may go live
(`ANTHROPIC_API_KEY`); with `SUPABASE_DB_URL` set, data lives in Supabase.
