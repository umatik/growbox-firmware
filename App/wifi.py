import network
import uasyncio as asyncio

import display
import events
import secrets

# Znane sieci: (ssid, haslo). Wzmacniacz TP-Link RE200 rozglasza siec
# routera jako <SSID>_EXT z tym samym haslem. secrets.py nie idzie przez
# OTA, wiec bez NETWORKS w secrets.py obie sieci skladamy tutaj.
NETWORKS = getattr(secrets, "NETWORKS", None) or (
    (secrets.SSID, secrets.PASSWORD),
    (secrets.SSID + "_EXT", secrets.PASSWORD),
)

# Powrot na lepsza siec, np. gdy wzmacniacz wroci po awarii, a ESP siedzi
# na routerze: przy slabym sygnale co ROAM_CHECK_S skan i przejscie, gdy
# znana siec jest wyraznie silniejsza. Zmiana to kilka sekund bez WiFi,
# stad margines - zeby nie skakac miedzy dwiema podobnymi sieciami.
ROAM_CHECK_S = 600
ROAM_WEAK_RSSI = -65
ROAM_MARGIN_DB = 10

wlan = None
display.init()


def _new_wlan():
    station = network.WLAN(network.STA_IF)
    station.active(True)
    station.config(dhcp_hostname="esp32")

    # Wylacz oszczedzanie energii WiFi - z nim ESP32 gubi/opoznia ACK-i
    # i dluzsze odpowiedzi HTTP potrafia stanac.
    try:
        station.config(pm=station.PM_NONE)
    except (AttributeError, ValueError):
        pass  # starsze MicroPython nie maja PM_NONE

    return station


def _candidates(station):
    """Znane sieci w zasiegu, najsilniejsza pierwsza: [(ssid, haslo, bssid)].

    Gdy wzmacniacz padnie, nie ma go w skanie i zostaje siec routera.
    Pusty skan (blad, chwilowa gluchota) - proba wszystkich po kolei.
    """

    passwords = dict(NETWORKS)
    found = {}

    try:
        scan = station.scan()
    except OSError as e:
        print("WiFi scan error:", e)
        scan = []

    for entry in scan:
        try:
            ssid = entry[0].decode()
        except UnicodeError:
            continue

        rssi = entry[3]

        if ssid in passwords and (
            ssid not in found or rssi > found[ssid][1]
        ):
            found[ssid] = (entry[1], rssi)

    if not found:
        return [(ssid, password, None) for ssid, password in NETWORKS]

    ranked = sorted(found, key=lambda ssid: found[ssid][1], reverse=True)

    for ssid in ranked:
        print("WiFi in range:", ssid, found[ssid][1], "dBm")

    return [(ssid, passwords[ssid], found[ssid][0]) for ssid in ranked]


async def _join_one(station, ssid, password, bssid, timeout_s):
    print("Connecting to WiFi:", ssid)

    try:
        if bssid:
            # konkretny punkt dostepu - wzmacniacz moze nadawac ta sama
            # nazwe co router
            station.connect(ssid, password, bssid=bssid)
        else:
            station.connect(ssid, password)
    except OSError as e:
        print("WiFi connect error:", e)
        return False

    for second in range(timeout_s):
        if station.isconnected():
            return True

        if second in (4, 9, 14):
            print(
                "WiFi still connecting...",
                second + 1,
                "s"
            )

        await asyncio.sleep(1)

    return station.isconnected()


async def _join_wifi(station, timeout_s=20):
    """Join Wi-Fi without blocking the rest of the asyncio application."""

    if station.isconnected():
        return True

    for ssid, password, bssid in _candidates(station):
        if await _join_one(station, ssid, password, bssid, timeout_s):
            events.log("wifi " + ssid)
            return True

        # przed nastepna siecia - inaczej ESP-IDF dalej probuje poprzedniej
        try:
            station.disconnect()
        except OSError:
            pass

    return False


def _better_network(station):
    """Nazwa znanej sieci silniejszej o ROAM_MARGIN_DB od obecnej albo None."""

    try:
        rssi = station.status("rssi")
    except (OSError, ValueError):
        return None

    if rssi >= ROAM_WEAK_RSSI:
        return None

    passwords = dict(NETWORKS)

    try:
        scan = station.scan()
    except OSError as e:
        print("WiFi scan error:", e)
        return None

    best = None
    best_rssi = rssi + ROAM_MARGIN_DB - 1

    for entry in scan:
        try:
            ssid = entry[0].decode()
        except UnicodeError:
            continue

        if ssid in passwords and entry[3] > best_rssi:
            best, best_rssi = ssid, entry[3]

    if best:
        print("WiFi roam:", rssi, "dBm ->", best, best_rssi, "dBm")

    return best


async def _roam(station):
    """Przejscie na wyraznie silniejsza siec. True, gdy po nim jest WiFi."""

    better = _better_network(station)

    if not better:
        return True

    events.log("wifi roam to " + better)
    station.disconnect()

    # _join_wifi skanuje jeszcze raz i bierze najsilniejsza; bez polaczenia
    # zajmie sie tym zwykla sciezka watchdoga
    return await _join_wifi(station)


async def connect_wifi():
    global wlan

    station = _new_wlan()

    if await _join_wifi(station):
        wlan = station
        myIp = wlan.ifconfig()[0]
        print("Connection IP:", myIp)
        return wlan

    print(
        "WiFi unavailable at startup; "
        "the application will keep retrying"
    )
    return None


async def wifi_watchdog():
    global wlan

    failed_attempts = 0
    since_roam_check = 0

    while True:
        if wlan is not None and wlan.isconnected():
            since_roam_check += 15

            if since_roam_check >= ROAM_CHECK_S:
                since_roam_check = 0

                try:
                    await _roam(wlan)
                except Exception as e:
                    print("WiFi roam error:", e)

        if wlan is None or not wlan.isconnected():
            print("WiFi lost! Reconnecting...")
            # LCD: dashboard pokazuje OFFLINE
            events.log("wifi lost")

            try:
                if wlan is not None:
                    wlan.disconnect()
                    wlan.active(False)

                    await asyncio.sleep(1)

                wlan = _new_wlan()

                if await _join_wifi(wlan):
                    failed_attempts = 0

                    print(
                        "WiFi restored:",
                        wlan.ifconfig()[0]
                    )
                    events.log("wifi restored")

                else:
                    failed_attempts += 1

                    delay = min(
                        60,
                        5 * (2 ** min(failed_attempts - 1, 3)))

                    print(
                        "WiFi reconnect failed; retry in",
                        delay,
                        "s"
                    )

                    await asyncio.sleep(delay)

            except Exception as e:
                failed_attempts += 1

                print(
                    "WiFi reconnect error:",
                    e
                )

        await asyncio.sleep(15)


def wifi_is_connected():
    return wlan is not None and wlan.isconnected()
