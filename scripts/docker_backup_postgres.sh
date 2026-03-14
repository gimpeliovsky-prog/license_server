#!/usr/bin/env sh
set -eu

COMPOSE_ARGS="${COMPOSE_ARGS:--f docker-compose.yml}"
DB_SERVICE="${DB_SERVICE:-db}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_FILE="${BACKUP_DIR}/license_server_${TIMESTAMP}.sql"

mkdir -p "${BACKUP_DIR}"

echo "Creating PostgreSQL backup into ${OUTPUT_FILE}"
docker compose ${COMPOSE_ARGS} exec -T "${DB_SERVICE}" sh -lc '
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' > "${OUTPUT_FILE}"

echo "Backup completed: ${OUTPUT_FILE}"
