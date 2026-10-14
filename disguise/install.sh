#!/usr/bin/env bash
# Copy the disguise onto the board. Usage: ./install.sh <serial-port> [config.json]
# Needs mpremote:  pip install mpremote
set -euo pipefail
PORT="${1:?usage: ./install.sh <serial-port> [config.json]}"
CFG="${2:-device/disguise_config.example.json}"
cd "$(dirname "$0")"
mpremote connect "$PORT" \
  cp device/wifi.py :wifi.py + \
  cp device/switches.py :switches.py + \
  cp device/tuya.py :tuya.py + \
  cp device/disguise_app.py :disguise_app.py + \
  cp "$CFG" :disguise_config.json + \
  cp device/main.py :main.py + \
  reset
echo "Installed. The board now boots into the Home controller."
