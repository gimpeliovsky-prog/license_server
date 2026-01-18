# Следующие шаги после внедрения OTA сервера

После выполнения всех создания компонентов OTA-сервера, следуйте этим шагам для полного развертывания.

## 1️⃣ Применить миграцию БД (ОБЯЗАТЕЛЬНО)

```bash
cd /path/to/license_server

# Проверить статус миграций
python -m alembic current

# Применить все миграции
python -m alembic upgrade head

# Проверить результат
python -c "from app.models.firmware import Firmware; print('✓ Tables created successfully')"
```

**Что это сделает:**
- Создаст таблицу `firmware` с 17 полями
- Создаст таблицу `device_ota_log` с 11 полями
- Создаст индексы для быстрого поиска

## 2️⃣ Подготовить первую прошивку

### Собрать прошивку из scales_bridge

```bash
cd /path/to/scales_bridge/tab5

# Собрать проект
idf.py fullclean
idf.py build

# Результат будет в:
# build/app.bin или build/firmware.bin

# Проверить размер и хеш
ls -lh build/*.bin
sha256sum build/*.bin

# Пример вывода:
# firmware.bin: 456 KB
# SHA256: abc123def456...
```

### Загрузить на OTA сервер

#### Вариант A: Используя Python скрипт (рекомендуется)

```bash
cd /path/to/license_server

# Получить JWT токен (если не имеете)
# Через веб-интерфейс или API

python scripts/ota_management.py \
  --server http://localhost:8000 \
  --token "YOUR_JWT_TOKEN_HERE" \
  upload \
  --file /path/to/scales_bridge/tab5/build/firmware.bin \
  --device-type scales_bridge_tab5 \
  --version 1.0.0
```

#### Вариант B: Используя curl

```bash
# 1. Загрузить файл
curl -F "file=@build/firmware.bin" \
     -F "device_type=scales_bridge_tab5" \
     -F "version=1.0.0" \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:8000/api/ota/admin/upload

# Сохранить результат (особенно file_hash)
# Вывод:
# {
#   "success": true,
#   "filename": "firmware.bin",
#   "binary_path": "scales_bridge_tab5/v1.0.0.bin",
#   "file_size": 456789,
#   "file_hash": "abc123def456..."
# }

# 2. Зарегистрировать в БД
curl -X POST http://localhost:8000/api/ota/admin/firmware \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "device_type": "scales_bridge_tab5",
       "version": "1.0.0",
       "build_number": 1,
       "filename": "firmware.bin",
       "file_size": 456789,
       "file_hash": "abc123def456...",
       "binary_path": "scales_bridge_tab5/v1.0.0.bin",
       "description": "Initial release - version 1.0.0",
       "is_stable": false
     }'

# 3. Пометить как стабильную (когда протестирована)
curl -X PATCH http://localhost:8000/api/ota/admin/firmware/1 \
     -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"is_stable": true}'
```

## 3️⃣ Тестировать API эндпоинты

### Проверить наличие обновлений (как будет делать ESP32)

```bash
curl -X POST http://localhost:8000/api/ota/check \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "device_type": "scales_bridge_tab5",
    "current_version": "0.9.0",
    "current_build": 0
  }' | jq .

# Ожидаемый результат:
# {
#   "update_available": true,
#   "firmware_id": 1,
#   "version": "1.0.0",
#   "build_number": 1,
#   "description": "Initial release - version 1.0.0",
#   "download_url": "/api/ota/download/1",
#   "file_hash": "abc123def456...",
#   "file_size": 456789
# }
```

### Скачать прошивку

```bash
curl http://localhost:8000/api/ota/download/1 \
  -o firmware_downloaded.bin

# Проверить
ls -lh firmware_downloaded.bin
sha256sum firmware_downloaded.bin
# Должен совпадать с file_hash из check ответа
```

### Отправить статус обновления

```bash
curl -X POST http://localhost:8000/api/ota/status \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "firmware_id": 1,
    "status": "downloading",
    "bytes_downloaded": 0
  }'

# Ожидаемый результат:
# {
#   "success": true,
#   "log_id": 1,
#   "status": "downloading"
# }
```

## 4️⃣ Интегрировать на ESP32 устройстве

### Скопировать файл

```bash
# Копировать пример кода в проект scales_bridge
cp ESP32_OTA_CLIENT_EXAMPLE.c /path/to/scales_bridge/tab5/main/

# Или встроить функции в существующий main.cpp
```

### Адаптировать для вашего проекта

Отредактировать конфигурацию в коде:

```c
// В файле или в конфигурации
#define OTA_SERVER_URL "https://your-license-server.com"  // ← Ваш сервер
#define OTA_DEVICE_TYPE "scales_bridge_tab5"              // ← Тип устройства
#define OTA_CHECK_INTERVAL_SEC (24 * 3600)                // ← Интервал проверки

// Получить device_id из конфига устройства
uint32_t device_id = get_device_id_from_nvs();
```

### Интегрировать в main цикл

```c
void app_main(void) {
    // ... остальная инициализация ...
    
    // Создать задачу для проверки OTA
    xTaskCreate(ota_check_task, "ota_check", 4096, NULL, 5, NULL);
}

void ota_check_task(void *param) {
    ota_config_t config = {
        .device_id = get_device_id(),
        .device_type = "scales_bridge_tab5",
        .server_url = OTA_SERVER_URL,
        .current_version = APP_VERSION,  // Из app_main.cpp или config.h
        .current_build = BUILD_NUMBER,
    };
    
    while (1) {
        ESP_LOGI(TAG, "Checking for OTA updates...");
        ota_check_and_update(&config);
        vTaskDelay(OTA_CHECK_INTERVAL_SEC * 1000 / portTICK_PERIOD_MS);
    }
}
```

## 5️⃣ Тестирование на реальном устройстве

### Тест 1: Проверить логирование

На устройстве в UART логах должно быть:

```
[OTA_CLIENT] Checking for firmware updates...
[OTA_CLIENT] Sending check request to: https://your-server/api/ota/check
[OTA_CLIENT] Received response: update_available=1, version=1.0.0
[OTA_CLIENT] Starting firmware download...
[OTA_CLIENT] Downloaded: 100 / 456789 bytes
[OTA_CLIENT] Downloaded: 200 / 456789 bytes
...
[OTA_CLIENT] Download completed
[OTA_CLIENT] Installing firmware...
[OTA_CLIENT] OTA update completed successfully
```

### Тест 2: Проверить в БД

```bash
# Проверить логи на сервере
curl http://localhost:8000/api/ota/admin/logs \
  -H "Authorization: Bearer TOKEN" | jq '.[] | {device_id, status, created_at}'

# Должны быть записи с status: downloading → success
```

### Тест 3: Проверить версию после обновления

После перезагрузки устройства:

```bash
# На устройстве выполнить:
curl -X POST http://localhost:8000/api/ota/check \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 123,
    "device_type": "scales_bridge_tab5",
    "current_version": "1.0.0",  # ← должна измениться
    "current_build": 1
  }'

# Результат: update_available=false (обновление уже установлено)
```

## 6️⃣ Мониторинг и управление

### Команды для управления

```bash
# Список всех версий
python scripts/ota_management.py list --device-type scales_bridge_tab5

# Просмотр всех попыток обновления
python scripts/ota_management.py logs

# Просмотр только неудачных попыток
python scripts/ota_management.py logs --status failed

# Просмотр логов конкретного устройства
python scripts/ota_management.py logs --device-id 123
```

### Загрузка новой версии

```bash
# После сборки новой версии
idf.py build

# Загрузить
python scripts/ota_management.py upload \
  --file build/firmware.bin \
  --device-type scales_bridge_tab5 \
  --version 1.1.0 \
  --token TOKEN

# После тестирования пометить как стабильную
python scripts/ota_management.py update \
  --id 2 \
  --stable
```

## 7️⃣ Обслуживание

### Очистка старых логов

```python
# scripts/cleanup_old_logs.py
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.firmware import DeviceOTALog

# Удалить логи старше 30 дней
db = sessionmaker(bind=engine)()
cutoff = datetime.utcnow() - timedelta(days=30)

deleted = db.query(DeviceOTALog).filter(
    DeviceOTALog.created_at < cutoff
).delete()

db.commit()
print(f"Deleted {deleted} old OTA logs")
```

### Архивирование старых версий

```bash
# После выпуска новой версии старую можно архивировать
mkdir -p firmware/archive/scales_bridge_tab5

# Переместить файл
mv firmware/scales_bridge_tab5/v1.0.0.bin firmware/archive/scales_bridge_tab5/

# Деактивировать в БД
python scripts/ota_management.py update --id 1 --inactive
```

## 🐛 Решение проблем

### Проблема: Миграция не применяется

```bash
# Проверить статус
python -m alembic current
python -m alembic history

# Если не видит новую миграцию, проверить:
ls -la alembic/versions/

# Должен быть файл: 0004_firmware_ota.py
```

### Проблема: Устройство не видит обновление

1. Проверить БД:
```python
from app.models.firmware import Firmware
from app.db.session import SessionLocal

db = SessionLocal()
fw = db.query(Firmware).filter(
    Firmware.device_type == "scales_bridge_tab5"
).first()

print(f"Version: {fw.version}")
print(f"Is Stable: {fw.is_stable}")
print(f"Is Active: {fw.is_active}")
```

2. Проверить логи сервера
3. Проверить логи устройства

### Проблема: Файл не скачивается

```bash
# Проверить наличие файла
ls -la firmware/scales_bridge_tab5/

# Проверить права
chmod 644 firmware/scales_bridge_tab5/*.bin

# Проверить хеш
sha256sum firmware/scales_bridge_tab5/v1.0.0.bin
# Должен совпадать с file_hash в БД
```

## ✅ Checklist завершения

- [ ] Миграция БД применена
- [ ] Первая версия прошивки загружена
- [ ] API эндпоинты протестированы
- [ ] ESP32 код интегрирован
- [ ] Устройство проверило обновление
- [ ] Логирование работает
- [ ] Версия успешно обновилась на устройстве
- [ ] Логи сохранились в БД
- [ ] Вторая версия загружена и протестирована

## 📞 Поддержка

Если возникли вопросы:

1. Проверьте логи:
   ```bash
   tail -f server.log
   tail -f device_uart.log
   ```

2. Проверьте документацию:
   - OTA_SERVER_README.md
   - OTA_INTEGRATION_GUIDE.md
   - OTA_API_QUICK_REFERENCE.md

3. Проверьте базу данных:
   ```bash
   # SQLite
   sqlite3 app.db "SELECT * FROM firmware;"
   sqlite3 app.db "SELECT * FROM device_ota_log LIMIT 10;"
   ```

4. Протестируйте API вручную:
   ```bash
   curl http://localhost:8000/api/ota/admin/firmware \
     -H "Authorization: Bearer TOKEN"
   ```

---

🎉 **OTA сервер готов к использованию!**

После выполнения всех шагов:
- ✅ OTA сервер полностью функционален
- ✅ Устройства могут обновляться
- ✅ История обновлений отслеживается
- ✅ Всё залогировано и отслеживается

**Удачи с развертыванием! 🚀**
