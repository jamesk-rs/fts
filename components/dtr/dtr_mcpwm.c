/**
 * DTR MCPWM Backend
 *
 * MCPWM-based timer implementation with hardware PWM pulse generation.
 * Available on ESP32, ESP32-S3, and other chips with MCPWM peripheral.
 *
 * This file is only compiled when CONFIG_DTR_BACKEND_MCPWM is enabled
 * (see CMakeLists.txt), which depends on SOC_MCPWM_SUPPORTED.
 */

#include "sdkconfig.h"
#include "dtr.h"
#include "dtr_backend.h"
#include "driver/mcpwm_timer.h"
#include "driver/mcpwm_oper.h"
#include "driver/mcpwm_cmpr.h"
#include "driver/mcpwm_gen.h"
#include "hal/mcpwm_ll.h"
#include "esp_log.h"
#include <stdlib.h>

static const char *TAG = "dtr_mcpwm";

/**
 * MCPWM backend-specific data
 */
typedef struct {
    mcpwm_timer_handle_t timer;
    mcpwm_oper_handle_t operator;
    mcpwm_cmpr_handle_t comparator;
    mcpwm_gen_handle_t generator;
} mcpwm_backend_t;

/**
 * TEZ (timer empty) ISR callback
 * Called at the start of each timer period.
 */
static bool IRAM_ATTR mcpwm_tez_handler(mcpwm_timer_handle_t timer,
                                        const mcpwm_timer_event_data_t *edata,
                                        void *user_ctx)
{
    dtr_instance_t *inst = (dtr_instance_t *)user_ctx;
    return dtr_core_period_handler(inst);
}

/**
 * Initialize MCPWM hardware
 */
static esp_err_t mcpwm_backend_init(dtr_instance_t *inst, gpio_num_t gpio, uint32_t period_ticks)
{
    mcpwm_backend_t *be = calloc(1, sizeof(mcpwm_backend_t));
    if (be == NULL) {
        return ESP_ERR_NO_MEM;
    }
    inst->backend_data = be;

    // Create MCPWM timer
    mcpwm_timer_config_t timer_config = {
        .group_id = 0,
        .clk_src = MCPWM_TIMER_CLK_SRC_DEFAULT,
        .resolution_hz = DTR_TIMER_RESOLUTION_HZ,
        .count_mode = MCPWM_TIMER_COUNT_MODE_UP,
        .period_ticks = period_ticks,
        .flags.update_period_on_empty = true,  // Shadow register
    };
    ESP_ERROR_CHECK(mcpwm_new_timer(&timer_config, &be->timer));

    // Register TEZ handler
    mcpwm_timer_event_callbacks_t cbs = {
        .on_empty = mcpwm_tez_handler,
    };
    ESP_ERROR_CHECK(mcpwm_timer_register_event_callbacks(be->timer, &cbs, inst));

    // Create operator
    mcpwm_operator_config_t operator_config = {
        .group_id = 0,
    };
    ESP_ERROR_CHECK(mcpwm_new_operator(&operator_config, &be->operator));
    ESP_ERROR_CHECK(mcpwm_operator_connect_timer(be->operator, be->timer));

    // Create comparator for pulse width
    mcpwm_comparator_config_t comparator_config = {
        .flags.update_cmp_on_tez = true,
    };
    ESP_ERROR_CHECK(mcpwm_new_comparator(be->operator, &comparator_config, &be->comparator));
    ESP_ERROR_CHECK(mcpwm_comparator_set_compare_value(be->comparator, DTR_PULSE_WIDTH_TICKS));

    // Create generator
    mcpwm_generator_config_t generator_config = {
        .gen_gpio_num = gpio,
    };
    ESP_ERROR_CHECK(mcpwm_new_generator(be->operator, &generator_config, &be->generator));

    // Configure generator actions: HIGH on TEZ, LOW on compare
    ESP_ERROR_CHECK(mcpwm_generator_set_action_on_timer_event(be->generator,
        MCPWM_GEN_TIMER_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, MCPWM_TIMER_EVENT_EMPTY, MCPWM_GEN_ACTION_HIGH)));
    ESP_ERROR_CHECK(mcpwm_generator_set_action_on_compare_event(be->generator,
        MCPWM_GEN_COMPARE_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, be->comparator, MCPWM_GEN_ACTION_LOW)));

    // Force GPIO LOW until aligned
    ESP_ERROR_CHECK(mcpwm_generator_set_force_level(be->generator, 0, true));

    ESP_LOGI(TAG, "MCPWM backend initialized");
    return ESP_OK;
}

/**
 * Enable and start the timer
 */
static esp_err_t mcpwm_backend_start(dtr_instance_t *inst)
{
    mcpwm_backend_t *be = (mcpwm_backend_t *)inst->backend_data;

    ESP_ERROR_CHECK(mcpwm_timer_enable(be->timer));
    ESP_ERROR_CHECK(mcpwm_timer_start_stop(be->timer, MCPWM_TIMER_START_NO_STOP));

    return ESP_OK;
}

/**
 * Set timer period (loaded into shadow register, takes effect at TEZ)
 * Called from ISR context.
 */
static void IRAM_ATTR mcpwm_backend_set_period(dtr_instance_t *inst, uint32_t period_ticks)
{
    mcpwm_backend_t *be = (mcpwm_backend_t *)inst->backend_data;
    mcpwm_timer_set_period(be->timer, (uint16_t)period_ticks);
}

/**
 * Read timer counter value directly (HAL layer)
 * May be called from ISR context.
 */
static uint32_t IRAM_ATTR mcpwm_backend_read_counter(dtr_instance_t *inst)
{
    (void)inst;
    // Access MCPWM group 0, timer 0 counter register
    return mcpwm_ll_timer_get_count_value(MCPWM_LL_GET_HW(0), 0);
}

/**
 * Force GPIO to specific level or release
 * Called from ISR context.
 */
static void IRAM_ATTR mcpwm_backend_gpio_force(dtr_instance_t *inst, int level)
{
    mcpwm_backend_t *be = (mcpwm_backend_t *)inst->backend_data;
    mcpwm_generator_set_force_level(be->generator, level, true);
}

/**
 * Cleanup and release resources
 */
static void mcpwm_backend_deinit(dtr_instance_t *inst)
{
    mcpwm_backend_t *be = (mcpwm_backend_t *)inst->backend_data;
    if (be == NULL) return;

    if (be->generator) mcpwm_del_generator(be->generator);
    if (be->comparator) mcpwm_del_comparator(be->comparator);
    if (be->operator) mcpwm_del_operator(be->operator);
    if (be->timer) {
        mcpwm_timer_disable(be->timer);
        mcpwm_del_timer(be->timer);
    }

    free(be);
    inst->backend_data = NULL;
}

/**
 * MCPWM backend operations vtable
 */
const dtr_backend_ops_t dtr_mcpwm_ops = {
    .init = mcpwm_backend_init,
    .start = mcpwm_backend_start,
    .set_period = mcpwm_backend_set_period,
    .read_counter = mcpwm_backend_read_counter,
    .gpio_force = mcpwm_backend_gpio_force,
    .deinit = mcpwm_backend_deinit,
};
