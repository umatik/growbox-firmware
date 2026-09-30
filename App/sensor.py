import uasyncio as asyncio
import utime
from machine import I2C, Pin

import esp_config

ADDRESS = 0x44
CMD_MEASURE = bytes([0xFD])

_i2c = None
_interval_ms = 10000

_last = {
    "temperature": None,
    "humidity": None,
    "ts": 0
}


def init(interval_ms=10000):
    global _i2c
    global _interval_ms

    _i2c = I2C(
        0,
        sda=Pin(
            esp_config.PINS["sensor_sda"]
        ),
        scl=Pin(
            esp_config.PINS["sensor_scl"]
        ),
        freq=100000
    )

    _interval_ms = interval_ms


def read():
    global _last

    if not _i2c:
        return

    try:
        _i2c.writeto(
            ADDRESS,
            CMD_MEASURE
        )

        utime.sleep_ms(20)

        data = _i2c.readfrom(
            ADDRESS,
            6
        )

        t_raw = (
                        data[0] << 8
                ) | data[1]

        h_raw = (
                        data[3] << 8
                ) | data[4]

        temp = round(
            -45 + 175 * (
                    t_raw / 65535
            ),
            1
        )

        hum = round(
            max(
                0,
                min(
                    100,
                    -6 + 125 * (
                            h_raw / 65535
                    )
                )
            ),
            1
        )

        _last = {
            "temperature": temp,
            "humidity": hum,
            "ts": utime.time()
        }

    except Exception as e:
        print(
            "Sensor error:",
            e
        )


def get():
    # po 3 chybionych pomiarach (odpiety czujnik, blad I2C) brak odczytu
    # zamiast ostatniej wartosci - inaczej API, log na SD i LCD pokazuja
    # zamrozona temperature jako aktualna
    if utime.time() - _last["ts"] > 3 * _interval_ms // 1000:
        return {
            "temperature": None,
            "humidity": None,
            "ts": _last["ts"]
        }

    return _last


async def sensor_task():
    while True:
        read()

        await asyncio.sleep_ms(
            _interval_ms
        )
