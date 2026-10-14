# Wi-Fi light switches driven over plain HTTP on the local network.
#
# Supported "type" values in disguise_config.json:
#   tasmota  - Tasmota firmware (also many re-flashed Smart Life / Tuya plugs)
#   shelly1  - Shelly Gen1 (Shelly 1, Plug S, ...)
#   shelly2  - Shelly Gen2+/Plus/Pro (RPC API)
#   tuya     - Smart Life / Tuya devices on the LAN: "host", "device_id", "local_key",
#              optional "version" (3.3 default, or 3.4) and "dp" (switch data point, default 1)
#   http     - anything else: give "on_url" and "off_url" (and optional "status_url")
import json

try:
    import socket
except ImportError:
    socket = None

TIMEOUT_S = 3


def http_get(url):
    """Minimal HTTP/1.0 GET. Returns the body as str, or raises OSError."""
    if socket is None:
        raise OSError('no network stack')
    if not url.startswith('http://'):
        raise ValueError('only http:// URLs are supported')
    rest = url[7:]
    slash = rest.find('/')
    if slash < 0:
        host, path = rest, '/'
    else:
        host, path = rest[:slash], rest[slash:]
    port = 80
    if ':' in host:
        host, p = host.split(':', 1)
        port = int(p)
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    try:
        s.settimeout(TIMEOUT_S)
        s.connect(addr)
        s.send(('GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n' % (path, host)).encode())
        chunks = []
        while True:
            data = s.recv(512)
            if not data:
                break
            chunks.append(data)
    finally:
        s.close()
    raw = b''.join(chunks)
    sep = raw.find(b'\r\n\r\n')
    head, body = (raw, b'') if sep < 0 else (raw[:sep], raw[sep + 4:])
    status_line = head.split(b'\r\n', 1)[0]
    parts = status_line.split()
    if len(parts) < 2 or not parts[1].startswith(b'2'):
        raise OSError('HTTP error: %s' % status_line)
    return body.decode()


class Switch:
    def __init__(self, cfg):
        self.name = cfg.get('name', 'Light')
        self.type = cfg.get('type', 'tasmota')
        self.host = cfg.get('host', '')
        self.channel = int(cfg.get('channel', 0))
        self.cfg = cfg
        self.is_on = False
        self.reachable = False

    def _base(self):
        return 'http://' + self.host

    def _set_url(self, on):
        t = self.type
        if t == 'tasmota':
            idx = '' if self.channel == 0 else str(self.channel)
            return '%s/cm?cmnd=Power%s%%20%s' % (self._base(), idx, 'On' if on else 'Off')
        if t == 'shelly1':
            return '%s/relay/%d?turn=%s' % (self._base(), self.channel, 'on' if on else 'off')
        if t == 'shelly2':
            return '%s/rpc/Switch.Set?id=%d&on=%s' % (self._base(), self.channel, 'true' if on else 'false')
        if t == 'http':
            return self.cfg['on_url'] if on else self.cfg['off_url']
        raise ValueError('unknown switch type: %s' % t)

    def _status_url(self):
        t = self.type
        if t == 'tasmota':
            idx = '' if self.channel == 0 else str(self.channel)
            return '%s/cm?cmnd=Power%s' % (self._base(), idx)
        if t == 'shelly1':
            return '%s/relay/%d' % (self._base(), self.channel)
        if t == 'shelly2':
            return '%s/rpc/Switch.GetStatus?id=%d' % (self._base(), self.channel)
        return self.cfg.get('status_url')

    def _parse_state(self, body):
        t = self.type
        if t == 'tasmota':
            d = json.loads(body)
            for k, v in d.items():
                if k.startswith('POWER'):
                    return v == 'ON'
            return None
        if t == 'shelly1':
            return bool(json.loads(body).get('ison'))
        if t == 'shelly2':
            return bool(json.loads(body).get('output'))
        # Generic: look for an "on"/"ON"/"true" style answer.
        b = body.strip().lower()
        return b in ('1', 'on', 'true', '"on"') or '"on"' in b or '"power":"on"' in b

    def refresh(self):
        if self.type == 'tuya':
            return self._tuya_refresh()
        url = self._status_url()
        if not url:
            return self.is_on
        try:
            state = self._parse_state(http_get(url))
            if state is not None:
                self.is_on = state
            self.reachable = True
        except Exception as e:
            print('[switch] %s status failed: %s' % (self.name, e))
            self.reachable = False
        return self.is_on

    # -- Smart Life / Tuya --
    def _tuya(self):
        dev = self.cfg.get('_dev')
        if dev is None:
            import tuya
            dev = tuya.TuyaDevice(self.host, self.cfg['device_id'], self.cfg['local_key'],
                                  self.cfg.get('version', 3.3), TIMEOUT_S)
            self.cfg['_dev'] = dev
        return dev

    def _dp(self):
        return str(self.cfg.get('dp', 1))

    def _tuya_refresh(self):
        try:
            dps = self._tuya().status()
            if self._dp() in dps:
                self.is_on = bool(dps[self._dp()])
            self.reachable = True
        except Exception as e:
            print('[switch] %s status failed: %s' % (self.name, e))
            self.reachable = False
        return self.is_on

    def set(self, on):
        """Switch on/off. Returns True if the device acknowledged it."""
        try:
            if self.type == 'tuya':
                self._tuya().set_dps({self._dp(): on})
            else:
                http_get(self._set_url(on))
            self.is_on = on
            self.reachable = True
            return True
        except Exception as e:
            print('[switch] %s set failed: %s' % (self.name, e))
            self.reachable = False
            # Keep the UI responsive even when the switch is unreachable.
            self.is_on = on
            return False

    def toggle(self):
        return self.set(not self.is_on)


def load(cfg):
    return [Switch(c) for c in cfg.get('switches', [])]
