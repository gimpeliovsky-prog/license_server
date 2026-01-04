# KadimaSoft License Server (Docker in `frappe_network`, no nginx)

This repository runs a **FastAPI license server** with **PostgreSQL**, designed to be reachable from your ERPNext/Frappe stack over a shared Docker network (`frappe_network`).

This setup **does not run nginx inside docker-compose**. You can:
- access the API directly on `:8000` (dev/testing), or
- put it behind your existing nginx (recommended for HTTPS + hide port 8000).

---

## Architecture

- `license_api` — FastAPI (uvicorn) on port `8000`
- `license_db` — PostgreSQL (internal only; not published to the host)
- Networks:
  - `license_internal` (private, internal): `license_api` ↔ `license_db`
  - `frappe_network` (external): allows ERPNext/Frappe containers to reach `license_api`

Inside `frappe_network`, API DNS name: `license-api` (network alias).

---

## Prerequisites

- Docker + Docker Compose plugin
- External docker network used by your frappe/erpnext stack:
  - `frappe_network`

Create the external network (if missing):
```bash
docker network create frappe_network || true


##1) Create .env

Create .env (or copy from example if it exists):

cp .env.example .env 2>/dev/null || true


Minimum required secrets (generate strong random values):

JWT_SECRET=CHANGE_ME_RANDOM_LONG
ADMIN_TOKEN=CHANGE_ME_RANDOM_LONG
SESSION_SECRET=CHANGE_ME_RANDOM_LONG


Database settings (example):

POSTGRES_DB=license
POSTGRES_USER=license
POSTGRES_PASSWORD=CHANGE_ME_STRONG


Generate secrets:

openssl rand -base64 64


Important: if your password contains special characters (@, :, /, *, etc.), either:

URL-encode it if you put it into DATABASE_URL, or

just use DB_* variables (recommended; this compose sets both).

## 2) docker-compose.frappe.yml

Create docker-compose.frappe.yml:

Note: We do not publish Postgres 5432 to the host to avoid conflicts with any other Postgres container already using host port 5432.

## 3) Start services
docker compose -f docker-compose.frappe.yml up -d --build
docker compose -f docker-compose.frappe.yml ps

## 4) Install missing dependency (if needed)

If you see:
ModuleNotFoundError: No module named 'itsdangerous'

Add to requirements.txt:

itsdangerous


Rebuild:

docker compose -f docker-compose.frappe.yml up -d --build --force-recreate

## 5) Run migrations (creates tables like tenants)
docker compose -f docker-compose.frappe.yml exec -w /app license_api sh -lc \
  'export PYTHONPATH=/app; alembic upgrade head'

## 6) Admin UI & Docs

If ports: 8000:8000 is enabled:

Admin UI:

http://SERVER_IP:8000/admin-ui/login

Swagger docs:

http://SERVER_IP:8000/docs

Login using ADMIN_TOKEN from .env.

Create Tenant

In Admin UI → Tenants:

Company code: e.g. kadima

ERPNext URL: e.g. https://erp-dev.kadimasoft.com

ERPNext API Key / Secret (integration user)

Status: active
