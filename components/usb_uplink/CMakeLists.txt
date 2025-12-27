idf_component_register(
    SRCS "usb_uplink.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_netif esp_event
    PRIV_REQUIRES esp_tinyusb
)
