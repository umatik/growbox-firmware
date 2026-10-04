# sim_live.py - nawilzacz z recznie ustawiana wilgotnoscia, na devowym ESP
#
# Jak sim_humidity.py, ale wilgotnosc przychodzi z konsoli: liczba + Enter
# ustawia nowa wartosc, "q" konczy. Co 2 s wypisuje wilgotnosc, zadany
# stan i stan zgloszony przez mini. Progi MANUAL: wlacza ponizej 45,
# wylacza od 50 (histereza - miedzy nimi stan sie nie zmienia).
import select
import sys

import uasyncio as asyncio
import utime

import humidifier
import sensor

START = 55

CONFIG = {
    "auto": {"enabled": False},
    "humidifier": {
        "peer": "10003baf0ae8",
        "manual": {"enabled": True, "min": 45, "max": 50},
        "auto": {"enabled": False, "min": 45, "max": 50},
        "fan": {"maxLevel": 40, "tempLimit": 27},
    },
}

humidifier.MIN_SWITCH_S = 5

_humidity = START


def _fake_get():
    return {"temperature": 24.0, "humidity": _humidity, "ts": utime.time()}


sensor.get = _fake_get


def _report():
    st = humidifier.status()
    print("LIVE: hum=%d%% want=%s mini=%s online=%s" % (
        _humidity, "ON" if st["want"] else "OFF",
        "ON" if st["on"] else "OFF", st["online"]))


async def main():
    global _humidity

    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    asyncio.create_task(humidifier.link_task(CONFIG))

    buf = ""
    tick = 0

    while True:
        while poll.poll(0):
            c = sys.stdin.read(1)

            if c not in "\r\n":
                buf += c
                continue

            cmd = buf.strip()
            buf = ""

            if cmd == "q":
                CONFIG["humidifier"]["manual"]["enabled"] = False
                humidifier.update(CONFIG, True)
                await asyncio.sleep(2)
                _report()
                return

            try:
                _humidity = int(cmd)
                print("SET: hum=%d%%" % _humidity)
            except ValueError:
                pass

        humidifier.update(CONFIG, True)

        if tick % 2 == 0:
            _report()

        tick += 1
        await asyncio.sleep(1)


asyncio.run(main())
