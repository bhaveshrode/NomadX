#!/bin/bash
# ==============================================================================
# Database Backup Script for NomadX (Production Readiness)
# ==============================================================================
set -euo pipefail

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/nomadx_backup_${TIMESTAMP}.sql.gz"

echo "[Backup] Starting PostgreSQL database backup..."
mkdir -p "${BACKUP_DIR}"

if [ -z "${DB_URL:-}" ]; then
  echo "[Backup] Error: DB_URL environment variable is not set." >&2
  exit 1
fi

# Parse connection string
# Supporting both postgresql+asyncpg and raw postgresql protocols
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

echo "[Backup] Running pg_dump against ${DB_HOST}:${DB_PORT}/${DB_NAME}..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"

echo "[Backup] Database backup completed: ${BACKUP_FILE}"

# Cleanup backups older than retention period
echo "[Backup] Cleaning up backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "nomadx_backup_*.sql.gz" -type f -mtime +"${RETENTION_DAYS}" -delete

echo "[Backup] Backup rotation completed."
