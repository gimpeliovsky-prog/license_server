# KadimaSoft License Server

Самостоятельный лиценз‑сервер для Android приложения KadimaSoft с прокси к ERPNext.

## Стек
- FastAPI + SQLAlchemy + Alembic
- Postgres
- PyJWT + bcrypt
- Docker Compose
- Nginx + Certbot (опционально, для HTTPS)

## Как устроено лицензирование
- Каждый клиент = отдельный tenant (company_code + erpnext_url).
- У каждого tenant своя подписка (subscription_expires_at) и свои license keys.
- Лицензия хранится только в виде хэша (bcrypt).
- Токен живёт 7 дней, оффлайн допускается до 7 дней.
- Unlimited devices, но устройства можно отзывать.

## Подготовка .env
1) Скопируй шаблон и поправь значения:

```bash
cp .env.example .env
```

2) Обязательно поменяй:
- `JWT_SECRET`
- `ADMIN_TOKEN`
- `SESSION_SECRET`
- `LE_EMAIL` (для Let's Encrypt)
- `LE_DOMAIN` = `license.kadimasoft.com`

## Полная инструкция по развертыванию в Docker

### 1) Установи Docker
Нужен Docker Engine и docker compose.

### 2) Выбери вариант запуска

#### Вариант A — без HTTPS (только dev)
1) В `.env` поставь `ALLOW_INSECURE_HTTP=true`.
2) Запусти:

```bash
docker compose up -d --build
```

URL:
- `http://localhost:8000`
- `http://localhost:8000/admin-ui/login`

#### Вариант B — HTTPS self‑signed (dev)
1) Сгенерируй сертификаты:

```bash
# Windows (PowerShell)
scripts\generate_self_signed_cert.ps1 -Domain localhost

# Linux/macOS
./scripts/generate_self_signed_cert.sh localhost
```

2) Запусти:

```bash
docker compose -f docker-compose.yml -f docker-compose.selfsigned.yml up -d --build
```

URL:
- `https://localhost/admin-ui/login`

#### Вариант C — HTTPS Let's Encrypt + авто‑продление (prod)
1) Проверь, что DNS указывает на сервер, порты 80/443 открыты.
2) В `.env` задай:

```
LE_DOMAIN=license.kadimasoft.com
LE_EMAIL=admin@kadimasoft.com
ALLOW_INSECURE_HTTP=false
```

3) Один раз запроси сертификат:

```bash
# Windows (PowerShell)
scripts\letsencrypt_init.ps1 -Domain license.kadimasoft.com -Email admin@kadimasoft.com

# Linux/macOS
./scripts/letsencrypt_init.sh license.kadimasoft.com admin@kadimasoft.com
```

4) Запусти полный стек:

```bash
docker compose -f docker-compose.yml -f docker-compose.letsencrypt.yml up -d --build
```

URL:
- `https://license.kadimasoft.com/admin-ui/login`

Certbot автоматически обновляет сертификаты каждые 12 часов.

### 3) Прогон миграций
После первого старта:

```bash
docker compose exec api alembic upgrade head
```

## Администрирование (ручное управление лицензиями)
Да, можно полностью управлять лицензиями вручную — через CLI или через Admin UI / Admin API.

### Admin UI (FastAPI + Jinja)
- Открывай `https://license.kadimasoft.com/admin-ui/login`
- Вводи `ADMIN_TOKEN`
- Создавай tenants, ключи лицензий, продлевай подписки, блокируй устройства
- Управляй ERP allowlist на странице `ERP Allowlist`

### CLI (консоль)
Примеры:

```bash
# создать tenant
docker compose exec api python scripts/license_admin.py create-tenant \
  --company-code menor \
  --erpnext-url https://menor.kadimasoft.com \
  --api-key ERP_API_KEY \
  --api-secret ERP_API_SECRET \
  --subscription-expires-at 2025-12-31

# создать ключ лицензии
docker compose exec api python scripts/license_admin.py create-license --company-code menor

# продлить подписку
docker compose exec api python scripts/license_admin.py add-days --company-code menor --days 30

# приостановить tenant
docker compose exec api python scripts/license_admin.py set-status --company-code menor --status suspended

# список устройств и отзыв
docker compose exec api python scripts/license_admin.py list-devices --company-code menor
docker compose exec api python scripts/license_admin.py revoke-device --company-code menor --device-id DEVICE123
```

### Admin API (token‑protected)
Все запросы требуют `X-Admin-Token`.

```bash
curl -H "X-Admin-Token: YOUR_ADMIN_TOKEN" https://license.kadimasoft.com/admin/tenants
curl -H "X-Admin-Token: YOUR_ADMIN_TOKEN" -X POST https://license.kadimasoft.com/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"company_code":"menor","erpnext_url":"https://menor.kadimasoft.com","api_key":"KEY","api_secret":"SECRET","subscription_expires_at":"2025-12-31T23:59:59Z","status":"active"}'
```

## API overview
- `POST /activate` -> выдаёт токен (7 дней)
- `POST /refresh` -> обновляет токен
- `GET /status` -> статус подписки
- ERPNext прокси: `/picklists`, `/items`, `/bin`, `/resource/{doctype}`

## Примечания
- Если запросы идут без HTTPS, ставь `ALLOW_INSECURE_HTTP=true`.
- Для продакшена обязательно HTTPS.
- Универсальный прокси ограничен whitelist: `ERP_ALLOWED_DOCTYPES`, методы регулируются через `ERP_ALLOWED_METHODS`.

## Tests
```bash
docker compose exec api pytest
```
