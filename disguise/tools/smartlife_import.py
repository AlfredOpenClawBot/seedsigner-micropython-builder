#!/usr/bin/env python3
"""Turn your Smart Life app setup into the Home Control config (disguise_config.json).

Run it on a laptop on the same Wi-Fi as your plugs, in the folder where you ran
`python -m tinytuya wizard` (it reads tinytuya.json and devices.json from there).

    python3 smartlife_import.py --ssid "MyWiFi" -o disguise_config.json

What it talks to:
  * Tuya cloud, ONCE: room names and which device sits in which room (plus local
    keys if devices.json is missing). The answer is cached in smartlife_export.json;
    later runs reuse it and never touch the cloud unless you pass --refresh.
  * Your LAN: a local UDP scan to find each plug's IP and protocol version.
  * Nothing else. The board itself never talks to the cloud.

Use --offline to forbid any cloud call (rooms then come from the cache, or the
devices end up in one flat list).
"""
import argparse
import getpass
import json
import os
import re
import sys

EXPORT_FILE = 'smartlife_export.json'
OTHER_ROOM = 'Other devices'
SUPPORTED_VERSIONS = ('3.3', '3.4')

# Boolean DP codes that switch a light or a socket outlet.
SWITCH_CODE = re.compile(r'^switch(_led)?(_\d+)?$')
# Categories whose main on/off DP is 20 ("switch_led") when no DP mapping is known.
LIGHT_CATEGORIES = ('dj', 'dd', 'fwd', 'xdd', 'dc', 'fsd', 'tyndj')


# --- cloud (one-time) -----------------------------------------------------------
def _result(resp, what):
    if not isinstance(resp, dict) or not resp.get('success', 'result' in resp):
        raise RuntimeError('%s failed: %s' % (what, resp))
    return resp.get('result')


def fetch_from_cloud(devices, home_name=None):
    """One cloud session: homes, rooms and room membership. Returns the export dict."""
    import tinytuya
    cloud = tinytuya.Cloud()  # reads tinytuya.json written by the wizard
    if getattr(cloud, 'error', None):
        raise RuntimeError('Tuya cloud login failed: %s' % cloud.error)
    if not devices:
        print('devices.json not found; downloading the device list and local keys.')
        devices = cloud.getdevices(include_map=True)
        if isinstance(devices, dict):
            raise RuntimeError('device list failed: %s' % devices)
    export = {'home': None, 'rooms': [], 'devices': devices}
    if not devices:
        return export

    try:
        uid = None
        for d in devices:
            uid = cloud._getuid(d['id'])
            if isinstance(uid, str):
                break
        if not isinstance(uid, str):
            raise RuntimeError('could not find your Smart Life user id')
        homes = _result(cloud.cloudrequest('/v1.0/users/%s/homes' % uid), 'list homes') or []
        if not homes:
            raise RuntimeError('no homes on this account')
        home = homes[0]
        if home_name:
            match = [h for h in homes if h.get('name', '').lower() == home_name.lower()]
            if not match:
                raise RuntimeError('home %r not found; homes: %s'
                                   % (home_name, ', '.join(h.get('name', '?') for h in homes)))
            home = match[0]
        elif len(homes) > 1:
            print('Several homes found (%s); using %r. Pick another with --home.'
                  % (', '.join(h.get('name', '?') for h in homes), home.get('name')))
        hid = home['home_id']
        export['home'] = home.get('name')
        info = _result(cloud.cloudrequest('/v1.0/homes/%s/rooms' % hid), 'list rooms') or {}
        for room in info.get('rooms', []):
            members = _result(cloud.cloudrequest('/v1.0/homes/%s/rooms/%s/devices'
                                                 % (hid, room['room_id'])), 'room devices') or []
            export['rooms'].append({'name': room.get('name', 'Room'),
                                    'devices': [m['id'] for m in members if 'id' in m]})
    except Exception as e:
        print('WARNING: could not read rooms from Smart Life (%s).' % e)
        print('         Devices will be listed without rooms.')
    return export


# --- LAN scan (local) -------------------------------------------------------------
def scan_lan(scantime):
    """UDP broadcast scan. Returns {device_id: {'ip':..., 'version':...}}."""
    import tinytuya
    print('Scanning your network for Tuya devices (%ss)...' % scantime)
    found = tinytuya.deviceScan(False, scantime, False, False)
    return normalize_scan(found.values())


def normalize_scan(entries):
    out = {}
    for e in entries:
        did = e.get('gwId') or e.get('id')
        if did and e.get('ip'):
            out[did] = {'ip': e['ip'], 'version': str(e.get('version') or e.get('ver') or '')}
    return out


def load_snapshot(path):
    with open(path) as f:
        snap = json.load(f)
    return normalize_scan(snap.get('devices', []))


# --- config building (pure, tested) -------------------------------------------------
def switch_dps(dev):
    """[(dp, code)] of the on/off switches on a device, in DP order."""
    mapping = dev.get('mapping') or {}
    found = []
    for dp, m in mapping.items():
        code = m.get('code', '') if isinstance(m, dict) else ''
        mtype = (m.get('type', '') if isinstance(m, dict) else '').lower()
        if SWITCH_CODE.match(code) and mtype in ('bool', 'boolean', ''):
            found.append((int(dp), code))
    found.sort()
    if found:
        return found
    if mapping:
        return []          # known device with no on/off switch (sensor, remote...)
    return [(20 if dev.get('category') in LIGHT_CATEGORIES else 1, 'guess')]


def build_config(export, lan, base=None, wifi=None):
    """Return (config dict, list of notes for the user)."""
    base = dict(base or {})
    notes = []
    devices = {d['id']: d for d in export.get('devices', []) if d.get('id')}

    # Room order and device order as Smart Life lists them.
    order = []                 # [(room name, device id)]
    placed = set()
    for room in export.get('rooms', []):
        for did in room.get('devices', []):
            if did in devices and did not in placed:
                order.append((room['name'], did))
                placed.add(did)
    use_rooms = bool(order)
    for did in devices:
        if did not in placed:
            order.append((OTHER_ROOM if use_rooms else None, did))

    imported = []
    for room, did in order:
        dev = devices[did]
        name = (dev.get('name') or 'Light').strip()
        if dev.get('sub') or dev.get('node_id') or dev.get('gateway_id') or dev.get('parent'):
            notes.append('%s: skipped (behind a Zigbee/BLE hub, not reachable directly)' % name)
            continue
        dps = switch_dps(dev)
        if not dps:
            notes.append('%s: skipped (no on/off switch)' % name)
            continue
        if not dev.get('key'):
            notes.append('%s: skipped (no local key; re-run the tinytuya wizard)' % name)
            continue
        seen = lan.get(did, {})
        host = seen.get('ip') or dev.get('ip') or dev.get('last_ip') or ''
        version = seen.get('version') or str(dev.get('version') or '') or '3.3'
        if version not in SUPPORTED_VERSIONS:
            notes.append('%s: skipped (protocol %s is not supported yet; 3.3 and 3.4 are)'
                         % (name, version))
            continue
        if not host:
            notes.append('%s: not found on your network; set its "host" by hand' % name)
        elif did not in lan:
            notes.append('%s: not seen in the scan; using last known IP %s' % (name, host))
        if dps[0][1] == 'guess':
            notes.append('%s: switch data point guessed as %d' % (name, dps[0][0]))
        for n, (dp, _code) in enumerate(dps):
            entry = {'name': name if len(dps) == 1 else '%s %d' % (name, n + 1),
                     'type': 'tuya', 'host': host, 'device_id': did,
                     'local_key': dev['key'], 'version': float(version), 'dp': dp}
            if room:
                entry['room'] = room
            imported.append(entry)

    # Keep non-Tuya switches the user added by hand.
    kept = [s for s in base.get('switches', []) if s.get('type') != 'tuya']
    cfg = {
        'title': base.get('title') or export.get('home') or 'Home Control',
        'wifi': wifi or base.get('wifi') or {'ssid': '', 'password': ''},
        'screensaver_ms': base.get('screensaver_ms', 0),
    }
    rooms = []
    for s in imported + kept:
        if s.get('room') and s['room'] not in rooms:
            rooms.append(s['room'])
    if rooms:
        cfg['rooms'] = rooms
    cfg['switches'] = imported + kept
    return cfg, notes


# --- CLI ---------------------------------------------------------------------------
def _load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('-o', '--output', default='disguise_config.json')
    p.add_argument('--ssid', help='Wi-Fi network the board joins (asks for the password)')
    p.add_argument('--password', help='Wi-Fi password (asked for if --ssid is given without it)')
    p.add_argument('--base', help='existing config to keep Wi-Fi, title and non-Tuya lights from')
    p.add_argument('--home', help='Smart Life home to import when you have several')
    p.add_argument('--refresh', action='store_true', help='ask the cloud again (rooms changed)')
    p.add_argument('--offline', action='store_true', help='never contact the Tuya cloud')
    p.add_argument('--snapshot', help='use this tinytuya snapshot.json instead of scanning')
    p.add_argument('--scantime', type=int, default=18)
    a = p.parse_args(argv)

    devices = _load_json('devices.json')
    export = None if a.refresh else _load_json(EXPORT_FILE)
    if export is None:
        if a.offline:
            if not devices:
                sys.exit('No devices.json or %s here, and --offline forbids the cloud.' % EXPORT_FILE)
            export = {'home': None, 'rooms': [], 'devices': devices}
        else:
            print('Reading rooms from Smart Life (one-time cloud call)...')
            export = fetch_from_cloud(devices, a.home)
            with open(EXPORT_FILE, 'w') as f:
                json.dump(export, f, indent=2)
            print('Saved %s; later runs work without the cloud.' % EXPORT_FILE)
    elif devices:
        export['devices'] = devices   # keys from a newer wizard run win

    lan = load_snapshot(a.snapshot) if a.snapshot else scan_lan(a.scantime)

    wifi = None
    if a.ssid:
        pw = a.password if a.password is not None else getpass.getpass('Password for %s: ' % a.ssid)
        wifi = {'ssid': a.ssid, 'password': pw}
    base = _load_json(a.base) if a.base else _load_json(a.output)
    cfg, notes = build_config(export, lan, base, wifi)

    with open(a.output, 'w') as f:
        json.dump(cfg, f, indent=2)
    print('\nWrote %s:' % a.output)
    for room in cfg.get('rooms') or [None]:
        names = [s['name'] for s in cfg['switches'] if s.get('room') == room]
        print('  %s%s' % (room + ': ' if room else '', ', '.join(names) or '-'))
    for n in notes:
        print('  note: ' + n)
    if not cfg['wifi'].get('ssid'):
        print('  note: no Wi-Fi set; pass --ssid "YourWiFi"')
    os.chmod(a.output, 0o600)   # holds your Wi-Fi password and device keys
    return 0


if __name__ == '__main__':
    sys.exit(main())
