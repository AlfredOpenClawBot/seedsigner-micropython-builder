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
srv.shutdown()
print('all good')
