# Smart Life / Tuya local control over the LAN (no cloud), protocol 3.3 and 3.4.
# Each device needs its id and "local key"; see README for how to get them.
import json
import struct
import time

try:
    import cryptolib
except ImportError:
    try:
        import ucryptolib as cryptolib
    except ImportError:
        cryptolib = None
import hashlib
import os

PORT = 6668
PREFIX = 0x000055AA
SUFFIX = 0x0000AA55

SESS_KEY_NEG_START = 0x03
SESS_KEY_NEG_RESP = 0x04
SESS_KEY_NEG_FINISH = 0x05
CONTROL = 0x07
DP_QUERY = 0x0A
CONTROL_NEW = 0x0D
DP_QUERY_NEW = 0x10

NO_HEADER_CMDS = (DP_QUERY, DP_QUERY_NEW, SESS_KEY_NEG_START, SESS_KEY_NEG_RESP, SESS_KEY_NEG_FINISH)


# --- crypto helpers ---------------------------------------------------------
def _ecb(key, data, encrypt):
    if cryptolib is None:
        raise OSError('no AES support in this firmware')
    c = cryptolib.aes(key, 1)  # 1 = ECB
    return c.encrypt(data) if encrypt else c.decrypt(data)


def aes_encrypt(key, data, pad=True):
    if pad:
        n = 16 - len(data) % 16
        data = data + bytes([n]) * n
    return _ecb(key, data, True)


def aes_decrypt(key, data, unpad=True):
    out = _ecb(key, data, False)
    if unpad and out:
        n = out[-1]
        if 1 <= n <= 16:
            out = out[:-n]
    return out


def hmac_sha256(key, msg):
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + b'\x00' * (64 - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in key) + inner).digest()


def crc32(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


# --- framing ----------------------------------------------------------------
def pack(seq, cmd, payload, key, version):
    """Build one frame. 3.3 trails a CRC32, 3.4 an HMAC-SHA256 keyed with `key`."""
    use_hmac = version >= 3.4
    tail_len = (32 if use_hmac else 4) + 4
    head = struct.pack('>IIII', PREFIX, seq, cmd, len(payload) + tail_len)
    body = head + payload
    check = hmac_sha256(key, body) if use_hmac else struct.pack('>I', crc32(body))
    return body + check + struct.pack('>I', SUFFIX)


def unpack(buf, key, version):
    """Parse one device frame -> (cmd, payload, rest). Raises ValueError if incomplete."""
    if len(buf) < 16:
        raise ValueError('short')
    prefix, seq, cmd, length = struct.unpack('>IIII', buf[:16])
    if prefix != PREFIX:
        raise OSError('bad prefix')
    end = 16 + length
    if len(buf) < end:
        raise ValueError('short')
    tail_len = (32 if version >= 3.4 else 4) + 4
    payload = buf[16:end - tail_len]
    if version >= 3.4:
        if hmac_sha256(key, buf[:end - tail_len]) != buf[end - tail_len:end - 4]:
            raise OSError('bad HMAC (wrong local key?)')
    # Device replies carry a 4-byte return code before the payload.
    if len(payload) >= 4 and payload[:4] == b'\x00\x00\x00\x00':
        payload = payload[4:]
    return cmd, payload, buf[end:]


class TuyaDevice:
    def __init__(self, host, dev_id, local_key, version=3.3, timeout=3):
        self.host = host
        self.dev_id = dev_id
        self.local_key = local_key.encode() if isinstance(local_key, str) else local_key
        self.version = float(version)
        self.timeout = timeout
        self.seq = 1
        self._sock = None
        self._key = self.local_key  # session key after 3.4 negotiation
        self._buf = b''

    # -- connection --
    def _connect(self):
        import socket
        port = PORT
        host = self.host
        if ':' in host:
            host, p = host.split(':', 1)
            port = int(p)
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(self.timeout)
        s.connect(addr)
        self._sock = s
        self._buf = b''
        self._key = self.local_key
        if self.version >= 3.4:
            self._negotiate()

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def _send(self, cmd, payload):
        frame = pack(self.seq, cmd, payload, self._key, self.version)
        self.seq += 1
        self._sock.send(frame)

    def _recv(self, want_cmd=None):
        while True:
            try:
                cmd, payload, self._buf = unpack(self._buf, self._key, self.version)
            except ValueError:
                data = self._sock.recv(1024)
                if not data:
                    raise OSError('connection closed')
                self._buf += data
                continue
            if want_cmd is None or cmd == want_cmd:
                return cmd, payload

    def _negotiate(self):
        local_nonce = os.urandom(16)
        self._send(SESS_KEY_NEG_START, aes_encrypt(self.local_key, local_nonce))
        _, payload = self._recv(SESS_KEY_NEG_RESP)
        data = aes_decrypt(self.local_key, payload)
        remote_nonce, proof = data[:16], data[16:48]
        if proof != hmac_sha256(self.local_key, local_nonce):
            raise OSError('session key check failed (wrong local key?)')
        self._send(SESS_KEY_NEG_FINISH,
                   aes_encrypt(self.local_key, hmac_sha256(self.local_key, remote_nonce)))
        xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
        self._key = aes_encrypt(self.local_key, xored, pad=False)[:16]

    # -- payloads --
    def _header(self):
        return ('%.1f' % self.version).encode() + b'\x00' * 12

    def _encode(self, cmd, obj):
        raw = json.dumps(obj).encode()
        if self.version >= 3.4:
            if cmd not in NO_HEADER_CMDS:
                raw = self._header() + raw
            return aes_encrypt(self._key, raw)
        enc = aes_encrypt(self._key, raw)
        return enc if cmd in NO_HEADER_CMDS else self._header() + enc

    def _decode(self, payload):
        if not payload:
            return {}
        if payload[:3] in (b'3.3', b'3.4', b'3.5'):
            payload = payload[15:]
        data = aes_decrypt(self._key, payload)
        if data[:3] in (b'3.3', b'3.4', b'3.5'):
            data = data[15:]
        try:
            return json.loads(data)
        except ValueError:
            return {}

    def _request(self, cmd, obj, reply_cmd):
        for attempt in (0, 1):
            try:
                if self._sock is None:
                    self._connect()
                self._send(cmd, self._encode(cmd, obj))
                _, payload = self._recv(reply_cmd)
                return self._decode(payload)
            except Exception:
                self.close()
                if attempt:
                    raise

    def _t(self):
        return str(int(time.time()))

    def status(self):
        """Return the device's data points, e.g. {'1': True}."""
        if self.version >= 3.4:
            d = self._request(DP_QUERY_NEW, {}, DP_QUERY_NEW)
        else:
            d = self._request(DP_QUERY, {'gwId': self.dev_id, 'devId': self.dev_id,
                                         'uid': self.dev_id, 't': self._t()}, DP_QUERY)
        if 'data' in d and isinstance(d['data'], dict):
            d = d['data']
        return d.get('dps', {})

    def set_dps(self, dps):
        if self.version >= 3.4:
            obj = {'protocol': 5, 't': int(time.time()), 'data': {'dps': dps}}
            return self._request(CONTROL_NEW, obj, CONTROL_NEW)
        obj = {'devId': self.dev_id, 'uid': self.dev_id, 't': self._t(), 'dps': dps}
        return self._request(CONTROL, obj, CONTROL)
