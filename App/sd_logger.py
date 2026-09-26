import gc
import json
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

# Predkosc SPI po inicjalizacji karty. Przy bledach odczytu zejdz do 1000000 (albo 400000 do testu).
SD_BAUDRATE = 4000000

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
        SD_BAUDRATE
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

        # Nowy plik TYLKO gdy go nie ma (ENOENT). Kazdy inny blad
        # (np. chwilowy I/O przy montowaniu) ma przerwac montowanie,
        # a nie wyczyscic log trybem "w".
        try:
            size = os.stat(_LOG_FILE)[6]
        except OSError as e:
            if e.args and e.args[0] == 2:  # ENOENT
                with open(_LOG_FILE, "w") as f:
                    f.write(
                        "datetime,day_night,temperature_c,humidity_percent\n"
                    )
                size = 0
                print("SD: created new log")
            else:
                raise

        _enabled = True

        print("SD: OK, log size", size, "B")

        return True

    except Exception as e:
        _enabled = False

        print("SD: FAIL -> retry", repr(e))

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
    if _readers > 0:
        return

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


_HEADER = b"datetime,day_night,temperature_c,humidity_percent\n"

# Rozmiar bloku przy czytaniu z karty (512 B = jeden sektor, czyli sprawdzona sciezka CMD17 w sdcard.py).
_READ_BLOCK = 512

# Ile blokow przeczytac, zanim oddamy sterowanie petli asyncio.
_BLOCKS_PER_YIELD = 4

# Licznik aktywnych odczytow - rotacja loga czeka, az nikt nie czyta.
_readers = 0


def mem_info():
    """(wolne GC, wolne IDF, najwiekszy wolny blok IDF) - diagnostyka."""
    try:
        import esp32
        heaps = esp32.idf_heap_info(esp32.HEAP_DATA)
        idf_free = sum(h[1] for h in heaps)
        idf_largest = max(h[2] for h in heaps)
    except Exception:
        idf_free = idf_largest = -1

    return gc.mem_free(), idf_free, idf_largest


def _parse_row(line):
    """bytes -> (datetime, day_night, temperature, humidity) albo None."""
    parts = line.strip().split(b",")

    if len(parts) != 4:
        return None

    try:
        temperature = float(parts[2])
        humidity = float(parts[3])
    except ValueError:
        return None

    # NaN / inf nie sa poprawnym JSON-em (x - x daje NaN dla obu)
    if temperature - temperature != 0 or humidity - humidity != 0:
        return None

    return (
        parts[0].decode(),
        parts[1].decode(),
        temperature,
        humidity,
    )


def _qualifies(line, before):
    """Wiersz danych pasujacy do zapytania -> sparsowana krotka, inaczej None."""
    if len(line) < 19:
        return None

    # szybki filtr na bajtach, bez parsowania
    if before is not None and line[:19] >= before:
        return None

    return _parse_row(line)


async def _find_start(path, state, limit, before):
    """
    Przebieg 1: czyta plik OD KONCA i tylko LICZY pasujace wiersze
    (nic nie trzyma w RAM-ie). `state` = [found, path, offset, datetime]
    najstarszego wiersza do wyslania. Zwraca True, gdy trzeba czytac
    dalej (starszy plik), False gdy znaleziono limit + 1 wierszy.
    """
    try:
        f = open(path, "rb")
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT - brak pliku to nie blad karty
            return True
        raise

    def hit(row, offset):
        state[0] += 1

        if state[0] <= limit:
            state[1] = path
            state[2] = offset
            state[3] = row[0]

        # +1 ponad limit = wiemy, ze jest cos dalej
        return state[0] > limit

    try:
        f.seek(0, 2)
        pos = f.tell()
        rest = b""
        blocks = 0

        while pos > 0:
            step = _READ_BLOCK if pos >= _READ_BLOCK else pos
            pos -= step
            f.seek(pos)

            chunk = f.read(step) + rest
            lines = chunk.split(b"\n")

            # pierwszy kawalek moze byc urwana linia - zostaje na potem
            rest = lines[0]

            # offset poczatku ostatniej linii w kawalku
            offset = pos + len(chunk) - len(lines[-1])

            for i in range(len(lines) - 1, 0, -1):
                row = _qualifies(lines[i], before)

                if row is not None and hit(row, offset):
                    return False

                offset -= len(lines[i - 1]) + 1

            blocks += 1

            if blocks % _BLOCKS_PER_YIELD == 0:
                await asyncio.sleep(0)

        # pierwsza linia pliku (naglowek) odpada w _parse_row
        row = _qualifies(rest, before)

        if row is not None and hit(row, 0):
            return False

        return True

    finally:
        f.close()


class _SdError(Exception):
    pass


async def _send_rows(path, offset, before, left, send, first):
    """
    Przebieg 2: czyta plik od `offset` DO PRZODU i wysyla pasujace
    wiersze jako JSON, kawalek po kawalku. Zwraca (ile zostalo, first).
    Bledy karty -> _SdError; bledy socketu leca wyzej bez zmian.
    """
    try:
        f = open(path, "rb")
        f.seek(offset)
    except Exception as e:
        raise _SdError(e)

    try:
        rest = b""

        while left > 0:
            try:
                block = f.read(_READ_BLOCK)
            except Exception as e:
                raise _SdError(e)

            if block:
                lines = (rest + block).split(b"\n")
                rest = lines.pop()
            else:
                lines = [rest]  # ostatnia linia bez "\n"

            part = []

            for line in lines:
                row = _qualifies(line, before)

                if row is None:
                    continue

                part.append(_ROW_JSON % row)
                left -= 1

                if left == 0:
                    break

            if part:
                chunk = ",".join(part)

                if not first:
                    chunk = "," + chunk

                first = False

                await send(chunk.encode())

            if not block:
                break

        return left, first

    finally:
        f.close()


_ROW_JSON = (
    '{"datetime":"%s","day_night":"%s",'
    '"temperature":%s,"humidity":%s}'
)


def _json_head(status, has_more, next_before):
    return (
        '{"status":%s,"has_more":%s,"next_before":%s,"data":['
        % (
            json.dumps(status),
            "true" if has_more else "false",
            json.dumps(next_before),
        )
    ).encode()


async def stream_environment_json(send, limit=500, before=None):
    """
    Wysyla przez async `send(bytes)` JSON
    {"status", "has_more", "next_before", "data": [...]}
    z najnowszymi `limit` pomiarami starszymi niz `before`,
    od najstarszego do najnowszego.

    Nie buduje listy wierszy w RAM-ie: duza lista rozpycha sterte GC
    kosztem sterty ESP-IDF, a wtedy WiFi/lwIP nie ma buforow
    i odpowiedzi HTTP staja.
    """
    global _readers

    try:
        limit = int(limit)
    except ValueError:
        limit = 500

    limit = max(1, min(500, limit))

    before = before.encode() if before else None

    if not _enabled:
        print("ENV: SD offline")
        await send(_json_head("offline", False, None) + b"]}")
        return

    t0 = utime.ticks_ms()
    print("ENV: read start, limit", limit, "before", before, "mem", mem_info())

    _readers += 1

    try:
        # przebieg 1: skad zaczac
        state = [0, None, 0, None]

        try:
            if await _find_start(_LOG_FILE, state, limit, before):
                await _find_start(_ARCHIVE_FILE, state, limit, before)
        except Exception as e:
            print("ENV: SD error:", repr(e))
            _handle_error(e)
            await send(_json_head("offline", False, None) + b"]}")
            return

        found, start_path, start_offset, start_dt = state

        has_more = found > limit
        count = limit if has_more else found
        left = count

        await send(_json_head("ok", has_more, start_dt if has_more else None))

        # przebieg 2: wyslij od najstarszego
        if start_path == _ARCHIVE_FILE:
            segments = ((_ARCHIVE_FILE, start_offset), (_LOG_FILE, 0))
        elif start_path == _LOG_FILE:
            segments = ((_LOG_FILE, start_offset),)
        else:
            segments = ()

        first = True

        for path, offset in segments:
            if left <= 0:
                break

            try:
                left, first = await _send_rows(
                    path, offset, before, left, send, first
                )
            except _SdError as e:
                # odpowiedz zostaje urwana - klient dostanie niepelny JSON
                err = e.args[0] if e.args else e
                print("ENV: SD error:", repr(err))
                _handle_error(err)
                return

        await send(b"]}")

        print(
            "ENV: sent",
            count - left,
            "rows in",
            utime.ticks_diff(utime.ticks_ms(), t0),
            "ms, mem",
            mem_info()
        )

    finally:
        _readers -= 1


async def stream_csv(send):
    """
    Wysyla caly log (archiwum + biezacy) jako CSV, kawalek po kawalku.
    `send` to async funkcja przyjmujaca bytes.
    Bledy socketu (klient sie rozlaczyl) leca wyzej i NIE odmontowuja karty.
    """
    global _readers

    if not _enabled:
        return False

    _readers += 1

    try:
        await send(_HEADER)

        for path in (_ARCHIVE_FILE, _LOG_FILE):
            try:
                f = open(path, "rb")
            except OSError as e:
                if e.args and e.args[0] == 2:  # ENOENT
                    continue
                _handle_error(e)
                return False

            try:
                try:
                    f.readline()  # pomin naglowek pliku
                except Exception as e:
                    _handle_error(e)
                    return False

                while True:
                    try:
                        chunk = f.read(_READ_BLOCK)
                    except Exception as e:
                        _handle_error(e)
                        return False

                    if not chunk:
                        break

                    await send(chunk)
                    await asyncio.sleep(0.01)

            finally:
                f.close()

        return True

    finally:
        _readers -= 1


def is_enabled():
    return _enabled


def erase():
    global _enabled

    # nie kasuj pliku, ktory ktos wlasnie czyta/pobiera
    if _readers > 0:
        raise OSError("SD busy")

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
