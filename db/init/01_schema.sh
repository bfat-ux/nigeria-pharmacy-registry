#!/usr/bin/env bash
# Initialise the NPR database schema.
# Runs automatically on first `docker compose up` via docker-entrypoint-initdb.d.
#
# Order matters:
#   1. 004 first — creates PostGIS & pg_trgm extensions (001 depends on them)
#   2. 001 — core tables & enums
#   3. 002 — status history (references core tables)
#   4. 003 — provenance / audit trail (references core tables)
#   Then re-run 004 for spatial indexes & functions that reference core tables.

set -euo pipefail

SQL_DIR="/sql"

echo ">>> Enabling extensions (PostGIS, pg_trgm)..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-SQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL

echo ">>> 001 — Core schema..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$SQL_DIR/001_core_schema.sql"

echo ">>> 002 — Status history..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$SQL_DIR/002_status_history.sql"

echo ">>> 003 — Provenance & audit..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$SQL_DIR/003_provenance_audit.sql"

echo ">>> 004 — Geospatial indexes & functions..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$SQL_DIR/004_geospatial.sql"

echo ">>> Schema initialisation complete."
