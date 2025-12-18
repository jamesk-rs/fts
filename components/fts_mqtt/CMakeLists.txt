idf_component_register(
    SRCS "fts_mqtt.c"
    INCLUDE_DIRS "include"
    REQUIRES mqtt json esp_timer
    PRIV_REQUIRES ftm
)
