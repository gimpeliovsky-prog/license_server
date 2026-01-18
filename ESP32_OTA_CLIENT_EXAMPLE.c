/**
 * ESP32 OTA Update Client Example
 * 
 * Использование OTA-сервера из проекта license_server
 * для обновления микропрограммы ESP32 устройств
 * 
 * Предназначен для интеграции в проект scales_bridge
 */

#include "esp_ota_ops.h"
#include "esp_https_client.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "cJSON.h"
#include "nvs_flash.h"

#include <string.h>
#include <stdlib.h>

#define TAG "OTA_CLIENT"

// Конфигурация
#define OTA_SERVER_URL "https://your-license-server.com"
#define OTA_DEVICE_TYPE "scales_bridge_tab5"
#define OTA_CHECK_INTERVAL_SEC (24 * 3600)  // Проверять раз в день

typedef struct {
    uint32_t device_id;
    const char *server_url;
    const char *device_type;
    const char *current_version;
    uint32_t current_build;
} ota_config_t;

typedef struct {
    uint32_t firmware_id;
    const char *version;
    uint32_t build_number;
    const char *download_url;
    const char *file_hash;
    uint32_t file_size;
} ota_firmware_info_t;

/**
 * Отправить статус OTA операции на сервер
 */
static esp_err_t ota_report_status(
    const ota_config_t *config,
    uint32_t firmware_id,
    const char *status,
    uint32_t bytes_downloaded,
    const char *error_message)
{
    ESP_LOGI(TAG, "Reporting OTA status: %s", status);
    
    // Создать JSON запрос
    cJSON *request = cJSON_CreateObject();
    cJSON_AddNumberToObject(request, "device_id", config->device_id);
    cJSON_AddNumberToObject(request, "firmware_id", firmware_id);
    cJSON_AddStringToObject(request, "status", status);
    cJSON_AddNumberToObject(request, "bytes_downloaded", bytes_downloaded);
    
    if (error_message) {
        cJSON_AddStringToObject(request, "error_message", error_message);
    }
    
    char *request_str = cJSON_Print(request);
    cJSON_Delete(request);
    
    // Отправить на сервер
    esp_http_client_config_t http_config = {
        .url = OTA_SERVER_URL "/api/ota/status",
        .method = HTTP_METHOD_POST,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 10000,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, request_str, strlen(request_str));
    
    esp_err_t err = esp_http_client_perform(client);
    
    if (err == ESP_OK) {
        int status_code = esp_http_client_get_status_code(client);
        if (status_code == 200) {
            ESP_LOGI(TAG, "Status reported successfully");
        } else {
            ESP_LOGW(TAG, "Server returned status code: %d", status_code);
        }
    } else {
        ESP_LOGE(TAG, "Failed to report status: %s", esp_err_to_name(err));
    }
    
    esp_http_client_cleanup(client);
    free(request_str);
    
    return err;
}

/**
 * Проверить доступность обновлений
 */
static esp_err_t ota_check_for_updates(
    const ota_config_t *config,
    ota_firmware_info_t *firmware_info)
{
    ESP_LOGI(TAG, "Checking for firmware updates...");
    
    // Создать JSON запрос
    cJSON *request = cJSON_CreateObject();
    cJSON_AddNumberToObject(request, "device_id", config->device_id);
    cJSON_AddStringToObject(request, "device_type", config->device_type);
    cJSON_AddStringToObject(request, "current_version", config->current_version);
    cJSON_AddNumberToObject(request, "current_build", config->current_build);
    
    char *request_str = cJSON_Print(request);
    cJSON_Delete(request);
    
    // Отправить запрос
    esp_http_client_config_t http_config = {
        .url = OTA_SERVER_URL "/api/ota/check",
        .method = HTTP_METHOD_POST,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, request_str, strlen(request_str));
    
    esp_err_t err = esp_http_client_perform(client);
    
    if (err == ESP_OK) {
        int status_code = esp_http_client_get_status_code(client);
        
        if (status_code == 200) {
            // Прочитать ответ
            int content_length = esp_http_client_get_content_length(client);
            char *response_buffer = malloc(content_length + 1);
            
            int bytes_read = esp_http_client_read_response(client, (uint8_t *)response_buffer, content_length);
            response_buffer[bytes_read] = '\0';
            
            // Парсить JSON ответ
            cJSON *response = cJSON_Parse(response_buffer);
            
            cJSON *update_available = cJSON_GetObjectItemCaseInsensitive(response, "update_available");
            if (update_available && update_available->type == cJSON_True) {
                firmware_info->firmware_id = cJSON_GetObjectItem(response, "firmware_id")->valueint;
                firmware_info->version = cJSON_GetObjectItem(response, "version")->valuestring;
                firmware_info->build_number = cJSON_GetObjectItem(response, "build_number")->valueint;
                firmware_info->download_url = cJSON_GetObjectItem(response, "download_url")->valuestring;
                firmware_info->file_hash = cJSON_GetObjectItem(response, "file_hash")->valuestring;
                firmware_info->file_size = cJSON_GetObjectItem(response, "file_size")->valueint;
                
                ESP_LOGI(TAG, "Update available: v%s (build %d)", firmware_info->version, firmware_info->build_number);
                cJSON_Delete(response);
                free(response_buffer);
                esp_http_client_cleanup(client);
                free(request_str);
                return ESP_OK;  // Обновление доступно
            } else {
                ESP_LOGI(TAG, "No updates available");
            }
            
            cJSON_Delete(response);
            free(response_buffer);
        } else {
            ESP_LOGW(TAG, "Server returned status code: %d", status_code);
        }
    } else {
        ESP_LOGE(TAG, "Failed to check updates: %s", esp_err_to_name(err));
    }
    
    esp_http_client_cleanup(client);
    free(request_str);
    
    return err;
}

/**
 * Скачать и установить прошивку
 */
static esp_err_t ota_download_and_install(
    const ota_config_t *config,
    const ota_firmware_info_t *firmware_info)
{
    ESP_LOGI(TAG, "Starting firmware download from %s", firmware_info->download_url);
    
    // Отправить статус "downloading"
    ota_report_status(config, firmware_info->firmware_id, "downloading", 0, NULL);
    
    // Инициализировать OTA
    const esp_partition_t *update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) {
        ESP_LOGE(TAG, "No OTA partition found");
        ota_report_status(config, firmware_info->firmware_id, "failed",
                         0, "No OTA partition found");
        return ESP_FAIL;
    }
    
    esp_ota_handle_t update_handle = 0;
    esp_err_t err = esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN, &update_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
        ota_report_status(config, firmware_info->firmware_id, "failed",
                         0, "OTA begin failed");
        return err;
    }
    
    // Скачать файл
    esp_http_client_config_t http_config = {
        .url = firmware_info->download_url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 60000,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    
    err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open HTTP connection: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        esp_ota_abort(update_handle);
        ota_report_status(config, firmware_info->firmware_id, "failed",
                         0, "HTTP connection failed");
        return err;
    }
    
    uint32_t bytes_downloaded = 0;
    uint8_t buffer[4096];
    int bytes_read;
    uint32_t last_report = 0;
    
    while (1) {
        bytes_read = esp_http_client_read(client, buffer, sizeof(buffer));
        
        if (bytes_read < 0) {
            ESP_LOGE(TAG, "Error during download");
            esp_http_client_cleanup(client);
            esp_ota_abort(update_handle);
            ota_report_status(config, firmware_info->firmware_id, "failed",
                             bytes_downloaded, "Download error");
            return ESP_FAIL;
        }
        
        if (bytes_read == 0) {
            break;  // Скачивание завершено
        }
        
        // Записать данные в OTA раздел
        err = esp_ota_write(update_handle, buffer, bytes_read);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
            esp_http_client_cleanup(client);
            esp_ota_abort(update_handle);
            ota_report_status(config, firmware_info->firmware_id, "failed",
                             bytes_downloaded, "OTA write failed");
            return err;
        }
        
        bytes_downloaded += bytes_read;
        
        // Отправлять статус каждые 100KB
        if (bytes_downloaded - last_report > 100 * 1024) {
            ota_report_status(config, firmware_info->firmware_id, "downloading",
                             bytes_downloaded, NULL);
            last_report = bytes_downloaded;
            ESP_LOGI(TAG, "Downloaded: %d / %d bytes", bytes_downloaded, firmware_info->file_size);
        }
    }
    
    esp_http_client_cleanup(client);
    
    // Завершить OTA
    err = esp_ota_end(update_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
        ota_report_status(config, firmware_info->firmware_id, "failed",
                         bytes_downloaded, "OTA end failed");
        return err;
    }
    
    // Установить как активный раздел
    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
        ota_report_status(config, firmware_info->firmware_id, "failed",
                         bytes_downloaded, "Set boot partition failed");
        return err;
    }
    
    ESP_LOGI(TAG, "OTA update completed successfully");
    ota_report_status(config, firmware_info->firmware_id, "success",
                     bytes_downloaded, NULL);
    
    // Перезагрузиться (в production коде)
    // esp_restart();
    
    return ESP_OK;
}

/**
 * Главная функция проверки и обновления
 * Должна вызываться периодически
 */
esp_err_t ota_check_and_update(const ota_config_t *config)
{
    ota_firmware_info_t firmware_info = {0};
    
    // Проверить обновления
    esp_err_t err = ota_check_for_updates(config, &firmware_info);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to check for updates");
        return err;
    }
    
    // Если обновление доступно, скачать и установить
    if (firmware_info.firmware_id > 0) {
        err = ota_download_and_install(config, &firmware_info);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to download and install firmware");
            return err;
        }
    }
    
    return ESP_OK;
}

/**
 * Пример использования в main приложении
 */
void app_main_ota_example(void)
{
    // Инициализировать NVS (для хранения настроек)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    
    // Конфигурация OTA
    ota_config_t ota_config = {
        .device_id = 123,  // ID вашего устройства
        .server_url = OTA_SERVER_URL,
        .device_type = OTA_DEVICE_TYPE,
        .current_version = "1.0.0",  // Получить из версии прошивки
        .current_build = 1,
    };
    
    // Получить текущую версию из приложения
    esp_app_desc_t app_desc;
    esp_ota_get_partition_description(
        esp_ota_get_running_partition(),
        &app_desc);
    
    ota_config.current_version = app_desc.version;
    
    // Периодически проверять обновления (в отдельной задаче)
    while (1) {
        ota_check_and_update(&ota_config);
        vTaskDelay(OTA_CHECK_INTERVAL_SEC * 1000 / portTICK_PERIOD_MS);
    }
}
