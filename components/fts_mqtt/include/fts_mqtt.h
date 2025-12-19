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
                                int64_t rtt_ps, int8_t rssi,
                                int64_t t1, int64_t t2,
                                int64_t t3, int64_t t4);

/**
 * Publish DTC alignment request
 *
 * @param ts_us Timestamp in microseconds
 * @param cycle_counter Aligned cycle counter
 * @param local_ticks Aligned local ticks
 * @param base_period Base period in timer ticks (integer part)
 * @param base_period_frac Base period FP16 fractional part (0-65535)
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_dtc_request(int64_t ts_us,
                                        int64_t cycle_counter,
                                        int64_t local_ticks,
                                        uint32_t base_period,
                                        uint32_t base_period_frac);

/**
 * Publish FTM session statistics
 *
 * @param ts_us Timestamp in microseconds
 * @param session_id FTM session ID
 * @param status FTM status code
 * @param count Number of valid entries
 * @param rtt_avg_ps Average RTT in picoseconds
 * @param rtt_min_ps Minimum RTT in picoseconds
 * @param rtt_max_ps Maximum RTT in picoseconds
 * @param rssi_avg Average RSSI
 * @param rssi_min Minimum RSSI
 * @param rssi_max Maximum RSSI
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_ftm_stats(int64_t ts_us, uint32_t session_id,
                                      uint8_t status, uint8_t count,
                                      int64_t rtt_avg_ps, int64_t rtt_min_ps, int64_t rtt_max_ps,
                                      int32_t rssi_avg, int8_t rssi_min, int8_t rssi_max);

/**
 * Publish CRM (Clock Relationship Model) statistics
 *
 * @param ts_us Timestamp in microseconds
 * @param samples Total samples in regression buffer
 * @param new_samples New samples added this update
 * @param r_squared R² goodness of fit (0.0 to 1.0)
 * @param std_ns Residual standard deviation in nanoseconds
 * @param ppm_lr Clock frequency offset in ppm (local/remote - 1)
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_crm_stats(int64_t ts_us, uint32_t samples,
                                      uint8_t new_samples, float r_squared,
                                      float std_ns, float ppm_lr);

/**
 * Publish DTR (Disciplined Timer Realtime) alignment feedback
 *
 * @param ts_us Timestamp in microseconds
 * @param period_ticks Actual period applied by ISR
 * @param period_ticks_delta Change from previous period
 * @param cycle_delta Change in cycle counter (normally 0)
 * @return ESP_OK on success
 */
esp_err_t fts_mqtt_publish_dtr_feedback(int64_t ts_us, int32_t period_ticks,
                                         int32_t period_ticks_delta,
                                         int32_t cycle_delta);

#ifdef __cplusplus
}
#endif
