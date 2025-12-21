/**
 * DTR - Disciplined Timer Realtime
 *
 * Instance-based timer with pluggable backends (MCPWM, GPTimer).
 * Core logic for cycle counting, alignment, and period dithering.
 */

#include "dtr.h"
#include "dtr_backend.h"
#include "dtc.h"
#include "soc/soc_caps.h"
#include "clock.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_private/wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "rom/ets_sys.h"
#include <stdlib.h>
#include <string.h>

#ifdef CONFIG_FTS_MQTT_ENABLED
#include "fts_mqtt.h"
#include "esp_timer.h"
#endif

#if CONFIG_FTS_MAC_TIMER_ALIGNMENT_TEST_CYCLES
#include "esp_random.h"
#endif

static const char *TAG = "dtr";

// Backend declarations (defined in backend source files)
#if CONFIG_DTR_BACKEND_MCPWM
extern const dtr_backend_ops_t dtr_mcpwm_ops;
#endif
#if CONFIG_DTR_BACKEND_GPTIMER
extern const dtr_backend_ops_t dtr_gptimer_ops;
#endif

// Default instance for backward-compatible static API
static dtr_instance_t *s_default_instance = NULL;

// ============================================================================
// Backend Registry
// ============================================================================

const dtr_backend_ops_t *dtr_get_backend(dtr_backend_type_t type)
{
    switch (type) {
#if CONFIG_DTR_BACKEND_MCPWM
        case DTR_BACKEND_MCPWM:
            return &dtr_mcpwm_ops;
#endif
#if CONFIG_DTR_BACKEND_GPTIMER
        case DTR_BACKEND_GPTIMER:
            return &dtr_gptimer_ops;
#endif
        default:
            return NULL;
    }
}

bool dtr_backend_available(dtr_backend_type_t type)
{
    return dtr_get_backend(type) != NULL;
}

// ============================================================================
// Core Period Handler (called by backends)
// ============================================================================

/**
 * Core period handler - called by backends at each period boundary (TEZ)
 * This is the heart of DTR: cycle counting, alignment, and dithering.
 *
 * Must be called from ISR context with instance spinlock NOT held.
 */
bool IRAM_ATTR dtr_core_period_handler(dtr_instance_t *inst)
{
    portENTER_CRITICAL_ISR(&inst->spinlock);

    // First aligned period just completed - release GPIO force to enable pulses
    if (inst->first_aligned_period) {
        inst->ops->gpio_force(inst, -1);
        inst->first_aligned_period = false;
    }

    // --- Cycle and tick accounting ---
    inst->cycle_counter++;
    inst->timer_base_ticks += inst->active_period_ticks;
    inst->active_period_ticks = inst->shadow_period_ticks;

    if (inst->align_request.pending) {
        // Capture old values for delta calculation
        int64_t old_cycle_counter = inst->cycle_counter;
        int64_t old_period_ticks = inst->period_ticks;

        // Apply alignment
        inst->cycle_counter = inst->align_request.aligned_cycle_counter;
        inst->period_ticks = inst->align_request.aligned_local_ticks - inst->timer_base_ticks;
        inst->base_period_fp16 = inst->align_request.aligned_base_period_fp16;
        inst->period_ticks_frac_acc = 0;

        // Roll forward if period too short
        // Cast to signed to avoid signed/unsigned comparison bug where -1 becomes ULLONG_MAX
        while (inst->period_ticks < (int64_t)DTR_MIN_PERIOD_TICKS) {
            inst->period_ticks += inst->base_period_fp16 / FP16_SCALE;
            inst->period_ticks_frac_acc += inst->base_period_fp16 % FP16_SCALE;
            if (inst->period_ticks_frac_acc >= FP16_SCALE) {
                inst->period_ticks++;
                inst->period_ticks_frac_acc -= FP16_SCALE;
            }
            inst->cycle_counter++;
        }

        // Provide feedback
        inst->align_request.pending = false;
        assert(!inst->align_feedback.ready);

        inst->align_feedback.cycle_counter = inst->cycle_counter;
        inst->align_feedback.cycle_delta = (int32_t)(inst->cycle_counter - old_cycle_counter);
        inst->align_feedback.period_ticks = inst->period_ticks;
        inst->align_feedback.period_ticks_delta = (int32_t)(inst->period_ticks - old_period_ticks);
        inst->align_feedback.ready = true;

        if (inst->state == DTR_STATE_RUNNING) {
            inst->state = DTR_STATE_ALIGNED;
            inst->first_aligned_period = true;
        }
    } else {
        // Regular dithering
        inst->period_ticks = inst->base_period_fp16 / FP16_SCALE;
        inst->period_ticks_frac_acc += inst->base_period_fp16 % FP16_SCALE;
        if (inst->period_ticks_frac_acc >= FP16_SCALE) {
            inst->period_ticks++;
            inst->period_ticks_frac_acc -= FP16_SCALE;
        }
    }

    portEXIT_CRITICAL_ISR(&inst->spinlock);

    // Update period via backend (outside critical section for backends that need it)
    // FIXME TODO: Instead of abort(), skip the bad alignment and recover gracefully.
    // If period_ticks is invalid, restore DTR_TIMER_PERIOD_TICKS and cancel alignment.
    // This provides defense-in-depth against bad CRM models producing invalid periods.
    if (inst->period_ticks <= 0 || inst->period_ticks > 65535) {
        ets_printf("FATAL: period_ticks=%lld out of range [1,65535]\n", (long long)inst->period_ticks);
        abort();
    }
    inst->ops->set_period(inst, (uint32_t)inst->period_ticks);
    inst->shadow_period_ticks = (uint16_t)inst->period_ticks;

    // Notify waiting task
    if (inst->tez_listener_task != NULL) {
        xTaskNotifyFromISR(inst->tez_listener_task, 0, eNoAction, NULL);
    }

    // Invoke application callback
    if ((inst->state == DTR_STATE_ALIGNED) && inst->app_callback) {
        inst->app_callback(inst->cycle_counter);
    }

    return false;
}

// ============================================================================
// Instance API Implementation
// ============================================================================

dtr_instance_t *dtr_create(dtr_backend_type_t backend, dtr_mode_t mode,
                           fts_callback_t callback, gpio_num_t pulse_gpio)
{
    // Get backend ops
    const dtr_backend_ops_t *ops = dtr_get_backend(backend);
    if (ops == NULL) {
        ESP_LOGE(TAG, "Backend type %d not available on this chip", backend);
        return NULL;
    }

    // Allocate instance
    dtr_instance_t *inst = calloc(1, sizeof(dtr_instance_t));
    if (inst == NULL) {
        ESP_LOGE(TAG, "Failed to allocate DTR instance");
        return NULL;
    }

    // Initialize instance fields
    inst->ops = ops;
    inst->mode = mode;
    inst->pulse_gpio = pulse_gpio;
    inst->state = DTR_STATE_NOT_STARTED;
    inst->cycle_counter = -1;  // First TEZ increments to 0
    inst->timer_base_ticks = 0;
    inst->period_ticks = DTR_TIMER_PERIOD_TICKS;
    inst->base_period_fp16 = (uint32_t)DTR_TIMER_PERIOD_TICKS * FP16_SCALE;
    inst->period_ticks_frac_acc = 0;
    inst->active_period_ticks = 0;
    inst->shadow_period_ticks = DTR_TIMER_PERIOD_TICKS;
    inst->first_aligned_period = false;
    inst->app_callback = callback;
    inst->tez_listener_task = NULL;
    inst->spinlock = (portMUX_TYPE)portMUX_INITIALIZER_UNLOCKED;

    // Initialize alignment request/feedback
    memset(&inst->align_request, 0, sizeof(inst->align_request));
    memset(&inst->align_feedback, 0, sizeof(inst->align_feedback));

    // Initialize backend hardware
    esp_err_t err = inst->ops->init(inst, pulse_gpio, DTR_TIMER_PERIOD_TICKS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Backend init failed: %s", esp_err_to_name(err));
        free(inst);
        return NULL;
    }

    ESP_LOGI(TAG, "DTR instance created: %s, %llu MHz, period=%lu ticks, GPIO %d, backend=%d",
             (mode == DTR_MODE_MASTER ? "MASTER" : "SLAVE"),
             DTR_TIMER_RESOLUTION_HZ / 1000000,
             (unsigned long)DTR_TIMER_PERIOD_TICKS,
             pulse_gpio,
             backend);

#ifdef CONFIG_FTS_ROLE_SLAVE
    ESP_LOGI(TAG, "Compensation: %d ns", DTR_COMPENSATION_NS);
#endif

#ifdef CONFIG_FTS_CSV_OUTPUT
    printf("DTR,cycle,cycle_delta,period_ticks,period_ticks_delta\n");
#endif

    return inst;
}

void dtr_destroy(dtr_instance_t *inst)
{
    if (inst == NULL) return;

    if (inst->ops && inst->ops->deinit) {
        inst->ops->deinit(inst);
    }

    free(inst);
}

// ============================================================================
// MAC Clock / Timer Offset Measurement
// ============================================================================

/**
 * Catch MAC clock transition (instance version)
 */
static bool s_catch_mac_clock_transition_inst(dtr_instance_t *inst,
                                              int64_t *timer_abs_before_ticks,
                                              int64_t *mac_clock_transition_us,
                                              int64_t *timer_abs_after_ticks,
                                              uint32_t *iterations_out)
{
    uint32_t timer_pre, timer_before, timer_after, timer_post;
    uint32_t mac_clock_us_pre, mac_clock_us_1, mac_clock_us_2, mac_clock_us_post;
    int64_t timer_base_ticks_pre, mac_clock_base_us;
    uint32_t iterations = 0;

    // Sample before critical section
    timer_pre = inst->ops->read_counter(inst);
    mac_clock_us_pre = esp_wifi_internal_get_mac_clock_time();
    mac_clock_base_us = clock_get_base_us();

    portENTER_CRITICAL(&inst->spinlock);
    timer_base_ticks_pre = inst->timer_base_ticks;

    // Tight loop to catch MAC transition
    // Timeout after 5000 iterations to avoid triggering interrupt WDT on slower chips (C3)
    do {
        iterations++;
        if (iterations > 5000) {
            // Timeout - exit critical section and signal retry
            portEXIT_CRITICAL(&inst->spinlock);
            return false;
        }
        timer_before = inst->ops->read_counter(inst);
        mac_clock_us_1 = esp_wifi_internal_get_mac_clock_time();
        mac_clock_us_2 = esp_wifi_internal_get_mac_clock_time();
        timer_after = inst->ops->read_counter(inst);
    } while (mac_clock_us_2 == mac_clock_us_1);

    timer_post = inst->ops->read_counter(inst);
    mac_clock_us_post = esp_wifi_internal_get_mac_clock_time();

    portEXIT_CRITICAL(&inst->spinlock);

    // Check timer monotonicity
    if (timer_before < timer_pre || timer_after < timer_before || timer_post < timer_after) {
        return false;
    }

    // Check MAC monotonicity
    if (mac_clock_us_1 < mac_clock_us_pre || mac_clock_us_2 < mac_clock_us_1 || mac_clock_us_post < mac_clock_us_2) {
        return false;
    }

    *timer_abs_before_ticks = timer_base_ticks_pre + timer_before;
    *timer_abs_after_ticks = timer_base_ticks_pre + timer_after;
    *mac_clock_transition_us = mac_clock_base_us + mac_clock_us_2;
    *iterations_out = iterations;

    return true;
}

/**
 * Refine MAC clock / timer start offset estimate
 */
static void s_refine_mac_clock_timer_start_offset(int64_t *min, int64_t *max,
                                                  int64_t timer_abs_before_ticks,
                                                  int64_t mac_clock_transition_us,
                                                  int64_t timer_abs_after_ticks)
{
    int64_t mac_at_transition_ticks = mac_clock_transition_us * TIMER_TICKS_PER_US;
    assert(timer_abs_after_ticks < mac_at_transition_ticks);

    int64_t new_min = mac_at_transition_ticks - timer_abs_after_ticks;
    int64_t new_max = mac_at_transition_ticks - timer_abs_before_ticks;
    assert(new_min <= new_max);

    if (new_min > *min) *min = new_min;
    if (new_max < *max) *max = new_max;
}

/**
 * Measure MAC clock / timer start offset (instance version)
 */
static int64_t s_measure_mac_clock_timer_start_offset_inst(dtr_instance_t *inst, uint32_t run_id)
{
    int64_t offset_ticks_min = 0;
    int64_t offset_ticks_max = INT64_MAX;
    uint32_t total_iterations = 0;
    uint32_t min_iterations = UINT32_MAX;
    uint32_t max_iterations = 0;

    ESP_LOGI(TAG, "Measuring MAC clock / timer start offset, %lu samples...",
             (unsigned long)DTR_MAC_TIMER_ALIGNMENT_MAX_SAMPLES);

    for (uint32_t samples = 0; samples < DTR_MAC_TIMER_ALIGNMENT_MAX_SAMPLES; samples++) {
        uint32_t iterations;
        int64_t mac_clock_transition_us, timer_abs_before, timer_abs_after;

        if (!s_catch_mac_clock_transition_inst(inst, &timer_abs_before, &mac_clock_transition_us,
                                               &timer_abs_after, &iterations)) {
            continue;
        }

        total_iterations += iterations;
        if (iterations < min_iterations) min_iterations = iterations;
        if (iterations > max_iterations) max_iterations = iterations;

        s_refine_mac_clock_timer_start_offset(&offset_ticks_min, &offset_ticks_max,
                                              timer_abs_before, mac_clock_transition_us, timer_abs_after);

        // Yield periodically to avoid task WDT (more frequently on single-core chips like C3)
        if ((samples & 0x3FF) == 0) vTaskDelay(1);
    }

    int64_t offset_ticks = (offset_ticks_min + offset_ticks_max) / 2;
    float avg_iterations = (float)total_iterations / DTR_MAC_TIMER_ALIGNMENT_MAX_SAMPLES;

    ESP_LOGI(TAG, "Offset: [%lld - %lld = %lld] avg %lld (%.3lf us), loop: avg=%.3f, min=%lu, max=%lu",
             (long long)offset_ticks_max, (long long)offset_ticks_min,
             (long long)(offset_ticks_max - offset_ticks_min),
             (long long)offset_ticks, (double)offset_ticks / TIMER_TICKS_PER_US,
             avg_iterations, min_iterations, max_iterations);

#if CONFIG_FTS_MAC_TIMER_ALIGNMENT_TEST_CYCLES
    printf("MAC_TIMER_ALIGN,%lu,%lld,%lld,%lld\n", (unsigned long)run_id, (long long)offset_ticks,
           (long long)offset_ticks_min, (long long)offset_ticks_max);
#endif

    return offset_ticks;
}

// ============================================================================
// Instance API Functions
// ============================================================================

void dtr_start_timer_inst(dtr_instance_t *inst)
{
    if (inst->state != DTR_STATE_NOT_STARTED) {
        ESP_LOGE(TAG, "Timer already started");
        abort();
    }

    // Initialize counters and enter RUNNING state
    portENTER_CRITICAL(&inst->spinlock);
    inst->timer_base_ticks = 0;
    inst->cycle_counter = 0;
    inst->state = DTR_STATE_RUNNING;
    portEXIT_CRITICAL(&inst->spinlock);

    // Start backend timer
    ESP_ERROR_CHECK(inst->ops->start(inst));

#if CONFIG_FTS_MAC_TIMER_ALIGNMENT_TEST_CYCLES
    uint32_t run_id = esp_random();
    printf("MAC_TIMER_ALIGN,run,offset_ticks,offset_ticks_min,offset_ticks_max\n");

    for (int i = 0; i < CONFIG_FTS_MAC_TIMER_ALIGNMENT_TEST_CYCLES; i++) {
        s_measure_mac_clock_timer_start_offset_inst(inst, run_id);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    ESP_LOGI(TAG, "Done %d test cycles, restarting...", CONFIG_FTS_MAC_TIMER_ALIGNMENT_TEST_CYCLES);
    esp_restart();
#else
    int64_t offset = s_measure_mac_clock_timer_start_offset_inst(inst, 0);
    portENTER_CRITICAL(&inst->spinlock);
    inst->timer_base_ticks += offset;
    portEXIT_CRITICAL(&inst->spinlock);
#endif
}

void dtr_align_master_timer_inst(dtr_instance_t *inst)
{
    dtr_register_tez_listener_inst(inst, xTaskGetCurrentTaskHandle());
    xTaskNotifyStateClear(NULL);
    dtr_wait_for_tez_inst(inst);

    int64_t timer_base = dtr_get_timer_base_ticks_inst(inst);
    int64_t current_cycle = timer_base / DTR_TIMER_PERIOD_TICKS;
    int64_t aligned_cycle = current_cycle + 2;
    int64_t aligned_ticks = aligned_cycle * DTR_TIMER_PERIOD_TICKS;

    dtr_set_align_request_inst(inst, aligned_cycle, aligned_ticks,
                               DTR_TIMER_PERIOD_TICKS * FP16_SCALE);

    ESP_LOGI(TAG, "Master alignment baseline: %lld ticks (%.3f us), cycle=%lld",
             (long long)timer_base, (float)timer_base / TIMER_TICKS_PER_US, (long long)current_cycle);

    dtr_wait_for_tez_inst(inst);
    dtr_grab_n_log_align_feedback_inst(inst);
    dtr_register_tez_listener_inst(inst, NULL);
}

void dtr_set_align_request_inst(dtr_instance_t *inst,
                                int64_t aligned_cycle_counter,
                                int64_t aligned_local_ticks,
                                int64_t aligned_base_period_fp16)
{
    portENTER_CRITICAL(&inst->spinlock);
    inst->align_request.aligned_cycle_counter = aligned_cycle_counter;
    inst->align_request.aligned_local_ticks = aligned_local_ticks;
    inst->align_request.aligned_base_period_fp16 = aligned_base_period_fp16;
    inst->align_request.pending = true;
    inst->align_feedback.ready = false;
    portEXIT_CRITICAL(&inst->spinlock);

    ESP_LOGI(TAG, "// Alignment request: cycle=%lld, ticks=%lld, base_period=%lld FP16 %ld",
             (long long)aligned_cycle_counter,
             (long long)aligned_local_ticks,
             (long long)aligned_base_period_fp16 / FP16_SCALE,
             (long long)aligned_base_period_fp16 % FP16_SCALE);

#ifdef CONFIG_FTS_MQTT_ENABLED
    if (fts_mqtt_is_connected()) {
        fts_mqtt_publish_dtc_request(
            esp_timer_get_time(),
            aligned_cycle_counter,
            aligned_local_ticks,
            (uint32_t)(aligned_base_period_fp16 / FP16_SCALE),
            (uint32_t)(aligned_base_period_fp16 % FP16_SCALE));
    }
#endif
}

void dtr_wait_for_tez_inst(dtr_instance_t *inst)
{
    (void)inst;  // TEZ notification is per-task, not per-instance
    if (xTaskNotifyWait(0, 0, NULL, pdMS_TO_TICKS(1000)) != pdTRUE) {
        ESP_LOGE(TAG, "TEZ notification timeout");
        abort();
    }
}

void dtr_grab_n_log_align_feedback_inst(dtr_instance_t *inst)
{
    align_feedback_t feedback;
    portENTER_CRITICAL(&inst->spinlock);
    feedback = inst->align_feedback;
    portEXIT_CRITICAL(&inst->spinlock);

    assert(feedback.ready);

    ESP_LOGI(TAG, "Alignment feedback: period_ticks=%ld (%+ld), cycle=%lld (%+ld)",
             feedback.period_ticks, feedback.period_ticks_delta,
             (long long)feedback.cycle_counter, feedback.cycle_delta);

#ifdef CONFIG_FTS_CSV_OUTPUT
    printf("DTR,%lld,%ld,%ld,%ld\n",
           (long long)feedback.cycle_counter,
           feedback.cycle_delta,
           feedback.period_ticks,
           feedback.period_ticks_delta);
#endif

#ifdef CONFIG_FTS_MQTT_ENABLED
    if (fts_mqtt_is_connected()) {
        fts_mqtt_publish_dtr_feedback(
            esp_timer_get_time(),
            feedback.period_ticks,
            feedback.period_ticks_delta,
            feedback.cycle_delta);
    }
#endif
}

int64_t dtr_get_timer_base_ticks_inst(dtr_instance_t *inst)
{
    portENTER_CRITICAL(&inst->spinlock);
    int64_t ticks = inst->timer_base_ticks;
    portEXIT_CRITICAL(&inst->spinlock);
    return ticks;
}

void dtr_register_tez_listener_inst(dtr_instance_t *inst, TaskHandle_t task_handle)
{
    inst->tez_listener_task = task_handle;
}

uint32_t dtr_read_timer_count_inst(dtr_instance_t *inst)
{
    return inst->ops->read_counter(inst);
}

// ============================================================================
// Static API (backward compatibility)
// ============================================================================

esp_err_t dtr_init(dtr_mode_t mode, fts_callback_t callback, gpio_num_t pulse_gpio)
{
    // Use MCPWM backend by default (for backward compatibility)
    // On chips without MCPWM, use GPTimer
    dtr_backend_type_t backend = DTR_BACKEND_MCPWM;
    if (!dtr_backend_available(DTR_BACKEND_MCPWM)) {
        backend = DTR_BACKEND_GPTIMER;
    }

    s_default_instance = dtr_create(backend, mode, callback, pulse_gpio);
    return (s_default_instance != NULL) ? ESP_OK : ESP_FAIL;
}

void dtr_start_timer(void)
{
    dtr_start_timer_inst(s_default_instance);
}

void dtr_align_master_timer(void)
{
    dtr_align_master_timer_inst(s_default_instance);
}

void dtr_set_align_request(int64_t aligned_cycle_counter,
                           int64_t aligned_local_ticks,
                           int64_t aligned_base_period_fp16)
{
    dtr_set_align_request_inst(s_default_instance, aligned_cycle_counter,
                               aligned_local_ticks, aligned_base_period_fp16);
}

void dtr_wait_for_tez(void)
{
    dtr_wait_for_tez_inst(s_default_instance);
}

void dtr_grab_n_log_align_feedback(void)
{
    dtr_grab_n_log_align_feedback_inst(s_default_instance);
}

int64_t dtr_get_timer_base_ticks(void)
{
    return dtr_get_timer_base_ticks_inst(s_default_instance);
}

uint32_t dtr_get_master_cycle(void)
{
    portENTER_CRITICAL(&s_default_instance->spinlock);
    uint32_t cycle = s_default_instance->cycle_counter;
    portEXIT_CRITICAL(&s_default_instance->spinlock);
    return cycle;
}

void dtr_register_tez_listener(TaskHandle_t task_handle)
{
    dtr_register_tez_listener_inst(s_default_instance, task_handle);
}
