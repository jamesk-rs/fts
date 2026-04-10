/**
 * FTS (FineTimeSync) Example Application
 *
 * Demonstrates synchronized measurements using FTS framework.
 */

#include "fts_main.h"

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "rom/ets_sys.h"
#include "nvs_flash.h"
#include "dtr.h"
#include "build_info.h"


#define LDO_EN_GPIO 17
#define RF_PATH_GPIO 11

#ifdef CONFIG_FTS_LED_WS2812
#include "ws2812.h"
#endif

#if defined(CONFIG_FTS_MODE_INTERNAL_AP) || defined(CONFIG_FTS_MODE_EXTERNAL_AP)
#include "ftm.h"
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
#ifdef CONFIG_FTS_PULSE_MCPWM_GPIO
#define TOGGLE_GPIO CONFIG_FTS_PULSE_MCPWM_GPIO
#else
#define TOGGLE_GPIO GPIO_NUM_7 //Put the pulses out on GPIO5
#endif

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

#ifdef CONFIG_FTS_LED_ENABLED
// LED state tracking - only update on state change
static bool led_on_last_state = false;
#endif

#ifdef CONFIG_FTS_LED_WS2812
// WS2812 LED state - set from ISR, updated via event group
#define WS2812_LED_ON_BIT   BIT0
#define WS2812_LED_OFF_BIT  BIT1
static EventGroupHandle_t ws2812_event_group = NULL;

/**
 * WS2812 LED update task - waits for events from ISR
 */
static void ws2812_task(void *arg)
{
    bool led_on = false;
    while (1) {
        EventBits_t bits = xEventGroupWaitBits(ws2812_event_group,
            WS2812_LED_ON_BIT | WS2812_LED_OFF_BIT,
            pdTRUE, pdFALSE, portMAX_DELAY);

        if (bits & WS2812_LED_ON_BIT) {
            led_on = true;
        } else if (bits & WS2812_LED_OFF_BIT) {
            led_on = false;
        }
        ws2812_set_color(0, led_on ? 255 : 0, led_on ? 255 : 0, led_on ? 255 : 0);
        ws2812_show();
    }
}
#endif

/**
 * FTS callback - invoked in ISR context on each timer cycle
 * Runs at 2.5kHz (every 400µs)
 */
static void IRAM_ATTR fts_callback(uint32_t master_cycle)
{
#ifdef CONFIG_FTS_LED_ENABLED
    // Blink at 1Hz, 20% on, 80% off
    int led_phase = master_cycle % TOGGLE_LED_GPIO_DTR_CYCLES;
    bool led_on = (led_phase < TOGGLE_LED_GPIO_DTR_CYCLES / 5);

    // Only update on state change
    if (led_on != led_on_last_state) {
        led_on_last_state = led_on;
#ifdef CONFIG_FTS_LED_WS2812
        // WS2812: signal task via event group
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        xEventGroupSetBitsFromISR(ws2812_event_group,
            led_on ? WS2812_LED_ON_BIT : WS2812_LED_OFF_BIT,
            &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
#else
        // Regular GPIO LED (active low)
        gpio_set_level(CONFIG_1PPS_LED_GPIO, led_on ? 0 : 1);
#endif
    }
#endif
}

void app_main(void)
{
    gpio_config_t ldo_en_conf = {
        .pin_bit_mask = (1ULL << LDO_EN_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&ldo_en_conf);
    gpio_set_level(LDO_EN_GPIO, 1);  //ldo on

        gpio_config_t rf_path_conf = {
        .pin_bit_mask = (1ULL << RF_PATH_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&rf_path_conf);
    gpio_set_level(RF_PATH_GPIO, 1);  // direct wifi out of u.fl connector


    ESP_LOGI(TAG, "FTS built %s - %s - %s",
             BUILD_TIMESTAMP,
             BUILD_GIT_DIRTY ? "DIRTY" : "CLEAN",
             BUILD_GIT_HASH);

#ifdef CONFIG_FTS_LED_ENABLED
#ifdef CONFIG_FTS_LED_WS2812
    // Initialize WS2812 RGB LED
    ESP_ERROR_CHECK(ws2812_init(CONFIG_1PPS_LED_GPIO, 1, 255));  // brightness=1
    ws2812_set_color(0, 100, 100, 100);  // White to start, to show it's working
    ws2812_show();
    ESP_LOGI(TAG, "Set LED to white");

    // Create event group and task for LED updates
    ws2812_event_group = xEventGroupCreate();
    xTaskCreate(ws2812_task, "ws2812", 2048, NULL, 1, NULL);
    ESP_LOGI(TAG, "WS2812 LED initialized on GPIO %d", CONFIG_1PPS_LED_GPIO);
#else
    // Initialize regular GPIO LED
    gpio_config_t led_conf = {
        .pin_bit_mask = (1ULL << CONFIG_1PPS_LED_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&led_conf);
    gpio_set_level(CONFIG_1PPS_LED_GPIO, 1);  // LED off (active low)
#endif
#endif

#ifdef CONFIG_FTS_ROLE_SLAVE
    // ========== SLAVE MODE INIT ==========

#if defined(CONFIG_FTS_MODE_INTERNAL_AP) || defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    // WiFi mode: Initialize WiFi STA with FTM initiator (starts WiFi and MAC clock)
    ESP_ERROR_CHECK(ftm_slave_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD));
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
    // ========== MASTER MODE INIT ==========

#if defined(CONFIG_FTS_MODE_INTERNAL_AP)
    // Internal AP mode: Master creates its own network
    ESP_ERROR_CHECK(ftm_master_ap_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD, CONFIG_FTS_AP_CHANNEL));
#elif defined(CONFIG_FTS_MODE_EXTERNAL_AP)
    // External AP mode: Master connects to external WiFi like slaves
    ESP_ERROR_CHECK(ftm_master_sta_init(CONFIG_FTS_WIFI_SSID, CONFIG_FTS_WIFI_PASSWORD));
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

    //Set the RTC to start accepting pulses and start getting it in sync

    //Wait 10 seconds, then turn off wifi
    vTaskDelay(pdMS_TO_TICKS(10000));

    //Disable the RTC from accepting sync pulses, set it to rely on its own clock

    //Change the output pulse being monitored to come from the RTC

    //De-init the FTM function and rely on the RTC instead
    ESP_LOGI(TAG, "10s passed. deinit ftm and watch the drift...");
    ftm_deinit();

    //Now watch the pulses and see how far out they go...
}