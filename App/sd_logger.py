import os

import uasyncio as asyncio
import utime
from machine import Pin, SPI

import esp_config
import sdcard
import sensor
from server import _should_light_be_on

SD_MOUNT_POINT = "/sd"
_LOG_FILE = "/sd/environment.csv"

_interval_seconds = 300
_enabled = False


def init(interval_seconds=300):
    global _interval_seconds
    global _enabled

    try:
        print("SD: create SPI")

        spi = SPI(
            2,
            baudrate=400000,
            polarity=0,
            phase=0,
            sck=Pin(
                esp_config.PINS["sd_sck"]
            ),
            mosi=Pin(
                esp_config.PINS["sd_mosi"]
            ),
            miso=Pin(
                esp_config.PINS["sd_miso"]
            ),
        )

        print("SD: create CS")

        cs = Pin(
            esp_config.PINS["sd_cs"],
            Pin.OUT
        )

        print("SD: init card")

        sd = sdcard.SDCard(
            spi,
            cs,
            400000
        )

        print("SD: test raw read")

        buf = bytearray(512)

        sd.readblocks(0, buf)

        print(
            "SD: raw read OK:",
            buf[:16]
        )

        print("SD: mount")

        os.mount(
            sd,
            SD_MOUNT_POINT
        )

        print("SD: mounted")

        print("SD: open log")

        try:
            with open(_LOG_FILE, "r"):
                pass

        except OSError:
            with open(_LOG_FILE, "w") as f:
                f.write(
                    "datetime,day_night,temperature_c,humidity_percent\n"
                )

        print("SD: log ready")

        _interval_seconds = interval_seconds
        _enabled = True

        print(
            "SD mounted:",
            SD_MOUNT_POINT
        )

        return True

    except Exception as e:
        _enabled = False

        print(
            "SD init error:",
            e
        )

        return False


async def task():
    while True:
        if _enabled:
            try:
                now = utime.localtime()

                data = sensor.get()

                temperature = data.get(
                    "temperature"
                )

                humidity = data.get(
                    "humidity"
                )

                if (
                        temperature is not None
                        and humidity is not None
                ):
                    day_night = (
                        "DAY"
                        if _should_light_be_on()
                        else "NIGHT"
                    )

                    timestamp = (
                            "%04d-%02d-%02d %02d:%02d:%02d"
                            % (
                                now[0],
                                now[1],
                                now[2],
                                now[3],
                                now[4],
                                now[5],
                            )
                    )

                    with open(
                            _LOG_FILE,
                            "a"
                    ) as f:
                        f.write(
                            "%s,%s,%s,%s\n"
                            % (
                                timestamp,
                                day_night,
                                temperature,
                                humidity,
                            )
                        )

                    print(
                        "SD log:",
                        timestamp,
                        day_night,
                        temperature,
                        humidity,
                    )

            except Exception as e:
                print(
                    "SD logger error:",
                    e
                )

        await asyncio.sleep(
            _interval_seconds
        )
