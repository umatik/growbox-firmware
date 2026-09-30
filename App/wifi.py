import network
import uasyncio as asyncio

import display
import events
from secrets import SSID, PASSWORD

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


async def _join_wifi(station, timeout_s=20):
    """Join Wi-Fi without blocking the rest of the asyncio application."""

    if station.isconnected():
        return True

    station.connect(SSID, PASSWORD)

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


async def connect_wifi():
    global wlan

    station = _new_wlan()

    print("Connecting to WiFi:", SSID)

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

    while True:
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
