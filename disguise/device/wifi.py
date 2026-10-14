# Wi-Fi helpers. Every call is a no-op when the firmware was built without
# networking (the stock SeedSigner image), so the disguise still runs offline.
import time

try:
    import network
except ImportError:
    network = None

_wlan = None


def available():
    return network is not None


def connect(ssid, password, timeout_ms=8000):
    """Join the network. Returns True when connected, False otherwise."""
    global _wlan
    if network is None or not ssid:
        return False
    try:
        _wlan = network.WLAN(network.STA_IF)
        _wlan.active(True)
        if not _wlan.isconnected():
            _wlan.connect(ssid, password)
            start = time.ticks_ms()
            while not _wlan.isconnected():
                if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                    return False
                time.sleep_ms(100)
        return True
    except Exception as e:
        print('[wifi] connect failed:', e)
        return False


def is_connected():
    try:
        return _wlan is not None and _wlan.isconnected()
    except Exception:
        return False


def shutdown():
    """Disconnect and power the radio down (called before SeedSigner starts)."""
    global _wlan
    if _wlan is None:
        return
    try:
        _wlan.disconnect()
    except Exception:
        pass
    try:
        _wlan.active(False)
    except Exception:
        pass
    _wlan = None
