/**
 * FTS MQTT Client Implementation
 *
 * Publishes FTM reports and timing metrics to RL platform,
 * receives period corrections via MQTT.
 */

#include "fts_mqtt.h"
#include "ftm.h"
#include "mqtt_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "cJSON.h"
#include <string.h>
#include <stdlib.h>
#include <inttypes.h>
#include <stdint.h>

static const char *TAG = "fts_mqtt";

// Client state
static esp_mqtt_client_handle_t s_mqtt_client = NULL;
static char s_device_id[32] = {0};
static fts_mqtt_control_cb_t s_control_callback = NULL;
static bool s_connected = false;

// Topic buffers
static char s_topic_ftm[64];
static char s_topic_ftm_stats[64];
static char s_topic_dtc_request[64];
static char s_topic_crm_stats[64];
static char s_topic_dtr_feedback[64];
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
                    // Handle wifi_disconnect command
                    cJSON *wifi_disconnect = cJSON_GetObjectItem(json, "wifi_disconnect");
                    if (wifi_disconnect && cJSON_IsTrue(wifi_disconnect)) {
                        ESP_LOGW(TAG, "Control: WiFi disconnect requested");
                        ftm_trigger_wifi_disconnect();
                    }

                    // Handle period correction
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
    snprintf(s_topic_ftm_stats, sizeof(s_topic_ftm_stats), "fts/%s/ftm_stats", s_device_id);
    snprintf(s_topic_dtc_request, sizeof(s_topic_dtc_request), "fts/%s/dtc_request", s_device_id);
    snprintf(s_topic_crm_stats, sizeof(s_topic_crm_stats), "fts/%s/crm_stats", s_device_id);
    snprintf(s_topic_dtr_feedback, sizeof(s_topic_dtr_feedback), "fts/%s/dtr_feedback", s_device_id);
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
    // RTT fits in int32, clamp and send as number for simpler handling
    int32_t rtt_clamped = (rtt_ps > INT32_MAX) ? INT32_MAX :
                          (rtt_ps < INT32_MIN) ? INT32_MIN : (int32_t)rtt_ps;
    cJSON_AddNumberToObject(json, "rtt_ps", rtt_clamped);
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

esp_err_t fts_mqtt_publish_dtc_request(int64_t ts_us,
                                        int64_t cycle_counter,
                                        int64_t local_ticks,
                                        uint32_t base_period,
                                        uint32_t base_period_frac)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    cJSON *json = cJSON_CreateObject();
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // Add fields - send 64-bit counters as strings to avoid overflow and JSON precision issues
    cJSON_AddNumberToObject(json, "ts", (double)ts_us / 1e6);
    char cycle_counter_str[21];
    snprintf(cycle_counter_str, sizeof(cycle_counter_str), "%lld", (long long)cycle_counter);
    cJSON_AddStringToObject(json, "cycle_counter", cycle_counter_str);
    char local_ticks_str[21];
    snprintf(local_ticks_str, sizeof(local_ticks_str), "%lld", (long long)local_ticks);
    cJSON_AddStringToObject(json, "local_ticks", local_ticks_str);
    cJSON_AddNumberToObject(json, "base_period", base_period);
    cJSON_AddNumberToObject(json, "base_period_frac", base_period_frac);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // QoS 1 for alignment request (important data)
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_dtc_request,
                                          payload, 0, 1, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t fts_mqtt_publish_ftm_stats(int64_t ts_us, uint32_t session_id,
                                      uint8_t status, uint8_t count,
                                      int64_t rtt_avg_ps, int64_t rtt_min_ps, int64_t rtt_max_ps,
                                      int32_t rssi_avg, int8_t rssi_min, int8_t rssi_max)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }

    cJSON *json = cJSON_CreateObject();
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // Add fields - RTT values clamped to int32 range
    cJSON_AddNumberToObject(json, "ts", (double)ts_us / 1e6);
    cJSON_AddNumberToObject(json, "session_id", session_id);
    cJSON_AddNumberToObject(json, "status", status);
    cJSON_AddNumberToObject(json, "count", count);

    // Clamp RTT values to int32 range
    int32_t rtt_avg_clamped = (rtt_avg_ps > INT32_MAX) ? INT32_MAX :
                              (rtt_avg_ps < INT32_MIN) ? INT32_MIN : (int32_t)rtt_avg_ps;
    int32_t rtt_min_clamped = (rtt_min_ps > INT32_MAX) ? INT32_MAX :
                              (rtt_min_ps < INT32_MIN) ? INT32_MIN : (int32_t)rtt_min_ps;
    int32_t rtt_max_clamped = (rtt_max_ps > INT32_MAX) ? INT32_MAX :
                              (rtt_max_ps < INT32_MIN) ? INT32_MIN : (int32_t)rtt_max_ps;

    cJSON_AddNumberToObject(json, "rtt_avg_ps", rtt_avg_clamped);
    cJSON_AddNumberToObject(json, "rtt_min_ps", rtt_min_clamped);
    cJSON_AddNumberToObject(json, "rtt_max_ps", rtt_max_clamped);
    cJSON_AddNumberToObject(json, "rssi_avg", rssi_avg);
    cJSON_AddNumberToObject(json, "rssi_min", rssi_min);
    cJSON_AddNumberToObject(json, "rssi_max", rssi_max);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // QoS 1 for stats (important summary data)
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_ftm_stats,
                                          payload, 0, 1, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t fts_mqtt_publish_crm_stats(int64_t ts_us, uint32_t samples,
                                      uint8_t new_samples, float r_squared,
                                      float std_ns, float ppm_lr)
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
    cJSON_AddNumberToObject(json, "samples", samples);
    cJSON_AddNumberToObject(json, "new_samples", new_samples);
    cJSON_AddNumberToObject(json, "r_squared", r_squared);
    cJSON_AddNumberToObject(json, "std_ns", std_ns);
    cJSON_AddNumberToObject(json, "ppm_lr", ppm_lr);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // QoS 1 for CRM stats (important regression data)
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_crm_stats,
                                          payload, 0, 1, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t fts_mqtt_publish_dtr_feedback(int64_t ts_us, int32_t period_ticks,
                                         int32_t period_ticks_delta,
                                         int32_t cycle_delta)
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
    cJSON_AddNumberToObject(json, "period_ticks", period_ticks);
    cJSON_AddNumberToObject(json, "period_ticks_delta", period_ticks_delta);
    cJSON_AddNumberToObject(json, "cycle_delta", cycle_delta);

    char *payload = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);

    if (payload == NULL) {
        return ESP_ERR_NO_MEM;
    }

    // QoS 1 for DTR feedback (important alignment data)
    int msg_id = esp_mqtt_client_publish(s_mqtt_client, s_topic_dtr_feedback,
                                          payload, 0, 1, 0);
    free(payload);

    return (msg_id >= 0) ? ESP_OK : ESP_FAIL;
}
