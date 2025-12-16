set(SRCS "dtr.c")

# Add MCPWM backend if enabled (depends on SOC support)
if(CONFIG_DTR_BACKEND_MCPWM)
    list(APPEND SRCS "dtr_mcpwm.c")
endif()

# Add GPTimer backend if enabled (always available)
if(CONFIG_DTR_BACKEND_GPTIMER)
    list(APPEND SRCS "dtr_gptimer.c")
endif()

idf_component_register(
    SRCS ${SRCS}
    INCLUDE_DIRS "include"
    REQUIRES driver dtc
    PRIV_REQUIRES esp_wifi clock
)
