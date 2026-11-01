# Backups and restore

How GaltonKS data is backed up, how to restore it, and how each restore is tested.
Code: `src/advisor_engine/api/backup.py`, `scripts/restore_drill.sh`,
`.github/workflows/backup.yml`. Tests: `tests/test_backup.py`.

## What is backed up, where

| Layer | What it holds | How often, how long | Restore-tested |
|---|---|---|---|
| **Supabase daily backups** (Pro plan) | The whole database, including Supabase Auth. **Not** the files in Storage: only their metadata. | Daily, kept 7 days | When the quarterly drill uses it (see below) |
| **Our nightly backup** (this repo) | App tables, the auth tables we depend on, and every holdings file. Encrypted with our own key. | Daily at 07:41 UTC, kept 90 days as a GitHub Actions artifact | Every night, automatically: the run fails if the backup doesn't restore exactly |

Two layers on purpose. Supabase's backup is the fastest way back for the database, but
it leaves out our holdings CSVs, which live in the `holdings` Storage bucket, and it sits
in the same account as the data it protects. Ours includes the files, is encrypted with
a key only we hold, and can be restored into any Postgres or a new Supabase project.

**Targets:** at most 24 hours of data lost (both layers are daily). Back up and running
within 4 hours for a restore in place, or within one working day into a new project.
Point-in-Time Recovery (a Supabase add-on, paid extra) would cut the data-loss window to
minutes. Decide on it before real client data arrives.

### Our backup in detail

`python -m advisor_engine.api.backup create OUT_DIR` writes a folder:

- `tables/<schema>.<table>.copy`: every table in `public` and `app`, plus `auth.users`,
  `auth.identities` and `auth.mfa_factors`, in PostgreSQL COPY format, all read in one
  consistent snapshot. **New tables in `public` / `app` are picked up automatically.**
- `files/<firm id>/<portfolio id>.csv`: every holdings file a portfolio points to. Each
  is checked against the sha256 recorded on its portfolio row, and the backup fails if
  any file is missing or changed.
- `manifest.json`: time of the snapshot, server and engine version, the migrations in the
  repo, and each table's columns, row count and sha256. No connection strings or keys.

Left out on purpose:

- `market.*`: licensed vendor prices. It is a cache that refills on demand, and the
  licence keeps it inside the API's database.
- Supabase's own `storage` schema. It is rebuilt when the files are uploaded again.
- Project settings: Auth settings, redirect URLs, signing keys, API keys, bucket
  policies. These are in the dashboard, not the database. The setup docs list them:
  `supabase/README.md` and `web/README.md`.

The schema is not in the backup, because it lives in `supabase/migrations/`. A restore
goes into a database with every migration applied and no data. The tool refuses a target
that already holds data, or whose tables or columns don't match the backup, so it can
never overwrite production. Tables load in a single transaction: all of them, or none.

The archive holds client holdings, conversations and password hashes, so it is
sensitive. The nightly job encrypts it with [age](https://age-encryption.org) before it
leaves the runner, and refuses to run if no key is set. GitHub only ever sees the
**public** key, so a leaked GitHub secret can't decrypt a backup.

## One-time setup

1. **Make the encryption key** on a trusted computer, not a server:

   ```bash
   age-keygen -o galtonks-backup-key.txt
   ```

   The file holds the private key (`AGE-SECRET-KEY-…`). Its `# public key: age1…` line is
   the public key. Keep the private key in the company password manager and in one
   offline copy (printed, in a safe). Without it no backup can be read. Delete the file
   from the computer afterwards.

2. **Create a `backup` environment** in GitHub: **Settings → Environments → New
   environment**. Under **Deployment branches and tags**, choose **Selected branches** and
   add `main`. Then add:

   | Kind | Name | Value |
   |---|---|---|
   | Secret | `SUPABASE_DB_URL` | Same as on Render (Connect → Transaction pooler, with the password) |
   | Secret | `SUPABASE_URL` | `https://znkwkvcnxtahwinrxbvn.supabase.co` |
   | Secret | `SUPABASE_SECRET_KEY` | Same as on Render (`sb_secret_…`) |
   | Variable | `BACKUP_AGE_RECIPIENT` | The `age1…` public key from step 1 |

3. **Run it once by hand:** **Actions → Nightly backup → Run workflow**. Check that every
   step is green and that the run lists a `galtonks-backup-<time>` artifact. The "Restore
   into a scratch database" step prints the table and row counts.

4. **Failure alerts:** GitHub emails the person who last changed the workflow's schedule
   when a scheduled run fails. Make sure that is someone who reads it, or add a
   notification rule under your GitHub notification settings for failed Actions runs.
   Look at the Actions tab once a week anyway.

## Restore runbooks

**First, in every case: take a backup of the current state** (run the workflow by hand,
or `backup create` locally). A restore rolls data back. That backup keeps whatever was
written since, including audit records, which must never be lost.

### Which one?

| What happened | Use |
|---|---|
| Whole database damaged or lost, project still fine | A. Supabase restore in place |
| Project, account or region gone, or Supabase backups unusable | B. Our backup into a new project |
| Holdings files lost or damaged in Storage | C. Files only, from our backup |
| A few rows wrong or deleted (one firm, one client) | D. Look up and repair by hand |

### A. Supabase restore in place

1. Dashboard → **Database → Backups** → choose the day → **Restore**. The project is
   offline while it runs. Everything written after that backup is gone, except what the
   backup you just took holds.
2. Storage is **not** rolled back. Holdings files are only ever added, never overwritten,
   so files newer than the restored database are simply unused. Nothing to do.
3. Check: sign in to the web app, open a client, and load a portfolio (that checks the
   file against its hash). On Render, check the API's logs for errors.
4. Missing audit records written after the backup point can be re-inserted from your
   pre-restore backup (runbook D). Record the incident and the gap.

### B. Our backup into a new Supabase project

1. **Get the backup:** **Actions → Nightly backup →** the latest green run **→**
   download the artifact. Unzip it and check the hash, then decrypt with the private key:

   ```bash
   sha256sum -c galtonks-backup-*.sha256
   ```

   ```bash
   age -d -i galtonks-backup-key.txt galtonks-backup-*.tar.gz.age | tar -xzf -
   ```

2. **New project:** create it (Pro, same region), then apply every migration in order
   (`supabase/README.md` → "Apply the migrations"). Set up Auth as before: email
   confirmation on, site URL and redirect URLs pointing at the web app.

3. **Restore** from a computer with this repo installed (`pip install -e ".[api]"`). Use
   the new project's **Session pooler** or direct connection string:

   ```bash
   export RESTORE_DB_URL='postgresql://postgres.<new ref>:<password>@<host>:5432/postgres'
   export RESTORE_SUPABASE_URL='https://<new ref>.supabase.co'
   export RESTORE_SUPABASE_SECRET_KEY='sb_secret_…'
   ```

   ```bash
   python -m advisor_engine.api.backup restore galtonks-backup-<time>
   ```

   ```bash
   python -m advisor_engine.api.backup verify galtonks-backup-<time>
   ```

   Both must end with `OK`. If the uploads stop partway (network), run `restore
   ... --files-only` to finish them.

   If the restore reports that `auth.*` tables or columns are missing, Supabase's auth
   schema has changed shape since the backup. **Don't** use `--auth-partial` here: it
   would leave advisers unable to sign in. Advisers can still be recovered: restore with
   `--auth-partial`, then have each adviser sign up again with the same email. Before
   they do, `public.advisers.id` must be moved to the new user id. Do that with Supabase
   support, or as a planned SQL change.

4. **Point the apps at it:** on Render, update `SUPABASE_URL`, `SUPABASE_DB_URL` and
   `SUPABASE_SECRET_KEY` for the API, and `VITE_SUPABASE_URL` and
   `VITE_SUPABASE_PUBLISHABLE_KEY` for the web app, then redeploy both. Update the
   `backup` environment's secrets too. The new project has new signing keys, so everyone
   is signed out once. Passwords still work because they were restored. Advisers set up
   MFA again only if `auth.mfa_factors` wasn't restored.

5. **Check** as in A.3, then run the nightly workflow by hand against the new project.

### C. Holdings files only

Upload the backup's files to the live project. The database is not touched:

```bash
RESTORE_SUPABASE_URL=<live url> RESTORE_SUPABASE_SECRET_KEY=<live key> \
  python -m advisor_engine.api.backup restore galtonks-backup-<time> --files-only
```

Files that are already there with the same content count as done. A file that is there
with *different* content is reported, not overwritten. Look into it before you change
anything.

### D. Look up and repair a few rows

Restore the backup into a local Postgres and query it. This never touches production:

```bash
createdb galtonks_lookup
psql -d galtonks_lookup -f tests/supabase_shim.sql
for f in supabase/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -d galtonks_lookup -f "$f"; done
RESTORE_DB_URL=postgresql:///galtonks_lookup python -m advisor_engine.api.backup restore galtonks-backup-<time> --skip-files --auth-partial
```

Find the rows, and write the repair as SQL for the production SQL Editor. Run it with a
second person checking it, and record what changed and why. Audit records are
append-only: re-insert missing ones, never edit existing ones. Drop the lookup database
afterwards (`dropdb galtonks_lookup`), since it holds client data.

## Restore drills

**Nightly (automatic).** Every backup is restored into a fresh scratch Postgres built from
`supabase/migrations/`, and every row is compared with the backup
(`scripts/restore_drill.sh`). The run fails if anything differs, if the live schema has
drifted from the migrations (a table or column the migrations don't create), or if any
holdings file is missing or fails its hash check. What it doesn't prove: restore into
Supabase's full auth schema, uploading the files, decrypting with the offline key, or
how long a real recovery takes. The quarterly drill covers those.

**Quarterly (by hand, about an hour).** Do runbook B against a **temporary** Supabase
project:

1. Download the latest artifact, check its sha256, and decrypt it with the key from the
   password manager. This proves the key is where we think it is.
2. Create a temporary project, apply the migrations, then `restore` **without**
   `--auth-partial` and with the files, then `verify`. Both must show `OK`.
3. Point a local API and web app at it (`.env`) and sign in as a test adviser. Open a
   client, load a portfolio, read the audit trail.
4. Note how long steps 1–3 took (this is the real recovery time). Delete the temporary
   project, then delete the downloaded and decrypted copies.
5. Add a row below. Every second drill, test runbook A instead, on a Supabase branch or
   a spare project, never production.

Also run a drill after any migration that adds a table or touches `auth`.

### Drill log

| Date | Who | What was tested | Result |
|---|---|---|---|
| 2026-11-01 | Claude Code (setup) | Local rehearsal on **synthetic data, not production**: `create` (with files, through the Storage REST client) → nightly drill script → age encrypt → decrypt → full restore with files into a fresh database → `verify`. 9 tables, 46 rows, 9 files. | OK. Not yet tested: a real Supabase project as the source or the target. The first nightly run and the first quarterly drill cover those. |

## Retention, deletion and keys

- Artifacts expire after 90 days, GitHub's limit for this setting.
- **Longer retention is an open decision.** Advisers' books-and-records rules
  (e.g. SEC Rule 204-2) can require some records for five years or more. Compliance
  should confirm which records GaltonKS holds on firms' behalf, and so whether we need a
  long-term copy. If so, the next step is to also copy the encrypted archive to object
  storage with a retention lock (e.g. S3 Object Lock). The workflow is built so that is
  one extra step.
- Deleting a client is a soft delete, so backups never hold less than the live database.
  A request to erase data that is genuinely gone has to wait until the backups holding
  it expire. The retention policy (see `docs/migration-plan.md`) should say so.
- **Rotating the key:** make a new key pair and change `BACKUP_AGE_RECIPIENT`. Keep the
  old private key until the last backup made with it has expired (90 days).
- **Never** commit a backup, a decrypted folder or the private key. `data/` is git-ignored
  if you work in there.
