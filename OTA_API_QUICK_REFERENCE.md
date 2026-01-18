# OTA API Quick Reference

Быстрые примеры использования OTA API через curl.

## Переменные окружения

```bash
# Установить переменные для удобства
export OTA_SERVER="http://localhost:8000"
export JWT_TOKEN="your_jwt_token_here"
export DEVICE_TYPE="scales_bridge_tab5"
export DEVICE_ID="123"
```

## Device Endpoints (public)

### 1. Проверить доступность обновлений

```bash
curl -X POST "$OTA_SERVER/api/ota/check" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "device_type": "scales_bridge_tab5",
    "current_version": "1.0.0",
    "current_build": 1
  }'
```

**Ответ при наличии обновления:**
```json
{
  "update_available": true,
  "firmware_id": 456,
  "version": "1.1.0",
  "build_number": 2,
  "description": "Bug fixes and improvements",
  "download_url": "/api/ota/download/456",
  "file_hash": "abc123def456...",
  "file_size": 524288
}
```

**Ответ при отсутствии обновлений:**
```json
{
  "update_available": false
}
```

### 2. Скачать прошивку

```bash
# Сохранить в файл
curl -o firmware.bin "$OTA_SERVER/api/ota/download/456"

# Или с более подробным выводом
curl -v -o firmware.bin \
  -H "Accept: application/octet-stream" \
  "$OTA_SERVER/api/ota/download/456"
```

### 3. Отправить статус обновления

```bash
# Начало скачивания
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "downloading",
    "bytes_downloaded": 0
  }'

# Прогресс скачивания (каждые 100KB)
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "downloading",
    "bytes_downloaded": 262144
  }'

# Начало установки
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "installing"
  }'

# Успешная установка
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "success",
    "bytes_downloaded": 524288
  }'

# Ошибка при обновлении
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "failed",
    "error_message": "CRC32 mismatch"
  }'
```

## Admin Endpoints (требуется JWT)

### 1. Загрузить файл прошивки

```bash
# Простая загрузка
curl -F "file=@firmware.bin" \
  -F "device_type=scales_bridge_tab5" \
  -F "version=1.1.0" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  "$OTA_SERVER/api/ota/admin/upload"

# С отображением прогресса
curl --progress-bar \
  -F "file=@firmware.bin" \
  -F "device_type=scales_bridge_tab5" \
  -F "version=1.1.0" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  "$OTA_SERVER/api/ota/admin/upload" | jq .
```

**Ответ:**
```json
{
  "success": true,
  "filename": "firmware.bin",
  "device_type": "scales_bridge_tab5",
  "version": "1.1.0",
  "binary_path": "scales_bridge_tab5/v1.1.0.bin",
  "file_size": 524288,
  "file_hash": "abc123def456..."
}
```

### 2. Зарегистрировать прошивку в БД

```bash
curl -X POST "$OTA_SERVER/api/ota/admin/firmware" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "device_type": "scales_bridge_tab5",
    "version": "1.1.0",
    "build_number": 2,
    "filename": "firmware.bin",
    "file_size": 524288,
    "file_hash": "abc123def456...",
    "binary_path": "scales_bridge_tab5/v1.1.0.bin",
    "description": "Bug fixes and improvements",
    "release_notes": "- Fixed Wi-Fi reconnection\n- Improved memory usage",
    "is_stable": false,
    "min_current_version": "1.0.0"
  }'
```

### 3. Список всех прошивок

```bash
# Все прошивки
curl "$OTA_SERVER/api/ota/admin/firmware" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# Только для конкретного типа устройства
curl "$OTA_SERVER/api/ota/admin/firmware?device_type=scales_bridge_tab5" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# С пагинацией
curl "$OTA_SERVER/api/ota/admin/firmware?skip=0&limit=10" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .
```

### 4. Получить детали прошивки

```bash
curl "$OTA_SERVER/api/ota/admin/firmware/456" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .
```

### 5. Обновить метаданные прошивки

```bash
# Пометить как стабильную версию
curl -X PATCH "$OTA_SERVER/api/ota/admin/firmware/456" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "is_stable": true
  }'

# Обновить описание
curl -X PATCH "$OTA_SERVER/api/ota/admin/firmware/456" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "description": "New description",
    "release_notes": "New release notes"
  }'

# Отключить прошивку
curl -X PATCH "$OTA_SERVER/api/ota/admin/firmware/456" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "is_active": false
  }'
```

### 6. Деактивировать прошивку

```bash
curl -X DELETE "$OTA_SERVER/api/ota/admin/firmware/456" \
  -H "Authorization: Bearer $JWT_TOKEN"
```

### 7. Получить логи OTA операций

```bash
# Все логи
curl "$OTA_SERVER/api/ota/admin/logs" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# Логи для конкретного устройства
curl "$OTA_SERVER/api/ota/admin/logs?device_id=123" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# Логи для конкретной версии
curl "$OTA_SERVER/api/ota/admin/logs?firmware_id=456" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# Логи с конкретным статусом
curl "$OTA_SERVER/api/ota/admin/logs?status=failed" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .

# С пагинацией
curl "$OTA_SERVER/api/ota/admin/logs?skip=0&limit=100" \
  -H "Authorization: Bearer $JWT_TOKEN" | jq .
```

## Полный Workflow Пример

### Шаг 1: Загрузить и зарегистрировать новую версию

```bash
#!/bin/bash

set -e

SERVER="http://localhost:8000"
TOKEN="your_jwt_token"
FIRMWARE="firmware.bin"
DEVICE_TYPE="scales_bridge_tab5"
VERSION="1.1.0"

echo "1. Uploading firmware..."
UPLOAD_RESULT=$(curl -s \
  -F "file=@$FIRMWARE" \
  -F "device_type=$DEVICE_TYPE" \
  -F "version=$VERSION" \
  -H "Authorization: Bearer $TOKEN" \
  "$SERVER/api/ota/admin/upload")

echo "Upload result:"
echo $UPLOAD_RESULT | jq .

# Извлечь значения
BINARY_PATH=$(echo $UPLOAD_RESULT | jq -r '.binary_path')
FILE_SIZE=$(echo $UPLOAD_RESULT | jq -r '.file_size')
FILE_HASH=$(echo $UPLOAD_RESULT | jq -r '.file_hash')
FILENAME=$(echo $UPLOAD_RESULT | jq -r '.filename')

echo ""
echo "2. Registering firmware..."
REGISTER_RESULT=$(curl -s -X POST "$SERVER/api/ota/admin/firmware" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"device_type\": \"$DEVICE_TYPE\",
    \"version\": \"$VERSION\",
    \"build_number\": 2,
    \"filename\": \"$FILENAME\",
    \"file_size\": $FILE_SIZE,
    \"file_hash\": \"$FILE_HASH\",
    \"binary_path\": \"$BINARY_PATH\",
    \"description\": \"Version $VERSION release\",
    \"is_stable\": false,
    \"min_current_version\": \"1.0.0\"
  }")

echo "Register result:"
echo $REGISTER_RESULT | jq .

FIRMWARE_ID=$(echo $REGISTER_RESULT | jq -r '.id')

echo ""
echo "3. Marking as stable..."
curl -s -X PATCH "$SERVER/api/ota/admin/firmware/$FIRMWARE_ID" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"is_stable": true}' | jq .

echo ""
echo "✓ Firmware $VERSION ready for deployment!"
```

### Шаг 2: Проверить на устройстве

```bash
# На ESP32 или тестовом окружении
curl -X POST "$OTA_SERVER/api/ota/check" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "device_type": "scales_bridge_tab5",
    "current_version": "1.0.0",
    "current_build": 1
  }'
```

### Шаг 3: Скачать обновление

```bash
# Если доступно обновление
curl -o firmware_update.bin \
  "http://server.com/api/ota/download/456"

# Проверить хеш
sha256sum firmware_update.bin
# Должен совпадать с file_hash из check ответа
```

### Шаг 4: Отправить статус

```bash
# Начало скачивания
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "downloading"
  }'

# ... скачивание файла ...

# Завершение
curl -X POST "$OTA_SERVER/api/ota/status" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 456,
    "status": "success"
  }'
```

## JSON Pretty Print Утилита

Для красивого вывода JSON используйте `jq`:

```bash
# Вывести с отступами и цветом
curl ... | jq .

# Вывести только конкретные поля
curl ... | jq '.[] | {id, version, is_stable}'

# Фильтровать результаты
curl ... | jq '.[] | select(.is_stable == true)'

# Сортировать
curl ... | jq 'sort_by(.version)'
```

## Common Error Responses

```json
// 404 - Firmware not found
{"detail": "Firmware not found or inactive"}

// 409 - Version already exists
{"detail": "Firmware version already exists"}

// 400 - Invalid request
{"detail": "device_type and version are required"}

// 401 - Unauthorized
{"detail": "Not authenticated"}

// 403 - Forbidden
{"detail": "Not enough permissions"}

// 500 - Server error
{"detail": "Error checking firmware update"}
```

## Tips & Tricks

### Получить текущее время сервера
```bash
curl "$OTA_SERVER/health" | jq .
```

### Экспортировать результаты в CSV
```bash
curl "$OTA_SERVER/api/ota/admin/firmware" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  | jq -r '.[] | [.device_type, .version, .is_stable] | @csv' \
  > firmware_list.csv
```

### Найти все неудачные обновления
```bash
curl "$OTA_SERVER/api/ota/admin/logs?status=failed" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  | jq '.[] | select(.error_message != null)'
```

### Автоматическая проверка всех устройств
```bash
for device_id in {1..100}; do
  curl -s -X POST "$OTA_SERVER/api/ota/check" \
    -H "Content-Type: application/json" \
    -d "{\"device_id\": $device_id, \"device_type\": \"scales_bridge_tab5\", \"current_version\": \"1.0.0\", \"current_build\": 1}" \
    | jq . >> check_results.log
done
```
