#!/usr/bin/env sh
set -eu

COMPOSE_ARGS="${COMPOSE_ARGS:--f docker-compose.frappe.yml}"
DB_SERVICE="${DB_SERVICE:-license_db}"
API_SERVICE="${API_SERVICE:-license_api}"
WORKER_SERVICE="${WORKER_SERVICE:-license_worker}"
MIGRATE_SERVICE="${MIGRATE_SERVICE:-license_migrate}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
READINESS_PATH="${READINESS_PATH:-/health/ready}"
READINESS_RETRIES="${READINESS_RETRIES:-20}"
READINESS_DELAY_SECONDS="${READINESS_DELAY_SECONDS:-3}"

echo "Creating PostgreSQL backup..."
COMPOSE_ARGS="${COMPOSE_ARGS}" DB_SERVICE="${DB_SERVICE}" BACKUP_DIR="${BACKUP_DIR}" sh ./scripts/docker_backup_postgres.sh

echo "Building Docker images..."
docker compose ${COMPOSE_ARGS} build

echo "Running Alembic migrations..."
docker compose ${COMPOSE_ARGS} --profile tools run --rm "${MIGRATE_SERVICE}"

echo "Starting database, API, and worker..."
docker compose ${COMPOSE_ARGS} up -d "${DB_SERVICE}" "${API_SERVICE}" "${WORKER_SERVICE}" --remove-orphans

echo "Waiting for readiness..."
attempt=1
while [ "${attempt}" -le "${READINESS_RETRIES}" ]; do
  if docker compose ${COMPOSE_ARGS} exec -T "${API_SERVICE}" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000${READINESS_PATH}', timeout=5).read()"; then
    echo "Deployment completed successfully"
    exit 0
  fi
  echo "Readiness check failed, retry ${attempt}/${READINESS_RETRIES}"
  attempt=$((attempt + 1))
  sleep "${READINESS_DELAY_SECONDS}"
done

echo "Deployment failed: readiness check did not pass"
exit 1
