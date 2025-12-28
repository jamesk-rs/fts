/**
 * FTS (FineTimeSync) Example Application
 *
 * Demonstrates synchronized measurements using FTS framework.
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "rom/ets_sys.h"
#include "nvs_flash.h"
#include "dtr.h"
#include "build_info.h"

#if defined(CONFIG_FTS_MODE_INTERNAL_AP) || defined(CONFIG_FTS_MODE_EXTERNAL_AP) || defined(CONFIG_FTS_MODE_USB_NCM)
#include "ftm.h"
#endif

#ifdef CONFIG_FTS_MODE_USB_NCM
#include "usb_uplink.h"
#endif

#ifdef CONFIG_FTS_ROLE_SLAVE
#include "crm.h"
#include "dtc.h"
#endif

#ifdef CONFIG_FTS_MQTT_ENABLED
#include "fts_mqtt.h"
#endif

static const char *TAG = "fts_main";

// --- LED for pulse output

// Toggle every 1s (@2.5kHz)
#define TOGGLE_LED_GPIO_DTR_CYCLES 2500

// GPIO pulse output (2.5kHz 20% duty cycle)
#define TOGGLE_GPIO GPIO_NUM_7

#if defined(CONFIG_FTS_MQTT_ENABLED) && defined(CONFIG_FTS_MQTT_ENABLE_CONTROL)
/**
 * MQTT control callback - receives period corrections from RL engine
 */
static void mqtt_control_callback(int32_t period_correction_fp16,
                                   float phase_error_ns, float gain_K)
{
    ESP_LOGI(TAG, "MQTT correction: %ld (phase_error=%.1fns, K=%.3f)",
             (long)period_correction_fp16, phase_error_ns, gain_K);
    dtc_apply_mqtt_correction(period_correction_fp16);
}
#endif

/**
 * FTS callback - invoked in ISR context on each timer cycle
 * Runs at 2.5kHz (every 400µs)
 */
static void IRAM_ATTR fts_callback(uint32_t master_cycle)
{
#ifdef CONFIG_FTS_LED_ENABLED
    // Blink at 1Hz, 20% on (active low), 80% off
    int led_phase = master_cycle % TOGGLE_LED_GPIO_DTR_CYCLES;
    int led_state = (led_phase < TOGGLE_LED_GPIO_DTR_CYCLES / 5) ? 0 : 1;
    gpio_set_level(CONFIG_FTS_LED_GPIO, led_state);
#endif
}

void app_main(void)
{
#ifdef CONFIG_FTS_MODE_USB_NCM
    // USB-NCM mode: Initialize USB FIRST - before anything else including NVS
    // Some boards (e.g., Waveshare) need USB init within tight timing window
    ESP_ERROR_CHECK(usb_uplink_init());

    // Now init NVS (needed for WiFi later)
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
#endif

    ESP_LOGI(TAG, "FTS built %s - %s - %s",
             BUILD_TIMESTAMP,
             BUILD_GIT_DIRTY ? "DIRTY" : "CLEAN",
             BUILD_GIT_HASH);

#ifdef CONFIG_FTS_LED_ENABLED
    // Initialize LED
    gpio_config_t led_conf = {
        .pin_bit_mask = (1ULL << CONFIG_FTS_LED_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&led_conf);
    gpio_set_level(CONFIG_FTS_LED_GPIO, 1);  // LED off (active low)
#endif

#ifdef CONFIG_FTS_ROLE_SLAVE
    // ========== SLAVE MODE ==========

#if defined(CONFIG_FTS_MODE_INTERNAL_AP) || defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    // WiFi mode: Initialize WiFi STA with FTM initiator (starts WiFi and MAC clock)
    ESP_ERROR_CHECK(ftm_slave_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD));
#elif defined(CONFIG_FTS_MODE_USB_NCM)
    // WiFi for ESP-NOW sync + FTM (USB already initialized above)
    ESP_ERROR_CHECK(ftm_slave_espnow_init(CONFIG_FTS_ESPNOW_CHANNEL));
#endif

    // Initialize DTR (MCPWM timer hardware)
    ESP_ERROR_CHECK(dtr_init(DTR_MODE_SLAVE, fts_callback, TOGGLE_GPIO));

    // Start timer and measure MAC/timer relationship
    dtr_start_timer();

    // Initialize CRM (ready to receive FTM reports)
    ESP_ERROR_CHECK(crm_init());

    // Initialize DTC (registers with CRM)
    ESP_ERROR_CHECK(dtc_init());

#ifdef CONFIG_FTS_MQTT_ENABLED
    // Wait for IP address before starting MQTT
    ESP_LOGI(TAG, "Waiting for IP address...");
#if defined(CONFIG_FTS_MODE_INTERNAL_AP) || defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    esp_err_t ip_err = ftm_wait_for_ip(10000);
#elif defined(CONFIG_FTS_MODE_USB_NCM)
    esp_err_t ip_err = usb_uplink_wait_for_ip(30000);  // USB may take longer
#endif
    if (ip_err != ESP_OK) {
        ESP_LOGW(TAG, "Timeout waiting for IP, MQTT may fail initially");
    }

    // Initialize MQTT client for telemetry and control
    fts_mqtt_config_t mqtt_cfg = {
        .broker_uri = CONFIG_FTS_MQTT_BROKER_URI,
        .device_id = CONFIG_FTS_MQTT_DEVICE_ID,
#ifdef CONFIG_FTS_MQTT_ENABLE_CONTROL
        .ctrl_cb = mqtt_control_callback,
#else
        .ctrl_cb = NULL,
#endif
    };
    ESP_ERROR_CHECK(fts_mqtt_init(&mqtt_cfg));
    ESP_ERROR_CHECK(fts_mqtt_start());
    ESP_LOGI(TAG, "MQTT client started for device: %s", CONFIG_FTS_MQTT_DEVICE_ID);
#endif // CONFIG_FTS_MQTT_ENABLED

#elif defined(CONFIG_FTS_ROLE_MASTER)
    // ========== MASTER MODE ==========

#if defined(CONFIG_FTS_MODE_INTERNAL_AP)
    // Internal AP mode: Master creates its own network
    ESP_ERROR_CHECK(ftm_master_ap_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD, CONFIG_FTS_AP_CHANNEL));
#elif defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    // External AP mode: Master connects to external WiFi like slaves
    ESP_ERROR_CHECK(ftm_master_sta_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD));
#elif defined(CONFIG_FTS_MODE_USB_NCM)
    // USB-NCM mode: WiFi for ESP-NOW sync + FTM responder
    // (USB already initialized at start of app_main)
    ESP_ERROR_CHECK(ftm_master_espnow_init(CONFIG_FTS_ESPNOW_CHANNEL));
#endif

    // Initialize DTR (MCPWM timer hardware)
    ESP_ERROR_CHECK(dtr_init(DTR_MODE_MASTER, fts_callback, TOGGLE_GPIO));

    // Start timer and measure MAC/timer relationship
    dtr_start_timer();

    // Align timer to MAC clock epoch boundaries (works in all modes - WiFi MAC clock available)
    dtr_align_master_timer();

#ifdef CONFIG_FTS_MQTT_ENABLED
    // Wait for IP address before starting MQTT
#if defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    ESP_LOGI(TAG, "Waiting for IP address...");
    esp_err_t ip_err = ftm_wait_for_ip(10000);
    if (ip_err != ESP_OK) {
        ESP_LOGW(TAG, "Timeout waiting for IP, MQTT may fail initially");
    }
#elif defined(CONFIG_FTS_MODE_USB_NCM)
    ESP_LOGI(TAG, "Waiting for USB IP address...");
    esp_err_t ip_err = usb_uplink_wait_for_ip(30000);  // USB may take longer
    if (ip_err != ESP_OK) {
        ESP_LOGW(TAG, "Timeout waiting for IP, MQTT may fail initially");
    }
#endif
    // Note: Internal AP mode doesn't need IP wait - master has fixed IP

    // Initialize MQTT client for telemetry (master doesn't receive control)
    fts_mqtt_config_t mqtt_cfg = {
        .broker_uri = CONFIG_FTS_MQTT_BROKER_URI,
        .device_id = CONFIG_FTS_MQTT_DEVICE_ID,
        .ctrl_cb = NULL,
    };
    ESP_ERROR_CHECK(fts_mqtt_init(&mqtt_cfg));
    ESP_ERROR_CHECK(fts_mqtt_start());
    ESP_LOGI(TAG, "MQTT client started for device: %s", CONFIG_FTS_MQTT_DEVICE_ID);
#endif

#else
    #error "CONFIG_FTS_ROLE_MASTER or CONFIG_FTS_ROLE_SLAVE must be defined"
#endif
    ESP_LOGI(TAG, "FTS started");
}