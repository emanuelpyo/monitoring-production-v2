#!/usr/bin/env bash
# Idempotent local setup for the Barcode Monitor PostgreSQL database.
#
# It will:
#   1. check whether a PostgreSQL server is already reachable on 5433 (preferred)
#      or 5432 (fallback);
#   2. create the `production_monitoring` database if it does not exist;
#   3. never drop, truncate, or modify any existing database.
#
# Usage:  bash scripts/init_db.sh
#
# Environment overrides:
#   PG_BIN   path to postgresql bin dir (default: Homebrew postgresql@16)
#   PGDATA_BARCODE  data dir for the dedicated 5433 cluster
set -euo pipefail

PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
export PATH="$PG_BIN:$PATH"
# macOS PostgreSQL exits at startup if no valid UTF-8 locale is set.
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

PGDATA_BARCODE="${PGDATA_BARCODE:-$HOME/.local/share/barcode-pg}"
DB_NAME="production_monitoring"
DB_USER="barcode"

echo "==> Checking PostgreSQL binaries"
command -v psql >/dev/null || { echo "psql not found. Install postgresql@16 via Homebrew."; exit 1; }

if pg_isready -h localhost -p 5433 >/dev/null 2>&1; then
  PORT=5433
  echo "==> PostgreSQL already reachable on 5433"
elif pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  PORT=5432
  echo "==> PostgreSQL reachable on 5432 (existing Homebrew server)"
else
  echo "==> No PostgreSQL server on 5433/5432. Creating a dedicated cluster on 5433."
  if [ ! -d "$PGDATA_BARCODE" ]; then
    initdb -D "$PGDATA_BARCODE" -U "$DB_USER" --auth=trust -E UTF8 --locale=C
  fi
  pg_ctl -D "$PGDATA_BARCODE" -o "-p 5433" -l "$PGDATA_BARCODE/server.log" start
  sleep 2
  PORT=5433
fi

echo "==> Ensuring database '$DB_NAME' exists (user: $DB_USER)"
if psql -h localhost -p "$PORT" -U "$DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  echo "    database already exists - leaving it untouched"
else
  createdb -h localhost -p "$PORT" -U "$DB_USER" "$DB_NAME" 2>/dev/null \
    || createdb -h localhost -p "$PORT" "$DB_NAME"
  echo "    created '$DB_NAME'"
fi

echo
echo "Done. Use this DATABASE_URL in barcode-monitor/.env:"
if [ "$PORT" = "5433" ]; then
  echo "  DATABASE_URL=postgresql://$DB_USER@localhost:5433/$DB_NAME"
else
  echo "  DATABASE_URL=postgresql://$(whoami)@localhost:5432/$DB_NAME"
fi
