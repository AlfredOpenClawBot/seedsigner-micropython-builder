# SmartHome disguise for SeedSigner (ESP32-P4-WiFi6-Touch-LCD-4.3)

The board boots into a "Home" light controller. Tap the **top-left corner
(the small back arrow) 6 times quickly** and SeedSigner starts. When
SeedSigner exits, crashes, or the board loses power, it comes back up as the
Home controller. No SeedSigner code is changed: only the board's `/main.py`
launcher is replaced.

## Install

```bash
pip install mpremote
cp device/disguise_config.example.json my_config.json   # edit Wi-Fi + lights,
                                                         # or build it from Smart Life (below)
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

Any light can carry a `"room"`; an optional top-level `"rooms": [...]` sets the
room order. With lights in two or more rooms, Home lists the rooms instead of the
lights (the Smart Life import below fills these in for you).

## Copy your Smart Life setup onto the board

`tools/smartlife_import.py` builds `disguise_config.json` from your Smart Life
account: the home name becomes the title, Home lists your Smart Life rooms in the
same order, and each room lists its lights by their Smart Life names. Tap a room
to open it, tap a light to switch it, tap the back arrow to return to Home.

Smart Life has no "export" button, and the one thing it hides is each device's
**local key** (the password the plug uses on your Wi-Fi). The only practical way
to read it is a **one-time** request to Tuya's cloud from your laptop. After
that, everything is local: the laptop finds plugs with a LAN scan, and the board
talks to the plugs directly over your Wi-Fi and never contacts any cloud.

### One-time setup (about 15 minutes)

On your laptop, on the same Wi-Fi as your plugs:

1. `pip install tinytuya mpremote`
2. Create a free account at [platform.tuya.com](https://platform.tuya.com) and a
   **Cloud project** (Cloud > Development > Create Cloud Project, development
   method "Smart Home", pick the data center for your region, e.g. Western America).
   Note the **Access ID** and **Access Secret**. The free trial of the API
   service is plenty, since you only need it once.
3. In that project open **Devices > Link App Account > Add App Account**. A QR code
   appears. On your iPhone open **Smart Life > Me > scan icon (top right)** and scan
   it, then confirm. Your Smart Life devices now show in the project.
4. In an empty folder run `python -m tinytuya wizard`, paste the Access ID and
   Secret, enter any one device id from step 3, and answer **Y** to the scan
   question. This writes `devices.json` (names, ids, local keys) and `tinytuya.json`.
5. In the same folder run the import:

   ```bash
   python3 /path/to/disguise/tools/smartlife_import.py --ssid "YourWiFi" -o disguise_config.json
   ```

   It asks for your Wi-Fi password, reads your rooms once, scans the LAN, and
   prints what it imported and anything it skipped and why.
6. Install: `./install.sh /dev/cu.usbmodem… /path/to/disguise_config.json`

After step 5 you can unlink the app account in the Tuya project (or delete the
project) if you want the cloud link gone; the board doesn't need it.

### Later changes

- Renamed or moved a device in Smart Life: `smartlife_import.py --refresh` (asks the
  cloud again), then `install.sh`.
- A plug got a new IP: just re-run `smartlife_import.py` (local scan only, no cloud).
  Better: give each plug a fixed IP in your router.
- Re-paired a plug in Smart Life: its local key changed; re-run the wizard, then
  the import.
- `--offline` forbids any cloud call; `--home NAME` picks one of several homes.
  Lights you added by hand (Tasmota, Shelly...) and your Wi-Fi are kept on re-runs.
- `disguise_config.json` holds your Wi-Fi password and device keys; keep it private.

### What comes across, and what doesn't

| Smart Life | Home Control |
|---|---|
| Home name | screen title |
| Rooms and their order | Home list (plus "All devices"), devices without a room go to "Other devices" |
| Plugs, switches, bulbs (on/off) | a light per switch; a 2-gang switch becomes "Name 1" and "Name 2" |
| Brightness, colour, scenes, schedules | not yet (on/off only) |
| Sensors, IR remotes | skipped |
| Zigbee/Bluetooth devices behind a hub | skipped (they have no IP of their own) |
| Protocol 3.5 devices (some 2023+ models) | skipped for now |

With only one room (or none), Home is a single list of lights as before.

### No-cloud alternatives

There is no reliable way to read a stock Smart Life device's local key without
that one cloud request. If you want zero cloud contact ever, the route is to
replace the plug firmware: many Tuya plugs can be flashed over the air with
[tuya-cloudcutter](https://github.com/tuya-cloudcutter/tuya-cloudcutter) to
OpenBeken or similar, or by serial to Tasmota/ESPHome. Those devices then leave
Smart Life, and you add them here as `tasmota` (or `http`) lights by hand.

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

The Home screen is SeedSigner's own native `button_list_screen` with the back
arrow shown in the top-left. The disguise counts back-arrow taps: 6 in a row,
each within 1.5 s of the last, unlocks. Any other tap resets the count.

## Tests (desktop)

```bash
python3 tests/test_disguise.py
python3 tests/test_tuya.py      # needs: pip install cryptography
python3 tests/test_import.py    # Smart Life import against a fake cloud and LAN scan
```

Uses a fake screen module, fake Tasmota/Shelly devices and a simulated
Tuya 3.3/3.4 plug on localhost. Not yet run against a real plug.
