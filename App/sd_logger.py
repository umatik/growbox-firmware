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


def _qualifies(line, before, since=None):
    """Wiersz danych pasujacy do zapytania -> sparsowana krotka, inaczej None."""
    if len(line) < 19:
        return None

    # szybki filtr na bajtach, bez parsowania
    if before is not None and line[:19] >= before:
        return None

    if since is not None and line[:19] <= since:
        return None

    return _parse_row(line)


def _is_data(line):
    # wiersz danych zaczyna sie od roku ("2026-..."), naglowek od "datetime"
    return len(line) >= 19 and 48 <= line[0] <= 57


async def _bisect(path, key, strict):
    """
    Log jest tylko dopisywany, wiec wiersze sa posortowane po czasie.
    Szuka binarnie offsetu pierwszego wiersza z datetime > key (strict)
    albo >= key. Zwraca rozmiar pliku, gdy takiego wiersza nie ma,
    None gdy pliku nie ma. Kilkanascie odczytow zamiast skanu calego pliku.
    """
    try:
        f = open(path, "rb")
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT
            return None
        raise

    def matches(line):
        return line[:19] > key if strict else line[:19] >= key

    try:
        f.seek(0, 2)
        size = f.tell()

        # pomin naglowek
        f.seek(0)
        f.readline()
        lo = f.tell()
        hi = size

        # niezmiennik: szukany wiersz zaczyna sie w [lo, hi]
        while hi - lo > _READ_BLOCK:
            mid = (lo + hi) // 2
            f.seek(mid)
            f.readline()  # urwana linia
            start = f.tell()

            if start >= hi:
                break

            line = f.readline()

            if _is_data(line) and matches(line):
                hi = start
            else:
                lo = start + len(line)

            await asyncio.sleep(0)

        # koncowka liniowo
        f.seek(lo)
        pos = lo

        while pos < hi:
            line = f.readline()

            if not line:
                break

            if _is_data(line) and matches(line):
                return pos

            pos += len(line)

        return hi

    finally:
        f.close()


async def _find_start(path, state, limit, before, end=None):
    """
    Przebieg 1: czyta plik OD KONCA (albo od offsetu `end`) i tylko LICZY pasujace wiersze
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
        if end is None:
            f.seek(0, 2)
            pos = f.tell()
        else:
            pos = end

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


async def _send_rows(path, offset, before, since, ctx, send):
    """
    Przebieg 2: czyta plik od `offset` DO PRZODU i wysyla pasujace
    wiersze jako JSON, kawalek po kawalku. `ctx` = [ile zostalo, first,
    datetime ostatniego wyslanego]. Zwraca True, gdy po wyczerpaniu
    limitu trafil sie jeszcze pasujacy wiersz (czyli jest cos dalej).
    Bledy karty -> _SdError; bledy socketu leca wyzej bez zmian.
    """
    try:
        f = open(path, "rb")
        f.seek(offset)
    except OSError as e:
        if e.args and e.args[0] == 2:  # ENOENT
            return False
        raise _SdError(e)
    except Exception as e:
        raise _SdError(e)

    async def flush(part):
        if not part:
            return

        chunk = ",".join(part)

        if not ctx[1]:
            chunk = "," + chunk

        ctx[1] = False

        await send(chunk.encode())

    try:
        rest = b""

        while True:
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
                row = _qualifies(line, before, since)

                if row is None:
                    continue

                if ctx[0] <= 0:
                    await flush(part)
                    return True

                part.append(_ROW_JSON % row)
                ctx[0] -= 1
                ctx[2] = row[0]

            await flush(part)

            if not block:
                return False

    finally:
        f.close()


_ROW_JSON = (
    '{"datetime":"%s","day_night":"%s",'
    '"temperature":%s,"humidity":%s}'
)

_JSON_HEAD = b'{"status":"ok","data":['


def _json_tail(has_more, next_before, next_since):
    # metadane na koncu: has_more wychodzi dopiero po przeczytaniu wierszy
    return (
        '],"has_more":%s,"next_before":%s,"next_since":%s}'
        % (
            "true" if has_more else "false",
            json.dumps(next_before),
            json.dumps(next_since),
        )
    ).encode()


def _json_offline():
    return b'{"status":"offline","data":[' + _json_tail(False, None, None)


def _parse_key(value):
    """'YYYY-MM-DD HH:MM:SS' -> bytes, cokolwiek innego -> None."""
    if not value or len(value) != 19:
        return None

    return value.encode()


async def _segments_before(limit, before, state):
    """Tryb `before`: najnowsze `limit` wierszy starszych niz `before`."""
    log_end = await _bisect(_LOG_FILE, before, False) if before else None

    if await _find_start(_LOG_FILE, state, limit, before, log_end):
        arch_end = (
            await _bisect(_ARCHIVE_FILE, before, False) if before else None
        )
        await _find_start(_ARCHIVE_FILE, state, limit, before, arch_end)

    start_path, start_offset = state[1], state[2]

    if start_path == _ARCHIVE_FILE:
        return ((_ARCHIVE_FILE, start_offset), (_LOG_FILE, 0))

    if start_path == _LOG_FILE:
        return ((_LOG_FILE, start_offset),)

    return ()


async def _segments_since(since):
    """Tryb `since`: wiersze nowsze niz `since`, od najstarszego."""
    log_start = await _bisect(_LOG_FILE, since, True)

    # czy biezacy log ma jakis wiersz <= since? jesli nie, zacznij w archiwum
    try:
        f = open(_LOG_FILE, "rb")
        try:
            header = len(f.readline())
        finally:
            f.close()
    except OSError:
        header = 0

    if log_start is None or log_start <= header:
        arch_start = await _bisect(_ARCHIVE_FILE, since, True)

        if arch_start is not None:
            return ((_ARCHIVE_FILE, arch_start), (_LOG_FILE, 0))

    return ((_LOG_FILE, log_start or 0),)


async def stream_environment_json(send, limit=500, before=None, since=None):
    """
    Wysyla przez async `send(bytes)` JSON
    {"status", "data": [...], "has_more", "next_before", "next_since"}
    od najstarszego do najnowszego:
    - bez `since`: najnowsze `limit` pomiarow starszych niz `before`
      (strona wstecz: kolejne zapytanie z before=next_before),
    - z `since`: najstarsze `limit` pomiarow nowszych niz `since`
      (synchronizacja przyrostowa: kolejne zapytanie z since=next_since).

    Poczatek szuka binarnie, wiec koszt nie rosnie z rozmiarem loga.
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

    before = _parse_key(before)
    since = _parse_key(since)

    if not _enabled:
        print("ENV: SD offline")
        await send(_json_offline())
        return

    t0 = utime.ticks_ms()
    print(
        "ENV: read start, limit", limit, "before", before, "since", since,
        "mem", mem_info()
    )

    _readers += 1

    try:
        # przebieg 1: skad zaczac
        state = [0, None, 0, None]

        try:
            if since is not None:
                segments = await _segments_since(since)
            else:
                segments = await _segments_before(limit, before, state)
        except Exception as e:
            print("ENV: SD error:", repr(e))
            _handle_error(e)
            await send(_json_offline())
            return

        await send(_JSON_HEAD)

        # przebieg 2: wyslij od najstarszego
        if since is not None:
            count = limit
        else:
            count = min(state[0], limit)

        ctx = [count, True, None]
        more = False

        for path, offset in segments:
            try:
                if await _send_rows(path, offset, before, since, ctx, send):
                    more = True
                    break
            except _SdError as e:
                # odpowiedz zostaje urwana - klient dostanie niepelny JSON
                err = e.args[0] if e.args else e
                print("ENV: SD error:", repr(err))
                _handle_error(err)
                return

        if since is not None:
            await send(_json_tail(more, None, ctx[2] if more else None))
        else:
            has_more = state[0] > limit
            await send(_json_tail(has_more, state[3] if has_more else None, None))

        print(
            "ENV: sent",
            count - ctx[0],
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

                # rok < 2025 = zegar po restarcie bez NTP; taki wiersz
                # zepsulby kolejnosc loga (wyszukiwanie binarne, since)
                if (
                        temperature is not None
                        and humidity is not None
                        and now[0] >= 2025
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
