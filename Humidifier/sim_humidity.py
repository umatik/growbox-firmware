# sim_humidity.py - test nawilzacza bez czujnika, na devowym (glownym) ESP
#
# Prawdziwa logika z App/humidifier.py, tylko sensor.get() podaje sztuczna
# wilgotnosc: spadek ponizej progu "min" i powrot ponad "max". Skrypt
# idzie tylko do RAM, korzysta z modulow juz wgranych na plytke:
#   mpremote connect /dev/cu.usbserial-0001 run Humidifier/sim_humidity.py
# Otwarcie portu resetuje ESP, a podczas startu firmware Ctrl-C nie
# dziala - gdy mpremote zglosi "could not enter raw repl", trzeba poczekac
# na koniec startu i polaczyc sie bez resetu. Przerwany firmware zostawia
# w petli asyncio swoje zadania (harmonogram tez wola humidifier.update).
#
# Po koncu watchdog z przerwanego programu resetuje plytke, wiec wraca
# normalny firmware.
import machine
import uasyncio as asyncio
import utime

import humidifier
import sensor

# wilgotnosc w %, kolejne kroki co STEP_S
PROFILE = (55, 52, 50, 48, 46, 44, 42, 40, 40, 42, 44, 46, 48, 50, 52, 55)
STEP_S = 5

CONFIG = {
    "auto": {"enabled": False},
    "humidifier": {
        "peer": "10003baf0ae8",
        "manual": {"enabled": True, "min": 45, "max": 50},
        "auto": {"enabled": False, "min": 45, "max": 50},
        "fan": {"maxLevel": 40, "tempLimit": 27},
    },
}

# blokada przelaczania skrocona z 60 s, zeby test trwal ~1,5 min
humidifier.MIN_SWITCH_S = 5

_humidity = PROFILE[0]


def _fake_get():
    return {"temperature": 24.0, "humidity": _humidity, "ts": utime.time()}


sensor.get = _fake_get


def _report(label):
    st = humidifier.status()
    print("SIM: %s hum=%d%% want=%s mini=%s online=%s" % (
        label, _humidity, "ON" if st["want"] else "OFF",
        "ON" if st["on"] else "OFF", st["online"]))


async def sim():
    global _humidity

    # watchdog z przerwanego programu dalej liczy - karmimy go
    wdt = machine.WDT(timeout=60000)
    asyncio.create_task(humidifier.link_task(CONFIG))

    for _ in range(10):
        wdt.feed()
        await asyncio.sleep(1)

    _report("start")

    for h in PROFILE:
        _humidity = h

        for _ in range(STEP_S):
            wdt.feed()
            humidifier.update(CONFIG, True)
            await asyncio.sleep(1)

        _report("step")

    # koniec: nawilzacz wylaczony niezaleznie od wilgotnosci
    CONFIG["humidifier"]["manual"]["enabled"] = False
    humidifier.update(CONFIG, True)

    for _ in range(8):
        wdt.feed()
        await asyncio.sleep(1)

    _report("end")


asyncio.run(sim())
