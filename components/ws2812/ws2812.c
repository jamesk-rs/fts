#include "ws2812.h"
#include "led_strip_encoder.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include <string.h>

#define RMT_LED_STRIP_RESOLUTION_HZ 10000000

static const char *TAG = "ws2812";

static rmt_channel_handle_t s_led_chan = NULL;
static rmt_encoder_handle_t s_led_encoder = NULL;
static uint8_t *s_pixels = NULL;
static int s_num_leds = 0;
static uint8_t s_brightness = 255;

esp_err_t ws2812_init(int gpio_num, int num_leds, uint8_t brightness)
{
    s_num_leds = num_leds;
    s_brightness = brightness;

    s_pixels = calloc(num_leds * 3, sizeof(uint8_t));
    if (!s_pixels) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Create RMT TX channel on GPIO %d", gpio_num);
    rmt_tx_channel_config_t tx_chan_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .gpio_num = gpio_num,
        .mem_block_symbols = 64,
        .resolution_hz = RMT_LED_STRIP_RESOLUTION_HZ,
        .trans_queue_depth = 4,
    };
    ESP_ERROR_CHECK(rmt_new_tx_channel(&tx_chan_config, &s_led_chan));

    ESP_LOGI(TAG, "Install led strip encoder");
    led_strip_encoder_config_t encoder_config = {
        .resolution = RMT_LED_STRIP_RESOLUTION_HZ,
    };
    ESP_ERROR_CHECK(rmt_new_led_strip_encoder(&encoder_config, &s_led_encoder));

    ESP_ERROR_CHECK(rmt_enable(s_led_chan));

    ESP_LOGI(TAG, "WS2812 initialized: %d LEDs, brightness %d", num_leds, brightness);
    return ESP_OK;
}

esp_err_t ws2812_set_color(int index, uint8_t r, uint8_t g, uint8_t b)
{
    if (index < 0 || index >= s_num_leds || !s_pixels) {
        return ESP_ERR_INVALID_ARG;
    }

    // Apply brightness
    r = (r * s_brightness) / 255;
    g = (g * s_brightness) / 255;
    b = (b * s_brightness) / 255;

    // WS2812 uses GRB order
    s_pixels[index * 3 + 0] = g;
    s_pixels[index * 3 + 1] = r;
    s_pixels[index * 3 + 2] = b;

    return ESP_OK;
}

esp_err_t ws2812_show(void)
{
    if (!s_led_chan || !s_led_encoder || !s_pixels) {
        return ESP_ERR_INVALID_STATE;
    }

    rmt_transmit_config_t tx_config = {
        .loop_count = 0,
    };

    esp_err_t ret = rmt_transmit(s_led_chan, s_led_encoder, s_pixels, s_num_leds * 3, &tx_config);
    if (ret == ESP_OK) {
        ret = rmt_tx_wait_all_done(s_led_chan, pdMS_TO_TICKS(100));
    }
    return ret;
}
