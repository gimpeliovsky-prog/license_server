# OTA Integration Guide

Это руководство по интеграции OTA-сервера для обновления ESP32 устройств (scales_bridge_tab5).

## Быстрый старт

### 1. Применить миграцию БД

```bash
cd /path/to/license_server
python -m alembic upgrade head
```

Это создаст таблицы `firmware` и `device_ota_log`.

### 2. Подготовить прошивку

Собрать прошивку для scales_bridge:

```bash
cd /path/to/scales_bridge/tab5
idf.py build
# Результат в: build/firmware.bin
```

### 3. Загрузить на сервер

#### Вариант A: Использовать Python скрипт

```bash
python scripts/ota_management.py \
  --server http://your-server.com \
  --admin-token YOUR_ADMIN_TOKEN \
  upload \
  --file /path/to/firmware.bin \
  --device-type scales_bridge_tab5 \
  --version 1.0.0
```

#### Вариант B: Использовать curl

```bash
# 1. Загрузить файл
curl -F "file=@firmware.bin" \
     -F "device_type=scales_bridge_tab5" \
     -F "version=1.0.0" \
     -H "X-Admin-Token: ADMIN_TOKEN" \
     http://your-server.com/api/ota/admin/upload

# 2. Зарегистрировать в БД
curl -X POST http://your-server.com/api/ota/admin/firmware \
     -H "X-Admin-Token: ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "device_type": "scales_bridge_tab5",
       "version": "1.0.0",
       "build_number": 1,
       "filename": "firmware.bin",
       "file_size": 524288,
       "file_hash": "abc123...",
       "binary_path": "scales_bridge_tab5/v1.0.0.bin",
       "is_stable": true,
       "description": "Initial release"
     }'
```

### 4. Интегрировать на ESP32 устройстве

1. Скопировать [ESP32_OTA_CLIENT_EXAMPLE.c](ESP32_OTA_CLIENT_EXAMPLE.c) в проект scales_bridge

2. Адаптировать для вашего проекта:
   - Установить правильный `OTA_SERVER_URL`
   - Установить правильный `OTA_DEVICE_TYPE`
   - Получить `device_id` из конфига устройства
   - Получить JWT устройства через `/activate` и сохранить (Bearer токен)
   - Получить текущую версию из `app_desc.version`

3. Периодически вызывать `ota_check_and_update()`:
   ```cpp
   // В main цикле устройства
   xTaskCreate(ota_check_task, "ota_check", 4096, NULL, 5, NULL);
   ```

## Архитектура OTA

```
ESP32 Device
    ↓
    ├─→ POST /api/ota/check
    │    ↓
    │   License Server (FastAPI)
    │    ↑
    └─← (version info)
    
    ├─→ GET /api/ota/download/{id}
    │    ↓
    │   License Server
    │    ↓
    │   Serve binary file
    │    ↑
    └─← (firmware.bin)
    
    ├─→ POST /api/ota/status
    │    ↓
    │   License Server
    │    ↓
    │   Update DeviceOTALog
    │    ↑
    └─← (success)
```

## Структура файлов

```
license_server/
├── app/
│   ├── api/
│   │   └── routes/
│   │       └── ota.py                    # API endpoints
│   ├── models/
│   │   └── firmware.py                   # Database models
│   ├── schemas/
│   │   └── ota.py                        # Pydantic schemas
│   └── services/
│       └── ota.py                        # Business logic
├── alembic/
│   └── versions/
│       └── 0004_firmware_ota.py          # Database migration
├── firmware/                              # Binary storage
│   ├── scales_bridge_tab5/
│   │   ├── v1.0.0.bin
│   │   └── v1.1.0.bin
│   └── README.md
├── ESP32_OTA_CLIENT_EXAMPLE.c            # Example client code
├── OTA_SERVER_README.md                  # Detailed OTA docs
└── scripts/
    └── ota_management.py                 # Management script
```

## API Endpoints

### Device endpoints (Bearer JWT required)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ota/check` | Check for available updates (Bearer JWT required) |
| GET | `/api/ota/download/{firmware_id}` | Download firmware binary (Bearer JWT required) |
| POST | `/api/ota/status` | Report OTA operation status (Bearer JWT required) |

### Admin endpoints (authenticated)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ota/admin/upload` | Upload firmware file |
| POST | `/api/ota/admin/firmware` | Create firmware record |
| GET | `/api/ota/admin/firmware` | List firmware versions |
| GET | `/api/ota/admin/firmware/{id}` | Get firmware details |
| PATCH | `/api/ota/admin/firmware/{id}` | Update firmware metadata |
| DELETE | `/api/ota/admin/firmware/{id}` | Deactivate firmware |
| GET | `/api/ota/admin/logs` | Get OTA operation logs |

## Configuration

### Server-side

Настройки в `.env`:

```bash
# Админ-доступ к OTA admin endpoints
ADMIN_TOKEN=change-me-admin

# Подпись download_url (защита от прямого скачивания)
OTA_DOWNLOAD_SECRET=change-me-download
OTA_DOWNLOAD_TTL_SECONDS=600  # 10 минут
```

### Device-side

Настройки в ESP32 коде:

```c
#define OTA_SERVER_URL "https://your-license-server.com"
#define OTA_DEVICE_TYPE "scales_bridge_tab5"
#define OTA_CHECK_INTERVAL_SEC (24 * 3600)  // Check once per day
#define OTA_REQUEST_TIMEOUT_MS 30000         // 30 seconds
// JWT устройства получите через /activate и передавайте как Bearer
```

## Управление версиями

### Семантическое версионирование

- `1.0.0` - MAJOR.MINOR.PATCH
- MAJOR: несовместимые изменения
- MINOR: новые функции (обратно совместимо)
- PATCH: багфиксы

### Версионная иерархия

1. **Стабильная версия** (`is_stable=true`)
   - Рекомендуется устройствам по умолчанию
   - Полностью протестирована

2. **Беты** (`is_stable=false`)
   - Новые функции для тестирования
   - Устройства не обновляются автоматически

3. **Архивные версии** (`is_active=false`)
   - Больше не доступны для скачивания
   - Видны в логах истории

### Стратегия rollout

Постепенное внедрение новой версии:

```bash
# День 1: загрузить и протестировать
python scripts/ota_management.py upload \
  --file firmware.bin \
  --device-type scales_bridge_tab5 \
  --version 1.1.0

# День 2: пометить как стабильную
python scripts/ota_management.py update \
  --id 123 \
  --stable
```

## Troubleshooting

### Устройство не видит обновление

```python
# Проверить записи в БД
SELECT * FROM firmware 
WHERE device_type='scales_bridge_tab5' 
  AND is_active=true 
  AND is_stable=true 
ORDER BY version DESC;

# Проверить логи устройства
# Device должно logировать результат check_update_available()
```

### Ошибка при скачивании

```bash
# Проверить наличие файла
ls -la firmware/scales_bridge_tab5/v1.0.0.bin

# Проверить права доступа
chmod 644 firmware/scales_bridge_tab5/v1.0.0.bin

# Проверить хеш
sha256sum firmware/scales_bridge_tab5/v1.0.0.bin
```

### OTA logs переполняют БД

```python
# Удалить старые логи
from datetime import datetime, timedelta
from app.models.firmware import DeviceOTALog

# Оставить последние 30 дней
cutoff = datetime.utcnow() - timedelta(days=30)
db.query(DeviceOTALog).filter(
    DeviceOTALog.created_at < cutoff
).delete()
db.commit()
```

## Безопасность

1. **Всегда используйте HTTPS**
   ```
   OTA обновления передают бинарные файлы
   Без HTTPS возможен Man-in-the-Middle атака
   ```

2. **Проверяйте ADMIN_TOKEN**
   ```
   Admin endpoints требуют аутентификации
   Device endpoints требуют Bearer JWT устройства
   ```

3. **Верифицируйте хеши**
   ```
   Сервер вычисляет SHA256 при загрузке
   Устройство должно проверить хеш перед установкой
   ```

4. **Версионируйте правильно**
   ```
   Используйте min_current_version для защиты от обновления с пропуска версий
   Пример: нельзя обновиться с 1.0 на 3.0, нужно 1.0 → 2.0 → 3.0
   ```

5. **Используйте подписанные ссылки на скачивание**
   ```
   download_url содержит подпись и срок действия (expires)
   Устройства должны скачивать только по URL из /api/ota/check
   ```

6. **Используйте JWT устройства**
   ```
   /api/ota/check и /api/ota/status требуют Authorization: Bearer <device_jwt>
   Получайте токен через /activate (см. auth API)
   ```

## Примеры использования

### Upload и регистрация через Python скрипт

```bash
#!/bin/bash

SERVER="http://localhost:8000"
ADMIN_TOKEN="your_admin_token"
FIRMWARE_FILE="firmware.bin"
DEVICE_TYPE="scales_bridge_tab5"
VERSION="1.1.0"

python scripts/ota_management.py \
  --server $SERVER \
  --admin-token $ADMIN_TOKEN \
  upload \
  --file $FIRMWARE_FILE \
  --device-type $DEVICE_TYPE \
  --version $VERSION
```

### Проверка статуса на сервере

```bash
python scripts/ota_management.py \
  --admin-token ADMIN_TOKEN \
  list \
  --device-type scales_bridge_tab5

python scripts/ota_management.py \
  --admin-token ADMIN_TOKEN \
  logs \
  --device-id 123
```

### Отправка статуса с устройства

```bash
curl -X POST http://server.com/api/ota/status \
  -H "Authorization: Bearer DEVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "success",
    "bytes_downloaded": 524288
  }'
```

## Performance

### Рекомендации

1. **Хранить файлы на диске, не в БД**
   - Текущая реализация хранит пути, а не сами файлы
   - Это экономит память и ускоряет доступ

2. **Использовать CDN для раздачи**
   ```
   Если много устройств:
   License Server → CDN → Device
   
   Настроить в OTA check response:
   "download_url": "https://cdn.example.com/firmware/..."
   ```

3. **Кэшировать список версий**
   ```python
   # На устройстве кэшировать информацию о доступных обновлениях
   # Проверять каждые 24 часа
   ```

## Дополнительные ресурсы

- [ESP32 OTA Programming Guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/ota.html)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/)
- [Semantic Versioning](https://semver.org/)

## Поддержка

Если возникли вопросы:
1. Проверить логи сервера: `tail -f server.log`
2. Проверить логи устройства: UART output
3. Проверить структуру БД: `sqlite3 app.db ".schema"`
