# ota.py - aktualizacja plikow .py przez WiFi
#
# Pliki .py albo prekompilowane .mpy (deploy.py wysyla .mpy: bez kompilacji
# na ESP starcza wiecej RAM-u). MicroPython laduje X.py przed X.mpy, wiec
# apply odklada druga wersje modulu do .bak - rollback w main.py ja przywraca.
#
# 1. PUT /api/files/<plik>  -> zapis do <plik>.new (strumieniowo, z sha256)
# 2. POST /api/update/apply -> <plik> -> <plik>.bak, <plik>.new -> <plik>,
#                              zapis ota_state.json (pending) i reset
# 3. main.py liczy starty z pending; po MAX_BOOTS bez potwierdzenia
#    przywraca .bak. Nowa wersja potwierdza sie sama (confirm_task),
#    gdy dziala od CONFIRM_AFTER_S sekund i serwer HTTP stoi.
#
# main.py, boot.py, secrets.py i config.json nie sa aktualizowane OTA:
# zepsuty main.py albo haslo WiFi = tylko kabel.

import binascii
import gc
import hashlib
import json
import os

import uasyncio as asyncio

STATE_FILE = "ota_state.json"

PROTECTED = ("main.py", "boot.py", "secrets.py")

CONFIRM_AFTER_S = 60

_CHUNK = 1024
_IO_TIMEOUT = 15

# maks. rozmiar jednego pliku - wiecej to raczej pomylka niz kod
MAX_FILE_SIZE = 128 * 1024


# Jeden staly bufor na odczyt plikow i uploadu. read() alokuje nowy bufor
# przy kazdym kawalku - kilkadziesiat KB smieci na plik rozpycha sterte GC
# kosztem sterty ESP-IDF i WiFi przestaje wysylac (jak w sd_logger).
_buf = None


def _buffer():
    global _buf

    if _buf is None:
        _buf = bytearray(_CHUNK)

    return _buf


class OtaError(Exception):
    pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _stem(name):
    """Nazwa modulu dla X.py / X.mpy, inaczej None."""
    for ext in (".py", ".mpy"):
        if name.endswith(ext):
            return name[:-len(ext)]

    return None


def valid_name(name):
    """Tylko pliki .py / .mpy w katalogu glownym, bez chronionych."""
    stem = _stem(name)

    if not stem or name in PROTECTED or stem + ".py" in PROTECTED:
        return False

    for c in stem:
        if not (c.isalpha() or c.isdigit() or c == "_"):
            return False

    return True


def _other(name):
    """Druga wersja modulu: X.py <-> X.mpy."""
    stem = _stem(name)
    return stem + (".mpy" if name.endswith(".py") else ".py")


def _staged_names():
    return [
        n[:-4] for n in os.listdir()
        if n.endswith(".new") and valid_name(n[:-4])
    ]


def _sha256_file(path):
    h = hashlib.sha256()
    buf = _buffer()
    view = memoryview(buf)

    with open(path, "rb") as f:
        while True:
            n = f.readinto(buf)

            if not n:
                break

            h.update(view[:n])

    return binascii.hexlify(h.digest()).decode()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


async def status(names=None):
    """
    names: tylko te pliki (po aktualizacji wystarcza wyslane). Sumy
    wszystkich plikow naraz wyczerpywaly sterte ESP-IDF (reset "heap
    exhausted"), dlatego po kazdym pliku odsmiecanie i oddanie petli.
    """
    gc.collect()

    files = []
    staged = []

    for name in sorted(os.listdir()):
        if name.endswith(".new") and valid_name(name[:-4]):
            staged.append(name[:-4])
        elif _stem(name) and (names is None or name in names):
            await asyncio.sleep_ms(0)
            files.append({
                "name": name,
                "size": os.stat(name)[6],
                "sha256": _sha256_file(name),
            })
            # obiekt sha256 (mbedtls) trzyma kontekst w stercie ESP-IDF az
            # do odsmiecenia - kilkadziesiat plikow naraz ja wyczerpywalo
            gc.collect()

    return {
        "state": load_state(),
        "staged": staged,
        "files": files,
    }


async def receive(name, reader, length, body, sha256):
    """
    Zapisuje body requestu do <name>.new. `body` = bajty juz przeczytane
    razem z naglowkami, reszte (do `length`) czyta z `reader`.
    """
    if not valid_name(name):
        raise OtaError("invalid name")

    if length <= 0 or length > MAX_FILE_SIZE:
        raise OtaError("invalid length")

    gc.collect()

    tmp = name + ".new"
    h = hashlib.sha256()
    received = 0
    buf = _buffer()
    view = memoryview(buf)

    try:
        with open(tmp, "wb") as f:
            if body:
                body = body[:length]
                f.write(body)
                h.update(body)
                received = len(body)

            while received < length:
                n = await asyncio.wait_for(
                    reader.readinto(view[:min(_CHUNK, length - received)]),
                    _IO_TIMEOUT
                )

                if not n:
                    raise OtaError("connection closed")

                f.write(view[:n])
                h.update(view[:n])
                received += n

        digest = binascii.hexlify(h.digest()).decode()

        if sha256 and digest != sha256.lower():
            raise OtaError("sha256 mismatch")

    except Exception:
        _remove(tmp)
        raise

    return digest


def discard():
    removed = _staged_names()

    for name in removed:
        _remove(name + ".new")

    return removed


def apply():
    """Podmienia pliki .new i zapisuje stan pending. Reset robi wolajacy."""
    staged = _staged_names()

    if not staged:
        raise OtaError("nothing staged")

    # wczesniejsza niepotwierdzona aktualizacja: jej .bak to ostatnia
    # dzialajaca wersja - nie nadpisujemy ich wersja niesprawdzona
    previous = load_state()
    keep_bak = previous["files"] if previous and previous.get("pending") else []

    files = list(keep_bak)

    def set_aside(name):
        # stara wersja do .bak (rollback), w poprzedniej niepotwierdzonej
        # aktualizacji .bak juz jest - biezacy plik po prostu znika
        if name in files:
            _remove(name)
        elif _exists(name):
            _remove(name + ".bak")
            os.rename(name, name + ".bak")
            files.append(name)

    for name in staged:
        if name not in files:
            if _exists(name):
                set_aside(name)
            else:
                # nowy plik - przy rollbacku do usuniecia
                _remove(name + ".bak")
                files.append(name)
        else:
            _remove(name)

        # X.py zaslanialby nowy X.mpy (i odwrotnie zostalby martwy plik)
        other = _other(name)

        if other not in staged and _exists(other):
            set_aside(other)

        os.rename(name + ".new", name)

    with open(STATE_FILE, "w") as f:
        json.dump({"pending": True, "boots": 0, "files": files}, f)

    return staged


def confirm():
    state = load_state()

    if state and state.get("pending"):
        _remove(STATE_FILE)
        print("OTA: update confirmed", state.get("files"))
        return True

    return False


async def confirm_task(is_ready):
    """Po CONFIRM_AFTER_S s pracy (i gdy is_ready()) zatwierdza aktualizacje."""
    if not (load_state() or {}).get("pending"):
        return

    await asyncio.sleep(CONFIRM_AFTER_S)

    while not is_ready():
        await asyncio.sleep(5)

    confirm()
