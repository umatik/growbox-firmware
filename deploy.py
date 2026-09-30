#!/usr/bin/env python3
"""
Aktualizacja ESP przez WiFi (OTA, patrz App/ota.py).

    ./deploy.py              # wyslij zmienione pliki z App/ i zrestartuj ESP
    ./deploy.py --dry-run    # tylko pokaz, co sie zmienilo
    ./deploy.py sensor.py    # wyslij wybrane pliki (nawet bez zmian)

Moduly ida jako prekompilowane .mpy (mpy-cross w wersji MicroPythona na
ESP): ESP nie kompiluje zrodel przy starcie i zostaje mu wiecej RAM-u.
    pipx install mpy-cross==1.27.0.post2

Adres: --url albo zmienna ESP_URL (domyslnie http://192.168.18.85).
Token: czytany z App/server.py (API_TOKEN).

main.py, boot.py i secrets.py nie ida przez OTA - tylko kablem:
    mpremote connect /dev/cu.usbserial-0001 cp App/main.py :main.py
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT, "App")

PROTECTED = ("main.py", "boot.py", "secrets.py")
DEFAULT_URL = "http://192.168.18.85"

# format .mpy MicroPythona 1.27 na ESP32 (xtensawin)
MPY_CROSS = "mpy-cross"
MPY_VERSION = "mpy v6.3"
MPY_ARCH = "xtensawin"

# nowa wersja potwierdza sie po 60 s pracy (ota.CONFIRM_AFTER_S)
BOOT_TIMEOUT_S = 90
CONFIRM_AFTER_S = 60
CONFIRM_TIMEOUT_S = 150

# Po restarcie ESP nie wolno go zasypywac: requesty ida po kolei, a
# /api/update liczy sha256 wszystkich plikow. Gesty polling z krotkim
# timeoutem pietrzyl polaczenia w lwIP -> sterta ESP-IDF sie konczyla,
# ESP resetowal sie w kolko i wracal do starej wersji (rollback).
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 20


def read_token():
    with open(os.path.join(APP_DIR, "server.py")) as f:
        match = re.search(r'^API_TOKEN\s*=\s*"([^"]+)"', f.read(), re.M)

    if not match:
        sys.exit("Nie znalazlem API_TOKEN w App/server.py")

    return match.group(1)


class Esp:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def request(self, method, path, data=None, timeout=30):
        req = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"Authorization": "Bearer " + self.token},
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError("%s %s -> HTTP %s %s" % (method, path, e.code, body))

    def status(self, timeout=30, names=None):
        path = "/api/update"

        # nowszy firmware liczy sumy tylko tych plikow, starszy ignoruje
        if names:
            path += "?files=" + urllib.parse.quote(",".join(names), safe=",")

        return self.request("GET", path, timeout=timeout)

    def status_batched(self, names, batch=5):
        """
        Sumy partiami - wszystkie pliki naraz wyczerpywaly sterte ESP-IDF.
        Pliki tylko na ESP (bez lokalnej kopii) nie sa tu potrzebne.
        """
        result = None

        for i in range(0, len(names), batch):
            part = self.status(names=names[i:i + batch])

            if result is None:
                result = part
            else:
                result["files"] += part["files"]

        return result

    def upload(self, name, content):
        sha = hashlib.sha256(content).hexdigest()
        path = "/api/files/%s?sha256=%s" % (urllib.parse.quote(name), sha)

        return self.request("PUT", path, content, timeout=60)


def check_mpy_cross():
    try:
        version = subprocess.run(
            [MPY_CROSS, "--version"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        sys.exit("Brak mpy-cross: pipx install mpy-cross==1.27.0.post2")

    if MPY_VERSION not in version:
        sys.exit("mpy-cross emituje inny format niz %s: %s"
                 % (MPY_VERSION, version.strip()))


def local_files():
    """{"X.mpy": bajty} - moduly z App/ skompilowane mpy-cross."""
    files = {}

    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(os.listdir(APP_DIR)):
            if not name.endswith(".py") or name in PROTECTED:
                continue

            out = os.path.join(tmp, name[:-3] + ".mpy")
            # -s: w .mpy sama nazwa pliku, nie sciezka z tego komputera -
            # te same bajty (i sumy) na kazdej maszynie
            result = subprocess.run(
                [MPY_CROSS, "-march=" + MPY_ARCH, "-s", name, "-o", out,
                 os.path.join(APP_DIR, name)],
                capture_output=True, text=True,
            )

            if result.returncode:
                sys.exit("mpy-cross %s:\n%s" % (name, result.stderr))

            with open(out, "rb") as f:
                files[os.path.basename(out)] = f.read()

    return files


def wait_for(fetch, timeout, done, label):
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            status = fetch()

            if done(status):
                return status
        except (OSError, RuntimeError, ValueError):
            pass  # ESP jeszcze wstaje

        print(".", end="", flush=True)
        time.sleep(POLL_INTERVAL_S)

    print()
    sys.exit("Timeout: %s" % label)


def main():
    parser = argparse.ArgumentParser(description="OTA update ESP")
    parser.add_argument("files", nargs="*", help="wyslij tylko te pliki")
    parser.add_argument("--url", default=os.environ.get("ESP_URL", DEFAULT_URL))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-wait", action="store_true",
                        help="nie czekaj na restart i potwierdzenie")
    args = parser.parse_args()

    check_mpy_cross()
    esp = Esp(args.url, read_token())
    files = local_files()

    print("ESP:", esp.url)

    # zrodla .py na ESP tez: zaslaniaja .mpy, apply odklada je do .bak
    sources = [name[:-4] + ".py" for name in files]

    try:
        status = esp.status_batched(
            list(files) + sources + list(PROTECTED)
        )
    except (OSError, RuntimeError) as e:
        sys.exit("Brak polaczenia z ESP: %s" % e)

    remote = {f["name"]: f["sha256"] for f in status["files"]}

    if status.get("state"):
        print("Uwaga: poprzednia aktualizacja jeszcze niepotwierdzona:",
              status["state"])

    if args.files:
        # sensor.py albo sensor.mpy - i tak idzie skompilowany
        wanted = [os.path.splitext(n)[0] + ".mpy" for n in args.files]
        unknown = [n for n, w in zip(args.files, wanted) if w not in files]

        if unknown:
            sys.exit("Nie ma takich plikow w App/ (albo sa chronione): %s"
                     % ", ".join(unknown))

        changed = wanted
    else:
        changed = [
            name for name, content in files.items()
            if remote.get(name) != hashlib.sha256(content).hexdigest()
            or name[:-4] + ".py" in remote
        ]

    for name in PROTECTED:
        path = os.path.join(APP_DIR, name)

        if name in remote and os.path.exists(path):
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != remote[name]:
                    print("Uwaga: %s rozni sie od wersji na ESP - "
                          "wgraj kablem (mpremote)" % name)

    if not changed:
        print("Wszystko aktualne.")
        return

    print("Do wyslania:", ", ".join(changed))

    if args.dry_run:
        return

    if status.get("staged"):
        esp.request("POST", "/api/update/discard")

    for name in changed:
        print("  %-18s %6d B" % (name, len(files[name])), end=" ", flush=True)

        try:
            esp.upload(name, files[name])
        except (OSError, RuntimeError) as e:
            print("BLAD")
            esp.request("POST", "/api/update/discard")
            sys.exit("Upload przerwany, nic nie zmieniono na ESP: %s" % e)

        print("ok")

    esp.request("POST", "/api/update/apply")
    print("Zastosowano, ESP sie restartuje")

    if args.no_wait:
        return

    time.sleep(POLL_INTERVAL_S)

    # lekki /api/status - tylko czy wstal i od ilu sekund dziala
    health = wait_for(
        lambda: esp.request("GET", "/api/status", timeout=POLL_TIMEOUT_S),
        BOOT_TIMEOUT_S, lambda s: True, "ESP nie wstal",
    )
    print()

    wait_s = CONFIRM_AFTER_S + 5 - health.get("uptime_s", 0)

    if wait_s > 0:
        print("ESP wstal, czekam %d s na potwierdzenie" % wait_s)
        time.sleep(wait_s)

    # pelny /api/update dopiero teraz, pojedynczo i z dlugim timeoutem
    status = wait_for(
        lambda: esp.status(timeout=60, names=changed),
        CONFIRM_TIMEOUT_S, lambda s: not s.get("state"),
        "nowa wersja sie nie potwierdzila",
    )

    # stan pusty jest tez po rollbacku - rozstrzygaja sumy plikow
    remote = {f["name"]: f["sha256"] for f in status["files"]}
    wrong = [
        name for name in changed
        if remote.get(name) != hashlib.sha256(files[name]).hexdigest()
    ]

    if wrong:
        sys.exit("ESP wrocil do poprzedniej wersji (rollback): %s"
                 % ", ".join(wrong))

    print("Gotowe - aktualizacja potwierdzona.")


if __name__ == "__main__":
    main()
