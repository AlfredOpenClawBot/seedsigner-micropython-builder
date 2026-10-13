# Older ESP32-P4 silicon (chip revisions 0.x and 1.x). The default build targets
# revision 3.x; the two silicon families cannot run the same image, so a board with an
# older chip needs this variant:
#
#   BOARD_VARIANT=PRE_REV3 make docker-build-all
#
# Mirrors upstream MicroPython's ESP32_GENERIC_P4 PRE_REV3 variant.
list(APPEND SDKCONFIG_DEFAULTS
    boards/sdkconfig.p4_pre_rev3
)
