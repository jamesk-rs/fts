#pragma once

#include <stdio.h>
#include <string.h>
#include <stdint.h>

//Put the defs here so the code is easier to read... All the other defs are in sdkconfig.defaults
//Uncomment the one you're trying to build
//#define CONFIG_FTS_ROLE_SLAVE
//#define CONFIG_FTS_ROLE_MASTER


// Time Sync Configuration - SLAVE role
#ifdef CONFIG_FTS_ROLE_SLAVE

    // MQTT device ID for slave and enable RL control
    #define CONFIG_FTS_MQTT_DEVICE_ID "slave1"

    #define CONFIG_1PPS_LED_GPIO 18
    #define CONFIG_FTS_LED_WS2812 1
    #define CONFIG_FTS_LED_ENABLED 1
    //#define CONFIG_FTS_PULSE_MCPWM_GPIO_ENABLE 1
    //#define CONFIG_FTS_PULSE_MCPWM_GPIO 7

#elif CONFIG_FTS_ROLE_MASTER
    // MQTT device ID for master
    #define CONFIG_FTS_MQTT_DEVICE_ID "master"

    #define CONFIG_1PPS_LED_GPIO 18
    #define CONFIG_FTS_LED_WS2812 1
    #define CONFIG_FTS_LED_ENABLED 1

#endif

// static void mqtt_control_callback(int32_t period_correction_fp16,
//                                    float phase_error_ns, float gain_K);