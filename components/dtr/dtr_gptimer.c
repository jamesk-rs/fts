/**
 * DTR GPTimer Backend
 *
 * GPTimer-based timer implementation with software GPIO pulse generation.
 * Available on all ESP32 chips. Pulse generation via software GPIO toggle
 * in ISR is adequate for 2kHz operation.
 */

#include "sdkconfig.h"
#include "dtr.h"
#include "dtr_backend.h"
#include "driver/gptimer.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "rom/ets_sys.h"
#include <stdlib.h>

static const char *TAG = "dtr_gptimer";

/**
 * GPTimer backend-specific data
 */
typedef struct {
    gptimer_handle_t timer;
    gpio_num_t gpio;
    uint32_t pulse_width_ticks;
    bool gpio_forced;       // True if GPIO is being forced (not ISR-controlled)
    int forced_level;       // Forced level (-1=released, 0=low, 1=high)
} gptimer_backend_t;

/**
 * GPTimer alarm callback - equivalent to MCPWM TEZ
 * Called at each timer period boundary.
 */
static bool IRAM_ATTR gptimer_alarm_handler(gptimer_handle_t timer,
                                            const gptimer_alarm_event_data_t *edata,
                                            void *user_ctx)
{
    dtr_instance_t *inst = (dtr_instance_t *)user_ctx;
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;

    // Generate pulse via software GPIO toggle (only if not forced)
    if (!be->gpio_forced && inst->state == DTR_STATE_ALIGNED && !inst->first_aligned_period) {
        // Set HIGH
        gpio_set_level(be->gpio, 1);

        // Brief busy-wait for pulse width
        // At 40MHz timer, pulse_width_ticks = 1000 ticks = 25µs
        // This is acceptable overhead at 2kHz (500µs period)
        uint32_t start = edata->count_value;
        while ((edata->count_value - start) < be->pulse_width_ticks) {
            // Busy wait - count_value won't change in this context
            // Use a simple delay loop instead
            break;
        }
        // Simple delay loop for pulse width (~25µs at 2kHz with 5% duty)
        for (volatile int i = 0; i < be->pulse_width_ticks / 4; i++) {
            __asm__ volatile("nop");
        }

        // Set LOW
        gpio_set_level(be->gpio, 0);
    }

    // Call core period handler
    return dtr_core_period_handler(inst);
}

/**
 * Initialize GPTimer hardware
 */
static esp_err_t gptimer_backend_init(dtr_instance_t *inst, gpio_num_t gpio, uint32_t period_ticks)
{
    gptimer_backend_t *be = calloc(1, sizeof(gptimer_backend_t));
    if (be == NULL) {
        return ESP_ERR_NO_MEM;
    }
    inst->backend_data = be;
    be->gpio = gpio;
    be->pulse_width_ticks = DTR_PULSE_WIDTH_TICKS;
    be->gpio_forced = true;      // Start with GPIO forced low
    be->forced_level = 0;

    // Configure GPIO
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << gpio),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
    gpio_set_level(gpio, 0);

    // Create GPTimer
    gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = DTR_TIMER_RESOLUTION_HZ,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&timer_config, &be->timer));

    // Set initial alarm for period
    gptimer_alarm_config_t alarm_config = {
        .alarm_count = period_ticks,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(be->timer, &alarm_config));

    // Register callback
    gptimer_event_callbacks_t cbs = {
        .on_alarm = gptimer_alarm_handler,
    };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(be->timer, &cbs, inst));

    ESP_LOGI(TAG, "GPTimer backend initialized, GPIO %d", gpio);
    return ESP_OK;
}

/**
 * Enable and start the timer
 */
static esp_err_t gptimer_backend_start(dtr_instance_t *inst)
{
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;

    ESP_ERROR_CHECK(gptimer_enable(be->timer));
    ESP_ERROR_CHECK(gptimer_start(be->timer));

    return ESP_OK;
}

/**
 * Set timer period (updates alarm for next period)
 * Called from ISR context.
 */
static void IRAM_ATTR gptimer_backend_set_period(dtr_instance_t *inst, uint32_t period_ticks)
{
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;

    gptimer_alarm_config_t alarm_config = {
        .alarm_count = period_ticks,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    gptimer_set_alarm_action(be->timer, &alarm_config);
}

/**
 * Read timer counter value
 * May be called from ISR context.
 */
static uint32_t IRAM_ATTR gptimer_backend_read_counter(dtr_instance_t *inst)
{
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;
    uint64_t count;
    gptimer_get_raw_count(be->timer, &count);
    return (uint32_t)count;
}

/**
 * Force GPIO to specific level or release
 * Called from ISR context.
 */
static void IRAM_ATTR gptimer_backend_gpio_force(dtr_instance_t *inst, int level)
{
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;

    if (level >= 0) {
        // Force to specific level
        be->gpio_forced = true;
        be->forced_level = level;
        gpio_set_level(be->gpio, level);
    } else {
        // Release - allow ISR to control GPIO
        be->gpio_forced = false;
        be->forced_level = -1;
    }
}

/**
 * Cleanup and release resources
 */
static void gptimer_backend_deinit(dtr_instance_t *inst)
{
    gptimer_backend_t *be = (gptimer_backend_t *)inst->backend_data;
    if (be == NULL) return;

    if (be->timer) {
        gptimer_stop(be->timer);
        gptimer_disable(be->timer);
        gptimer_del_timer(be->timer);
    }

    free(be);
    inst->backend_data = NULL;
}

/**
 * GPTimer backend operations vtable
 */
const dtr_backend_ops_t dtr_gptimer_ops = {
    .init = gptimer_backend_init,
    .start = gptimer_backend_start,
    .set_period = gptimer_backend_set_period,
    .read_counter = gptimer_backend_read_counter,
    .gpio_force = gptimer_backend_gpio_force,
    .deinit = gptimer_backend_deinit,
};
