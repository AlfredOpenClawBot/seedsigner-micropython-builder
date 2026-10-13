# ESP32-P4 chip revision v3: separate images, newer IDF, two runtime traps

ESP32-P4 boards started shipping with chip revision v3.x silicon in 2026 (Waveshare made
v3 the default for its 4.3" board in August 2026). v3 is not a drop-in replacement for
the v1.x chips the earlier boards carried, and a firmware image built for one family does
not boot on the other.

## Symptoms of a mismatched image

- esptool refuses to write the bootloader: `bootloader.bin requires chip revision in range
  [v1.0 - v1.99] (this chip is revision v3.1). Use the force argument to flash anyway.`
- If forced, the bootloader stops at boot with `chip revision check failed. Required <=
  v1.99, found v3.1.` and the screen stays dark.

Every image this repo built before the v3 work resolved to `CONFIG_ESP_REV_MIN_FULL=0`
and `CONFIG_ESP_REV_MAX_FULL=199`, so all of them are pre-v3 only.

## Why one image cannot cover both

Espressif's v3.x user guide: v3.1 changed the memory architecture (cached regions now map
from the bottom of L2MEM) and more than 50 hardware and register details, so "v1.x and
v3.x chips cannot share the same firmware image." Even the P4's PIE vector instructions
were re-encoded: the toolchain ships separate assemblers per family, so `esp.vld.128.ip`
assembles to `0x0201223b` for pre-v3 and `0x0311009f` for v3. Source-level inline
assembly (cUR's SIMD XOR) is fine after a rebuild, but any prebuilt blob containing PIE
instructions is locked to one family.

## What the build needs

- **ESP-IDF v5.5.3 or later** (or v6.0+). v5.5.1 has no v3 support at all: its Kconfig
  caps `ESP_REV_MAX_FULL` at 199. The base image now pins v5.5.5, which is also upstream
  MicroPython's recommended version.
- **Explicit revision Kconfig.** IDF v5.5.5 adds `CONFIG_ESP32P4_SELECTS_REV_LESS_V3`
  (y = build for v0.x/v1.x, default n = build for v3.x) and changes the default minimum
  to `ESP32P4_REV_MIN_301` (v3.1). Left at the default, a build silently refuses v3.0
  chips. MicroPython 1.27's `sdkconfig.p4` set `CONFIG_ESP32P4_REV_MIN_0=y`, a symbol
  that depends on `SELECTS_REV_LESS_V3` under the new IDF and is dropped without a
  warning.

The builder backports upstream MicroPython's fix (micropython/micropython 3e892434fb,
first released in v1.29.0): `sdkconfig.p4` sets `CONFIG_ESP32P4_REV_MIN_300=y`, and each
P4 board carries a `PRE_REV3` variant that appends `sdkconfig.p4_pre_rev3`:

```bash
make docker-build-all BOARD=WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_43                        # v3.x
make docker-build-all BOARD=WAVESHARE_ESP32_P4_WIFI6_TOUCH_LCD_43 BOARD_VARIANT=PRE_REV3  # v0.x/v1.x
```

A variant builds into `build/<BOARD>-<VARIANT>/` and packages into
`dist/<BOARD>-<VARIANT>/`. `build_firmware.sh` reads the RESOLVED `sdkconfig` after the
build and fails unless the default build spans v3.0 to v3.99 and a `PRE_REV3` build tops
out at v1.99. It also refuses a v3 build on an IDF older than v5.5.3 before compiling. An
IDF that does not know a revision symbol drops it and still builds cleanly, so checking
the fragments would prove nothing.

## Two runtime traps on v3 silicon

Both were found and fixed in Kern (odudex/kern) on the same Waveshare 4.3" board.

1. **MIPI-DSI PHY clock.** `MIPI_DSI_PHY_CLK_SRC_DEFAULT` is a back-compat alias for
   `PLL_F20M`, which v3 does not have, so naming it makes `esp_lcd_new_dsi_bus()` abort.
   Leaving `.phy_clk_src` unset lets IDF v5.5.5 pick per revision (`PLL_F20M` before v3,
   `XTAL` on v3); IDF v5.5.1 falls back to the same legacy default, so the change is safe
   on every combination. Fixed in esp-board-common's `board_display_st7701.c`. (Kern
   a9d3f7a)
2. **Core bus timeout on the PSRAM path.** v3 can hold a core load or store to PSRAM
   longer than the 65535-cycle bus timeout; the timeout turns a slow-but-valid access
   into an asynchronous load/store access fault. Kern saw it at boot and during QR
   scanning on a v3.2 board with PSRAM at 200 MHz, and fixed it by clearing
   `HP_SYSTEM_CORE_{D,I}BUS_TIMEOUT_EN` once the scheduler runs. The MicroPython patch
   does the same at the top of `app_main` (`MICROPY_ESP_IDF_ENTRY`) for v3 builds only.
   (Kern 3ad82dd)

## Camera stack status

The camera components predate v3 (esp_video 0.8.0, esp_ipa 0.2.0, esp_cam_sensor 0.9.0)
but look safe on inspection: the esp_ipa 0.2.0 blob contains only base RV32IMFC
instructions (no PIE), and esp_video's v3 AWB-subwindow bug (fixed upstream in 2.2.0)
cannot fire in 0.8.0, which zero-initialises that struct. Confirmed on hardware: a v3.2
Waveshare 4.3" running this firmware scanned an animated PSBT, then reviewed and signed
it.
For reference, espressif/esp-idf#19032 reports an OV5647 in the same RAW10 1280x960 mode
whose MIPI-CSI clock lane never enters HS mode on a v3.1 DEV-KIT; it did not reproduce
on the v3.2 Waveshare 4.3".

## Identifying a board's revision

- **Software:** `esptool -p <port> chip-id` prints `Chip type: ESP32-P4 (revision v3.2)`.
  Reading v3 chips needs esptool v4.10+/v5.1+ (v3.0), v4.11+/v5.2+ (v3.1), or
  v4.12+/v5.3+ (v3.2). Browser flashers need esptool-js 0.7.0 or later for v3.1/v3.2.
- **Package marking:** the middle character of the manufacturing-code line gives the
  revision (Espressif errata, chip identification table): `C` = v1.0, `E` = v1.3,
  `F` = v3.0, `G` = v3.1, `H` or `J` = v3.2. Espressif's v3 part numbers also add an
  `X` suffix (ESP32-P4NRW32X), but board product pages do not always say so.
