"""Desktop tests: fake LVGL module + fake clock + a local HTTP switch server.

Run: python3 tests/test_disguise.py   (from the disguise/ folder)
"""
import http.server
import json
import os
import sys
import threading
import time as _real_time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'device'))

# --- MicroPython time shims ---------------------------------------------------
clock = [0]
_real_time.ticks_ms = lambda: clock[0]
_real_time.ticks_diff = lambda a, b: a - b
_real_time.sleep_ms = lambda ms: clock.__setitem__(0, clock[0] + ms)

# --- fake screen module -------------------------------------------------------
events = []
screens = []
toasts = []
ui = types.ModuleType('seedsigner_lvgl_screens')
ui.init = lambda: None
covers = []
ui.loading_spinner_screen = lambda cfg: covers.append(cfg['text'])
ui.set_screensaver_timeout = lambda ms: None
ui.clear_result_queue = lambda: None
ui.button_list_screen = lambda cfg: screens.append(cfg)
ui.show_toast = lambda cfg: toasts.append(cfg['label_text'])
def _poll():
    if events:
        delay, ev = events.pop(0)
        clock[0] += delay
        return ev
    raise SystemExit('ran out of events')
ui.poll_for_result = _poll
sys.modules['seedsigner_lvgl_screens'] = ui

# --- fake Tasmota + Shelly devices -------------------------------------------
state = {'tasmota': False, 'shelly': False}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path
        if p.startswith('/cm?cmnd=Power'):
            if p.endswith('On'): state['tasmota'] = True
            if p.endswith('Off'): state['tasmota'] = False
            body = {'POWER': 'ON' if state['tasmota'] else 'OFF'}
        elif p.startswith('/rpc/Switch.Set'):
            state['shelly'] = p.endswith('on=true'); body = {'was_on': True}
        elif p.startswith('/rpc/Switch.GetStatus'):
            body = {'output': state['shelly']}
        else:
            self.send_response(404); self.end_headers(); return
        b = json.dumps(body).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
srv = http.server.HTTPServer(('127.0.0.1', 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
host = '127.0.0.1:%d' % srv.server_port

import disguise_app, switches, wifi  # noqa: E402
cfg = {'title': 'Home', 'switches': [
    {'name': 'Living Room', 'type': 'tasmota', 'host': host},
    {'name': 'Porch', 'type': 'shelly2', 'host': host},
    {'name': 'Garage', 'type': 'tasmota', 'host': '127.0.0.1:1'},  # dead
]}
disguise_app.load_config = lambda path=None: dict(disguise_app.DEFAULT_CONFIG, **cfg)

B = lambda i: ('button_selected', i, 'x')
BACK = ('button_selected', 1000, 'back')

def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond: sys.exit(1)

# 1) toggles reach the devices; 5 taps then a pause does NOT unlock; 6 quick taps do
events[:] = [(0, B(0)), (0, B(1)), (0, B(2)),
             *[(200, BACK)] * 5, (3000, BACK),           # pause resets the count
             *[(200, BACK)] * 4, (0, B(0)),              # 5 in a row, then other input resets
             *[(300, BACK)] * 6]
check('run() unlocks on 6 quick top-left taps', disguise_app.run() is True)
check('HOME CONTROL cover shown before Home', covers[:1] == ['HOME CONTROL'])
check('Tasmota light switched on', screens[1]['checked_buttons'] == [0])
check('Shelly light switched on', state['shelly'] is True)
check('dead switch shows a toast', any('Garage' in t for t in toasts))
check('home screen has back chevron + checkboxes',
      screens[0]['top_nav']['show_back_button'] and screens[0]['button_style'] == 'checkbox')
check('checked rows follow state', screens[3]['checked_buttons'] == [0, 1, 2])
check('Living Room toggled back off before unlock', state['tasmota'] is False)
check('no extra events left (unlocked on the 6th quick tap)', events == [])

# 2) "All lights off"
state.update(tasmota=True, shelly=True); screens.clear()
events[:] = [(0, B(3)), *[(100, BACK)] * 6]
disguise_app.run()
check('All lights off turns everything off', state == {'tasmota': False, 'shelly': False})

# 3) no network stack: offline mode still renders and unlocks
check('wifi.connect is a no-op without network module', wifi.connect('x', 'y') is False)
events[:] = [*[(100, BACK)] * 6]
check('offline run unlocks', disguise_app.run() is True)
# 4) firmware with the invisible hotspot: no back arrow, hidden taps unlock
hotspot = []
ui.secret_hotspot = lambda on, size=100: hotspot.append(on)
SECRET = ('button_selected', 2000, 'secret')
screens.clear()
events[:] = [*[(100, SECRET)] * 5, (0, B(0)), *[(100, SECRET)] * 6]
check('hidden-tap run unlocks', disguise_app.run() is True and events == [])
check('home has no back arrow with hotspot', screens[0]['top_nav']['show_back_button'] is False)
disguise_app.shutdown()
check('hotspot on at start, off at shutdown', hotspot == [True, False])
del ui.secret_hotspot

# 5) Smart Life rooms: Home lists rooms, rooms list lights, back returns Home
cfg.clear(); cfg.update({'title': 'My Home', 'rooms': ['Kitchen', 'Porch'], 'switches': [
    {'name': 'Ceiling', 'type': 'tasmota', 'host': host, 'room': 'Kitchen'},
    {'name': 'Lamp', 'type': 'shelly2', 'host': host, 'room': 'Porch'},
    {'name': 'Garage', 'type': 'tasmota', 'host': '127.0.0.1:1'},   # no room
]})
state.update(tasmota=False, shelly=False); screens.clear()
events[:] = [(0, B(1)),                 # open Kitchen
             (0, B(0)),                 # toggle Ceiling on
             *[(100, BACK)] * 2,        # back to Home, then 1 secret tap
             (0, B(2)),                 # open Porch (resets the count)
             (0, BACK),                 # back to Home
             *[(100, BACK)] * 6]
check('rooms run unlocks from Home', disguise_app.run() is True)
home = screens[0]
labels = [r['label'] if isinstance(r, dict) else r for r in home['button_list']]
check('Home lists All devices, rooms in Smart Life order, Other, All off',
      labels == ['All devices', 'Kitchen', 'Porch', 'Other devices', 'All lights off'])
check('Kitchen screen lists its light', screens[1]['top_nav']['title'] == 'Kitchen'
      and screens[1]['button_list'] == ['Ceiling'])
check('Kitchen light switched on', state['tasmota'] is True and screens[2]['checked_buttons'] == [0])
check('Home shows Kitchen on-count', screens[3]['button_list'][1]['label'] == 'Kitchen  (1 on)')
check('Porch tap did not toggle anything', state['shelly'] is False)
check('back inside a room does not count toward the secret', events == [])

# 6) one room only falls back to the flat list
groups = disguise_app.group_rooms({}, switches.load({'switches': [
    {'name': 'a', 'room': 'Kitchen'}, {'name': 'b', 'room': 'Kitchen'}]}))
check('single room keeps the flat list', groups == [])

# 7) rooms with the hotspot: off inside a room, back on at Home; only Home counts
hotspot.clear()
ui.secret_hotspot = lambda on, size=100: hotspot.append(on)
screens.clear()
events[:] = [(0, B(1)),                 # open Kitchen
             *[(100, SECRET)] * 5,      # stray hidden taps in a room are ignored
             (0, BACK),                 # real back arrow -> Home
             *[(100, SECRET)] * 6]
check('rooms + hotspot run unlocks', disguise_app.run() is True and events == [])
check('Home hides the back arrow', screens[0]['top_nav']['show_back_button'] is False)
check('room shows its back arrow', screens[1]['top_nav']['show_back_button'] is True)
check('hotspot off in the room, on again at Home', hotspot == [True, False, True])
check('Home after the room still hides the back arrow',
      screens[-1]['top_nav']['show_back_button'] is False)
disguise_app.shutdown()
srv.shutdown()
print('all good')
