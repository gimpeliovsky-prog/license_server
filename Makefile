.DEFAULT_GOAL := help

COMPOSE_ARGS ?= -f docker-compose.yml
DB_SERVICE ?= db
API_SERVICE ?= api
WORKER_SERVICE ?= worker
MIGRATE_SERVICE ?= migrate
BASE_URL ?= http://localhost:8000
ADMIN_TOKEN ?=
BACKUP_FILE ?=

.PHONY: help build migrate up up-selfsigned up-letsencrypt down restart ps logs-api logs-worker \
	health-live health-ready backup restore process-summary process-check

help:
	@echo "Targets:"
	@echo "  make build               Build images"
	@echo "  make migrate             Run Alembic migrations"
	@echo "  make up                  Start db/api/worker"
	@echo "  make up-selfsigned       Start stack with self-signed nginx overlay"
	@echo "  make up-letsencrypt      Start stack with letsencrypt nginx overlay"
	@echo "  make down                Stop stack"
	@echo "  make restart             Restart api and worker"
	@echo "  make ps                  Show container status"
	@echo "  make logs-api            Tail api logs"
	@echo "  make logs-worker         Tail worker logs"
	@echo "  make health-live         Check /health/live"
	@echo "  make health-ready        Check /health/ready"
	@echo "  make backup              Create PostgreSQL backup"
	@echo "  make restore BACKUP_FILE=backups/file.sql"
	@echo "  make process-summary     Fetch process job summary"
	@echo "  make process-check       Run process job alert check"
	@echo ""
	@echo "Override variables when needed:"
	@echo "  COMPOSE_ARGS='-f docker-compose.frappe.yml'"
	@echo "  DB_SERVICE=license_db API_SERVICE=license_api WORKER_SERVICE=license_worker MIGRATE_SERVICE=license_migrate"
	@echo "  BASE_URL=https://license.example.com ADMIN_TOKEN=..."

build:
	docker compose $(COMPOSE_ARGS) build

migrate:
	docker compose $(COMPOSE_ARGS) --profile tools run --rm $(MIGRATE_SERVICE)

up:
	docker compose $(COMPOSE_ARGS) up -d $(DB_SERVICE) $(API_SERVICE) $(WORKER_SERVICE)

up-selfsigned:
	docker compose -f docker-compose.yml -f docker-compose.selfsigned.yml up -d

up-letsencrypt:
	docker compose -f docker-compose.yml -f docker-compose.letsencrypt.yml up -d

down:
	docker compose $(COMPOSE_ARGS) down

restart:
	docker compose $(COMPOSE_ARGS) restart $(API_SERVICE) $(WORKER_SERVICE)

ps:
	docker compose $(COMPOSE_ARGS) ps

logs-api:
	docker compose $(COMPOSE_ARGS) logs --tail=100 -f $(API_SERVICE)

logs-worker:
	docker compose $(COMPOSE_ARGS) logs --tail=100 -f $(WORKER_SERVICE)

health-live:
	curl -fsS $(BASE_URL)/health/live

health-ready:
	curl -fsS $(BASE_URL)/health/ready

backup:
	COMPOSE_ARGS="$(COMPOSE_ARGS)" DB_SERVICE="$(DB_SERVICE)" ./scripts/docker_backup_postgres.sh

restore:
	test -n "$(BACKUP_FILE)"
	COMPOSE_ARGS="$(COMPOSE_ARGS)" DB_SERVICE="$(DB_SERVICE)" ./scripts/docker_restore_postgres.sh "$(BACKUP_FILE)"

process-summary:
	test -n "$(ADMIN_TOKEN)"
	python scripts/process_job_monitor.py --base-url "$(BASE_URL)" --admin-token "$(ADMIN_TOKEN)" summary --window-hours 1

process-check:
	test -n "$(ADMIN_TOKEN)"
	python scripts/process_job_monitor.py --base-url "$(BASE_URL)" --admin-token "$(ADMIN_TOKEN)" check --window-hours 1 --failed-threshold 1 --stale-threshold 1 --show-jobs
