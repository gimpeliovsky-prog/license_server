# KadimaSoft License Server

FastAPI license server + PostgreSQL для ERPNext/Android. Есть два режима запуска:
- `frappe_network` (рекомендуется, если ERPNext уже работает в Docker; без nginx внутри compose)
- standalone compose с опциональным nginx для HTTPS

---

## Архитектура

- `license_api` - FastAPI (uvicorn) на порту `8000`
- `license_db` - PostgreSQL
- Сети:
  - `license_internal` (private): `license_api` <-> `license_db`
  - `frappe_network` (external): общий доступ для ERPNext/Frappe

В сети `frappe_network` API доступен как `license-api` (network alias).

---

## Вариант A: ERPNext/Frappe network (docker-compose.frappe.yml)

1) Создайте внешнюю сеть (если нет):
```bash
docker network create frappe_network || true
```

2) Создайте `.env`:
```powershell
# Windows (PowerShell)
copy .env.example .env
```
```bash
# Linux/macOS
cp .env.example .env
```

3) Заполните обязательные секреты:
- `JWT_SECRET`
- `ADMIN_TOKEN`
- `SESSION_SECRET`
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

4) Запустите сервисы:
```bash
docker compose -f docker-compose.frappe.yml up -d --build
```

5) Примените миграции:
```bash
docker compose -f docker-compose.frappe.yml exec -w /app license_api sh -lc \
  'export PYTHONPATH=/app; alembic upgrade head'
```

6) Доступ:
- Из контейнеров `frappe_network`: `http://license-api:8000`
- Для доступа с хоста добавьте `ports: "8000:8000"` или проксируйте через свой nginx.

---

## Вариант B: локальный dev (docker-compose.yml, без nginx)

1) `.env` как выше + `ALLOW_INSECURE_HTTP=true`.
2) Запуск:
```bash
docker compose up -d --build
```

URL:
- `http://localhost:8000/admin-ui/login`
- `http://localhost:8000/docs`

---

## Вариант C: HTTPS через nginx

### Self-signed (dev)
1) Сгенерируйте сертификат:
```powershell
# Windows (PowerShell)
scripts\generate_self_signed_cert.ps1 -Domain localhost
```
```bash
# Linux/macOS
./scripts/generate_self_signed_cert.sh localhost
```

2) Запуск:
```bash
docker compose -f docker-compose.yml -f docker-compose.selfsigned.yml up -d --build
```

URL:
- `https://localhost/admin-ui/login`

### Let's Encrypt (prod)
1) В `.env`:
```
LE_DOMAIN=license.kadimasoft.com
LE_EMAIL=admin@kadimasoft.com
ALLOW_INSECURE_HTTP=false
```

2) Инициализация сертификата:
```powershell
# Windows (PowerShell)
scripts\letsencrypt_init.ps1 -Domain license.kadimasoft.com -Email admin@kadimasoft.com
```
```bash
# Linux/macOS
./scripts/letsencrypt_init.sh license.kadimasoft.com admin@kadimasoft.com
```

3) Запуск:
```bash
docker compose -f docker-compose.yml -f docker-compose.letsencrypt.yml up -d --build
```

URL:
- `https://license.kadimasoft.com/admin-ui/login`

---

## Admin UI и ERP allowlist

- Вход: `/admin-ui/login` (используйте `ADMIN_TOKEN` из `.env`).
- Управление tenants и license keys.
- Страница `/admin-ui/erp-allowlist`:
  - добавление разрешенных doctypes и HTTP-методов
  - кнопка "Load defaults into DB" грузит значения из `.env`
  - если в БД нет записей, сервер использует `.env` (`ERP_ALLOWED_DOCTYPES`, `ERP_ALLOWED_METHODS`)

---

## CLI (скрипты админа)

```bash
# Создать tenant
docker compose exec api python scripts/license_admin.py create-tenant \
  --company-code menor \
  --erpnext-url https://menor.kadimasoft.com \
  --api-key ERP_API_KEY \
  --api-secret ERP_API_SECRET \
  --subscription-expires-at 2025-12-31

# Создать license key
docker compose exec api python scripts/license_admin.py create-license --company-code menor

# Продлить подписку
docker compose exec api python scripts/license_admin.py add-days --company-code menor --days 30

# Изменить статус tenant
docker compose exec api python scripts/license_admin.py set-status --company-code menor --status suspended

# Список устройств и отзыв
docker compose exec api python scripts/license_admin.py list-devices --company-code menor
docker compose exec api python scripts/license_admin.py revoke-device --company-code menor --device-id DEVICE123
```

Для `docker-compose.frappe.yml` замените сервис `api` на `license_api`.

---

## API overview

- `POST /activate` — активация лицензии (привязка устройства)
- `POST /refresh` — продление токена
- `GET /status` — статус лицензии/устройства
- ERPNext proxy: `/picklists`, `/items`, `/bin`, `/resource/{doctype}`

---

## Notes

- Храните секреты в `.env`, не коммитьте их.
- Если пароль БД содержит спецсимволы, используйте `POSTGRES_*` или URL-encode в `DATABASE_URL`.
- Тесты:
```bash
docker compose exec api pytest
```
