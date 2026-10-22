# net.py - WiFi, zegar (NTP) i zapytania HTTP do glownego ESP.
#
# Wszystko synchronicznie, z limitem czasu: monitor nic innego nie robi,
# a watchdog w main.py pilnuje, zeby nic nie wisialo.
import json
import socket

import network
import ntptime
import utime

import config
import model
import secrets

NETWORKS = getattr(secrets, "NETWORKS", None) or (
    (secrets.SSID, secrets.PASSWORD),
    (secrets.SSID + "_EXT", secrets.PASSWORD),
)

HOST_FILE = "host.txt"

_wlan = None


# --- WiFi ---

def _candidates(wlan):
    """Znane sieci w zasiegu, najsilniejsza pierwsza; pusty skan = wszystkie."""
    passwords = dict(NETWORKS)
    found = {}

    try:
        scan = wlan.scan()
    except OSError:
        scan = []

    for entry in scan:
        try:
            ssid = entry[0].decode()
        except UnicodeError:
            continue

        if ssid in passwords and (ssid not in found or entry[3] > found[ssid]):
            found[ssid] = entry[3]

    if not found:
        return list(NETWORKS)

    return [(s, passwords[s]) for s in sorted(found, key=lambda s: -found[s])]


def wifi_connected():
    return _wlan is not None and _wlan.isconnected()


def connect_wifi(feed, timeout_s=20):
    """Laczy z najsilniejsza znana siecia. feed() karmi watchdoga."""
    global _wlan

    if wifi_connected():
        return True

    try:
        network.hostname("growbox-monitor")
    except (AttributeError, ValueError, OSError):
        pass

    if _wlan is None:
        _wlan = network.WLAN(network.STA_IF)

    _wlan.active(True)

    for ssid, password in _candidates(_wlan):
        print("WiFi: connecting to", ssid)

        try:
            _wlan.connect(ssid, password)
        except OSError as e:
            print("WiFi: connect error", e)
            continue

        for _ in range(timeout_s * 4):
            feed()

            if _wlan.isconnected():
                print("WiFi: IP", _wlan.ifconfig()[0])
                return True

            utime.sleep_ms(250)

        try:
            _wlan.disconnect()
        except OSError:
            pass

    return False


def my_ip():
    return _wlan.ifconfig()[0] if wifi_connected() else ""


# --- zegar ---

_synced = False


def sync_clock():
    global _synced

    try:
        ntptime.settime()  # RTC w UTC
        _synced = True
        print("NTP: ok", model.format_local(local_now()))
    except (OSError, OverflowError) as e:
        print("NTP: error", e)

    return _synced


def local_now():
    """Czas lokalny w Polsce (s od 2000) albo None przed pierwszym NTP."""
    if not _synced:
        return None

    utc = model.epoch(*utime.gmtime()[:6])
    return utc + model.polish_utc_offset(utc)


def utc_offset():
    utc = model.epoch(*utime.gmtime()[:6])
    return model.polish_utc_offset(utc)


# --- HTTP ---

def get(host, path, timeout=config.HTTP_TIMEOUT_S):
    """GET z tokenem -> JSON. Odpowiedz czytana do zamkniecia polaczenia
    (log klimatu idzie bez Content-Length)."""
    addr = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)

    try:
        s.connect(addr)
        s.write(
            "GET %s HTTP/1.0\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
            "X-Client: monitor\r\nConnection: close\r\n\r\n" % (path, host, secrets.API_TOKEN)
        )
        chunks = []

        while True:
            chunk = s.recv(1024)

            if not chunk:
                break

            chunks.append(chunk)
    finally:
        s.close()

    data = b"".join(chunks)
    chunks = None
    end = data.find(b"\r\n\r\n")

    if end < 0:
        raise OSError("bad response")

    code = data[9:12]

    if code != b"200":
        raise OSError("HTTP " + code.decode())

    return json.loads(data[end + 4:])


def load_host():
    try:
        with open(HOST_FILE) as f:
            return f.read().strip() or config.ESP_HOST
    except OSError:
        return config.ESP_HOST


def save_host(host):
    with open(HOST_FILE, "w") as f:
        f.write(host)


def _is_growbox(host):
    try:
        return "config" in get(host, "/api/config", 3)
    except Exception:
        return False


def find_esp(last_host, feed):
    """Glowne ESP pod nowym adresem (np. po resecie routera): najpierw
    esp32.local, potem siec /24, najblizsze adresy pierwsze."""
    try:
        # na ESP32 adres to krotka ("192.168.18.100", 80)
        ip = socket.getaddrinfo(config.ESP_MDNS, 80)[0][-1][0]

        if ip != last_host and _is_growbox(ip):
            return ip
    except Exception as e:
        print("Search: mDNS", e)

    mine = my_ip()

    if not mine:
        return None

    prefix = mine.rsplit(".", 1)[0]

    try:
        last = int(last_host.rsplit(".", 1)[1])
    except (ValueError, IndexError):
        last = 100

    own = int(mine.rsplit(".", 1)[1])
    hosts = sorted((h for h in range(1, 255) if h not in (last, own)), key=lambda h: abs(h - last))

    for h in hosts:
        feed()
        host = "%s.%d" % (prefix, h)
        s = socket.socket()
        s.settimeout(0.3)

        try:
            s.connect(socket.getaddrinfo(host, 80)[0][-1])
            open_port = True
        except OSError:
            open_port = False
        finally:
            s.close()

        if open_port and _is_growbox(host):
            return host

    return None
