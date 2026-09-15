import uasyncio as asyncio
import utime
from machine import Pin, I2C

import esp_config
import ssd1306

_oled = None
_state_provider = None
_enabled = False


def init():
    global _oled

    i2c = I2C(
        1,
        scl=Pin(esp_config.PINS["oled_scl"]),
        sda=Pin(esp_config.PINS["oled_sda"]),
        freq=400000
    )

    _oled = ssd1306.SSD1306_I2C(
        128,
        64,
        i2c,
        addr=0x3C
    )

    _oled.fill(0)
    _oled.show()


def set_state_provider(provider):
    global _state_provider

    _state_provider = provider


def text(message, x=0, y=0, clear=True):
    if _oled is None or not _enabled:
        return

    if clear:
        _oled.fill(0)

    _oled.text(str(message), x, y)

    _oled.show()


def clear():
    if _oled is None:
        return

    _oled.fill(0)


def clear_buffer():
    if _oled is None:
        return

    _oled.fill(0)


def show():
    if _oled is None:
        return

    _oled.show()


def render_dashboard():
    if _oled is None or _state_provider is None:
        return

    state = _state_provider()

    now = utime.localtime()

    blink = ":" if now[5] % 2 == 0 else " "

    time_str = "%02d%s%02d" % (
        now[3],
        blink,
        now[4]
    )

    connection_text = (
        "ONLINE"
        if state["wifi"]
        else "OFFLINE"
    )

    flowering_text = ""
    flowering_start = state.get(
        "floweringStartDate"
    )

    if flowering_start:
        try:
            year, month, day = [
                int(x)
                for x in flowering_start.split("-")
            ]

            start_timestamp = utime.mktime(
                (
                    year,
                    month,
                    day,
                    0,
                    0,
                    0,
                    0,
                    0
                )
            )

            now_timestamp = utime.mktime(now)

            flowering_week = (
                (
                    (
                        now_timestamp
                        - start_timestamp
                    ) // 86400
                ) // 7
            ) + 1

            if flowering_week > 0:
                flowering_text = (
                    ", T%d  "
                    % flowering_week
                )

        except Exception:
            flowering_text = ""

    mode = state["mode"]

    if mode == "AUTO":
        day_night = (
            "DAY"
            if state["light"]
            else "NIGHT"
        )

        mode_text = (
            "AUTO: "
            + day_night
            + flowering_text
        )
    else:
        day_night = (
            "DAY"
            if state["light"]
            else "NIGHT"
        )

        mode_text = (
            "MANUAL: "
            + day_night
        )

    light_text = (
        "ON"
        if state["light"]
        else "OFF"
    )

    if state["fan"]:
        if (
            mode == "AUTO"
            and day_night == "NIGHT"
        ):
            fan_text = "ON (NIGHT)"
        else:
            fan_text = "ON"
    else:
        fan_text = "OFF"

    temperature = state.get(
        "temperature",
        "--"
    )

    humidity = state.get(
        "humidity",
        "--"
    )

    if isinstance(
        temperature,
        (int, float)
    ):
        temperature = (
            str(int(temperature))
            + "C"
        )
    else:
        temperature = "--"

    if isinstance(
        humidity,
        (int, float)
    ):
        humidity = (
            str(int(humidity))
            + "%"
        )
    else:
        humidity = "--"

    _oled.fill(0)

    _oled.text(
        connection_text,
        0,
        0
    )

    time_width = (
        len(time_str) * 8
    )

    x_time = max(
        0,
        128 - time_width
    )

    _oled.text(
        time_str,
        x_time,
        0
    )

    _oled.text(
        mode_text,
        0,
        12
    )

    _oled.text(
        "LIGHT: " + light_text,
        0,
        24
    )

    _oled.text(
        "FAN: " + fan_text,
        0,
        36
    )

    _oled.text(
        "T: "
        + temperature
        + " H: "
        + humidity,
        0,
        48
    )

    _oled.show()


def refresh():
    render_dashboard()


def toggle(enabled):
    global _enabled

    _enabled = enabled

    if _oled is None:
        return

    if enabled:
        render_dashboard()
    else:
        _oled.fill(0)
        _oled.show()


async def display_updater():
    while True:
        try:
            if _enabled:
                render_dashboard()

        except Exception as e:
            print(
                "Display loop error:",
                e
            )

        await asyncio.sleep(1)