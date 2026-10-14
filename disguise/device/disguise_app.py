# Smart-home controller "disguise" shown at every boot.
#
# Home screen: one row per light (checked = on); tapping a row toggles it.
# When the lights carry Smart Life rooms (see tools/smartlife_import.py), Home
# lists "All devices" and each room instead, and a room lists its lights.
# Secret: tap the top-left corner (the back chevron) 6 times quickly and run()
# returns True, which tells main.py to start SeedSigner.
import json
import time

import seedsigner_lvgl_screens as ui

import switches
import wifi

CONFIG_PATH = '/disguise_config.json'

RET_BACK = 1000          # SEEDSIGNER_RET_BACK_BUTTON (top-left chevron)
SECRET_TAPS = 6
SECRET_GAP_MS = 1500     # max pause between secret taps
REFRESH_MS = 30000       # re-read switch states while idle
ALL_OFF_LABEL = 'All lights off'
ALL_DEVICES_LABEL = 'All devices'
OTHER_ROOM = 'Other devices'
ICON_CHEVRON_RIGHT = '\ue90a'   # SeedSignerIconConstants::CHEVRON_RIGHT

DEFAULT_CONFIG = {
    'title': 'Home Control',
    'wifi': {'ssid': '', 'password': ''},
    'switches': [],
}

_switches = []


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


def _checked(sw, idx):
    return [n for n, i in enumerate(idx) if sw[i].is_on]


def _show(cfg):
    ui.clear_result_queue()
    ui.button_list_screen(cfg)


def _render(title, sw):
    labels = [s.name for s in sw]
    checked = _checked(sw, range(len(sw)))
    if sw:
        labels.append(ALL_OFF_LABEL)
    else:
        labels.append('No devices yet')
    _show({
        'top_nav': {'title': title, 'show_back_button': True, 'show_power_button': False},
        'button_list': labels,
        'button_style': 'checkbox',
        'checked_buttons': checked,
    })
    return tuple(checked)


def group_rooms(cfg, sw):
    """[(room name, [switch indexes])] in the order Smart Life lists them.

    Returns [] when the lights don't span at least two rooms (flat list then)."""
    order = list(cfg.get('rooms') or [])
    for s in sw:
        room = s.cfg.get('room')
        if room and room not in order:
            order.append(room)
    groups = [(r, [i for i, s in enumerate(sw) if s.cfg.get('room') == r]) for r in order]
    groups = [g for g in groups if g[1]]
    loose = [i for i, s in enumerate(sw) if not s.cfg.get('room')]
    if loose:
        other = [g for g in groups if g[0] == OTHER_ROOM]
        if other:
            other[0][1].extend(loose)
        else:
            groups.append((OTHER_ROOM, loose))
    return groups if len(groups) > 1 else []


def _render_home(title, sw, groups):
    rows = [{'label': ALL_DEVICES_LABEL, 'right_icon': ICON_CHEVRON_RIGHT}]
    for name, idx in groups:
        on = len(_checked(sw, idx))
        label = '%s  (%d on)' % (name, on) if on else name
        rows.append({'label': label, 'right_icon': ICON_CHEVRON_RIGHT})
    rows.append(ALL_OFF_LABEL)
    _show({
        'top_nav': {'title': title, 'show_back_button': True, 'show_power_button': False},
        'button_list': rows,
    })
    return ('home', tuple(_checked(sw, range(len(sw)))))


def _render_room(name, sw, idx):
    checked = _checked(sw, idx)
    _show({
        'top_nav': {'title': name, 'show_back_button': True, 'show_power_button': False},
        'button_list': [sw[i].name for i in idx],
        'button_style': 'checkbox',
        'checked_buttons': checked,
    })
    return (name, tuple(checked))


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
    global _switches
    cfg = load_config()
    title = cfg.get('title') or 'Home Control'

    ui.init()
    ui.set_screensaver_timeout(int(cfg.get('screensaver_ms', 0)))

    _switches = switches.load(cfg)
    w = cfg.get('wifi') or {}
    online = wifi.connect(w.get('ssid'), w.get('password'))
    if online:
        for s in _switches:
            s.refresh()

    groups = group_rooms(cfg, _switches)
    if groups:
        return _run_rooms(title, groups, online)

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
        if index == RET_BACK:
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


def _run_rooms(title, groups, online):
    """Smart Life style: Home lists rooms, a room lists its lights."""
    sw = _switches
    views = [(ALL_DEVICES_LABEL, list(range(len(sw))))] + groups
    room = None                    # None = Home, else index into views

    def render():
        if room is None:
            return _render_home(title, sw, groups)
        name, idx = views[room]
        return _render_room(name, sw, idx)

    shown = render()
    secret = SecretCounter()
    last_refresh = time.ticks_ms()

    while True:
        ev = ui.poll_for_result()
        now = time.ticks_ms()
        if ev is None:
            if online and time.ticks_diff(now, last_refresh) > REFRESH_MS:
                last_refresh = now
                idx = range(len(sw)) if room is None else views[room][1]
                for i in idx:
                    sw[i].refresh()
                if _state(room, views, sw) != shown[1]:
                    shown = render()
            time.sleep_ms(20)
            continue

        _kind, index, _label = ev
        if room is None:
            if index == RET_BACK:
                if secret.tap(now):
                    return True
                continue
            secret.reset()
            if index < len(views):
                room = index
            elif sw:
                failed = [s.name for s in sw if not s.set(False)]
                if failed:
                    _toast('%d light(s) not responding' % len(failed))
        else:
            if index == RET_BACK:
                room = None
            else:
                idx = views[room][1]
                if index < len(idx):
                    s = sw[idx[index]]
                    if not s.toggle():
                        _toast('%s is not responding' % s.name)
        shown = render()


def _state(room, views, sw):
    idx = range(len(sw)) if room is None else views[room][1]
    return tuple(_checked(sw, idx))


def shutdown():
    """Release everything the disguise holds before SeedSigner starts."""
    global _switches
    _switches = []
    wifi.shutdown()
    try:
        ui.clear_result_queue()
    except Exception:
        pass
