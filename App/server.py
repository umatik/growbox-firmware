import json

import machine
import uasyncio as asyncio
import utime

import config_store
import dimmer
import display
import esp_config
import events
import fan_auto
import ota
import ota_http
import relay
import sd_logger
import sensor

API_TOKEN = "hftqpPVbeLwaqPDfJ25eCzxRETqsoX8sh1K56NRUp2zW8GxHr1M6u5s4fEMv"

config = config_store.load()

# ile ostatnich podlan trzymac w config.json (idzie w kazdym /api/config)
_FEEDING_HISTORY = 30

DEFAULT_LIGHT_SCHEDULE = [
    {
        "on": "18:00",
        "off": "06:00",
    }
]


# --- Relays ---

def _on_light_change(state):
    print("Server Light state:", state)
    config["relayLight"]["state"] = state
    config_store.save(config)


def _on_fan_change(state):
    print("Server Fan state:", state)
    config["relayFan"]["state"] = state
    config_store.save(config)

    # z postoju wentylator nie rusza na niskim poziomie - krotki rozruch
    if state:
        _dimmer.kick()

    # przelaczenie wentylatora potrafi zaklocic przekaznik swiatla
    try:
        asyncio.create_task(_verify_light_after_fan())
    except Exception as e:
        print("Light verify start error:", e)


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

# --- Dimmer ---

_dimmer = dimmer.Dimmer(
    pin=esp_config.PINS["dimmer_pwm"],
    enabled=config.get("dimmer", {}).get("enabled", True),
    level=config.get("dimmer", {}).get("day", {}).get("level", 60),
    freq=esp_config.DIMMER_DEFAULTS["freq"],
    min_pct=esp_config.DIMMER_DEFAULTS["min_pct"],
    max_pct=esp_config.DIMMER_DEFAULTS["max_pct"],
    kick_pct=esp_config.DIMMER_DEFAULTS["kick_pct"],
    kick_ms=esp_config.DIMMER_DEFAULTS["kick_ms"],
)


# --- Weryfikacja swiatla ---

# Kiedy (s po przelaczeniu wentylatora) sprawdzic pin swiatla.
_LIGHT_VERIFY_DELAYS = (0.2, 1, 3)


async def _verify_light_after_fan():
    expected = light_relay.get_state()

    for delay in _LIGHT_VERIFY_DELAYS:
        await asyncio.sleep(delay)

        # ktos celowo zmienil swiatlo w miedzyczasie - nie wtracamy sie
        if light_relay.get_state() != expected:
            return

        if light_relay.refresh():
            print("LIGHT: pin mismatch after fan switch -> restored", expected)


def _refresh_relays():
    """Cykliczne wymuszenie stanu pinow - naprawia ewentualne zaklocenia."""
    if light_relay.refresh():
        print("LIGHT: pin mismatch -> restored", light_relay.get_state())

    if fan_relay.refresh():
        print("FAN: pin mismatch -> restored", fan_relay.get_state())


# --- Scheduler ---

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


def time_is_valid():
    # przed synchronizacja NTP zegar ESP startuje od 2000 roku
    return utime.localtime()[0] >= 2024


def apply_auto_logic():
    if not config.get("auto", {}).get("enabled", False):
        return

    # bez prawdziwej godziny harmonogram przelaczalby na slepo -
    # zostaw przekazniki w ostatnim zapisanym stanie
    if not time_is_valid():
        print("AUTO: no valid time, keeping relay states")
        return

    should_on = _should_light_be_on()

    if should_on:
        light_relay.on()
    else:
        light_relay.off()

    night_fan = config.get(
        "auto",
        {}
    ).get(
        "nightFan",
        {}
    ).get(
        "enabled",
        False
    )

    # nightFan w nocy: wilgotnosc ponad sweet point podkreca obroty
    humid_level = None

    if not should_on and night_fan:
        humid_level = fan_auto.night_humidity_level(
            config,
            config["dimmer"]["night"]["level"]
        )

    if should_on or night_fan:
        fan_relay.on()
    else:
        fan_relay.off()

    if config.get(
            "dimmer",
            {}
    ).get(
        "enabled",
        False
    ):
        if should_on:
            _dimmer.set_level(
                config["dimmer"]["day"]["level"]
            )
        else:
            _dimmer.set_level(
                humid_level or config["dimmer"]["night"]["level"]
            )


# --- Fan auto (MANUAL / vege) - logika w fan_auto.py ---

def fan_auto_enabled():
    return fan_auto.enabled(config)


def apply_fan_auto(force=False):
    fan_auto.apply(config, light_relay.get_state(), _dimmer, force)


# Ostatni obieg harmonogramu - watchdog karmi ESP tylko, gdy to jest swieze.
_scheduler_tick = utime.ticks_ms()


def scheduler_age_ms():
    return utime.ticks_diff(utime.ticks_ms(), _scheduler_tick)


async def clock_scheduler():
    global _scheduler_tick

    while True:
        # blad w jednym obiegu nie moze zatrzymac kolejnych
        try:
            if config.get(
                    "auto",
                    {}
            ).get(
                "enabled",
                False
            ):
                apply_auto_logic()
        except Exception as e:
            print("SCHED: auto error:", repr(e))

        try:
            apply_fan_auto()
        except Exception as e:
            print("SCHED: fan auto error:", repr(e))

        try:
            _refresh_relays()
        except Exception as e:
            print("SCHED: refresh error:", repr(e))

        _scheduler_tick = utime.ticks_ms()

        await asyncio.sleep(30)


# --- Authorization ---

def _is_authorized(request):
    expected = (
            "Authorization: Bearer "
            + API_TOKEN
    )

    for line in request.split("\r\n"):
        if line == expected:
            return True

    return False


def _url_decode(value):
    value = value.replace("+", " ")

    if "%" not in value:
        return value

    out = bytearray()
    i = 0
    n = len(value)

    while i < n:
        c = value[i]

        if c == "%" and i + 2 < n:
            try:
                out.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass

        out.extend(c.encode())
        i += 1

    return out.decode()


def _parse_query(path):
    query = {}

    if "?" not in path:
        return query

    raw_query = path.split("?", 1)[1]

    for item in raw_query.split("&"):
        if "=" not in item:
            continue

        key, value = item.split("=", 1)

        query[_url_decode(key)] = _url_decode(value)

    return query


# --- Timeouty I/O ---

# Bez limitu handler, ktoremu klient przestal odbierac, wisi w drain()
# w nieskonczonosc i trzyma socket. Po kilku takich lwIP nie ma wolnych
# socketow i serwer przestaje przyjmowac polaczenia.
_IO_TIMEOUT = 15

# Requesty ida po kolei (_request_lock), wiec klient, ktory przestal
# wysylac/odbierac (telefon zgubil pakiet, przeszedl w tlo), blokowal
# cala kolejke na _IO_TIMEOUT - reszta dostawala timeouty. Request
# przychodzi zaraz po polaczeniu, porcja odpowiedzi to ulamek sekundy.
_READ_TIMEOUT = 3
_WRITE_TIMEOUT = 5


async def _drain(writer):
    await asyncio.wait_for(writer.drain(), _WRITE_TIMEOUT)


# diagnostyka dla GET /api/status; reset_cause ustawia app.boot()
boot_info = {"reset_cause": None, "boot_ticks": utime.ticks_ms()}
stats = {"requests": 0, "dropped": 0, "timeouts": 0, "errors": 0}


def _wifi_rssi():
    try:
        import network
        return network.WLAN(network.STA_IF).status("rssi")
    except Exception:
        return None


def _status():
    return {
        "uptime_s": utime.ticks_diff(
            utime.ticks_ms(), boot_info["boot_ticks"]
        ) // 1000,
        "reset_cause": boot_info["reset_cause"],
        "mem": sd_logger.mem_info(),
        "rssi": _wifi_rssi(),
        "waiting": _waiting,
        "stats": stats,
        "events": events.tail(),
    }


# --- Environment JSON (streaming) ---

# Przerwa miedzy porcjami (s) - daje lwIP czas na ACK-i.
_WRITE_PAUSE = 0.01


async def _send_environment_json(writer, cors, query):
    """
    JSON z sd_logger idzie prosto do socketu, porcja po porcji
    (bez Content-Length: koniec odpowiedzi = zamkniecie polaczenia).
    """
    writer.write(
        (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n"
                + cors +
                "\r\n"
        ).encode()
    )

    async def send(chunk):
        writer.write(chunk)
        await _drain(writer)
        await asyncio.sleep(_WRITE_PAUSE)

    await sd_logger.stream_environment_json(
        send,
        limit=query.get("limit", str(sd_logger.MAX_ROWS)),
        before=query.get("before"),
        since=query.get("since"),
    )


# --- HTTP Server ---

async def _read_request(reader):
    """
    Czyta naglowki i cale body wg Content-Length. Klient moze wyslac
    body osobnym pakietem - jednorazowy read() gubil wtedy dane z POST-a.
    """
    data = await asyncio.wait_for(reader.read(2048), _READ_TIMEOUT)

    head_end = data.find(b"\r\n\r\n")

    if head_end != -1:
        # upload pliku (OTA): body czyta ota.receive strumieniowo, bez RAM-u
        if data.startswith(b"PUT "):
            return data[:head_end].decode(), data[head_end + 4:]

        length = 0

        for line in data[:head_end].split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line[15:].strip())
                except ValueError:
                    pass

        # limit, zeby nie zjesc RAM-u
        length = min(length, 4096)

        while len(data) - (head_end + 4) < length:
            more = await asyncio.wait_for(reader.read(1024), _READ_TIMEOUT)

            if not more:
                break

            data += more

    return data.decode(), b""


def _header(request, name):
    """Wartosc naglowka (name malymi literami) albo None."""
    prefix = name + ":"

    for line in request.split("\r\n")[1:]:
        if line.lower().startswith(prefix):
            return line[len(prefix):].strip()

    return None


async def _send_json(writer, cors, status_line, data):
    body = json.dumps(data)

    writer.write(
        (
            "HTTP/1.1 " + status_line + "\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "{}"
            "\r\n"
            "{}"
        ).format(len(body.encode()), cors, body).encode()
    )

    await _drain(writer)


async def _handle_ota(method, route, request, body, reader, writer, cors):
    return await ota_http.handle(
        method, route, request, body, reader, writer, cors
    )


async def _handle_request(reader, writer):
    try:
        # upload = poczatek body PUT-a (OTA), dla reszty pusty
        request, upload = await _read_request(reader)

        lines = request.split("\r\n")
        first_line = lines[0].split(" ")

        method = first_line[0]

        path = (
            first_line[1]
            if len(first_line) > 1
            else "/"
        )

        route = path.split("?", 1)[0]
        query = _parse_query(path)

        print("HTTP:", method, path, "mem", sd_logger.mem_info())

        cors = (
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: "
            "GET, POST, PUT, OPTIONS\r\n"
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

            await _drain(writer)
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

            await _drain(writer)
            return

        stats["requests"] += 1

        # GET /api/status  (diagnostyka: uptime, restarty, pamiec, liczniki)

        if method == "GET" and route == "/api/status":
            await _send_json(writer, cors, "200 OK", _status())
            return

        if await _handle_ota(
                method, route, request, upload, reader, writer, cors
        ):
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
                    "fanLevel": _dimmer.get_level(),
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

            await _drain(writer)
            return

        # POST /api/display/toggle

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

            await _drain(writer)
            return

        # POST /api/mode/toggle

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
            elif fan_auto_enabled():
                apply_fan_auto(force=True)
            else:
                if config.get(
                        "auto",
                        {}
                ).get(
                    "nightFan",
                    {}
                ).get(
                    "enabled",
                    False
                ):
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

            await _drain(writer)
            return

        # POST /api/light/toggle

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

            await _drain(writer)
            return

        # POST /api/fan/toggle

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

            await _drain(writer)
            return

        # POST /api/debug/dimmer  {"duty": 0..1023, "freq": Hz}  (kalibracja)

        if method == "POST" and route == "/api/debug/dimmer":
            body_start = request.find("\r\n\r\n")
            body = json.loads(request[body_start + 4:] or "{}")

            duty, freq = _dimmer.set_raw(
                duty=body.get("duty"),
                freq=body.get("freq"),
            )

            print("DIMMER RAW: duty", duty, "freq", freq)

            body = json.dumps({"duty": duty, "freq": freq})

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: application/json\r\n"
                        "Connection: close\r\n"
                        + cors +
                        "\r\n"
                        + body
                ).encode()
            )

            await _drain(writer)
            return

        # POST /api/fan/level

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
                elif not fan_auto_enabled():
                    _dimmer.set_level(level)

            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        + cors +
                        "\r\n"
                        "OK"
                ).encode()
            )

            await _drain(writer)
            return

        # POST /api/fan/night-level

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

            await _drain(writer)
            return

        # POST /api/auto/night-fan/toggle

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

            await _drain(writer)
            return

        # POST /api/feeding  {"date": ISO}  (podlanie z aplikacji)

        if method == "POST" and path == "/api/feeding":
            date = json.loads(request[request.find("\r\n\r\n") + 4:])["date"]

            if not isinstance(date, str) or len(date) > 40:
                raise ValueError("Invalid feeding date")

            feeding = config["feeding"]
            feeding["lastFedAt"] = date
            feeding["count"] += 1
            # aplikacja liczy z historii rytm podlewania; ogon wystarczy
            feeding["history"] = (feeding["history"] + [date])[-_FEEDING_HISTORY:]
            config_store.save(config)

            writer.write(("HTTP/1.1 200 OK\r\n" + cors + "\r\nOK").encode())
            await _drain(writer)
            return

        # POST /api/fan/auto/toggle

        if method == "POST" and path == "/api/fan/auto/toggle":
            fa = config["fanAuto"]
            fa["enabled"] = not fa["enabled"]
            config_store.save(config)

            if fan_auto_enabled():
                apply_fan_auto(True)
            elif not config["auto"]["enabled"]:
                # wylaczony automat - wraca reczny poziom
                _dimmer.set_level(config["dimmer"]["day"]["level"])

            writer.write(("HTTP/1.1 200 OK\r\n" + cors + "\r\nOK").encode())
            await _drain(writer)
            return

        # POST /api/light-schedule

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

            await _drain(writer)
            return

        # POST /api/flowering/start-date

        if method == "POST" and path == "/api/flowering/start-date":
            body_start = request.find("\r\n\r\n")

            if body_start != -1:
                body = json.loads(
                    request[body_start + 4:]
                )

                start_date = body.get("date")

                if (
                        start_date is not None
                        and not isinstance(start_date, str)
                ):
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

            await _drain(writer)
            return

        # POST /api/environment/start

        if method == "POST" and route == "/api/environment/start":
            started = sd_logger.start()

            body = json.dumps({
                "status": (
                    "started"
                    if started
                    else "offline"
                )
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

            await _drain(writer)
            return

        # GET /api/environment.csv  (caly log jako plik CSV)

        if method == "GET" and route == "/api/environment.csv":
            if not sd_logger.is_enabled():
                writer.write(
                    (
                            "HTTP/1.1 503 Service Unavailable\r\n"
                            "Content-Type: text/plain\r\n"
                            "Connection: close\r\n"
                            + cors +
                            "\r\n"
                            "SD offline"
                    ).encode()
                )

                await _drain(writer)
                return

            # bez Content-Length: koniec odpowiedzi = zamkniecie polaczenia
            writer.write(
                (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/csv\r\n"
                        "Content-Disposition: attachment; "
                        "filename=\"environment.csv\"\r\n"
                        "Connection: close\r\n"
                        + cors +
                        "\r\n"
                ).encode()
            )

            await _drain(writer)

            async def send(chunk):
                writer.write(chunk)
                await _drain(writer)

            await sd_logger.stream_csv(send)
            return

        # GET /api/environment?limit=500&before=YYYY-MM-DD HH:MM:SS
        # GET /api/environment?limit=500&since=YYYY-MM-DD HH:MM:SS

        if method == "GET" and route == "/api/environment":
            await _send_environment_json(
                writer,
                cors,
                query
            )
            return

        # POST /api/environment/erase

        if method == "POST" and route == "/api/environment/erase":
            try:
                sd_logger.erase()
                status_line = "200 OK"
                body = json.dumps({
                    "status": "erased"
                })
            except OSError as e:
                status_line = "503 Service Unavailable"
                body = json.dumps({
                    "status": "error",
                    "error": str(e)
                })

            headers = (
                "HTTP/1.1 " + status_line + "\r\n"
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

            await _drain(writer)
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

        await _drain(writer)

    except Exception as e:
        if isinstance(e, asyncio.TimeoutError):
            stats["timeouts"] += 1
        else:
            stats["errors"] += 1

        print(
            "Request error:",
            repr(e)
        )

    finally:
        writer.close()
        await writer.wait_closed()


# Requesty obslugiwane po kolei. Kilka naraz (np. strumien historii
# i szybkie przelaczanie przekaznikow z aplikacji) potrafilo przekroczyc
# sterte GC nawet po odsmiecaniu - MicroPython dokladal wtedy ~25 KB
# ze sterty ESP-IDF i WiFi/lwIP zostawaly bez buforow (serwer wisial
# do odlaczenia zasilania). Kolejne polaczenia czekaja na swoja kolej.
# Rownolegle nawet 1 request sterujacy + 1 strumien historii przepelnial
# sterte (sprawdzone) - dlatego calkiem po kolei; zeby przelaczanie nie
# czekalo dlugo, aplikacja pobiera historie malymi stronami.
_request_lock = asyncio.Lock()

# ile polaczen moze czekac; kolejne sa od razu zamykane
_MAX_WAITING = 6

_waiting = 0


async def handle_request(reader, writer):
    global _waiting

    if _waiting >= _MAX_WAITING:
        stats["dropped"] += 1
        print("HTTP: busy, connection dropped")
        writer.close()
        await writer.wait_closed()
        return

    _waiting += 1

    try:
        await _request_lock.acquire()
    finally:
        _waiting -= 1

    try:
        await _handle_request(reader, writer)
    finally:
        _request_lock.release()


# --- Display ---

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


# --- Server ---

_server = None


async def start_server():
    global _server

    try:
        _server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    except OSError as e:
        # po awarii petli stary socket potrafi dalej trzymac port 80 -
        # serwer juz by nie wstal, a WDT tego nie widzi (harmonogram
        # dziala) i rollback OTA tez nie. Czysty start zwalnia port.
        if e.errno == 112:  # EADDRINUSE
            events.log("reset: port 80 in use")
            utime.sleep_ms(500)
            machine.reset()
        raise

    print(
        "HTTP server ready on port 80"
    )

    await asyncio.Event().wait()
