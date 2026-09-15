import json

import uasyncio as asyncio
import utime
from machine import Pin

import config_store
import dimmer
import esp_config
import relay
import sensor
import display

API_TOKEN = "hftqpPVbeLwaqPDfJ25eCzxRETqsoX8sh1K56NRUp2zW8GxHr1M6u5s4fEMv"

config = config_store.load()

DEFAULT_LIGHT_SCHEDULE = [
    {
        "on": "18:00",
        "off": "06:00",
    }
]

fan_led = Pin(
    esp_config.PINS["btn_fan_led"],
    Pin.OUT,
)

light_led = Pin(
    esp_config.PINS["btn_light_led"],
    Pin.OUT,
)


# --- Button LEDs ---
def _set_fan_led(state):
    # LED is active LOW
    fan_led.value(0 if state else 1)


def _set_light_led(state):
    # LED is active LOW
    light_led.value(0 if state else 1)


# --- Relays ---
def _on_light_change(state):
    print("Server Light state:", state)
    config["relayLight"]["state"] = state
    config_store.save(config)
    _set_light_led(state)


def _on_fan_change(state):
    print("Server Fan state:", state)
    config["relayFan"]["state"] = state
    config_store.save(config)
    _set_fan_led(state)


light_relay = relay.Relay(
    pin=esp_config.PINS["relay_light"],
    state=config.get("relayLight", {}).get("state", 0),
    on_change=_on_light_change
)

fan_relay = relay.Relay(
    pin=esp_config.PINS["relay_fan"],
    state=config.get("relayFan", {}).get("state", 0),
    on_change=_on_fan_change
)

_set_fan_led(fan_relay.get_state())
_set_light_led(light_relay.get_state())

# --- Dimmer ---

_dimmer = dimmer.Dimmer(
    pin=esp_config.PINS["dimmer_pwm"],
    enabled=config.get("dimmer", {}).get("enabled", True),
    level=config.get("dimmer", {}).get("day", {}).get("level", 60),
    freq=esp_config.DIMMER_DEFAULTS["freq"],
    max_level=esp_config.DIMMER_DEFAULTS["max_level"],
)


# Scheduler

def _parse_time(value):
    return int(value[0:2]) * 60 + int(value[3:5])


def _should_light_be_on():
    schedule = config.get(
        "lightSchedule",
        DEFAULT_LIGHT_SCHEDULE
    )

    now = utime.localtime()
    now_min = now[3] * 60 + now[4]

    for entry in schedule:
        start = _parse_time(entry["on"])
        end = _parse_time(entry["off"])

        if start <= end:
            if start <= now_min < end:
                return True

        else:
            if now_min >= start or now_min < end:
                return True

    return False


def apply_auto_logic():
    if not config.get("auto", {}).get("enabled", False):
        return

    should_on = _should_light_be_on()

    if should_on:
        light_relay.on()
    else:
        light_relay.off()

    night_fan = config.get("auto", {}).get("nightFan", {}).get("enabled", False)

    if should_on or night_fan:
        fan_relay.on()
    else:
        fan_relay.off()

    if config.get("dimmer", {}).get("enabled", False):
        if should_on:
            _dimmer.set_level(
                config["dimmer"]["day"]["level"]
            )
        else:
            _dimmer.set_level(
                config["dimmer"]["night"]["level"]
            )


async def clock_scheduler():
    while True:
        if config.get("auto", {}).get("enabled", False):
            apply_auto_logic()

        await asyncio.sleep(30)


# Authorization

def _is_authorized(request):
    expected = "Authorization: Bearer " + API_TOKEN

    for line in request.split("\r\n"):
        if line == expected:
            return True

    return False


# HTTP Server

async def handle_request(reader, writer):
    try:
        request = await reader.read(2048)
        request = request.decode()

        lines = request.split("\r\n")
        first_line = lines[0].split(" ")

        method = first_line[0]

        path = (
            first_line[1]
            if len(first_line) > 1
            else "/"
        )

        cors = (
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            "Access-Control-Allow-Headers: "
            "Content-Type, Authorization\r\n"
        )

        # OPTIONS

        if method == "OPTIONS":
            writer.write(
                (
                        "HTTP/1.1 204 No Content\r\n"
                        "Connection: close\r\n"
                        + cors +
                        "\r\n"
                ).encode()
            )

            await writer.drain()
            return

        # Authorization

        if not _is_authorized(request):
            writer.write(
                (
                        "HTTP/1.1 401 Unauthorized\r\n"
                        "Content-Type: text/plain\r\n"
                        "Connection: close\r\n"
                        + cors +
                        "\r\n"
                        "Unauthorized"
                ).encode()
            )

            await writer.drain()
            return

        # GET /api/config
        if method == "GET" and path == "/api/config":
            sensor_data = sensor.get()

            body = json.dumps({
                "config": config,
                "sensor": {
                    "temperature": sensor_data["temperature"],
                    "humidity": sensor_data["humidity"],
                },
                "status": {
                    "mode": (
                        "AUTO"
                        if config.get(
                            "auto",
                            {}
                        ).get(
                            "enabled",
                            False
                        )
                        else "MANUAL"
                    ),

                    "state": (
                        "ON"
                        if light_relay.get_state()
                        else "OFF"
                    ),
                }
            })

            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: {}\r\n"
                "Connection: close\r\n"
                "{}"
                "\r\n"
            ).format(
                len(body.encode()),
                cors
            )

            writer.write(
                (headers + body).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/display/toggle":
            enabled = not config.get(
                "display",
                {}
            ).get(
                "enabled",
                False
            )

            config["display"]["enabled"] = enabled

            config_store.save(config)

            print(
                "Display:",
                "ON" if enabled else "OFF"
            )

            display.toggle(enabled)

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/mode/toggle":
            enabled = not config.get(
                "auto",
                {}
            ).get(
                "enabled",
                False
            )

            config["auto"]["enabled"] = enabled

            config_store.save(config)

            print(
                "AUTO mode:",
                "ON" if enabled else "OFF"
            )

            if enabled:
                apply_auto_logic()
            else:
                if config.get("auto", {}).get("nightFan", {}).get("enabled", False):
                    _dimmer.set_level(
                        config["dimmer"]["day"]["level"]
                    )

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/light/toggle":
            light_relay.toggle()

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/fan/toggle":
            fan_relay.toggle()

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/fan/level":
            body_start = request.find("\r\n\r\n")

            if body_start != -1:
                body = json.loads(
                    request[body_start + 4:]
                )

                level = int(
                    body.get("level", 0)
                )

                level = max(
                    0,
                    min(100, level)
                )

                config["dimmer"]["day"]["level"] = level

                config_store.save(config)

                if config.get(
                        "auto",
                        {}
                ).get(
                    "enabled",
                    False
                ):
                    if _should_light_be_on():
                        _dimmer.set_level(level)
                else:
                    _dimmer.set_level(level)

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/fan/night-level":
            body_start = request.find("\r\n\r\n")

            if body_start != -1:
                body = json.loads(
                    request[body_start + 4:]
                )

                level = int(
                    body.get("level", 0)
                )

                level = max(
                    0,
                    min(100, level)
                )

                config["dimmer"]["night"]["level"] = level

                config_store.save(config)

                if (
                        config.get(
                            "auto",
                            {}
                        ).get(
                            "enabled",
                            False
                        )
                        and not _should_light_be_on()
                        and config.get(
                    "auto",
                    {}
                ).get(
                    "nightFan",
                    {}
                ).get(
                    "enabled",
                    False
                )
                ):
                    _dimmer.set_level(level)

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/auto/night-fan/toggle":
            if "auto" not in config:
                config["auto"] = {}

            if "nightFan" not in config["auto"]:
                config["auto"]["nightFan"] = {}

            config["auto"]["nightFan"]["enabled"] = (
                not config["auto"]["nightFan"].get(
                    "enabled",
                    False
                )
            )

            config_store.save(config)

            apply_auto_logic()

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/light-schedule":
            body_start = request.find("\r\n\r\n")

            if body_start != -1:
                schedule = json.loads(
                    request[body_start + 4:]
                )

                if not isinstance(schedule, list):
                    raise ValueError(
                        "Schedule must be an array"
                    )

                if not schedule:
                    schedule = DEFAULT_LIGHT_SCHEDULE.copy()

                for item in schedule:
                    if (
                            not isinstance(item, dict)
                            or "on" not in item
                            or "off" not in item
                    ):
                        raise ValueError(
                            "Invalid schedule entry"
                        )

                config["lightSchedule"] = schedule

                config_store.save(config)

                if config.get(
                        "auto",
                        {}
                ).get(
                    "enabled",
                    False
                ):
                    apply_auto_logic()

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        if method == "POST" and path == "/api/flowering/start-date":
            body_start = request.find("\r\n\r\n")

            if body_start != -1:
                body = json.loads(
                    request[body_start + 4:]
                )

                start_date = body.get("date")

                if start_date is not None and not isinstance(start_date, str):
                    raise ValueError(
                        "Flowering start date must be a string or null"
                    )

                if "auto" not in config:
                    config["auto"] = {}

                config["auto"]["floweringStartDate"] = start_date

                config_store.save(config)

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await writer.drain()
            return

        # 404

        writer.write(
            (
                    "HTTP/1.1 404 Not Found\r\n"
                    + cors +
                    "\r\n"
                    "Not found"
            ).encode()
        )

        await writer.drain()

    except Exception as e:
        print(
            "Request error:",
            e
        )

    finally:
        writer.close()
        await writer.wait_closed()


# Display
def wifi_is_connected():
    import network

    wlan = network.WLAN(
        network.STA_IF
    )

    return wlan.isconnected()


def get_display_state():
    sensor_data = sensor.get()

    return {
        "wifi": wifi_is_connected(),
        "mode": (
            "AUTO"
            if config.get(
                "auto",
                {}
            ).get(
                "enabled",
                False
            )
            else "MANUAL"
        ),
        "light": bool(
            config["relayLight"]["state"]
        ),
        "fan": bool(
            config["relayFan"]["state"]
        ),
        "temperature": sensor_data["temperature"],
        "humidity": sensor_data["humidity"],
        "floweringStartDate": config.get(
            "auto",
            {}
        ).get(
            "floweringStartDate"
        ),
    }


# Server

async def start_server():
    server = await asyncio.start_server(
        handle_request,
        "0.0.0.0",
        80
    )

    print(
        "HTTP server ready on port 80"
    )

    await asyncio.Event().wait()
