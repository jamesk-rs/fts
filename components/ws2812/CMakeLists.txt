idf_component_register(
    SRCS "ws2812.c" "led_strip_encoder.c"
    INCLUDE_DIRS "include" "."
    REQUIRES driver freertos
)
