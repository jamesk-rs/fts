/*
 * USB NCM Uplink Component
 *
 * Provides USB-NCM network interface for telemetry uplink.
 * The ESP32 acts as a USB network device (gadget mode).
 * Host runs DHCP server to assign IP to the ESP32.
 */

#include "usb_uplink.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "tinyusb.h"
#include "tinyusb_net.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "lwip/esp_netif_net_stack.h"

static const char *TAG = "usb_uplink";

// Event group for IP acquisition
static EventGroupHandle_t s_usb_event_group = NULL;
#define USB_GOT_IP_BIT BIT0

// esp_netif handle
static esp_netif_t *s_usb_netif = NULL;

// Forward declarations
static esp_err_t usb_netif_transmit(void *h, void *buffer, size_t len);
static void usb_netif_free_rx_buffer(void *h, void *buffer);

/**
 * Callback when packet received from USB host
 */
static esp_err_t usb_recv_callback(void *buffer, uint16_t len, void *ctx)
{
    if (s_usb_netif && buffer && len > 0) {
        // Copy buffer because esp_netif_receive may process asynchronously
        void *buf_copy = malloc(len);
        if (buf_copy) {
            memcpy(buf_copy, buffer, len);
            esp_err_t ret = esp_netif_receive(s_usb_netif, buf_copy, len, NULL);
            if (ret != ESP_OK) {
                free(buf_copy);
            }
        }
    }
    return ESP_OK;
}

/**
 * Transmit packet to USB host
 */
static esp_err_t usb_netif_transmit(void *h, void *buffer, size_t len)
{
    if (tinyusb_net_send_sync(buffer, len, NULL, pdMS_TO_TICKS(100)) != ESP_OK) {
        ESP_LOGD(TAG, "USB transmit failed");
        return ESP_FAIL;
    }
    return ESP_OK;
}

/**
 * Free receive buffer (called by lwIP after processing)
 */
static void usb_netif_free_rx_buffer(void *h, void *buffer)
{
    free(buffer);
}

/**
 * IP event handler
 */
static void usb_ip_event_handler(void *arg, esp_event_base_t event_base,
                                  int32_t event_id, void *event_data)
{
    if (event_base != IP_EVENT) {
        return;
    }

    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;

    // Check if this event is for our USB netif
    if (event->esp_netif != s_usb_netif) {
        return;
    }

    if (event_id == IP_EVENT_ETH_GOT_IP) {
        ESP_LOGI(TAG, "USB got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "Gateway: " IPSTR, IP2STR(&event->ip_info.gw));
        ESP_LOGI(TAG, "Netmask: " IPSTR, IP2STR(&event->ip_info.netmask));
        if (s_usb_event_group) {
            xEventGroupSetBits(s_usb_event_group, USB_GOT_IP_BIT);
        }
    } else if (event_id == IP_EVENT_ETH_LOST_IP) {
        ESP_LOGW(TAG, "USB lost IP");
        if (s_usb_event_group) {
            xEventGroupClearBits(s_usb_event_group, USB_GOT_IP_BIT);
        }
    }
}

esp_err_t usb_uplink_init(void)
{
    ESP_LOGI(TAG, "Initializing USB NCM uplink...");

    // Create event group
    s_usb_event_group = xEventGroupCreate();
    if (!s_usb_event_group) {
        ESP_LOGE(TAG, "Failed to create event group");
        return ESP_ERR_NO_MEM;
    }

    // Initialize TinyUSB driver
    const tinyusb_config_t tusb_cfg = {
        .external_phy = false,
    };
    esp_err_t ret = tinyusb_driver_install(&tusb_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to install TinyUSB driver: %s", esp_err_to_name(ret));
        return ret;
    }

    // Get MAC address - derive from WiFi STA MAC with local admin bit set
    uint8_t mac_addr[6];
    esp_read_mac(mac_addr, ESP_MAC_WIFI_STA);
    // Set locally administered bit, clear multicast bit
    mac_addr[0] = (mac_addr[0] | 0x02) & 0xFE;

    // Configure TinyUSB network with receive callback
    tinyusb_net_config_t net_config = {
        .on_recv_callback = usb_recv_callback,
        .user_context = NULL,
    };
    memcpy(net_config.mac_addr, mac_addr, 6);

    ret = tinyusb_net_init(TINYUSB_USBDEV_0, &net_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init TinyUSB net: %s", esp_err_to_name(ret));
        return ret;
    }

    // Create esp_netif driver configuration
    esp_netif_driver_ifconfig_t driver_cfg = {
        .transmit = usb_netif_transmit,
        .driver_free_rx_buffer = usb_netif_free_rx_buffer,
        .handle = NULL,
    };

    // Create esp_netif inherent configuration - DHCP client mode
    esp_netif_inherent_config_t base_cfg = {
        .flags = ESP_NETIF_DHCP_CLIENT | ESP_NETIF_FLAG_AUTOUP |
                 ESP_NETIF_FLAG_EVENT_IP_MODIFIED,
        .ip_info = NULL,  // Will be assigned by DHCP
        .get_ip_event = IP_EVENT_ETH_GOT_IP,
        .lost_ip_event = IP_EVENT_ETH_LOST_IP,
        .if_key = "USB_NCM",
        .if_desc = "usb ncm uplink",
        .route_prio = 50,  // Lower than WiFi STA (100)
    };

    // Use default Ethernet stack config (USB-NCM is Ethernet-like from lwIP perspective)
    esp_netif_config_t cfg = {
        .base = &base_cfg,
        .driver = &driver_cfg,
        .stack = ESP_NETIF_NETSTACK_DEFAULT_ETH,
    };

    s_usb_netif = esp_netif_new(&cfg);
    if (!s_usb_netif) {
        ESP_LOGE(TAG, "Failed to create USB netif");
        return ESP_FAIL;
    }

    // Set MAC address on netif
    esp_netif_set_mac(s_usb_netif, mac_addr);

    // Register IP event handler
    ret = esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID,
                                      &usb_ip_event_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to register IP event handler: %s", esp_err_to_name(ret));
        return ret;
    }

    // Attach driver to netif (manual attach since we have custom driver)
    esp_netif_attach(s_usb_netif, NULL);

    // Start the interface - this will bring up the link and start DHCP
    esp_netif_action_start(s_usb_netif, NULL, 0, NULL);

    ESP_LOGI(TAG, "USB NCM uplink initialized, MAC=%02x:%02x:%02x:%02x:%02x:%02x",
             mac_addr[0], mac_addr[1], mac_addr[2],
             mac_addr[3], mac_addr[4], mac_addr[5]);
    ESP_LOGI(TAG, "Waiting for USB host connection and DHCP...");

    return ESP_OK;
}

esp_err_t usb_uplink_wait_for_ip(uint32_t timeout_ms)
{
    if (!s_usb_event_group) {
        return ESP_ERR_INVALID_STATE;
    }

    TickType_t wait_ticks = (timeout_ms == 0) ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);

    EventBits_t bits = xEventGroupWaitBits(s_usb_event_group,
                                           USB_GOT_IP_BIT,
                                           false,  // Don't clear on exit
                                           true,   // Wait for all bits
                                           wait_ticks);

    return (bits & USB_GOT_IP_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

bool usb_uplink_is_connected(void)
{
    if (!s_usb_event_group) {
        return false;
    }
    EventBits_t bits = xEventGroupGetBits(s_usb_event_group);
    return (bits & USB_GOT_IP_BIT) != 0;
}

esp_err_t usb_uplink_deinit(void)
{
    if (s_usb_netif) {
        esp_netif_action_stop(s_usb_netif, NULL, 0, NULL);
        esp_netif_destroy(s_usb_netif);
        s_usb_netif = NULL;
    }

    if (s_usb_event_group) {
        vEventGroupDelete(s_usb_event_group);
        s_usb_event_group = NULL;
    }

    esp_event_handler_unregister(IP_EVENT, ESP_EVENT_ANY_ID, &usb_ip_event_handler);

    return ESP_OK;
}
