/**
 * FTS MQTT Client
 *
 * MQTT client for publishing FTM reports and timing metrics to the
 * RL platform, and receiving period corrections.
 */

#pragma once

#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Callback for receiving period corrections from RL engine
 *
 * @param period_correction_fp16 Period correction in FP16 format
 * @param phase_error_ns Phase error reported by RL engine (for logging)
 * @param gain_K Current RL gain (for logging)
 */
typedef void (*fts_mqtt_control_cb_t)(int32_t period_correction_fp16,
                                       float phase_error_ns,
                                       float gain_K);

/**
 * MQTT configuration
 */
typedef struct {
    const char *broker_uri;      // e.g., "mqtt://192.168.1.100:1883"
    const char *device_id;       // e.g., "slave1" or "master"
    fts_mqtt_control_cb_t ctrl_cb;  // Callback for control messages
} fts_mqtt_config_t;

/**
 * Initialize FTS MQTT client
 *
 * @param config MQTT configuration
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_init(const fts_mqtt_config_t *config);

/**
 * Start MQTT client (connect to broker)
 *
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_start(void);

/**
 * Stop MQTT client
 *
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_stop(void);

/**
 * Check if MQTT is connected
 *
 * @return true if connected
 */
bool fts_mqtt_is_connected(void);

/**
 * Publish FTM report
 *
 * @param ts_us Timestamp in microseconds (from esp_timer_get_time)
 * @param session_id FTM session ID
 * @param rtt_ps Round-trip time in picoseconds
 * @param rssi RSSI value
 * @param t1 T1 timer value
 * @param t2 T2 timer value
 * @param t3 T3 timer value
 * @param t4 T4 timer value
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_ftm(int64_t ts_us, uint32_t session_id,
                                int32_t rtt_ps, int8_t rssi,
                                uint32_t t1, uint32_t t2,
                                uint32_t t3, uint32_t t4);

/**
 * Publish timing metrics
 *
 * @param ts_us Timestamp in microseconds
 * @param cycle_counter Current cycle counter
 * @param period_ticks Current period in timer ticks
 * @param period_delta Period delta from last alignment
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_metrics(int64_t ts_us, int64_t cycle_counter,
                                    int32_t period_ticks, int32_t period_delta);

#ifdef __cplusplus
}
#endif
