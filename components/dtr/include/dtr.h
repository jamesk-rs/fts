/**
 * DTR - Disciplined Timer Realtime
 *
 * Timer with ISR for synchronized operation.
 * Supports multiple backends (MCPWM, GPTimer) and multiple simultaneous instances.
 * Handles TEZ (timer empty) events and invokes application callback.
 */

#pragma once

#include "esp_err.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "dtr_backend.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Timer configuration constants - primary parameters
#define DTR_TIMER_RESOLUTION_HZ 40000000ULL     // 40 MHz timer clock
#define DTR_TIMER_PERIOD_US 500                 // 500µs nominal period (2 kHz)
#define DTR_PULSE_DUTY_PERCENT 5                // GPIO pulse duty cycle (%)

// Fixed delay compensation in nanoseconds
#define DTR_COMPENSATION_NS -200

// Derived constants - calculated from primary parameters
#define TIMER_TICKS_PER_US (DTR_TIMER_RESOLUTION_HZ / 1000000ULL)
#define DTR_TIMER_PERIOD_TICKS (DTR_TIMER_PERIOD_US * TIMER_TICKS_PER_US)
#define DTR_PULSE_WIDTH_TICKS (DTR_TIMER_PERIOD_TICKS * DTR_PULSE_DUTY_PERCENT / 100)

// Minimum period in timer ticks to prevent race conditions
// Derived from CPU cycles - ensures ISR + user callback have time to complete
// At 240MHz CPU, 5000 cycles ≈ 21µs ≈ 3360 ticks at 160MHz timer
#define DTR_MIN_PERIOD_TICKS ((CONFIG_FTS_MIN_PERIOD_CPU_CYCLES * DTR_TIMER_RESOLUTION_HZ) / (CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ * 1000000))

#define DTR_MAC_TIMER_ALIGNMENT_MAX_SAMPLES 100000  // Max samples for MAC-timer alignment

// Time unit conversion constants - derived from resolution
#define DTR_PS_PER_TICK (1000000000000ULL / DTR_TIMER_RESOLUTION_HZ)  // picoseconds per tick
#define DTR_NS_TO_TICKS(ns) ((int32_t)((ns) * (int64_t)TIMER_TICKS_PER_US / 1000))  // Convert ns to ticks

// Fixed-point arithmetic
#define FP16_SCALE 65536                        // 16-bit fixed-point scale (2^16)

/**
 * DTR mode
 */
typedef enum {
    DTR_MODE_MASTER,  // Free-running timer, no disciplining
    DTR_MODE_SLAVE    // Disciplined timer with error correction
} dtr_mode_t;

/**
 * DTR state machine
 */
typedef enum {
    DTR_STATE_NOT_STARTED,   // Timer not yet started
    DTR_STATE_RUNNING,       // Running at operational period, not yet aligned
    DTR_STATE_ALIGNED        // Locked and aligned with disciplining
} dtr_state_t;

/**
 * Application callback invoked on each timer cycle
 * Runs in ISR context!
 *
 * @param master_cycle Current master cycle number
 */
typedef void (*fts_callback_t)(uint32_t master_cycle);

/**
 * Alignment feedback structure (returned by dtr_grab_n_log_align_feedback)
 * Reports DELTAS per Slide 23: cycle_counter - old_cycle_counter, period_ticks - old_period_ticks
 */
typedef struct {
    bool ready;                 // True if ISR has updated the feedback
    int64_t cycle_counter;      // Current cycle_counter after alignment
    int32_t cycle_delta;        // cycle_counter - old_cycle_counter (normally 1)
    int32_t period_ticks;       // New period_ticks (the jump period)
    int32_t period_ticks_delta; // period_ticks - old_period_ticks (normally ~0)
} align_feedback_t;

// ============================================================================
// Instance-based API (supports multiple simultaneous timers)
// ============================================================================

/**
 * DTR instance structure
 * Contains all state for a single timer instance.
 * Defined here to allow stack allocation; treat fields as private.
 */
struct dtr_instance_s {
    // Backend
    const dtr_backend_ops_t *ops;
    void *backend_data;  // Backend-specific handle storage

    // Configuration
    dtr_mode_t mode;
    gpio_num_t pulse_gpio;

    // State machine
    dtr_state_t state;

    // Cycle and tick counters
    int64_t cycle_counter;
    int64_t timer_base_ticks;
    int64_t period_ticks;

    // Period dithering (fixed-point)
    uint32_t base_period_fp16;
    int32_t period_ticks_frac_acc;

    // Shadow register tracking (MCPWM-specific semantics but useful for all)
    uint16_t active_period_ticks;
    uint16_t shadow_period_ticks;

    // Alignment request (from DTC to ISR)
    struct {
        bool pending;
        int64_t aligned_local_ticks;
        int64_t aligned_cycle_counter;
        int64_t aligned_base_period_fp16;
    } align_request;

    // Alignment feedback (from ISR to DTC)
    align_feedback_t align_feedback;

    // First aligned period flag (pulse suppressed during stretch)
    bool first_aligned_period;

    // Application callback
    fts_callback_t app_callback;

    // Task handle for TEZ notifications
    TaskHandle_t tez_listener_task;

    // Spinlock for ISR synchronization
    portMUX_TYPE spinlock;
};

/**
 * Create and initialize a DTR instance
 *
 * @param backend Backend type to use
 * @param mode Master or slave mode
 * @param callback Application callback (invoked in ISR!)
 * @param pulse_gpio GPIO pin for hardware pulse generation
 * @return Pointer to instance, or NULL on failure
 */
dtr_instance_t *dtr_create(dtr_backend_type_t backend, dtr_mode_t mode,
                           fts_callback_t callback, gpio_num_t pulse_gpio);

/**
 * Destroy a DTR instance and release resources
 *
 * @param inst Instance to destroy
 */
void dtr_destroy(dtr_instance_t *inst);

/**
 * Start timer in free-running mode (instance API)
 *
 * @param inst DTR instance
 */
void dtr_start_timer_inst(dtr_instance_t *inst);

/**
 * Align timer to MAC clock epoch boundaries - master mode only (instance API)
 *
 * @param inst DTR instance
 */
void dtr_align_master_timer_inst(dtr_instance_t *inst);

/**
 * Set alignment parameters (instance API)
 *
 * @param inst DTR instance
 * @param aligned_cycle_counter Target master cycle value
 * @param aligned_local_ticks Target local tick value
 * @param aligned_base_period_fp16 New timer period in FP16
 */
void dtr_set_align_request_inst(dtr_instance_t *inst,
                                int64_t aligned_cycle_counter,
                                int64_t aligned_local_ticks,
                                int64_t aligned_base_period_fp16);

/**
 * Wait for TEZ event (instance API)
 *
 * @param inst DTR instance
 */
void dtr_wait_for_tez_inst(dtr_instance_t *inst);

/**
 * Get and log alignment feedback (instance API)
 *
 * @param inst DTR instance
 */
void dtr_grab_n_log_align_feedback_inst(dtr_instance_t *inst);

/**
 * Get current local ticks (instance API)
 *
 * @param inst DTR instance
 * @return Current local tick count
 */
int64_t dtr_get_timer_base_ticks_inst(dtr_instance_t *inst);

/**
 * Register task handle for TEZ notifications (instance API)
 *
 * @param inst DTR instance
 * @param task_handle Task handle to notify on TEZ
 */
void dtr_register_tez_listener_inst(dtr_instance_t *inst, TaskHandle_t task_handle);

/**
 * Read timer counter directly (instance API)
 *
 * @param inst DTR instance
 * @return Current counter value
 */
uint32_t dtr_read_timer_count_inst(dtr_instance_t *inst);

/**
 * Core period handler - called by backends at each period boundary
 * Implements the DTR state machine logic.
 *
 * @param inst DTR instance
 * @return true if higher-priority task was woken, false otherwise
 */
bool dtr_core_period_handler(dtr_instance_t *inst);

// ============================================================================
// Static API (backward compatibility - uses default instance)
// ============================================================================

/**
 * Initialize DTR module
 *
 * @param mode Master or slave mode
 * @param callback Application callback (invoked in ISR!)
 * @param pulse_gpio GPIO pin for hardware pulse generation
 * @return ESP_OK on success
 */
esp_err_t dtr_init(dtr_mode_t mode, fts_callback_t callback, gpio_num_t pulse_gpio);

/**
 * Start timer in free-running mode
 * Captures MAC clock and starts timer with local_ticks=0, cycle=0
 * Timer will run unaligned until DTC calculates alignment
 */
void dtr_start_timer(void);

/**
 * Align timer to MAC clock epoch boundaries (master mode only)
 * Calculates and applies alignment to synchronize timer with MAC clock epochs
 * Must be called after dtr_start_timer()
 */
void dtr_align_master_timer(void);

/**
 * Set alignment parameters (called by DTC)
 * These params will be applied on next TEZ (per Slide 19)
 *
 * @param aligned_cycle_counter Target master cycle value
 * @param aligned_local_ticks Target local tick value (aligned to epoch)
 * @param aligned_base_period_fp16 New timer period in FP16 (period_ticks * 65536 + fraction)
 */
void dtr_set_align_request(int64_t aligned_cycle_counter,
                              int64_t aligned_local_ticks,
                              int64_t aligned_base_period_fp16);

void dtr_wait_for_tez(void);

/**
 * Get alignment feedback from ISR (called by DTC after setting alignment params)
 * Returns feedback about how the alignment was applied
 *
 * @param feedback Pointer to structure to fill with feedback data
 */
void dtr_grab_n_log_align_feedback(void);

/**
 * Get current local ticks (thread-safe)
 *
 * @return Current local tick count
 */
int64_t dtr_get_timer_base_ticks(void);

/**
 * Register task handle for TEZ notifications
 * The registered task will receive a notification on each TEZ event
 *
 * @param task_handle Task handle to notify on TEZ
 */
void dtr_register_tez_listener(TaskHandle_t task_handle);
