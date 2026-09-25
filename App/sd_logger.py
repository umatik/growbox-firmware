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
_ARCHIVE_FILE = "/sd/environment.old.csv"

MAX_LOG_SIZE = 1024 * 1024

DEFAULT_INTERVAL_SECONDS = 300
RECOVERY_INTERVAL_SECONDS = 30

_interval_seconds = DEFAULT_INTERVAL_SECONDS
_enabled = False

_spi = None
_cs = None
_sd = None


def _create_sd():
    global _spi
    global _cs
    global _sd

    _spi = SPI(
        2,
        baudrate=400000,
        polarity=0,
        phase=0,
        sck=Pin(esp_config.PINS["sd_sck"]),
        mosi=Pin(esp_config.PINS["sd_mosi"]),
        miso=Pin(esp_config.PINS["sd_miso"]),
    )

    _cs = Pin(
        esp_config.PINS["sd_cs"],
        Pin.OUT
    )

    _sd = sdcard.SDCard(
        _spi,
        _cs,
        400000
    )


def _unmount():
    try:
        os.umount(SD_MOUNT_POINT)
    except Exception:
        pass


def _mount():
    global _enabled

    try:
        _create_sd()

        os.mount(
            _sd,
            SD_MOUNT_POINT
        )

        try:
            with open(_LOG_FILE, "r"):
                pass
        except OSError:
            with open(_LOG_FILE, "w") as f:
                f.write(
                    "datetime,day_night,temperature_c,humidity_percent\n"
                )

        _enabled = True

        print("SD: OK")

        return True

    except Exception:
        _enabled = False

        print("SD: FAIL -> retry")

        _unmount()

        return False


def init(interval_seconds=DEFAULT_INTERVAL_SECONDS):
    global _interval_seconds
    global _enabled

    _interval_seconds = interval_seconds
    _enabled = False

    _unmount()

    return _mount()


def _rotate_log():
    try:
        size = os.stat(_LOG_FILE)[6]
    except OSError:
        return

    if size < MAX_LOG_SIZE:
        return

    try:
        os.remove(_ARCHIVE_FILE)
    except OSError:
        pass

    os.rename(
        _LOG_FILE,
        _ARCHIVE_FILE
    )

    with open(_LOG_FILE, "w") as f:
        f.write(
            "datetime,day_night,temperature_c,humidity_percent\n"
        )


def _handle_error(error):
    global _enabled
    global _spi
    global _cs
    global _sd

    _enabled = False

    print("SD: lost -> retry")

    _unmount()

    _spi = None
    _cs = None
    _sd = None


def start():
    global _enabled

    if not _enabled:
        return _mount()

    try:
        with open(_LOG_FILE, "a"):
            pass

        _enabled = True

        return True

    except Exception:
        return _mount()


def _parse_line(line):
    line = line.strip()

    if not line:
        return None

    parts = line.split(",")

    if len(parts) != 4:
        return None

    try:
        return {
            "datetime": parts[0],
            "day_night": parts[1],
            "temperature": float(parts[2]),
            "humidity": float(parts[3]),
        }

    except ValueError:
        return None


def _read_file(path, data, limit, before):
    count = 0

    try:
        with open(path, "r") as f:
            f.readline()

            for line in f:
                item = _parse_line(line)

                if item is None:
                    continue

                if (
                        before is not None
                        and item["datetime"] >= before
                ):
                    continue

                count += 1

                data.append(item)

                if len(data) > limit:
                    data.pop(0)

    except OSError:
        pass

    return count


def get_environment(limit=500, before=None):
    if not _enabled:
        return {
            "status": "offline",
            "data": [],
            "has_more": False,
            "next_before": None,
        }

    try:
        limit = int(limit)

        if limit < 1:
            limit = 1
        elif limit > 500:
            limit = 500

        if before == "":
            before = None

        data = []

        archive_count = _read_file(
            _ARCHIVE_FILE,
            data,
            limit,
            before
        )

        current_count = _read_file(
            _LOG_FILE,
            data,
            limit,
            before
        )

        total_count = archive_count + current_count

        has_more = total_count > limit

        next_before = None

        if data and has_more:
            next_before = data[0]["datetime"]

        return {
            "status": "ok",
            "data": data,
            "has_more": has_more,
            "next_before": next_before,
        }

    except Exception as e:
        _handle_error(e)

        return {
            "status": "offline",
            "data": [],
            "has_more": False,
            "next_before": None,
        }


def get_top():
    return get_environment(20)


def erase():
    global _enabled

    try:
        if not _enabled:
            if not _mount():
                raise OSError("SD offline")

        try:
            os.remove(_LOG_FILE)
        except OSError:
            pass

        try:
            os.remove(_ARCHIVE_FILE)
        except OSError:
            pass

        with open(_LOG_FILE, "w") as f:
            f.write(
                "datetime,day_night,temperature_c,humidity_percent\n"
            )

        _enabled = True

    except Exception as e:
        _handle_error(e)
        raise


async def task():
    while True:
        if _enabled:
            try:
                now = utime.localtime()

                data = sensor.get()

                temperature = data.get("temperature")
                humidity = data.get("humidity")

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

                    _rotate_log()

                    with open(_LOG_FILE, "a") as f:
                        f.write(
                            "%s,%s,%s,%s\n"
                            % (
                                timestamp,
                                day_night,
                                temperature,
                                humidity,
                            )
                        )

                await asyncio.sleep(
                    _interval_seconds
                )

            except Exception as e:
                _handle_error(e)

                await asyncio.sleep(
                    RECOVERY_INTERVAL_SECONDS
                )

        else:
            if _mount():
                pass

            await asyncio.sleep(
                RECOVERY_INTERVAL_SECONDS
            )
