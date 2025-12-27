#pragma once

#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize USB NCM network interface
 *
 * Creates esp_netif for USB, initializes TinyUSB driver,
 * and starts DHCP client.
 *
 * @return ESP_OK on success
 */
esp_err_t usb_uplink_init(void);

/**
 * Wait for USB network to obtain IP address
 *
 * @param timeout_ms Maximum time to wait in milliseconds (0 = wait forever)
 * @return ESP_OK if IP obtained, ESP_ERR_TIMEOUT if timeout
 */
esp_err_t usb_uplink_wait_for_ip(uint32_t timeout_ms);

/**
 * Check if USB uplink has IP connectivity
 *
 * @return true if IP is assigned and interface is up
 */
bool usb_uplink_is_connected(void);

/**
 * Deinitialize USB uplink
 *
 * @return ESP_OK on success
 */
esp_err_t usb_uplink_deinit(void);

#ifdef __cplusplus
}
#endif
