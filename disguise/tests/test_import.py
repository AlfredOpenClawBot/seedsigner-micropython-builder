"""Desktop tests for tools/smartlife_import.py with a fake Tuya cloud + LAN scan.

Run: python3 tests/test_import.py   (from the disguise/ folder; no tinytuya needed)
"""
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        sys.exit(1)


SW = lambda code, dp=1: {str(dp): {'code': code, 'type': 'Boolean'}}
DEVICES = [  # what the tinytuya wizard writes to devices.json
    {'id': 'plug1', 'name': 'Floor Lamp', 'key': 'k' * 16, 'category': 'cz', 'mapping': SW('switch_1')},
    {'id': 'bulb1', 'name': 'Ceiling', 'key': 'b' * 16, 'category': 'dj', 'mapping': SW('switch_led', 20)},
    {'id': 'wall2', 'name': 'Hall Switch', 'key': 'w' * 16, 'category': 'kg',
     'mapping': dict(SW('switch_1', 1), **SW('switch_2', 2), **{'9': {'code': 'countdown_1', 'type': 'Integer'}})},
    {'id': 'sens1', 'name': 'Door Sensor', 'key': 's' * 16, 'category': 'mcs',
     'mapping': {'1': {'code': 'doorcontact_state', 'type': 'Boolean'}}},
    {'id': 'zb1', 'name': 'Zigbee Bulb', 'key': 'z' * 16, 'category': 'dj', 'sub': True, 'mapping': SW('switch_led', 20)},
    {'id': 'new35', 'name': 'New Plug', 'key': 'n' * 16, 'category': 'cz', 'mapping': SW('switch_1')},
    {'id': 'gone', 'name': 'Patio', 'key': 'p' * 16, 'category': 'cz', 'mapping': SW('switch_1')},
]
SCAN = {  # tinytuya.deviceScan() result, keyed by IP
    '192.168.1.40': {'ip': '192.168.1.40', 'gwId': 'plug1', 'version': '3.3'},
    '192.168.1.41': {'ip': '192.168.1.41', 'gwId': 'bulb1', 'version': '3.4'},
    '192.168.1.42': {'ip': '192.168.1.42', 'gwId': 'wall2', 'version': '3.3'},
    '192.168.1.43': {'ip': '192.168.1.43', 'gwId': 'new35', 'version': '3.5'},
}
calls = []


class FakeCloud:
    error = None

    def _getuid(self, did):
        return 'uid123'

    def getdevices(self, include_map=False):
        return DEVICES

    def cloudrequest(self, url, **kw):
        calls.append(url)
        if url == '/v1.0/users/uid123/homes':
            return {'success': True, 'result': [{'home_id': 7, 'name': 'Rich Home'}]}
        if url == '/v1.0/homes/7/rooms':
            return {'success': True, 'result': {'rooms': [{'room_id': 2, 'name': 'Living Room'},
                                                          {'room_id': 1, 'name': 'Hallway'}]}}
        if url == '/v1.0/homes/7/rooms/2/devices':
            return {'success': True, 'result': [{'id': 'bulb1'}, {'id': 'plug1'}]}
        if url == '/v1.0/homes/7/rooms/1/devices':
            return {'success': True, 'result': [{'id': 'wall2'}]}
        return {'success': False, 'msg': 'unexpected ' + url}


tt = types.ModuleType('tinytuya')
tt.Cloud = FakeCloud
tt.deviceScan = lambda *a: SCAN
sys.modules['tinytuya'] = tt

import smartlife_import as si  # noqa: E402

work = tempfile.mkdtemp()
os.chdir(work)
with open('devices.json', 'w') as f:
    json.dump(DEVICES, f)

# 1) first run: one cloud session, LAN scan, config written
si.main(['--ssid', 'HomeNet', '--password', 'pw', '-o', 'cfg.json'])
cfg = json.load(open('cfg.json'))
names = [(s['name'], s.get('room')) for s in cfg['switches']]
check('title is the Smart Life home name', cfg['title'] == 'Rich Home')
check('rooms keep Smart Life order', cfg['rooms'] == ['Living Room', 'Hallway', 'Other devices'])
check('devices land in their rooms, in room order', names == [
    ('Ceiling', 'Living Room'), ('Floor Lamp', 'Living Room'),
    ('Hall Switch 1', 'Hallway'), ('Hall Switch 2', 'Hallway'), ('Patio', 'Other devices')])
bulb = cfg['switches'][0]
check('bulb uses switch_led DP 20 and scanned IP/version',
      (bulb['dp'], bulb['host'], bulb['version'], bulb['local_key']) == (20, '192.168.1.41', 3.4, 'b' * 16))
check('two-gang switch becomes two lights on DP 1 and 2',
      [s['dp'] for s in cfg['switches'][2:4]] == [1, 2])
check('Patio not on the LAN keeps an empty host', cfg['switches'][4]['host'] == '')
check('Wi-Fi set', cfg['wifi'] == {'ssid': 'HomeNet', 'password': 'pw'})
check('config is private', os.stat('cfg.json').st_mode & 0o077 == 0)
check('cloud export cached', os.path.exists(si.EXPORT_FILE))

# 2) second run: no cloud at all, keeps Wi-Fi and hand-added lights
calls.clear()
cfg['switches'].append({'name': 'Garden', 'type': 'shelly2', 'host': '192.168.1.60'})
json.dump(cfg, open('cfg.json', 'w'))
tt.Cloud = None   # any cloud use would now crash
si.main(['-o', 'cfg.json'])
cfg2 = json.load(open('cfg.json'))
check('re-run makes no cloud calls', calls == [])
check('re-run keeps Wi-Fi', cfg2['wifi']['ssid'] == 'HomeNet')
check('re-run keeps hand-added Shelly', cfg2['switches'][-1]['name'] == 'Garden')
check('re-run does not duplicate Tuya lights', len(cfg2['switches']) == 6)

# 3) notes explain what was left out
_, notes = si.build_config(json.load(open(si.EXPORT_FILE)), si.normalize_scan(SCAN.values()))
text = '\n'.join(notes)
check('sensor skipped', 'Door Sensor: skipped (no on/off switch)' in text)
check('hub device skipped', 'Zigbee Bulb: skipped' in text)
check('protocol 3.5 skipped', 'New Plug: skipped (protocol 3.5' in text)
check('missing IP reported', 'Patio: not found on your network' in text)

# 4) --offline with no cache: flat list, no rooms, no cloud
os.remove(si.EXPORT_FILE)
si.main(['--offline', '-o', 'flat.json'])
flat = json.load(open('flat.json'))
check('offline import has no rooms', 'rooms' not in flat and all('room' not in s for s in flat['switches']))

# 5) the result loads on the device side as rooms
sys.path.insert(0, os.path.join(HERE, '..', 'device'))
sys.modules['seedsigner_lvgl_screens'] = types.ModuleType('seedsigner_lvgl_screens')
import disguise_app, switches  # noqa: E402
groups = disguise_app.group_rooms(cfg, switches.load(cfg))
check('device groups match Smart Life rooms',
      [g[0] for g in groups] == ['Living Room', 'Hallway', 'Other devices'])
check('hand-added light without a room joins Other devices', groups[2][1] == [4, 5])
print('all good')
