"""Desktop test of the Tuya driver against a simulated Smart Life plug (3.3 and 3.4).

The simulator implements the device side of the same protocol, so this checks
framing, encryption, the 3.4 key exchange and the switch wiring end to end. It
is not a substitute for a real plug.
Run: python3 tests/test_tuya.py   (needs the `cryptography` package)
"""
import json
import os
import socket
import struct
import sys
import threading
import types

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'device'))


class _Aes:  # MicroPython cryptolib.aes(key, 1) stand-in
    def __init__(self, key, mode):
        assert mode == 1
        self.c = Cipher(algorithms.AES(key), modes.ECB())
    def encrypt(self, d):
        e = self.c.encryptor(); return e.update(d) + e.finalize()
    def decrypt(self, d):
        e = self.c.decryptor(); return e.update(d) + e.finalize()
sys.modules['cryptolib'] = types.SimpleNamespace(aes=_Aes)

import tuya  # noqa: E402
import switches  # noqa: E402

KEY = b'0123456789abcdef'
DEV_ID = 'bf1234567890abcdef'


class FakePlug(threading.Thread):
    def __init__(self, version):
        super().__init__(daemon=True)
        self.version = version
        self.dps = {'1': False, '9': 0}
        self.srv = socket.socket(); self.srv.bind(('127.0.0.1', 0)); self.srv.listen(1)
        self.port = self.srv.getsockname()[1]

    def reply(self, conn, key, seq, cmd, payload):
        conn.send(tuya.pack(seq, cmd, b'\x00\x00\x00\x00' + payload, key, self.version))

    def run(self):
        while True:
            conn, _ = self.srv.accept()
            key = KEY; buf = b''; local_nonce = None; remote_nonce = os.urandom(16)
            try:
                while True:
                    try:
                        cmd, payload, buf = tuya.unpack(buf, key, self.version)
                    except ValueError:
                        d = conn.recv(1024)
                        if not d: break
                        buf += d; continue
                    if cmd == tuya.SESS_KEY_NEG_START:
                        local_nonce = tuya.aes_decrypt(KEY, payload)
                        self.reply(conn, KEY, 1, tuya.SESS_KEY_NEG_RESP, tuya.aes_encrypt(
                            KEY, remote_nonce + tuya.hmac_sha256(KEY, local_nonce)))
                    elif cmd == tuya.SESS_KEY_NEG_FINISH:
                        assert tuya.aes_decrypt(KEY, payload) == tuya.hmac_sha256(KEY, remote_nonce)
                        x = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
                        key = tuya.aes_encrypt(KEY, x, pad=False)[:16]
                    else:
                        if self.version < 3.4 and payload[:3] == b'3.3':
                            payload = payload[15:]
                        req = tuya.aes_decrypt(key, payload)
                        if req[:3] == b'3.4': req = req[15:]
                        req = json.loads(req)
                        if cmd in (tuya.CONTROL, tuya.CONTROL_NEW):
                            dps = req['data']['dps'] if self.version >= 3.4 else req['dps']
                            self.dps.update(dps)
                            self.reply(conn, key, 2, cmd, b'')
                        else:
                            out = {'devId': DEV_ID, 'dps': self.dps}
                            if self.version >= 3.4:
                                out = {'protocol': 4, 'data': {'dps': self.dps}}
                            self.reply(conn, key, 2, cmd, tuya.aes_encrypt(key, json.dumps(out).encode()))
            finally:
                conn.close()


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond: sys.exit(1)

check('crc32 matches zlib', tuya.crc32(b'hello') == __import__('zlib').crc32(b'hello'))
import hmac, hashlib  # noqa: E402
check('hmac_sha256 matches stdlib', tuya.hmac_sha256(KEY, b'x') == hmac.new(KEY, b'x', hashlib.sha256).digest())

for v in (3.3, 3.4):
    plug = FakePlug(v); plug.start()
    sw = switches.Switch({'name': 'Lamp', 'type': 'tuya', 'host': '127.0.0.1:%d' % plug.port,
                          'device_id': DEV_ID, 'local_key': KEY.decode(), 'version': v})
    check('v%s status reads off' % v, sw.refresh() is False and sw.reachable)
    check('v%s switch on acknowledged' % v, sw.set(True) and plug.dps['1'] is True)
    check('v%s status reads on' % v, sw.refresh() is True)
    check('v%s toggle off' % v, sw.toggle() and plug.dps['1'] is False)
    sw.cfg['_dev'].close()
    check('v%s reconnects after drop' % v, sw.set(True) and plug.dps['1'] is True)

bad = switches.Switch({'name': 'Bad', 'type': 'tuya', 'host': '127.0.0.1:%d' % plug.port,
                       'device_id': DEV_ID, 'local_key': 'wrongwrongwrong!', 'version': 3.4})
check('wrong local key is reported as not responding', bad.set(False) is False)
print('all good')
