#!/usr/bin/env bash
# Restore drill: load a backup folder into a brand-new scratch database built from
# supabase/migrations, then check every row matches the backup.
#
#   DRILL_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres \
#     scripts/restore_drill.sh BACKUP_DIR
#
# DRILL_ADMIN_URL is an admin connection to a throwaway Postgres (never production): the
# script creates a database there and drops it at the end. The scratch database is plain
# Postgres with tests/supabase_shim.sql standing in for Supabase's auth schema, so it runs
# with --auth-partial and without the holdings files (their hashes are already checked
# when the backup is made and again when it is loaded). A full restore into a Supabase
# project is the quarterly drill in docs/backups.md.
set -euo pipefail

backup_dir="${1:?usage: scripts/restore_drill.sh BACKUP_DIR}"
admin="${DRILL_ADMIN_URL:?set DRILL_ADMIN_URL to an admin connection on a throwaway Postgres}"
root="$(cd "$(dirname "$0")/.." && pwd)"
name="restore_drill_$(date -u +%Y%m%d%H%M%S)"
target="${admin%/*}/${name}"

psql -X -q -v ON_ERROR_STOP=1 "$admin" -c "create database \"$name\""
trap 'psql -X -q "$admin" -c "drop database if exists \"$name\" with (force)"' EXIT

psql -X -q -v ON_ERROR_STOP=1 "$target" -f "$root/tests/supabase_shim.sql"
for migration in "$root"/supabase/migrations/*.sql; do
    psql -X -q -v ON_ERROR_STOP=1 "$target" -f "$migration"
done

export RESTORE_DB_URL="$target"
python -m advisor_engine.api.backup restore "$backup_dir" --skip-files --auth-partial
python -m advisor_engine.api.backup verify "$backup_dir" --skip-files --auth-partial
