#pragma once

#include "esp_err.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize WS2812 LED strip
 * @param gpio_num GPIO pin connected to LED data line
 * @param num_leds Number of LEDs (usually 1 for single RGB LED)
 * @param brightness Global brightness (0-255)
 */
esp_err_t ws2812_init(int gpio_num, int num_leds, uint8_t brightness);

/**
 * Set LED color (buffers only, call ws2812_show() to update)
 * @param index LED index (0-based)
 * @param r Red (0-255)
 * @param g Green (0-255)
 * @param b Blue (0-255)
 */
esp_err_t ws2812_set_color(int index, uint8_t r, uint8_t g, uint8_t b);

/**
 * Send buffered colors to LEDs
 */
esp_err_t ws2812_show(void);

#ifdef __cplusplus
}
#endif
