# Docker Production Runbook

This runbook is for operating `license_server` in Docker only.

## Services

- `db`: PostgreSQL
- `api`: FastAPI HTTP server
- `worker`: process job runner
- `migrate`: one-shot Alembic job
- `nginx`: optional TLS reverse proxy
- `certbot`: optional Let's Encrypt renewer

## Makefile Shortcuts

Common operations are wrapped in `Makefile`.

Examples:

```bash
make build
make migrate
make up
make health-ready
make backup
make logs-worker
```

For frappe compose:

```bash
make migrate COMPOSE_ARGS="-f docker-compose.frappe.yml" \
  DB_SERVICE=license_db API_SERVICE=license_api WORKER_SERVICE=license_worker MIGRATE_SERVICE=license_migrate
```

## GitLab Deploy

This repo includes:
- `.gitlab-ci.yml`
- `scripts/docker_deploy.sh`

The deploy flow is:
1. fetch/reset repository on the server
2. create PostgreSQL backup
3. build images
4. run migrations
5. start `db + api + worker`
6. wait for `/health/ready`

## First Deploy

1. Copy `.env.example` to `.env`.
2. Fill all secrets and database settings.
3. Build images:

```bash
docker compose build
```

4. Run migrations:

```bash
docker compose --profile tools run --rm migrate
```

5. Start core services:

```bash
docker compose up -d db api worker
```

6. Check health:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

## HTTPS Deploy

### Self-signed

```bash
docker compose -f docker-compose.yml -f docker-compose.selfsigned.yml up -d
```

### Let's Encrypt

Initialize certificates first, then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.letsencrypt.yml up -d
```

## Daily Operations

### Check containers

```bash
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=100 worker
```

### Check process jobs

```bash
python scripts/process_job_monitor.py \
  --base-url https://license.example.com \
  --admin-token "$ADMIN_TOKEN" \
  summary --window-hours 1
```

### Check readiness

`/health/live`:
- process is up

`/health/ready`:
- DB is reachable
- process job summary is available

## Backup Strategy

Recommended:
- daily logical PostgreSQL dump
- keep at least 7 daily backups
- keep one backup before every deploy

Use:

```bash
./scripts/docker_backup_postgres.sh
```

The script writes dumps into `./backups/`.

## Restore Strategy

Restore only when stack is stopped or when you explicitly accept overwriting current data.

Use:

```bash
./scripts/docker_restore_postgres.sh backups/license_server_YYYYMMDD_HHMMSS.sql
```

This script recreates the public schema and restores the dump.

## Upgrade Playbook

1. Take backup.
2. Pull new code.
3. Rebuild images.
4. Run migrations.
5. Restart services.
6. Verify health.
7. Check worker logs.
8. Check process jobs summary.

Commands:

```bash
./scripts/docker_backup_postgres.sh
docker compose build
docker compose --profile tools run --rm migrate
docker compose up -d db api worker
curl http://localhost:8000/health/ready
docker compose logs --tail=100 worker
```

## Rollback Plan

If deploy is bad but DB schema is still compatible:

1. Switch code/image tag back.
2. Rebuild or pull previous image.
3. Restart `api` and `worker`.

If deploy included a bad migration:

1. Stop write traffic.
2. Stop `api` and `worker`.
3. Restore PostgreSQL from backup.
4. Start services using previous code/image.

Commands:

```bash
docker compose stop api worker
./scripts/docker_restore_postgres.sh backups/license_server_YYYYMMDD_HHMMSS.sql
docker compose up -d db api worker
```

## Production Rules

- Do not run without `worker`.
- Do not skip migrations before starting a new version.
- Always take a backup before upgrade.
- Treat `/health/ready` as the real readiness signal.
- Watch `api` and `worker` separately.
