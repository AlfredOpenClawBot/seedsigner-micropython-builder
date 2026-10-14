# Smart-home controller "disguise" shown at every boot.
#
# Home screen: one row per light (checked = on); tapping a row toggles it.
# Secret: tap the empty top-left corner 6 times quickly and run() returns True,
# which tells main.py to start SeedSigner. Firmware with ui.secret_hotspot()
# makes that corner an invisible tap target; older firmware falls back to a
# visible back arrow there.
import json
import time

import seedsigner_lvgl_screens as ui

import switches
import wifi

CONFIG_PATH = '/disguise_config.json'

RET_BACK = 1000          # SEEDSIGNER_RET_BACK_BUTTON (top-left chevron)
RET_SECRET = 2000        # DM_SECRET_HOTSPOT_RET (invisible top-left tap target)
HOTSPOT_PX = 100
SECRET_TAPS = 6
SECRET_GAP_MS = 1500     # max pause between secret taps
REFRESH_MS = 30000       # re-read switch states while idle
ALL_OFF_LABEL = 'All lights off'

DEFAULT_CONFIG = {
    'title': 'Home Control',
    'wifi': {'ssid': '', 'password': ''},
    'switches': [],
}

_switches = []
_hotspot = False


def load_config(path=CONFIG_PATH):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        print('[disguise] using default config:', e)
        cfg = {}
    out = dict(DEFAULT_CONFIG)
    out.update(cfg)
    return out


def _toast(text):
    try:
        ui.show_toast({'label_text': text, 'duration_ms': 2000})
    except Exception:
        pass


def _render(title, sw):
    labels = [s.name for s in sw]
    checked = [i for i, s in enumerate(sw) if s.is_on]
    if sw:
        labels.append(ALL_OFF_LABEL)
    else:
        labels.append('No devices yet')
    ui.clear_result_queue()
    ui.button_list_screen({
        'top_nav': {'title': title, 'show_back_button': not _hotspot, 'show_power_button': False},
        'button_list': labels,
        'button_style': 'checkbox',
        'checked_buttons': checked,
    })
    return tuple(checked)


class SecretCounter:
    """Counts back-button taps; any other input or a long pause resets it."""

    def __init__(self, taps=SECRET_TAPS, gap_ms=SECRET_GAP_MS):
        self.taps = taps
        self.gap_ms = gap_ms
        self.count = 0
        self.last = None

    def tap(self, now):
        if self.last is not None and time.ticks_diff(now, self.last) > self.gap_ms:
            self.count = 0
        self.count += 1
        self.last = now
        if self.count >= self.taps:
            self.reset()
            return True
        return False

    def reset(self):
        self.count = 0
        self.last = None


def run():
    """Run the disguise until the secret is entered. Returns True to unlock."""
    global _switches, _hotspot
    cfg = load_config()
    title = cfg.get('title') or 'Home Control'

    ui.init()
    ui.set_screensaver_timeout(int(cfg.get('screensaver_ms', 0)))
    _hotspot = _set_hotspot(True)

    _switches = switches.load(cfg)
    w = cfg.get('wifi') or {}
    online = wifi.connect(w.get('ssid'), w.get('password'))
    if online:
        for s in _switches:
            s.refresh()

    shown = _render(title, _switches)
    secret = SecretCounter()
    last_refresh = time.ticks_ms()

    while True:
        ev = ui.poll_for_result()
        now = time.ticks_ms()
        if ev is None:
            if online and time.ticks_diff(now, last_refresh) > REFRESH_MS:
                last_refresh = now
                for s in _switches:
                    s.refresh()
                if tuple(i for i, s in enumerate(_switches) if s.is_on) != shown:
                    shown = _render(title, _switches)
            time.sleep_ms(20)
            continue

        _kind, index, _label = ev
        if index in (RET_BACK, RET_SECRET):
            if secret.tap(now):
                return True
            continue
        secret.reset()

        if index < len(_switches):
            s = _switches[index]
            if not s.toggle():
                _toast('%s is not responding' % s.name)
        elif _switches:
            failed = [s.name for s in _switches if not s.set(False)]
            if failed:
                _toast('%d light(s) not responding' % len(failed))
        shown = _render(title, _switches)


def _set_hotspot(on):
    """Toggle the invisible tap target. Returns False on firmware without it."""
    try:
        ui.secret_hotspot(on, HOTSPOT_PX)
        return on
    except AttributeError:
        return False


def shutdown():
    """Release everything the disguise holds before SeedSigner starts."""
    global _switches, _hotspot
    # The hotspot sits on LVGL's top layer, which outlives screen changes: remove
    # it or it would swallow taps in SeedSigner's top-left corner (its back arrow).
    _set_hotspot(False)
    _hotspot = False
    _switches = []
    wifi.shutdown()
    try:
        ui.clear_result_queue()
    except Exception:
        pass
