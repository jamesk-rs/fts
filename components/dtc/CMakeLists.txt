idf_component_register(
    SRCS "dtc.c"
    INCLUDE_DIRS "include"
    REQUIRES crm dtr
    PRIV_REQUIRES fts_mqtt
)
