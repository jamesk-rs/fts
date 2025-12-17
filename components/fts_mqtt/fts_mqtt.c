/**
 * FTS MQTT Client Implementation
 *
 * Publishes FTM reports and timing metrics to RL platform,
 * receives period corrections via MQTT.
 */

#include "fts_mqtt.h"
#include "mqtt_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "cJSON.h"
#include <string.h>
#include <stdlib.h>
#include <inttypes.h>

static const char *TAG = "fts_mqtt";

// Client state
static esp_mqtt_client_handle_t s_mqtt_client = NULL;
static char s_device_id[32] = {0};
static fts_mqtt_control_cb_t s_control_callback = NULL;
static bool s_connected = false;

// Topic buffers
static char s_topic_ftm[64];
static char s_topic_metrics[64];
static char s_topic_control[64];

/**
 * MQTT event handler
 */
static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
        case MQTT_EVENT_CONNECTED:
            ESP_LOGI(TAG, "Connected to MQTT broker");
            s_connected = true;

            // Subscribe to control topic
            int msg_id = esp_mqtt_client_subscribe(s_mqtt_client, s_topic_control, 1);
            ESP_LOGI(TAG, "Subscribed to %s, msg_id=%d", s_topic_control, msg_id);
            break;

        case MQTT_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "Disconnected from MQTT broker");
            s_connected = false;
            break;

        case MQTT_EVENT_SUBSCRIBED:
            ESP_LOGD(TAG, "Subscribed, msg_id=%d", event->msg_id);
            break;

        case MQTT_EVENT_PUBLISHED:
            ESP_LOGD(TAG, "Published, msg_id=%d", event->msg_id);
            break;

        case MQTT_EVENT_DATA:
            // Check if this is our control topic
            if (event->topic_len > 0 &&
                strncmp(event->topic, s_topic_control, event->topic_len) == 0) {

                // Parse JSON payload
                cJSON *json = cJSON_ParseWithLength(event->data, event->data_len);
                if (json) {
                    cJSON *correction = cJSON_GetObjectItem(json, "period_correction_fp16");
                    cJSON *phase_error = cJSON_GetObjectItem(json, "phase_error_ns");
                    cJSON *gain = cJSON_GetObjectItem(json, "gain_K");

                    if (correction && s_control_callback) {
                        int32_t corr_value = (int32_t)correction->valuedouble;
                        float pe = phase_error ? (float)phase_error->valuedouble : 0.0f;
                        float k = gain ? (float)gain->valuedouble : 0.0f;

                        ESP_LOGI(TAG, "Control: correction=%ld, phase_error=%.1f, K=%.3f",
                                 (long)corr_value, pe, k);

                        s_control_callback(corr_value, pe, k);
                    }

                    cJSON_Delete(json);
                } else {
                    ESP_LOGW(TAG, "Failed to parse control JSON");
                }
            }
            break;

        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "MQTT error");
            if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                if (event->error_handle->esp_transport_sock_errno != 0) {
                    ESP_LOGE(TAG, "Transport error: %s (errno=%d)",
                             strerror(event->error_handle->esp_transport_sock_errno),
                             event->error_handle->esp_transport_sock_errno);
                }
                if (event->error_handle->esp_tls_last_esp_err != 0) {
                    ESP_LOGE(TAG, "TLS error: 0x%x",
                             (unsigned int)event->error_handle->esp_tls_last_esp_err);
                }
            } else if (event->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                ESP_LOGE(TAG, "Connection refused, error: 0x%x",
                         event->error_handle->connect_return_code);
            }
            break;

        default:
            ESP_LOGD(TAG, "MQTT event: %d", event->event_id);
            break;
    }
}

esp_err_t fts_mqtt_init(const fts_mqtt_config_t *config)
{
    if (config == NULL || config->broker_uri == NULL || config->device_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    // Store device ID and callback
    strncpy(s_device_id, config->device_id, sizeof(s_device_id) - 1);
    s_control_callback = config->ctrl_cb;

    // Build topic names
    snprintf(s_topic_ftm, sizeof(s_topic_ftm), "fts/%s/ftm", s_device_id);
    snprintf(s_topic_metrics, sizeof(s_topic_metrics), "fts/%s/metrics", s_device_id);
    snprintf(s_topic_control, sizeof(s_topic_control), "fts/%s/control", s_device_id);

    ESP_LOGI(TAG, "Initializing MQTT client for device: %s", s_device_id);
    ESP_LOGI(TAG, "Broker: %s", config->broker_uri);

    // Configure MQTT client
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker = {
            .address = {
                .uri = config->broker_uri,
            },
        },
        .session = {
            .keepalive = 60,
        },
        .network = {
            .reconnect_timeout_ms = 5000,
        },
    };

    s_mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    if (s_mqtt_client == NULL) {
        ESP_LOGE(TAG, "Failed to initialize MQTT client");
        return ESP_FAIL;
    }

    // Register event handler
    esp_err_t ret = esp_mqtt_client_register_event(s_mqtt_client, ESP_EVENT_ANY_ID,
                                                    mqtt_event_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to register event handler");
        esp_mqtt_client_destroy(s_mqtt_client);
        s_mqtt_client = NULL;
        return ret;
    }

    ESP_LOGI(TAG, "MQTT client initialized");
    return ESP_OK;
}

esp_err_t fts_mqtt_start(void)
{
    if (s_mqtt_client == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG, "Starting MQTT client...");
    return esp_mqtt_client_start(s_mqtt_client);
}

esp_err_t fts_mqtt_stop(void)
{
    if (s_mqtt_client == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    s_connected = false;
    return esp_mqtt_client_stop(s_mqtt_client);
}

bool fts_mqtt_is_connected(void)
{
    return s_connected;
}

esp_err_t fts_mqtt_publish_ftm(int64_t ts_us, uint32_t session_id,
                                int64_t rtt_ps, int8_t rssi,
                                int64_t t1, int64_t t2,
                                int64_t t3, int64_t t4)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    cJSON *json = cJSON_CreateObject();
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // Add fields - use strings for 64-bit integers to preserve precision
    char buf[24];
    cJSON_AddNumberToObject(json, "ts", (double)ts_us / 1e6);
    cJSON_AddNumberToObject(json, "session_id", session_id);
    snprintf(buf, sizeof(buf), "%" PRId64, rtt_ps);
    cJSON_AddStringToObject(json, "rtt_ps", buf);
    cJSON_AddNumberToObject(json, "rssi", rssi);
    snprintf(buf, sizeof(buf), "%" PRId64, t1);
    cJSON_AddStringToObject(json, "t1", buf);
    snprintf(buf, sizeof(buf), "%" PRId64, t2);
    cJSON_AddStringToObject(json, "t2", buf);
    snprintf(buf, sizeof(buf), "%" PRId64, t3);
    cJSON_AddStringToObject(json, "t3", buf);
    snprintf(buf, sizeof(buf), "%" PRId64, t4);
    cJSON_AddStringToObject(json, "t4", buf);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_ftm,
                                          payload, 0, 1, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t fts_mqtt_publish_metrics(int64_t ts_us, int64_t cycle_counter,
                                    int32_t period_ticks, int32_t period_delta)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    cJSON *json = cJSON_CreateObject();
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // Add fields
    cJSON_AddNumberToObject(json, "ts", (double)ts_us / 1e6);
    cJSON_AddNumberToObject(json, "cycle_counter", (double)cycle_counter);
    cJSON_AddNumberToObject(json, "period_ticks", period_ticks);
    cJSON_AddNumberToObject(json, "period_delta", period_delta);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // QoS 0 for metrics (high frequency, loss acceptable)
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_metrics,
                                          payload, 0, 0, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}
