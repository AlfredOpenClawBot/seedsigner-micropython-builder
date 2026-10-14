# SmartHome disguise for SeedSigner (ESP32-P4-WiFi6-Touch-LCD-4.3)

The board boots into a "Home" light controller. Tap the **empty top-left
corner 6 times quickly** and SeedSigner starts. When
SeedSigner exits, crashes, or the board loses power, it comes back up as the
Home controller. No SeedSigner code is changed: only the board's `/main.py`
launcher is replaced.

## Install

```bash
pip install mpremote
cp device/disguise_config.example.json my_config.json   # edit Wi-Fi + lights
./install.sh /dev/ttyACM0 my_config.json                 # macOS: /dev/cu.usbmodem…
```

Files written to the board: `main.py`, `disguise_app.py`, `switches.py`,
`tuya.py`, `wifi.py`, `disguise_config.json`.

Note: the builder's `tools/deploy_app.py --mode run` and `set_p4_boot_app.py`
rewrite `/main.py` with the stock launcher. Re-run `install.sh` after using them.

## Lights config (`disguise_config.json`)

| type | Device | Fields |
|---|---|---|
| `tuya` | Smart Life / Tuya plugs and switches, controlled locally (no cloud) | `host`, `device_id`, `local_key`, optional `version` (3.3 default or 3.4), `dp` (default 1) |
| `tasmota` | Tasmota | `host`, optional `channel` (1-8 for multi-relay) |
| `shelly1` | Shelly Gen1 | `host`, optional `channel` |
| `shelly2` | Shelly Plus / Pro / Gen2+ | `host`, optional `channel` |
| `http` | anything with on/off URLs | `on_url`, `off_url`, optional `status_url` |

Tap a light to toggle it (checked = on). "All lights off" turns everything off.
States are re-read every 30 s.

## Smart Life (Tuya) devices

The same plugs you use in the Smart Life app work here, over your home Wi-Fi,
without the cloud. Each one needs its **device id** and **local key**, which
Smart Life doesn't show. Get them once on a laptop with
[tinytuya](https://github.com/jasonacox/tinytuya):

```bash
pip install tinytuya
python -m tinytuya wizard   # links a free Tuya IoT developer account to your Smart Life app
python -m tinytuya scan     # shows each device's IP and protocol version
```

Copy `id`, `key`, IP and version into `disguise_config.json`. Give each plug a
fixed IP in your router so the config doesn't go stale. If you re-pair a plug in
Smart Life its local key changes; re-run the wizard. Protocol 3.5 (some 2023+
devices) is not supported yet.

## Important: Wi-Fi in the stock SeedSigner firmware

The SeedSigner MicroPython image is built **with networking stripped out**
(`MICROPY_DISABLE_NETWORK`), so on that image the Home screen works but the
lights can't actually switch (each tap shows "not responding"). Real light
control needs a firmware built with networking on, which also puts a radio
under SeedSigner. The disguise powers Wi-Fi down before SeedSigner starts,
but the device would no longer be air-gapped by construction. Tuya control
also needs the firmware's AES module (`cryptolib`), which a networked build
normally includes.

## How the secret works

Tap the empty top-left corner of the Home screen 6 times, each tap within
1.5 s of the last; any other tap resets the count. The firmware's
`secret_hotspot()` makes that 100x100 px corner an invisible tap target on
LVGL's top layer, and the disguise removes it before SeedSigner starts so it
can't block SeedSigner's own back arrow. On firmware without
`secret_hotspot()`, the Home screen falls back to a visible back arrow there.

## Tests (desktop)

```bash
python3 tests/test_disguise.py
python3 tests/test_tuya.py      # needs: pip install cryptography
```

Uses a fake screen module, fake Tasmota/Shelly devices and a simulated
Tuya 3.3/3.4 plug on localhost. Not yet run against a real plug.
