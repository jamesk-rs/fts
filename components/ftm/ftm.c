/**
 * FTM - Fine Timing Measurement Module
 *
 * Manages WiFi connection, FTM sessions, MAC clock unwrapping,
 * and ESP-NOW clock synchronization between master and slaves.
 */

#include "ftm.h"
#include "crm.h"
#include "clock.h"
#ifdef CONFIG_FTS_MQTT_ENABLED
#include "fts_mqtt.h"
#include "esp_timer.h"
#endif
#include <limits.h>
#include "esp_wifi.h"
#include "esp_private/wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_now.h"
#include "esp_random.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include <string.h>

static const char *TAG = "ftm";

// ============================================================================
// Constants
// ============================================================================

// Task configuration
#define FTM_POLL_TASK_STACK_SIZE    4096
#define FTM_POLL_TASK_PRIORITY      5
#define FTM_SESSION_TIMEOUT_MS      10000
#define FTM_BURST_PERIOD            2

// Clock sync configuration
#define FTM_SYNC_MAGIC              0x46545330  // 'FTS0'
#define FTM_SYNC_BROADCAST_INTERVAL_MS  500
#define FTM_SYNC_TASK_STACK_SIZE    2048
#define FTM_SYNC_TASK_PRIORITY      3

// Wrap constants for timestamp unwrapping
#define WRAP_48BIT       (1ULL << 48)                  // 281,474,976,710,656 ps (~281.5s)
#define WRAP_32BIT_1E6   ((1ULL << 32) * 1000000ULL)   // 4,294,967,296,000,000 ps
#define WRAP2_T1_T4      (WRAP_32BIT_1E6 % WRAP_48BIT) // Abnormal wrap threshold for t1/t4

// ============================================================================
// Sync packet format
// ============================================================================

typedef struct __attribute__((packed)) {
    uint32_t magic;          // FTM_SYNC_MAGIC
    uint32_t run_id;         // esp_random() at master boot
    uint64_t mac_clock_us;   // clock_get_us() (unwrapped 64-bit µs)
} ftm_sync_packet_t;

// ============================================================================
// FTM Session Statistics
// ============================================================================

typedef struct {
    uint32_t session;        // Session number
    unsigned short status;   // FTM status code
    uint8_t count;           // Entry count
    int64_t rtt_avg_ps;      // RTT average in picoseconds
    int64_t rtt_min_ps;      // RTT minimum
    int64_t rtt_max_ps;      // RTT maximum
    int32_t rssi_avg;        // RSSI average
    int8_t rssi_min;         // RSSI minimum
    int8_t rssi_max;         // RSSI maximum
} ftm_stats_t;

// ============================================================================
// Static state
// ============================================================================

// FTM state
static EventGroupHandle_t s_ftm_event_group = NULL;
static const int FTM_REPORT_BIT = BIT0;
static const int FTM_FAILURE_BIT = BIT1;

// FTM session tracking
static uint8_t s_master_bssid[6] = {0};
static uint8_t s_ap_channel = 0;

// Static FTM report buffer
#define FTM_REPORT_MAX_ENTRIES FTM_FRAMES_PER_SESSION

static uint32_t s_ftm_session_number = 0;
static wifi_ftm_report_entry_t s_ftm_report_buffer[FTM_REPORT_MAX_ENTRIES];
static uint8_t s_ftm_report_count = 0;
static unsigned short s_ftm_status = FTM_STATUS_SUCCESS;

// Static unwrapped timestamp buffers
static int64_t s_t1_ps[FTM_REPORT_MAX_ENTRIES];
static int64_t s_t2_ps[FTM_REPORT_MAX_ENTRIES];
static int64_t s_t3_ps[FTM_REPORT_MAX_ENTRIES];
static int64_t s_t4_ps[FTM_REPORT_MAX_ENTRIES];

// Task handle
static TaskHandle_t s_ftm_task_handle = NULL;

// Sync state (master)
static uint32_t s_run_id = 0;
static const uint8_t s_broadcast_mac[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Sync state (slave)
static volatile uint32_t s_remote_run_id = 0;
static volatile bool s_sync_valid = false;
static volatile uint64_t s_remote_mac_clock_us = 0;

// ============================================================================
// ESP-NOW Sync - Master side
// ============================================================================

/**
 * ESP-NOW send callback (master)
 */
static void ftm_sync_send_cb(const esp_now_send_info_t *send_info, esp_now_send_status_t status)
{
    (void)send_info;
    if (status != ESP_NOW_SEND_SUCCESS) {
        ESP_LOGD(TAG, "ESP-NOW send failed");
    }
}

/**
 * Sync broadcast task (master)
 */
static void ftm_sync_broadcast_task(void *arg)
{
    ESP_LOGI(TAG, "Sync broadcast task started (interval %d ms)", FTM_SYNC_BROADCAST_INTERVAL_MS);

    ftm_sync_packet_t pkt = {
        .magic = FTM_SYNC_MAGIC,
        .run_id = s_run_id,
    };

    while (1) {
        pkt.mac_clock_us = clock_get_us();
        esp_err_t ret = esp_now_send(s_broadcast_mac, (uint8_t *)&pkt, sizeof(pkt));
        if (ret != ESP_OK) {
            ESP_LOGD(TAG, "esp_now_send failed: %s", esp_err_to_name(ret));
        }
        vTaskDelay(pdMS_TO_TICKS(FTM_SYNC_BROADCAST_INTERVAL_MS));
    }
}

/**
 * Initialize ESP-NOW sync for master
 */
static esp_err_t ftm_sync_master_init(uint8_t channel)
{
    esp_err_t ret;

    // Generate run_id
    s_run_id = esp_random();
    ESP_LOGI(TAG, "Sync master init: run_id=0x%08lx, channel=%d", (unsigned long)s_run_id, channel);

    // Initialize ESP-NOW
    ret = esp_now_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // Register send callback
    ret = esp_now_register_send_cb(ftm_sync_send_cb);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_register_send_cb failed: %s", esp_err_to_name(ret));
        esp_now_deinit();
        return ret;
    }

    // Add broadcast peer on AP's channel
    esp_now_peer_info_t peer = {
        .channel = channel,
        .ifidx = WIFI_IF_AP,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, s_broadcast_mac, 6);

    ret = esp_now_add_peer(&peer);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_add_peer failed: %s", esp_err_to_name(ret));
        esp_now_deinit();
        return ret;
    }

    // Start broadcast task
    BaseType_t xret = xTaskCreate(
        ftm_sync_broadcast_task,
        "ftm_sync",
        FTM_SYNC_TASK_STACK_SIZE,
        NULL,
        FTM_SYNC_TASK_PRIORITY,
        NULL
    );

    if (xret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create sync broadcast task");
        esp_now_deinit();
        return ESP_FAIL;
    }

    return ESP_OK;
}

// ============================================================================
// ESP-NOW Sync - Slave side
// ============================================================================

/**
 * ESP-NOW receive callback (slave)
 */
static void ftm_sync_recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    if (len != sizeof(ftm_sync_packet_t)) {
        return;
    }

    const ftm_sync_packet_t *pkt = (const ftm_sync_packet_t *)data;

    if (pkt->magic != FTM_SYNC_MAGIC) {
        return;
    }

    // Detect reboot: run_id changed
    if (s_sync_valid && pkt->run_id != s_remote_run_id) {
        ESP_LOGW(TAG, "Master reboot detected (run_id: 0x%08lx -> 0x%08lx)",
                 (unsigned long)s_remote_run_id, (unsigned long)pkt->run_id);
    }

    s_remote_run_id = pkt->run_id;
    s_remote_mac_clock_us = pkt->mac_clock_us;

    if (!s_sync_valid) {
        s_sync_valid = true;
        ESP_LOGI(TAG, "Initial sync received: run_id=0x%08lx, clock=%llu us",
                 (unsigned long)s_remote_run_id, (unsigned long long)pkt->mac_clock_us);
    }
}


/**
 * Initialize ESP-NOW sync for slave
 */
static esp_err_t ftm_sync_slave_init(void)
{
    esp_err_t ret;

    ESP_LOGI(TAG, "Sync slave init...");

    // Initialize ESP-NOW
    ret = esp_now_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // Register receive callback
    ret = esp_now_register_recv_cb(ftm_sync_recv_cb);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_now_register_recv_cb failed: %s", esp_err_to_name(ret));
        esp_now_deinit();
        return ret;
    }

    return ESP_OK;
}

// ============================================================================
// FTM Event Handlers
// ============================================================================

/**
 * FTM event handler
 */
static void ftm_event_handler(void *arg, esp_event_base_t event_base,
                              int32_t event_id, void *event_data)
{
    if (event_id == WIFI_EVENT_FTM_REPORT) {
        wifi_event_ftm_report_t *event = (wifi_event_ftm_report_t *)event_data;
        s_ftm_status = event->status;

        if (s_ftm_status == FTM_STATUS_SUCCESS && event->ftm_report_num_entries > 0) {
            uint8_t num_entries = event->ftm_report_num_entries;

            if (num_entries > FTM_REPORT_MAX_ENTRIES) {
                ESP_LOGE(TAG, "FATAL: FTM report has %d entries, max is %d", num_entries, FTM_REPORT_MAX_ENTRIES);
                abort();
            }

            esp_err_t ret = esp_wifi_ftm_get_report(s_ftm_report_buffer, num_entries);
            if (ret != ESP_OK) {
                ESP_LOGW(TAG, "Failed to get FTM report: %s", esp_err_to_name(ret));
                xEventGroupSetBits(s_ftm_event_group, FTM_FAILURE_BIT);
                return;
            }

            s_ftm_report_count = num_entries;
            xEventGroupSetBits(s_ftm_event_group, FTM_REPORT_BIT);
        } else {
            xEventGroupSetBits(s_ftm_event_group, FTM_FAILURE_BIT);
        }
    }
}

/**
 * Process FTM report: unwrap timestamps, calculate statistics, and pass to CRM
 */
static void process_ftm_report(unwrap_state_t *t1_unwrap, unwrap_state_t *t2_unwrap,
                                unwrap_state_t *t3_unwrap, unwrap_state_t *t4_unwrap,
                                ftm_stats_t *stats)
{
    if (s_ftm_report_count == 0) {
        return;
    }

    // Statistics accumulators
    int64_t rtt_sum_ps = 0;
    int64_t rtt_min_ps = INT64_MAX;
    int64_t rtt_max_ps = INT64_MIN;
    int32_t rssi_sum = 0;
    int8_t rssi_min = INT8_MAX;
    int8_t rssi_max = INT8_MIN;

    for (uint8_t i = 0; i < s_ftm_report_count; i++) {
        s_t1_ps[i] = clock_unwrap(s_ftm_report_buffer[i].t1, t1_unwrap);
        s_t2_ps[i] = clock_unwrap(s_ftm_report_buffer[i].t2, t2_unwrap);
        s_t3_ps[i] = clock_unwrap(s_ftm_report_buffer[i].t3, t3_unwrap);
        s_t4_ps[i] = clock_unwrap(s_ftm_report_buffer[i].t4, t4_unwrap);

        // Calculate RTT for this entry: (t4 - t1) - (t3 - t2)
        int64_t rtt_ps = (s_t4_ps[i] - s_t1_ps[i]) - (s_t3_ps[i] - s_t2_ps[i]);
        rtt_sum_ps += rtt_ps;
        if (rtt_ps < rtt_min_ps) rtt_min_ps = rtt_ps;
        if (rtt_ps > rtt_max_ps) rtt_max_ps = rtt_ps;

        // Collect RSSI
        int8_t rssi = s_ftm_report_buffer[i].rssi;
        rssi_sum += rssi;
        if (rssi < rssi_min) rssi_min = rssi;
        if (rssi > rssi_max) rssi_max = rssi;
    }

    // Populate stats
    stats->rtt_avg_ps = rtt_sum_ps / s_ftm_report_count;
    stats->rtt_min_ps = rtt_min_ps;
    stats->rtt_max_ps = rtt_max_ps;
    stats->rssi_avg = rssi_sum / s_ftm_report_count;
    stats->rssi_min = rssi_min;
    stats->rssi_max = rssi_max;

    crm_process_ftm_report(s_ftm_session_number, s_t1_ps, s_t2_ps, s_t3_ps, s_t4_ps, s_ftm_report_count);
}

// ============================================================================
// FTM Poll Task
// ============================================================================

/**
 * Log FTM session statistics (CSV and console)
 */
static void log_ftm_stats(const ftm_stats_t *stats)
{
    if (stats->count > 0) {
#ifdef CONFIG_FTS_CSV_OUTPUT
        printf("FTM,%lu,%u,%u,%.1f,%.1f,%.1f,%ld,%d,%d\n",
               (unsigned long)s_ftm_session_number,
               stats->status,
               stats->count,
               (double)stats->rtt_avg_ps / 1000.0,
               (double)stats->rtt_min_ps / 1000.0,
               (double)stats->rtt_max_ps / 1000.0,
               (long)stats->rssi_avg, stats->rssi_min, stats->rssi_max);
#endif
        ESP_LOGI(TAG, "FTM #%lu: %u/%u entries, RTT avg=%.1fns [%.1f,%.1f], RSSI avg=%ld [%d, %d]",
                 (unsigned long)s_ftm_session_number,
                 stats->count, FTM_FRAMES_PER_SESSION,
                 (double)stats->rtt_avg_ps / 1000.0,
                 (double)stats->rtt_min_ps / 1000.0,
                 (double)stats->rtt_max_ps / 1000.0,
                 (long)stats->rssi_avg, stats->rssi_min, stats->rssi_max);
    } else {
#ifdef CONFIG_FTS_CSV_OUTPUT
        printf("FTM,%lu,%u,0,,,,,,\n", (unsigned long)s_ftm_session_number, stats->status);
#endif
        ESP_LOGE(TAG, "FTM #%lu: status=%u (error/timeout)", (unsigned long)s_ftm_session_number, stats->status);
    }
}

/**
 * FTM poll task - runs FTM sessions periodically
 */
static void ftm_poll_task(void *pvParameters)
{
    ESP_LOGI(TAG, "FTM poll task started");

    // Prepare FTM configuration
    wifi_ftm_initiator_cfg_t ftm_cfg = {
        .frm_count = FTM_FRAMES_PER_SESSION,
        .burst_period = FTM_BURST_PERIOD,
        .use_get_report_api = true,
    };
    memcpy(ftm_cfg.resp_mac, s_master_bssid, 6);
    ftm_cfg.channel = s_ap_channel;

    // Initialize unwrap state (original logic, no sync dependency)
    unwrap_state_t t1_unwrap = {0, 0, 0, WRAP_48BIT, WRAP2_T1_T4};
    unwrap_state_t t2_unwrap = {0, 0, 0, WRAP_32BIT_1E6, 0};
    unwrap_state_t t3_unwrap = {0, 0, 0, WRAP_32BIT_1E6, 0};
    unwrap_state_t t4_unwrap = {0, 0, 0, WRAP_48BIT, WRAP2_T1_T4};

    // Session statistics
    ftm_stats_t stats = {0};

    // Track run_id for passive monitoring (warnings only)
    uint32_t last_seen_run_id = 0;

    while (1) {
        // Run FTM session
        esp_err_t ret = esp_wifi_ftm_initiate_session(&ftm_cfg);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "FTM initiation failed: %s", esp_err_to_name(ret));
            vTaskDelay(pdMS_TO_TICKS(FTM_PERIOD_MS));
            continue;
        }

        // FTM session started - increment session number
        ++s_ftm_session_number;

        // Wait for FTM report
        EventBits_t bits = xEventGroupWaitBits(s_ftm_event_group,
                                               FTM_REPORT_BIT | FTM_FAILURE_BIT,
                                               true, false,
                                               pdMS_TO_TICKS(FTM_SESSION_TIMEOUT_MS));

        // Process FTM report if received
        if (bits & FTM_REPORT_BIT) {
            process_ftm_report(&t1_unwrap, &t2_unwrap, &t3_unwrap, &t4_unwrap, &stats);
            stats.count = s_ftm_report_count;
            stats.status = FTM_STATUS_SUCCESS;
        } else if (bits & FTM_FAILURE_BIT) {
            stats.count = 0;
            stats.status = s_ftm_status;
        } else {
            stats.count = 0;
            stats.status = 250; // Timeout
        }
        log_ftm_stats(&stats);

#ifdef CONFIG_FTS_MQTT_ENABLED
        // Publish FTM report via MQTT
        if (stats.count > 0 && fts_mqtt_is_connected()) {
            fts_mqtt_publish_ftm(
                esp_timer_get_time(),
                s_ftm_session_number,
                (int32_t)stats.rtt_avg_ps,
                (int8_t)stats.rssi_avg,
                (uint32_t)(s_t1_ps[0] / 1000),  // Convert ps to ns for first entry
                (uint32_t)(s_t2_ps[0] / 1000),
                (uint32_t)(s_t3_ps[0] / 1000),
                (uint32_t)(s_t4_ps[0] / 1000)
            );
        }
#endif

        s_ftm_report_count = 0;

        // Wait before next session
        vTaskDelay(pdMS_TO_TICKS(FTM_PERIOD_MS));
    }
}

// ============================================================================
// WiFi Event Handler
// ============================================================================

/**
 * WiFi event handler
 */
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_id == WIFI_EVENT_STA_CONNECTED) {
        wifi_event_sta_connected_t *event = (wifi_event_sta_connected_t *)event_data;
        ESP_LOGI(TAG, "Connected to master: SSID=%s, Channel=%d", event->ssid, event->channel);
        memcpy(s_master_bssid, event->bssid, 6);
        s_ap_channel = event->channel;

        if (s_ftm_task_handle == NULL) {
            // Clear stale event bits from previous session (e.g., FTM_STATUS_USER_TERM from disconnect)
            xEventGroupClearBits(s_ftm_event_group, FTM_REPORT_BIT | FTM_FAILURE_BIT);
            BaseType_t ret = xTaskCreate(ftm_poll_task, "ftm_poll", FTM_POLL_TASK_STACK_SIZE, NULL, FTM_POLL_TASK_PRIORITY, &s_ftm_task_handle);
            if (ret != pdPASS) {
                ESP_LOGE(TAG, "Failed to create FTM poll task");
            }
        }
    } else if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "Disconnected from master, reconnecting...");

        if (s_ftm_task_handle != NULL) {
            esp_wifi_ftm_end_session();  // Clean up any in-progress FTM session
            vTaskDelete(s_ftm_task_handle);
            s_ftm_task_handle = NULL;
        }

        esp_wifi_connect();
    }
}

// ============================================================================
// Public API
// ============================================================================

esp_err_t ftm_master_init(const char *ssid, const char *password, uint8_t channel)
{
    esp_err_t ret;

    ESP_LOGI(TAG, "Initializing FTM master (AP mode)...");

    // Initialize NVS
    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize network interface
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();

    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    // Configure AP with FTM responder
    wifi_config_t wifi_config = {
        .ap = {
            .ssid = "",
            .password = "",
            .ssid_len = strlen(ssid),
            .channel = channel,
            .authmode = WIFI_AUTH_WPA2_PSK,
            .max_connection = 4,
            .ftm_responder = true,
        },
    };
    strlcpy((char *)wifi_config.ap.ssid, ssid, sizeof(wifi_config.ap.ssid));
    strlcpy((char *)wifi_config.ap.password, password, sizeof(wifi_config.ap.password));

    if (strlen(password) == 0) {
        wifi_config.ap.authmode = WIFI_AUTH_OPEN;
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    // Suppress verbose WiFi FTM warnings
    esp_log_level_set("wifi", ESP_LOG_ERROR);

    // Start WiFi
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "Master AP started: SSID='%s', Channel=%d", ssid, channel);

    // Initialize clock (must be after WiFi start and power save disable)
    ESP_ERROR_CHECK(clock_init());

    // Initialize ESP-NOW sync broadcast
    ESP_ERROR_CHECK(ftm_sync_master_init(channel));

    return ESP_OK;
}

esp_err_t ftm_slave_init(const char *master_ssid, const char *master_password)
{
    esp_err_t ret;

    ESP_LOGI(TAG, "Initializing FTM slave (STA mode)...");

    // Create FTM event group
    s_ftm_event_group = xEventGroupCreate();
    if (!s_ftm_event_group) {
        ESP_LOGE(TAG, "Failed to create FTM event group");
        return ESP_FAIL;
    }

    // Initialize NVS
    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize network interface
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    // Register event handlers
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_FTM_REPORT, &ftm_event_handler, NULL));

    // Configure STA
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = "",
            .password = "",
        },
    };
    strlcpy((char *)wifi_config.sta.ssid, master_ssid, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, master_password, sizeof(wifi_config.sta.password));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    // Suppress verbose WiFi FTM warnings
    esp_log_level_set("wifi", ESP_LOG_ERROR);

    // Start WiFi and connect
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "Slave STA started: SSID='%s'", master_ssid);

    // Initialize clock (must be after WiFi start and power save disable)
    ESP_ERROR_CHECK(clock_init());

    // Initialize ESP-NOW sync receiver
    ESP_ERROR_CHECK(ftm_sync_slave_init());

    ESP_ERROR_CHECK(esp_wifi_connect());

#ifdef CONFIG_FTS_CSV_OUTPUT
    printf("FTM,session,status,entries,rtt_avg_ns,rtt_min_ns,rtt_max_ns,rssi_avg,rssi_min,rssi_max\n");
#endif

    return ESP_OK;
}

esp_err_t ftm_deinit(void)
{
    if (s_ftm_task_handle) {
        vTaskDelete(s_ftm_task_handle);
        s_ftm_task_handle = NULL;
    }

    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_wifi_deinit();

    if (s_ftm_event_group) {
        vEventGroupDelete(s_ftm_event_group);
        s_ftm_event_group = NULL;
    }

    esp_now_deinit();

    return ESP_OK;
}
