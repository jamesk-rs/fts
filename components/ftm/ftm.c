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
#include "esp_random.h"
#include "lwip/sockets.h"
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

// Clock sync configuration (UDP broadcast)
#define FTM_SYNC_MAGIC              0x46545330  // 'FTS0'
#define FTM_SYNC_BROADCAST_INTERVAL_MS  500
#define FTM_SYNC_TASK_STACK_SIZE    4096
#define FTM_SYNC_TASK_PRIORITY      3
#define FTM_SYNC_UDP_PORT           5000
#define FTM_SYNC_BROADCAST_ADDR     "255.255.255.255"

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
    uint8_t mac[6];          // Master's WiFi MAC address
    uint8_t channel;         // Master's WiFi channel
    uint8_t reserved;        // Padding for alignment
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
static const int FTM_GOT_IP_BIT = BIT2;
static const int FTM_MASTER_SYNC_BIT = BIT3;  // Master sync received via UDP

// FTM session tracking
static uint8_t s_master_mac[6] = {0};
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

// Sync state (slave)
static volatile uint32_t s_remote_run_id = 0;
static volatile bool s_sync_valid = false;
static volatile uint64_t s_remote_mac_clock_us = 0;

// ============================================================================
// UDP Sync - Master side
// ============================================================================

/**
 * UDP sync broadcast task (master)
 */
static void ftm_sync_broadcast_task(void *arg)
{
    ESP_LOGI(TAG, "UDP sync broadcast task started (port %d, interval %d ms)",
             FTM_SYNC_UDP_PORT, FTM_SYNC_BROADCAST_INTERVAL_MS);

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP socket: %d", errno);
        vTaskDelete(NULL);
        return;
    }

    // Enable broadcast
    int broadcast = 1;
    if (setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast)) < 0) {
        ESP_LOGE(TAG, "Failed to set SO_BROADCAST: %d", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    // Setup broadcast address
    struct sockaddr_in dest_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(FTM_SYNC_UDP_PORT),
    };
    inet_pton(AF_INET, FTM_SYNC_BROADCAST_ADDR, &dest_addr.sin_addr);

    // Get our own MAC address
    uint8_t own_mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, own_mac);

    ftm_sync_packet_t pkt = {
        .magic = FTM_SYNC_MAGIC,
        .run_id = s_run_id,
        .channel = s_ap_channel,
    };
    memcpy(pkt.mac, own_mac, 6);

    ESP_LOGI(TAG, "Broadcasting MAC=%02x:%02x:%02x:%02x:%02x:%02x, channel=%d",
             own_mac[0], own_mac[1], own_mac[2], own_mac[3], own_mac[4], own_mac[5],
             s_ap_channel);

    while (1) {
        pkt.mac_clock_us = clock_get_us();
        pkt.channel = s_ap_channel;  // Update in case it changed

        int sent = sendto(sock, &pkt, sizeof(pkt), 0,
                          (struct sockaddr *)&dest_addr, sizeof(dest_addr));
        if (sent < 0) {
            ESP_LOGD(TAG, "UDP sendto failed: %d", errno);
        }
        vTaskDelay(pdMS_TO_TICKS(FTM_SYNC_BROADCAST_INTERVAL_MS));
    }
}

/**
 * Initialize UDP sync for master
 */
static esp_err_t ftm_sync_master_init(uint8_t channel, wifi_interface_t ifidx)
{
    (void)ifidx;  // Not used for UDP

    // Generate run_id
    s_run_id = esp_random();
    ESP_LOGI(TAG, "Sync master init (UDP): run_id=0x%08lx, channel=%d",
             (unsigned long)s_run_id, channel);

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
        return ESP_FAIL;
    }

    return ESP_OK;
}

// ============================================================================
// UDP Sync - Slave side
// ============================================================================

/**
 * UDP sync listener task (slave)
 */
static void ftm_sync_listener_task(void *arg)
{
    ESP_LOGI(TAG, "UDP sync listener task started (port %d)", FTM_SYNC_UDP_PORT);

    // Create UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP socket: %d", errno);
        vTaskDelete(NULL);
        return;
    }

    // Bind to port
    struct sockaddr_in bind_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(FTM_SYNC_UDP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };

    if (bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        ESP_LOGE(TAG, "Failed to bind UDP socket: %d", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ftm_sync_packet_t pkt;

    while (1) {
        int len = recvfrom(sock, &pkt, sizeof(pkt), 0, NULL, NULL);

        if (len != sizeof(ftm_sync_packet_t)) {
            continue;
        }

        if (pkt.magic != FTM_SYNC_MAGIC) {
            continue;
        }

        // Check if we're already synced to a master
        EventBits_t bits = xEventGroupGetBits(s_ftm_event_group);
        if (!(bits & FTM_MASTER_SYNC_BIT)) {
            // First sync: capture master info from packet
            memcpy(s_master_mac, pkt.mac, 6);
            uint8_t master_channel = pkt.channel;

            // Check if we need to switch channels
            if (s_ap_channel != 0 && s_ap_channel != master_channel) {
                ESP_LOGW(TAG, "Channel mismatch: slave=%d, master=%d. Reconnecting...",
                         s_ap_channel, master_channel);

                // Disconnect and reconnect on master's channel
                esp_wifi_disconnect();
                vTaskDelay(pdMS_TO_TICKS(100));

                // Get current config and update channel
                wifi_config_t wifi_config;
                esp_wifi_get_config(WIFI_IF_STA, &wifi_config);
                wifi_config.sta.channel = master_channel;
                esp_wifi_set_config(WIFI_IF_STA, &wifi_config);

                // Reconnect (event handler will update s_ap_channel)
                esp_wifi_connect();

                // Don't set sync bit yet - wait for reconnection on correct channel
                continue;
            }

            // Set the flag
            xEventGroupSetBits(s_ftm_event_group, FTM_MASTER_SYNC_BIT);
            ESP_LOGI(TAG, "Master sync: MAC=%02x:%02x:%02x:%02x:%02x:%02x, channel=%d",
                     s_master_mac[0], s_master_mac[1], s_master_mac[2],
                     s_master_mac[3], s_master_mac[4], s_master_mac[5],
                     s_ap_channel);
        } else {
            // Already synced - ignore packets from other masters
            if (memcmp(pkt.mac, s_master_mac, 6) != 0) {
                ESP_LOGW(TAG, "Ignoring sync from unknown master MAC=%02x:%02x:%02x:%02x:%02x:%02x",
                         pkt.mac[0], pkt.mac[1], pkt.mac[2],
                         pkt.mac[3], pkt.mac[4], pkt.mac[5]);
                continue;
            }
        }

        // FIXME TODO: actually do something with the sync info
        // Detect reboot: run_id changed
        if (s_sync_valid && pkt.run_id != s_remote_run_id) {
            ESP_LOGW(TAG, "Master reboot detected (run_id: 0x%08lx -> 0x%08lx)",
                     (unsigned long)s_remote_run_id, (unsigned long)pkt.run_id);
        }

        s_remote_run_id = pkt.run_id;
        s_remote_mac_clock_us = pkt.mac_clock_us;

        if (!s_sync_valid) {
            s_sync_valid = true;
            ESP_LOGI(TAG, "Initial sync received: run_id=0x%08lx, clock=%llu us, channel=%d",
                     (unsigned long)s_remote_run_id, (unsigned long long)pkt.mac_clock_us,
                     pkt.channel);
        }
    }
}

/**
 * Initialize UDP sync for slave
 */
static esp_err_t ftm_sync_slave_init(void)
{
    ESP_LOGI(TAG, "Sync slave init (UDP)...");

    // Start listener task
    BaseType_t xret = xTaskCreate(
        ftm_sync_listener_task,
        "ftm_sync",
        FTM_SYNC_TASK_STACK_SIZE,
        NULL,
        FTM_SYNC_TASK_PRIORITY,
        NULL
    );

    if (xret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create sync listener task");
        return ESP_FAIL;
    }

    return ESP_OK;
}

// ============================================================================
// FTM Event Handlers
// ============================================================================

/**
 * FTM event handler
 */
static void slave_ftm_event_handler(void *arg, esp_event_base_t event_base,
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
static void slave_ftm_poll_task(void *pvParameters)
{
    ESP_LOGI(TAG, "FTM poll task started, waiting for master MAC...");

    // Wait for master sync
    xEventGroupWaitBits(s_ftm_event_group, FTM_MASTER_SYNC_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);

    ESP_LOGI(TAG, "Master synced, starting FTM sessions");

    // Prepare FTM configuration
    wifi_ftm_initiator_cfg_t ftm_cfg = {
        .frm_count = FTM_FRAMES_PER_SESSION,
        .burst_period = FTM_BURST_PERIOD,
        .use_get_report_api = true,
    };
    memcpy(ftm_cfg.resp_mac, s_master_mac, 6);
    ftm_cfg.channel = s_ap_channel;

    // Initialize unwrap state (original logic, no sync dependency)
    unwrap_state_t t1_unwrap = {0, 0, 0, WRAP_48BIT, WRAP2_T1_T4};
    unwrap_state_t t2_unwrap = {0, 0, 0, WRAP_32BIT_1E6, 0};
    unwrap_state_t t3_unwrap = {0, 0, 0, WRAP_32BIT_1E6, 0};
    unwrap_state_t t4_unwrap = {0, 0, 0, WRAP_48BIT, WRAP2_T1_T4};

    // Session statistics
    ftm_stats_t stats = {0};

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
        if (fts_mqtt_is_connected()) {
            // Publish FTM report via MQTT (first entry with full timestamps)
            // Only when we have valid data
            if (stats.count > 0) {
                fts_mqtt_publish_ftm(
                    esp_timer_get_time(),
                    s_ftm_session_number,
                    stats.rtt_avg_ps,
                    (int8_t)stats.rssi_avg,
                    s_t1_ps[0],  // Full precision picoseconds
                    s_t2_ps[0],
                    s_t3_ps[0],
                    s_t4_ps[0]
                );
            }
            // Always publish session statistics (captures error status)
            fts_mqtt_publish_ftm_stats(
                esp_timer_get_time(),
                s_ftm_session_number,
                stats.status, stats.count,
                stats.rtt_avg_ps, stats.rtt_min_ps, stats.rtt_max_ps,
                stats.rssi_avg, stats.rssi_min, stats.rssi_max
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
 * IP event handler - sets GOT_IP bit when IP is obtained
 */
static void slave_ip_event_handler(void *arg, esp_event_base_t event_base,
                             int32_t event_id, void *event_data)
{
    if (event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_ftm_event_group, FTM_GOT_IP_BIT);
    }
}

/**
 * WiFi event handler (slave)
 */
static void slave_sta_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_id == WIFI_EVENT_STA_CONNECTED) {
        wifi_event_sta_connected_t *event = (wifi_event_sta_connected_t *)event_data;
        ESP_LOGI(TAG, "Connected to AP: SSID=%s, Channel=%d", event->ssid, event->channel);

        // Check for channel mismatch if master was already discovered
        EventBits_t bits = xEventGroupGetBits(s_ftm_event_group);
        if ((bits & FTM_MASTER_SYNC_BIT) && s_ap_channel != 0 && event->channel != s_ap_channel) {
            ESP_LOGW(TAG, "Channel mismatch: connected=%d, expected=%d. Reconnecting...",
                     event->channel, s_ap_channel);
            esp_wifi_disconnect();  // Disconnect handler will set channel hint and reconnect
            return;
        }

        s_ap_channel = event->channel;

        // Start FTM poll task (it will wait for master MAC internally)
        if (s_ftm_task_handle == NULL) {
            xEventGroupClearBits(s_ftm_event_group, FTM_REPORT_BIT | FTM_FAILURE_BIT);
            BaseType_t ret = xTaskCreate(slave_ftm_poll_task, "ftm_poll", FTM_POLL_TASK_STACK_SIZE, NULL, FTM_POLL_TASK_PRIORITY, &s_ftm_task_handle);
            if (ret != pdPASS) {
                ESP_LOGE(TAG, "Failed to create FTM poll task");
            }
        }
    } else if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "Disconnected, reconnecting...");

        if (s_ftm_task_handle != NULL) {
            esp_wifi_ftm_end_session();
            vTaskDelete(s_ftm_task_handle);
            s_ftm_task_handle = NULL;
        }

        // Set channel hint if master was already discovered
        EventBits_t bits = xEventGroupGetBits(s_ftm_event_group);
        if ((bits & FTM_MASTER_SYNC_BIT) && s_ap_channel != 0) {
            wifi_config_t wifi_config;
            esp_wifi_get_config(WIFI_IF_STA, &wifi_config);
            wifi_config.sta.channel = s_ap_channel;
            esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
        }

        esp_wifi_connect();
    }
}

// ============================================================================
// Common initialization helper
// ============================================================================

/**
 * Common WiFi initialization for all modes
 */
static esp_err_t ftm_wifi_init_common(wifi_mode_t mode)
{
    esp_err_t ret;

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
    if (mode == WIFI_MODE_AP) {
        esp_netif_create_default_wifi_ap();
    } else {
        esp_netif_create_default_wifi_sta();
    }

    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(mode));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    // Suppress verbose WiFi logs
    esp_log_level_set("wifi", ESP_LOG_ERROR);

    return ESP_OK;
}

// ============================================================================
// Public API
// ============================================================================

/**
 * WiFi event handler for master STA mode
 */
static void master_sta_event_handler(void *arg, esp_event_base_t event_base,
                                      int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT) {
        if (event_id == WIFI_EVENT_STA_CONNECTED) {
            wifi_event_sta_connected_t *event = (wifi_event_sta_connected_t *)event_data;
            ESP_LOGI(TAG, "Master connected to AP: SSID=%s, Channel=%d",
                     event->ssid, event->channel);

            // Check for channel mismatch if channel already locked (s_ap_channel != 0)
            if (s_ap_channel != 0 && event->channel != s_ap_channel) {
                ESP_LOGW(TAG, "Channel mismatch: connected=%d, expected=%d. Reconnecting...",
                         event->channel, s_ap_channel);
                esp_wifi_disconnect();  // Disconnect handler will set channel hint and reconnect
                return;
            }

            // Lock channel on first successful connect (s_ap_channel was 0)
            if (s_ap_channel == 0) {
                ESP_LOGI(TAG, "Master channel locked to %d", event->channel);
            }
            s_ap_channel = event->channel;
        } else if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
            ESP_LOGW(TAG, "Master disconnected from AP, reconnecting...");

            // Set channel hint if channel was locked
            if (s_ap_channel != 0) {
                wifi_config_t wifi_config;
                esp_wifi_get_config(WIFI_IF_STA, &wifi_config);
                wifi_config.sta.channel = s_ap_channel;
                esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
            }

            esp_wifi_connect();
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Master got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        if (s_ftm_event_group) {
            xEventGroupSetBits(s_ftm_event_group, FTM_GOT_IP_BIT);
        }
    }
}

esp_err_t ftm_master_sta_init(const char *ssid, const char *password)
{
    ESP_LOGI(TAG, "Initializing FTM master (STA mode)...");

    // Create event group
    s_ftm_event_group = xEventGroupCreate();
    if (!s_ftm_event_group) {
        ESP_LOGE(TAG, "Failed to create event group");
        return ESP_FAIL;
    }

    // Common WiFi init
    ESP_ERROR_CHECK(ftm_wifi_init_common(WIFI_MODE_STA));

    // Register event handlers
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                &master_sta_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                &master_sta_event_handler, NULL));

    // Configure STA
    wifi_config_t sta_config = {.sta = {.ssid = "", .password = ""}};
    strlcpy((char *)sta_config.sta.ssid, ssid, sizeof(sta_config.sta.ssid));
    strlcpy((char *)sta_config.sta.password, password, sizeof(sta_config.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_config));

    // Start WiFi and connect
    ESP_ERROR_CHECK(esp_wifi_start());
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    ESP_LOGI(TAG, "Master MAC: %02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    ESP_ERROR_CHECK(esp_wifi_connect());

    // Wait for IP
    // FIXME: align slave implemenation and wait indefinitely
    EventBits_t bits = xEventGroupWaitBits(s_ftm_event_group, FTM_GOT_IP_BIT,
                                            pdFALSE, pdTRUE, pdMS_TO_TICKS(30000));
    if (!(bits & FTM_GOT_IP_BIT)) {
        ESP_LOGE(TAG, "Failed to connect within 30 seconds");
        return ESP_FAIL;
    }

    // Initialize clock and UDP sync
    ESP_ERROR_CHECK(clock_init());
    ESP_ERROR_CHECK(ftm_sync_master_init(0, WIFI_IF_STA));

    return ESP_OK;
}

esp_err_t ftm_master_ap_init(const char *ssid, const char *password, uint8_t channel)
{
    ESP_LOGI(TAG, "Initializing FTM master (AP mode)...");

    // Common WiFi init
    ESP_ERROR_CHECK(ftm_wifi_init_common(WIFI_MODE_AP));

    // Configure AP with FTM responder
    wifi_config_t wifi_config = {
        .ap = {
            .ssid = "",
            .password = "",
            .ssid_len = strlen(ssid),
            .channel = channel,
            .authmode = strlen(password) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN,
            .max_connection = 4,
            .ftm_responder = true,
        },
    };
    strlcpy((char *)wifi_config.ap.ssid, ssid, sizeof(wifi_config.ap.ssid));
    strlcpy((char *)wifi_config.ap.password, password, sizeof(wifi_config.ap.password));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));

    // Start WiFi
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "Master AP: SSID='%s', Channel=%d", ssid, channel);

    // Set channel for UDP broadcast (in STA mode this is set by event handler)
    s_ap_channel = channel;

    // Initialize clock and UDP sync
    ESP_ERROR_CHECK(clock_init());
    ESP_ERROR_CHECK(ftm_sync_master_init(channel, WIFI_IF_AP));

    return ESP_OK;
}

esp_err_t ftm_slave_init(const char *ssid, const char *password)
{
    ESP_LOGI(TAG, "Initializing FTM slave...");

    // Create event group
    s_ftm_event_group = xEventGroupCreate();
    if (!s_ftm_event_group) {
        ESP_LOGE(TAG, "Failed to create event group");
        return ESP_FAIL;
    }

    // Common WiFi init
    ESP_ERROR_CHECK(ftm_wifi_init_common(WIFI_MODE_STA));

    // Register event handlers
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &slave_sta_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_FTM_REPORT, &slave_ftm_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &slave_ip_event_handler, NULL));

    // Configure STA
    wifi_config_t wifi_config = {.sta = {.ssid = "", .password = ""}};
    strlcpy((char *)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, password, sizeof(wifi_config.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    // Start WiFi
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "Slave connecting to: '%s'", ssid);

    // Initialize clock and ESP-NOW
    ESP_ERROR_CHECK(clock_init());
    ESP_ERROR_CHECK(ftm_sync_slave_init());

    // Connect
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

    return ESP_OK;
}

esp_err_t ftm_wait_for_ip(uint32_t timeout_ms)
{
    if (s_ftm_event_group == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    EventBits_t bits = xEventGroupWaitBits(s_ftm_event_group,
                                           FTM_GOT_IP_BIT,
                                           false, true,
                                           pdMS_TO_TICKS(timeout_ms));

    if (bits & FTM_GOT_IP_BIT) {
        return ESP_OK;
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t ftm_trigger_wifi_disconnect(void)
{
    ESP_LOGW(TAG, "WiFi disconnect triggered via control command");
    vTaskDelay(pdMS_TO_TICKS(100));  // Let MQTT finish processing
    return esp_wifi_disconnect();
}
