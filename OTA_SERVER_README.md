# OTA (Over-The-Air) Update Server

Комплексная система обновления микропрограмм для ESP32 устройств через веб-сервер.

## Обзор

OTA-сервер позволяет:
- Управлять версиями микропрограмм для разных типов устройств
- Проверять доступность обновлений на устройствах
- Загружать бинарные файлы микропрограмм
- Отслеживать ход обновления на каждом устройстве
- Хранить историю попыток обновления

## Структура

### Модели данных

#### `Firmware`
Запись о доступной версии микропрограммы.

**Поля:**
- `id` - уникальный идентификатор
- `device_type` - тип устройства (e.g., "scales_bridge_tab5")
- `version` - версия (semantic: "1.2.3")
- `build_number` - номер сборки (для версий с одинаковым version)
- `filename` - имя файла бинарника
- `file_size` - размер в байтах
- `file_hash` - SHA256 хеш файла
- `binary_path` - путь к файлу на диске (относительно папки `firmware/`)
- `description` - описание изменений
- `release_notes` - заметки о выпуске
- `is_stable` - стабильный релиз (доступен для обычных устройств)
- `is_active` - активен ли (можно скачать)
- `min_current_version` - минимальная требуемая текущая версия для обновления
- `created_at` - когда добавлена в систему
- `updated_at` - когда обновлена запись
- `released_at` - когда релизнулась версия

#### `DeviceOTALog`
История попыток обновления на устройствах.

**Поля:**
- `id` - уникальный идентификатор
- `device_id` - ID устройства
- `firmware_id` - ID версии микропрограммы
- `status` - текущий статус:
  - `pending` - ожидает
  - `downloading` - загружается
  - `installing` - устанавливается
  - `success` - успешно установлена
  - `failed` - ошибка
- `error_message` - сообщение об ошибке если была
- `bytes_downloaded` - скачано байт
- `download_started_at` - начало загрузки
- `download_completed_at` - конец загрузки
- `installed_at` - время установки
- `created_at` - создание записи
- `updated_at` - последнее обновление

### Сервис `OTAService`

Основная бизнес-логика, находится в `app/services/ota.py`.

**Основные методы:**

```python
# Проверить доступность обновления
check_update_available(db, request: OTACheckRequest) -> OTACheckResponse

# Получить файл для скачивания
get_firmware_for_download(db, firmware_id) -> Firmware

# Получить полный путь к файлу бинарника
get_firmware_binary_path(firmware) -> Path

# Проверить наличие файла
firmware_binary_exists(firmware) -> bool

# Проверить хеш файла
verify_firmware_hash(firmware, file_data) -> bool

# Создать запись в лог OTA
create_ota_log(db, device_id, firmware_id, status) -> DeviceOTALog

# Обновить статус OTA операции
update_ota_status(db, log_id, status_update) -> DeviceOTALog

# Вычислить SHA256 хеш файла
calculate_file_hash(file_path) -> str
```

## API Endpoints

### Для устройств (Bearer JWT required)

#### `POST /api/ota/check`
**Проверить наличие обновления**

> Требуется `Authorization: Bearer <device_jwt>`

Запрос:
```json
{
  "device_id": 123,
  "device_type": "scales_bridge_tab5",
  "current_version": "1.0.0",
  "current_build": 1
}
```

Ответ (обновление доступно):
```json
{
  "update_available": true,
  "firmware_id": 456,
  "version": "1.1.0",
  "build_number": 2,
  "description": "Bug fixes and improvements",
  "download_url": "/api/ota/download/456?device_id=123&expires=1700000000&sig=abc123...",
  "file_hash": "abc123...",
  "file_size": 524288
}
```

Ответ (обновления нет):
```json
{
  "update_available": false
}
```

#### `GET /api/ota/download/{firmware_id}`
**Скачать бинарник микропрограммы**

Возвращает бинарный файл (`.bin`).
Если включена подпись, требуются query params: `device_id`, `expires`, `sig`.
Требуется `Authorization: Bearer <device_jwt>`.

> Если задан `OTA_DOWNLOAD_SECRET`, download_url содержит подпись (expires + sig).

**Заголовки ответа:**
- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename=...`
- `X-Firmware-Version: 1.1.0`
- `X-Firmware-Build: 2`
- `X-Firmware-Hash: abc123...`

#### `POST /api/ota/status`
**Отправить статус операции обновления**

> Требуется `Authorization: Bearer <device_jwt>`

Запрос:
```json
{
  "device_id": 123,
  "firmware_id": 456,
  "status": "installing",
  "bytes_downloaded": 262144,
  "error_message": null
}
```

Ответ:
```json
{
  "success": true,
  "log_id": 789,
  "status": "installing"
}
```

### Для администраторов (требует аутентификации)

#### `POST /api/ota/admin/upload`
**Загрузить файл микропрограммы**

Параметры:
- `file` - файл (multipart)
- `device_type` - тип устройства
- `version` - версия

```bash
curl -F "file=@firmware.bin" \
     -F "device_type=scales_bridge_tab5" \
     -F "version=1.1.0" \
     -H "X-Admin-Token: ADMIN_TOKEN" \
     http://localhost:8000/api/ota/admin/upload
```

Ответ:
```json
{
  "success": true,
  "filename": "firmware.bin",
  "device_type": "scales_bridge_tab5",
  "version": "1.1.0",
  "binary_path": "scales_bridge_tab5/v1.1.0.bin",
  "file_size": 524288,
  "file_hash": "abc123..."
}
```

#### `POST /api/ota/admin/firmware`
**Создать запись о микропрограмме**

Запрос:
```json
{
  "device_type": "scales_bridge_tab5",
  "version": "1.1.0",
  "build_number": 2,
  "filename": "firmware.bin",
  "file_size": 524288,
  "file_hash": "abc123...",
  "binary_path": "scales_bridge_tab5/v1.1.0.bin",
  "description": "Bug fixes",
  "is_stable": true
}
```

#### `GET /api/ota/admin/firmware`
**Список всех микропрограмм**

Параметры запроса:
- `device_type` - фильтр по типу устройства
- `skip` - пропустить записей
- `limit` - лимит записей

#### `GET /api/ota/admin/firmware/{firmware_id}`
**Получить информацию о микропрограмме**

#### `PATCH /api/ota/admin/firmware/{firmware_id}`
**Обновить информацию о микропрограмме**

Может обновлять:
- `description`
- `release_notes`
- `is_stable`
- `is_active`
- `min_current_version`

#### `DELETE /api/ota/admin/firmware/{firmware_id}`
**Деактивировать микропрограмму**

(Устанавливает `is_active=false`, не удаляет из БД)

#### `GET /api/ota/admin/logs`
**Получить логи OTA операций**

Параметры:
- `device_id` - фильтр по устройству
- `firmware_id` - фильтр по версии
- `status` - фильтр по статусу
- `skip` - пропустить записей
- `limit` - лимит записей

## Рабочий процесс

### На стороне сервера

1. **Подготовить бинарник:**
   ```bash
   # Собрать прошивку в проекте ESP-IDF
   cd /path/to/scales_bridge/tab5
   idf.py build
   # Файл будет в build/firmware.bin
   ```

2. **Загрузить на OTA-сервер:**
   ```bash
   curl -F "file=@build/firmware.bin" \
        -F "device_type=scales_bridge_tab5" \
        -F "version=1.1.0" \
        -H "X-Admin-Token: YOUR_TOKEN" \
        http://your-server.com/api/ota/admin/upload
   ```

3. **Зарегистрировать в БД:**
   ```bash
   curl -X POST http://your-server.com/api/ota/admin/firmware \
        -H "X-Admin-Token: YOUR_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "device_type": "scales_bridge_tab5",
          "version": "1.1.0",
          "build_number": 2,
          "filename": "firmware.bin",
          "file_size": 524288,
          "file_hash": "SHA256_HEX",
          "binary_path": "scales_bridge_tab5/v1.1.0.bin",
          "is_stable": true,
          "description": "Version 1.1.0: Bug fixes and improvements"
        }'
   ```

### На стороне ESP32 устройства

1. **Проверить обновление:**
   ```cpp
   // Периодически (каждый час/день) отправлять запрос:
   POST /api/ota/check
   {
     "device_id": 123,
     "device_type": "scales_bridge_tab5",
     "current_version": "1.0.0",
     "current_build": 1
   }
   ```

2. **Если обновление доступно:**
   ```cpp
   // Отправить статус
   POST /api/ota/status
   {
     "device_id": 123,
     "firmware_id": 456,
     "status": "downloading"
   }
   
   // Скачать бинарник
    GET /api/ota/download/456?device_id=123&expires=1700000000&sig=abc123...
   
   // Периодически обновлять прогресс
   POST /api/ota/status
   {
     "device_id": 123,
     "firmware_id": 456,
     "status": "downloading",
     "bytes_downloaded": 262144
   }
   ```

3. **При установке:**
   ```cpp
   // Вызвать esp_ota_begin(), esp_ota_write(), esp_ota_end()
   
   // Отправить статус установки
   POST /api/ota/status
   {
     "device_id": 123,
     "firmware_id": 456,
     "status": "installing"
   }
   ```

4. **После успеха/ошибки:**
   ```cpp
   // Успех
   POST /api/ota/status
   {
     "device_id": 123,
     "firmware_id": 456,
     "status": "success"
   }
   
   // Или ошибка
   POST /api/ota/status
   {
     "device_id": 123,
     "firmware_id": 456,
     "status": "failed",
     "error_message": "CRC32 mismatch"
   }
   ```

## Версионирование

Используется семантическое версионирование: `MAJOR.MINOR.PATCH`

- `1.0.0` - стабильный релиз
- `1.0.1` - патч (багфиксы)
- `1.1.0` - минорное обновление (новые функции, обратно совместимо)
- `2.0.0` - мажорное обновление (может быть несовместимо)

**Правила обновления:**

- Сервер автоматически пытается обновить на последнюю стабильную версию
- Если задана `min_current_version`, устройство не может обновиться, если его текущая версия старше
- Можно использовать для постепенного роллаута (сначала стабилизировать на одну версию, потом выкатывать дальше)

## Пример использования с Python

```python
import requests
import hashlib
from pathlib import Path

# Параметры
server_url = "http://localhost:8000"
admin_token = "your_admin_token"
firmware_path = Path("firmware.bin")
device_type = "scales_bridge_tab5"
version = "1.1.0"

# 1. Загрузить файл
with open(firmware_path, "rb") as f:
    files = {"file": f}
    data = {
        "device_type": device_type,
        "version": version,
    }
    response = requests.post(
        f"{server_url}/api/ota/admin/upload",
        files=files,
        data=data,
        headers={"X-Admin-Token": admin_token},
    )
    upload_info = response.json()
    print(f"Uploaded: {upload_info}")

# 2. Зарегистрировать в БД
firmware_record = {
    "device_type": device_type,
    "version": version,
    "build_number": 2,
    "filename": upload_info["filename"],
    "file_size": upload_info["file_size"],
    "file_hash": upload_info["file_hash"],
    "binary_path": upload_info["binary_path"],
    "is_stable": True,
    "description": "Version 1.1.0: Bug fixes",
}

response = requests.post(
    f"{server_url}/api/ota/admin/firmware",
    json=firmware_record,
    headers={"X-Admin-Token": admin_token},
)
firmware = response.json()
print(f"Registered: {firmware}")

# 3. Проверить обновления (от устройства)
check_request = {
    "device_id": 123,
    "device_type": device_type,
    "current_version": "1.0.0",
    "current_build": 1,
}

response = requests.post(
    f"{server_url}/api/ota/check",
    json=check_request,
    headers={"Authorization": "Bearer DEVICE_TOKEN"},
)
ota_check = response.json()
print(f"Update available: {ota_check['update_available']}")
```

## Безопасность

- OTA эндпоинты для администраторов требуют ADMIN_TOKEN
- Эндпоинты для устройств требуют Bearer JWT (device token)
- download_url содержит подпись и срок действия (expires)
- Все файлы проверяются по SHA256 хешу
- Поддерживается версионирование для отката при необходимости

## Обслуживание

### Очистка старых логов
```python
# Оставить только последние 100 попыток обновления per device
from datetime import datetime, timedelta
from app.models.firmware import DeviceOTALog

# Удалить логи старше 30 дней
cutoff = datetime.utcnow() - timedelta(days=30)
db.query(DeviceOTALog).filter(
    DeviceOTALog.created_at < cutoff
).delete()
db.commit()
```

### Архивирование старых версий
```bash
# Переместить старые бинарники в архив
mv firmware/scales_bridge_tab5/v1.0.0.bin firmware/archive/
```

Затем деактивировать в БД:
```python
firmware = db.query(Firmware).filter(
    Firmware.version == "1.0.0"
).first()
firmware.is_active = False
db.commit()
```

## Резолюция проблем

### Устройство не видит обновления
1. Проверить, что `is_active=true` и `is_stable=true` для версии
2. Проверить, что `current_version` корректная версия на устройстве
3. Проверить, что `min_current_version` не требует более новую текущую версию

### Ошибка при скачивании
1. Проверить, что файл существует: `ls -la firmware/scales_bridge_tab5/`
2. Проверить логи сервера
3. Проверить, что `file_hash` совпадает: `sha256sum firmware/.../firmware.bin`

### Ошибка CRC32 на устройстве
- Скачанный файл поврежден во время передачи
- Проверить стабильность сети
- Переспробовать скачивание
