/**
 * FTM - Fine Timing Measurement Module
 *
 * Manages WiFi connection and FTM sessions with master device.
 * Unwraps MAC clock timestamps and provides them to CRM.
 */

#pragma once

#include "esp_err.h"
#include <stdint.h>

// FTM session configuration
#define FTM_FRAMES_PER_SESSION 64    // Frames per FTM session (8/16/24/32/64)
#define FTM_PERIOD_MS 1000           // FTM measurement period in milliseconds

#ifdef __cplusplus
extern "C" {
#endif

/**
 * FTM timestamp entry (unwrapped to 64-bit picoseconds)
 */
typedef struct {
    uint64_t t1_ps;  // Master TX timestamp
    uint64_t t2_ps;  // Slave RX timestamp
    uint64_t t3_ps;  // Slave TX timestamp
    uint64_t t4_ps;  // Master RX timestamp
} ftm_entry_t;

/**
 * FTM session report
 */
typedef struct {
    uint32_t session_number;
    uint8_t entry_count;
    const ftm_entry_t *entries;
} ftm_report_t;

/**
 * Callback invoked when FTM report is ready
 * Called from FTM task context (not ISR)
 */
typedef void (*ftm_callback_t)(const ftm_report_t *report);

/**
 * Initialize FTM master in AP mode (WiFi AP with FTM responder)
 *
 * Master creates its own AP for slaves to connect. Use this when you want
 * an isolated FTM network without external connectivity.
 *
 * @param ssid AP SSID
 * @param password AP password
 * @param channel AP channel
 * @return ESP_OK on success
 */
esp_err_t ftm_master_init(const char *ssid, const char *password, uint8_t channel);

/**
 * Initialize FTM master in STA mode (connects to external WiFi)
 *
 * Master connects to external network like slaves do. This allows master to
 * reach external services (MQTT, etc). ESP-NOW sync still works. FTM is not
 * available in this mode (requires AP).
 *
 * @param ssid WiFi SSID to connect to
 * @param password WiFi password
 * @return ESP_OK on success
 */
esp_err_t ftm_master_init_sta(const char *ssid, const char *password);

/**
 * Initialize FTM slave (connect to AP and run FTM sessions)
 *
 * @param ssid AP SSID to connect to
 * @param password AP password
 * @return ESP_OK on success
 */
esp_err_t ftm_slave_init(const char *ssid, const char *password);

/**
 * Register callback for FTM reports
 *
 * @param callback Function to call when FTM report is ready
 */
void ftm_register_callback(ftm_callback_t callback);

/**
 * Deinitialize FTM module
 */
esp_err_t ftm_deinit(void);

/**
 * Wait for IP address to be obtained
 *
 * @param timeout_ms Maximum time to wait in milliseconds
 * @return ESP_OK if IP obtained, ESP_ERR_TIMEOUT if timeout
 */
esp_err_t ftm_wait_for_ip(uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
