#!/bin/bash
# ==============================================================================
# Database Restore Script for NomadX (Production Readiness)
# ==============================================================================
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <path_to_backup_file.sql.gz>" >&2
  exit 1
fi

BACKUP_FILE=$1

if [ ! -f "$BACKUP_FILE" ]; then
  echo "[Restore] Error: Backup file not found at ${BACKUP_FILE}" >&2
  exit 1
fi

if [ -z "${DB_URL:-}" ]; then
  echo "[Restore] Error: DB_URL environment variable is not set." >&2
  exit 1
fi

# Parse connection string
CLEAN_CONN_STR=$(echo "$DB_URL" | sed -e 's/postgresql+asyncpg:\/\///' -e 's/postgresql:\/\///')
DB_USER=$(echo "$CLEAN_CONN_STR" | cut -d':' -f1)
DB_PASS=$(echo "$CLEAN_CONN_STR" | cut -d'@' -f1 | cut -d':' -f2-)
DB_HOST_PORT_DB=$(echo "$CLEAN_CONN_STR" | cut -d'@' -f2)
DB_HOST=$(echo "$DB_HOST_PORT_DB" | cut -d'/' -f1 | cut -d':' -f1)
DB_PORT=$(echo "$DB_HOST_PORT_DB" | cut -d'/' -f1 | cut -d':' -f2 -s)
DB_NAME=$(echo "$DB_HOST_PORT_DB" | cut -d'/' -f2)

if [ -z "$DB_PORT" ]; then
  DB_PORT=5432
fi

export PGPASSWORD="$DB_PASS"

# Prompt if not forced
if [ "${FORCE:-0}" -ne 1 ]; then
  echo "[Restore] WARNING: This will DROP and recreate the schema on database '${DB_NAME}' at ${DB_HOST}:${DB_PORT}."
  read -p "Are you sure you want to proceed? (y/N) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "[Restore] Restore execution cancelled."
      exit 0
  fi
fi

echo "[Restore] Cleaning target database schema (dropping public schema)..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public; GRANT ALL ON SCHEMA public TO pg_database_owner;"

echo "[Restore] Restoring database structure and records from ${BACKUP_FILE}..."
gunzip -c "$BACKUP_FILE" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"

echo "[Restore] Database restore completed successfully."
