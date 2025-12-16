/**
 * DTR Backend Interface
 *
 * Defines the vtable interface for timer backends (MCPWM, GPTimer).
 * Supports multiple simultaneous timer instances with different backends.
 */

#pragma once

#include "esp_err.h"
#include "driver/gpio.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Forward declaration
typedef struct dtr_instance_s dtr_instance_t;

/**
 * Backend types
 */
typedef enum {
    DTR_BACKEND_MCPWM,    // MCPWM-based (ESP32, ESP32-S3) - hardware pulse
    DTR_BACKEND_GPTIMER,  // GPTimer-based (all chips) - software GPIO toggle
} dtr_backend_type_t;

/**
 * Backend operations vtable
 *
 * Each backend implements these operations. The core DTR logic calls
 * these through the vtable, enabling runtime backend selection.
 */
typedef struct {
    /**
     * Initialize backend hardware
     *
     * @param inst DTR instance
     * @param gpio GPIO pin for pulse output
     * @param period_ticks Initial period in timer ticks
     * @return ESP_OK on success
     */
    esp_err_t (*init)(dtr_instance_t *inst, gpio_num_t gpio, uint32_t period_ticks);

    /**
     * Enable and start the timer
     *
     * @param inst DTR instance
     * @return ESP_OK on success
     */
    esp_err_t (*start)(dtr_instance_t *inst);

    /**
     * Set timer period (takes effect at next period boundary)
     *
     * @param inst DTR instance
     * @param period_ticks New period in timer ticks
     */
    void (*set_period)(dtr_instance_t *inst, uint32_t period_ticks);

    /**
     * Read current timer counter value
     *
     * @param inst DTR instance
     * @return Current counter value in ticks
     */
    uint32_t (*read_counter)(dtr_instance_t *inst);

    /**
     * Force GPIO to specific level or release
     *
     * @param inst DTR instance
     * @param level -1=release (normal operation), 0=force low, 1=force high
     */
    void (*gpio_force)(dtr_instance_t *inst, int level);

    /**
     * Cleanup and release hardware resources
     *
     * @param inst DTR instance
     */
    void (*deinit)(dtr_instance_t *inst);
} dtr_backend_ops_t;

/**
 * Get backend operations by type
 *
 * @param type Backend type
 * @return Pointer to backend ops, or NULL if not available on this chip
 */
const dtr_backend_ops_t *dtr_get_backend(dtr_backend_type_t type);

/**
 * Check if a backend is available on this chip
 *
 * @param type Backend type
 * @return true if available, false otherwise
 */
bool dtr_backend_available(dtr_backend_type_t type);

#ifdef __cplusplus
}
#endif
