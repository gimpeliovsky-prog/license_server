#!/usr/bin/env sh
set -eu

if [ "${1:-}" = "" ]; then
  echo "Usage: $0 <backup-file.sql>"
  exit 1
fi

BACKUP_FILE="$1"
COMPOSE_ARGS="${COMPOSE_ARGS:--f docker-compose.yml}"
DB_SERVICE="${DB_SERVICE:-db}"

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "Backup file not found: ${BACKUP_FILE}"
  exit 1
fi

echo "Restoring PostgreSQL from ${BACKUP_FILE}"
docker compose ${COMPOSE_ARGS} exec -T "${DB_SERVICE}" sh -lc '
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<EOF
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO public;
EOF
'

docker compose ${COMPOSE_ARGS} exec -T "${DB_SERVICE}" sh -lc '
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1
' < "${BACKUP_FILE}"

echo "Restore completed from ${BACKUP_FILE}"
