#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKDIR="${1:-$ROOT_DIR/deps}"
MP_DIR="$WORKDIR/micropython/upstream"
SCREENS_DIR="$WORKDIR/seedsigner-lvgl-screens"

IDF_DIR="${IDF_DIR:-}"
if [ -z "$IDF_DIR" ]; then
  if [ -d "/opt/toolchains/esp-idf" ]; then
    IDF_DIR="/opt/toolchains/esp-idf"
  else
    IDF_DIR="$WORKDIR/esp-idf"
  fi
fi

BOARD="${BOARD:-WAVESHARE_ESP32_S3_TOUCH_LCD_35B}"
# Optional MicroPython board variant (the board's mpconfigvariant_<VARIANT>.cmake).
# ESP32-P4 boards use it to pick the silicon family: the default build targets chip
# revision v3.x, and BOARD_VARIANT=PRE_REV3 targets the older v0.x/v1.x chips. The two
# families cannot run the same image, so each variant gets its own build dir.
BOARD_VARIANT="${BOARD_VARIANT:-}"
BUILD_NAME="$BOARD${BOARD_VARIANT:+-$BOARD_VARIANT}"
BUILD_DIR="${BUILD_DIR:-$ROOT_DIR/build/$BUILD_NAME}"
LOGS_DIR="${LOGS_DIR:-$ROOT_DIR/logs}"

if [ ! -e "$MP_DIR/.git" ]; then
  echo "ERROR: expected MicroPython repo at $MP_DIR"
  exit 1
fi
if [ ! -e "$SCREENS_DIR/.git" ]; then
  echo "ERROR: expected seedsigner-lvgl-screens repo at $SCREENS_DIR"
  exit 1
fi
if [ ! -d "$IDF_DIR" ]; then
  echo "ERROR: expected ESP-IDF at $IDF_DIR"
  exit 1
fi

mkdir -p "$BUILD_DIR" "$LOGS_DIR"
TS="$(date -u +%Y-%m-%d_%H%M%SZ)"
BUILD_LOG="$LOGS_DIR/${TS}-build-${BUILD_NAME}.log"

echo "Build log: $BUILD_LOG"

if [ -z "${IDF_TOOLS_PATH:-}" ]; then
  if [ -d "/opt/espressif" ]; then
    export IDF_TOOLS_PATH="/opt/espressif"
  else
    export IDF_TOOLS_PATH="$ROOT_DIR/.espressif"
  fi
fi

export IDF_PATH="$IDF_DIR"
# shellcheck disable=SC1091
source "$IDF_PATH/export.sh"
idf.py --version >/dev/null 2>&1 || { echo "ERROR: idf.py not runnable (GHCR base image ESP-IDF toolchain missing/broken)"; exit 1; }

# ESP32-P4 chip revision v3.x needs ESP-IDF v5.5.3 or later. An older IDF does not
# know the v3 Kconfig symbols, drops them without a word, and produces an image capped
# at revision v1.99 that the bootloader refuses to run on a v3 chip. Catch that here,
# before the long compile; the resolved-sdkconfig check after the build is the
# authoritative one.
IDF_TARGET_NAME="$(sed -n 's/^set(IDF_TARGET \([a-z0-9]*\))/\1/p' \
  "$MP_DIR/ports/esp32/boards/$BOARD/mpconfigboard.cmake" 2>/dev/null | head -1)"
idf_version_part() {
  sed -n "s/^set(IDF_VERSION_$1 \([0-9]*\))/\1/p" "$IDF_PATH/tools/cmake/version.cmake"
}
IDF_VERSION_STR="$(idf_version_part MAJOR).$(idf_version_part MINOR).$(idf_version_part PATCH)"
IDF_VERSION_NUM=$(( $(idf_version_part MAJOR) * 10000 + $(idf_version_part MINOR) * 100 + $(idf_version_part PATCH) ))
if [ "$IDF_TARGET_NAME" = "esp32p4" ] && [ "$BOARD_VARIANT" != "PRE_REV3" ] \
    && [ "$IDF_VERSION_NUM" -lt 50503 ]; then
  echo "ERROR: ESP-IDF v$IDF_VERSION_STR cannot target ESP32-P4 chip revision v3.x (needs v5.5.3+)."
  echo "  Use a base image built with a newer ESP_IDF_REF (see README-dev.md), or build"
  echo "  for the older silicon with BOARD_VARIANT=PRE_REV3."
  exit 1
fi

PORTS_ESP32_DIR="$ROOT_DIR/ports/esp32"
USER_C_MODULES_FILE="$ROOT_DIR/usercmodule.cmake"
MICROPY_CMAKE_ARGS="${CMAKE_ARGS:-} -DUSER_C_MODULES=$USER_C_MODULES_FILE"

# SeedSigner is an air-gapped signer: strip the entire radio/TCP-IP stack
# (WiFi, BT, LWIP, esp_netif, ethernet, ESP-NOW) from the firmware at the
# component level. This drives the MICROPY_DISABLE_NETWORK machinery in the
# MicroPython patch (ports/esp32/CMakeLists.txt EXCLUDE_COMPONENTS +
# esp32_common.cmake source/component lists). Default ON for all boards; set
# MP_DISABLE_NETWORK=0 to build a networked debug image.
#
# IMPORTANT: the strip flags are activated ONLY for the real firmware build
# (see below), NOT for the `submodules` reconfigure. That throwaway pass is
# what fetches the managed components, and the strip's EXCLUDE_COMPONENTS can
# only resolve once apply_component_patches.sh has patched tinyusb to drop
# its esp_netif requirement, and patching requires the component to have been
# fetched first. Order: fetch (unstripped) -> patch -> real build (stripped).
MP_DISABLE_NETWORK="${MP_DISABLE_NETWORK:-1}"
unset MICROPY_DISABLE_NETWORK
# Component search path. Includes board_common's nested camera components so that
# board_common's REQUIRES (esp-camera-pipeline, cam_pipeline_qr) resolve: the same set
# the board_common apps (scan_coord_test, qr_overlay_test) point EXTRA_COMPONENT_DIRS at.
BOARD_COMMON_COMPONENTS="${PORTS_ESP32_DIR}/board_common/components"
MICROPY_EXTRA_DIRS="${PORTS_ESP32_DIR}"
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${BOARD_COMMON_COMPONENTS}"
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${BOARD_COMMON_COMPONENTS}/esp-camera-pipeline"
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${BOARD_COMMON_COMPONENTS}/esp-camera-pipeline/components"
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${SCREENS_DIR}/components"
# cUR: native BC-UR (fountain) decoder as a plain IDF component (dir name -> component `cUR`).
# The uUR.c MicroPython binding is compiled in usermod (bindings/micropython.cmake) and
# links __idf_cUR (the same plain-C-lib split as camera_scanner/k_quirc).
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${WORKDIR}/cUR"
# esp-secp256k1: native libsecp256k1 (ElementsProject/secp256k1-zkp) as a plain IDF
# component (dir name -> component `esp-secp256k1`). The modsecp256k1.c MicroPython
# binding is compiled in usermod (bindings/micropython.cmake) and links
# __idf_esp-secp256k1 (the same plain-C-lib split as cUR/camera_scanner).
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${WORKDIR}/esp-secp256k1"
# esp-hashlib-ext: mbedtls-backed SHA-512 + PBKDF2 for hashlib (dir name -> component
# `esp-hashlib-ext`). The modhashlibext.c binding is compiled in usermod and links
# __idf_esp-hashlib-ext; a frozen hashlib.py merges it into the built-in hashlib.
MICROPY_EXTRA_DIRS="${MICROPY_EXTRA_DIRS}\;${WORKDIR}/esp-hashlib-ext"
MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DMICROPY_EXTRA_COMPONENT_DIRS=${MICROPY_EXTRA_DIRS}"
MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DSEEDSIGNER_LVGL_SCREENS_DIR=$SCREENS_DIR"

# board_common board config: maps MicroPython board name to board_common board dir.
# Override with BOARD_CONFIG_DIR env var, or auto-map from BOARD name.
BOARD_COMMON_DIR="$PORTS_ESP32_DIR/board_common"
if [ -z "${BOARD_CONFIG_DIR:-}" ]; then
  case "$BOARD" in
    WAVESHARE_ESP32_S3_TOUCH_LCD_35B) BOARD_CONFIG_DIR="$BOARD_COMMON_DIR/boards/waveshare_s3_lcd35b" ;;
    WAVESHARE_ESP32_S3_TOUCH_LCD_35)  BOARD_CONFIG_DIR="$BOARD_COMMON_DIR/boards/waveshare_s3_lcd35" ;;
    WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_35) BOARD_CONFIG_DIR="$BOARD_COMMON_DIR/boards/waveshare_p4_lcd35" ;;
    WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_43) BOARD_CONFIG_DIR="$BOARD_COMMON_DIR/boards/waveshare_p4_lcd43" ;;
    GUITION_JC4880P443) BOARD_CONFIG_DIR="$BOARD_COMMON_DIR/boards/guition_jc4880p443" ;;
    *) echo "WARNING: No board_common mapping for BOARD=$BOARD"; BOARD_CONFIG_DIR="" ;;
  esac
fi
if [ -n "$BOARD_CONFIG_DIR" ] && [ -d "$BOARD_CONFIG_DIR" ]; then
  MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DBOARD_CONFIG_DIR=$BOARD_CONFIG_DIR"
  # Exported for ESP-IDF's script-mode component passes, where -D cache args
  # are invisible: board_common parses $BOARD_CONFIG_DIR/board_config.h to
  # decide its I/O-expander REQUIRE in BOTH passes (see its CMakeLists.txt).
  export BOARD_CONFIG_DIR
else
  echo "WARNING: BOARD_CONFIG_DIR not found: ${BOARD_CONFIG_DIR:-<unset>}"
fi

# Display height profile: maps board to the SUPPORT_DISPLAY_HEIGHT_* compile flag.
# Override with SEEDSIGNER_DISPLAY_HEIGHT env var, or auto-map from BOARD name.
if [ -z "${SEEDSIGNER_DISPLAY_HEIGHT:-}" ]; then
  case "$BOARD" in
    WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_43) SEEDSIGNER_DISPLAY_HEIGHT=480 ;;
    GUITION_JC4880P443) SEEDSIGNER_DISPLAY_HEIGHT=480 ;;
    *) SEEDSIGNER_DISPLAY_HEIGHT=320 ;;
  esac
fi
MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DSEEDSIGNER_DISPLAY_HEIGHT=$SEEDSIGNER_DISPLAY_HEIGHT"

# Build profile. dev (default) = the board's maximal-debug sdkconfig chain
# unchanged. PROFILE=release appends ports/esp32/profiles/sdkconfig.release
# LAST (overrides the debug block: SPIRAM memtest off, WARN logs, LVGL
# asserts off) via the MICROPY_SDKCONFIG_EXTRA hook in the MicroPython patch.
PROFILE="${PROFILE:-dev}"
if [ "$PROFILE" = "release" ]; then
  RELEASE_FRAGMENT="$ROOT_DIR/ports/esp32/profiles/sdkconfig.release"
  if [ ! -f "$RELEASE_FRAGMENT" ]; then
    echo "ERROR: PROFILE=release but $RELEASE_FRAGMENT is missing"
    exit 1
  fi
  MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DMICROPY_SDKCONFIG_EXTRA=$RELEASE_FRAGMENT"
  echo "Build profile: RELEASE ($RELEASE_FRAGMENT)"
else
  echo "Build profile: dev (maximal debug; PROFILE=release for the perf build)"
fi

{
  make -C "$MP_DIR/mpy-cross" USER_C_MODULES= -j"$(nproc)"
  rm -rf "$BUILD_DIR"

  # Initialize the MicroPython submodules this board needs (e.g.
  # lib/berkeley-db-1.xx for the default MICROPY_PY_BTREE, lib/micropython-lib
  # for frozen modules). CI checks out submodules non-recursively
  # (actions/checkout submodules: true), so MicroPython's nested submodules are
  # absent and CMake config fails. MicroPython's own 'submodules' target
  # reconfigures in a throwaway dir with UPDATE_SUBMODULES=1, initializing
  # exactly the board's required set. Pass the same args as the build so the
  # reconfigure sees our components/board config. Idempotent on full clones.
  make -C "$MP_DIR/ports/esp32" \
    BOARD="$BOARD" \
    ${BOARD_VARIANT:+BOARD_VARIANT="$BOARD_VARIANT"} \
    BUILD="$BUILD_DIR" \
    USER_C_MODULES="$USER_C_MODULES_FILE" \
    CMAKE_ARGS="$MICROPY_CMAKE_ARGS" \
    submodules

  # Patch fetched ESP-IDF managed components (e.g. LVGL, tinyusb). The
  # `submodules` reconfigure above runs the IDF component manager, which
  # materializes ports/esp32/managed_components/; patch it now, before the
  # real build compiles it. Idempotent (sentinel/dry-run guarded). See
  # apply_component_patches.sh and docs/approach-a-cache-psram-design.md.
  "$SCRIPT_DIR/apply_component_patches.sh" "$WORKDIR"

  # Activate the network strip for the REAL build only (the submodules
  # reconfigure above must run unstripped; see the MP_DISABLE_NETWORK note).
  # Both the -D and the env var are needed: ESP-IDF's early component
  # expansion evaluates component CMakeLists in script mode, where -D cache
  # variables don't exist but the environment does. Without the env var the
  # network components still enter the component graph and compile (unlinked).
  if [ "$MP_DISABLE_NETWORK" = "1" ]; then
    MICROPY_CMAKE_ARGS="$MICROPY_CMAKE_ARGS -DMICROPY_DISABLE_NETWORK=ON"
    export MICROPY_DISABLE_NETWORK=1
  fi

  make -C "$MP_DIR/ports/esp32" -j"$(nproc)" \
    BOARD="$BOARD" \
    ${BOARD_VARIANT:+BOARD_VARIANT="$BOARD_VARIANT"} \
    BUILD="$BUILD_DIR" \
    USER_C_MODULES="$USER_C_MODULES_FILE" \
    CMAKE_ARGS="$MICROPY_CMAKE_ARGS" \
    MICROPY_MPYCROSS="$MP_DIR/mpy-cross/build/mpy-cross" \
    IDF_CCACHE_ENABLE=1

  if ! grep -Rqs "usercmodule.cmake" "$BUILD_DIR"/CMakeCache.txt "$BUILD_DIR"/esp-idf/main/CMakeFiles 2>/dev/null; then
    echo "ERROR: USER_C_MODULES not detected in build metadata (expected $USER_C_MODULES_FILE)."
    exit 1
  fi

  # Confirm the image targets the intended ESP32-P4 silicon family. Read the RESOLVED
  # sdkconfig, not the fragments: an IDF that does not know a revision symbol drops it
  # and falls back to its own default, which still builds cleanly.
  CHIP_REV_RANGE=""
  if [ "$IDF_TARGET_NAME" = "esp32p4" ]; then
    rev_min="$(sed -n 's/^CONFIG_ESP_REV_MIN_FULL=//p' "$BUILD_DIR/sdkconfig")"; rev_min="${rev_min:-0}"
    rev_max="$(sed -n 's/^CONFIG_ESP_REV_MAX_FULL=//p' "$BUILD_DIR/sdkconfig")"; rev_max="${rev_max:-0}"
    CHIP_REV_RANGE="v$((rev_min / 100)).$((rev_min % 100)) to v$((rev_max / 100)).$((rev_max % 100))"
    if [ "$BOARD_VARIANT" = "PRE_REV3" ]; then
      rev_ok=$(( rev_max == 199 ))
    else
      rev_ok=$(( rev_min == 300 && rev_max == 399 ))
    fi
    if [ "$rev_ok" != "1" ]; then
      echo "ERROR: $BUILD_NAME resolved to ESP32-P4 chip revisions $CHIP_REV_RANGE (ESP-IDF v$IDF_VERSION_STR)."
      echo "  Expected v3.0 to v3.99 for the default build, or a v1.99 ceiling for PRE_REV3."
      exit 1
    fi
    echo "Targets ESP32-P4 chip revisions $CHIP_REV_RANGE"
  fi

  echo "Build complete. Artifacts:"
  ls -lh "$BUILD_DIR"/micropython.bin "$BUILD_DIR"/micropython.elf "$BUILD_DIR"/flash_args

  # Package flash-ready files for easy download/flashing.
  FLASH_DIR="$BUILD_DIR/flash"
  rm -rf "$FLASH_DIR"
  mkdir -p "$FLASH_DIR/bootloader" "$FLASH_DIR/partition_table"
  cp "$BUILD_DIR"/flash_args "$FLASH_DIR/"
  cp "$BUILD_DIR"/micropython.bin "$FLASH_DIR/"
  cp "$BUILD_DIR"/bootloader/bootloader.bin "$FLASH_DIR/bootloader/"
  cp "$BUILD_DIR"/partition_table/partition-table.bin "$FLASH_DIR/partition_table/"
  echo "Flash package: $FLASH_DIR"
  # Detect chip type from board name
  case "$BOARD" in
    *ESP32_P4*)         CHIP_TYPE="esp32p4" ;;
    GUITION_JC4880P443) CHIP_TYPE="esp32p4" ;;
    *)                  CHIP_TYPE="esp32s3" ;;
  esac
  echo "  Flash with: python -m esptool --chip $CHIP_TYPE write_flash @flash_args"
  if [ -n "$CHIP_REV_RANGE" ]; then
    echo "  Only for ESP32-P4 chip revisions $CHIP_REV_RANGE (esptool prints the chip revision on connect)"
  fi
  ls -lhR "$FLASH_DIR"
} 2>&1 | tee "$BUILD_LOG"

echo "Log saved to: $BUILD_LOG"
