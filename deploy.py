#!/usr/bin/env python3
"""
Aktualizacja ESP przez WiFi (OTA, patrz App/ota.py).

    ./deploy.py              # wyslij zmienione pliki z App/ i zrestartuj ESP
    ./deploy.py --dry-run    # tylko pokaz, co sie zmienilo
    ./deploy.py sensor.py    # wyslij wybrane pliki (nawet bez zmian)

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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT, "App")

PROTECTED = ("main.py", "boot.py", "secrets.py")
DEFAULT_URL = "http://192.168.18.85"

# nowa wersja potwierdza sie po 60 s pracy (ota.CONFIRM_AFTER_S)
BOOT_TIMEOUT_S = 90
CONFIRM_TIMEOUT_S = 150


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

    def status(self, timeout=30):
        return self.request("GET", "/api/update", timeout=timeout)

    def upload(self, name, content):
        sha = hashlib.sha256(content).hexdigest()
        path = "/api/files/%s?sha256=%s" % (urllib.parse.quote(name), sha)

        return self.request("PUT", path, content, timeout=60)


def local_files():
    files = {}

    for name in sorted(os.listdir(APP_DIR)):
        if name.endswith(".py") and name not in PROTECTED:
            with open(os.path.join(APP_DIR, name), "rb") as f:
                files[name] = f.read()

    return files


def wait_for(esp, timeout, done, label):
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            status = esp.status(timeout=5)

            if done(status):
                return status
        except (OSError, RuntimeError, ValueError):
            pass  # ESP jeszcze wstaje

        print(".", end="", flush=True)
        time.sleep(3)

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

    esp = Esp(args.url, read_token())
    files = local_files()

    print("ESP:", esp.url)

    try:
        status = esp.status()
    except (OSError, RuntimeError) as e:
        sys.exit("Brak polaczenia z ESP: %s" % e)

    remote = {f["name"]: f["sha256"] for f in status["files"]}

    if status.get("state"):
        print("Uwaga: poprzednia aktualizacja jeszcze niepotwierdzona:",
              status["state"])

    if args.files:
        unknown = [n for n in args.files if n not in files]

        if unknown:
            sys.exit("Nie ma takich plikow w App/ (albo sa chronione): %s"
                     % ", ".join(unknown))

        changed = args.files
    else:
        changed = [
            name for name, content in files.items()
            if remote.get(name) != hashlib.sha256(content).hexdigest()
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

    time.sleep(3)
    status = wait_for(esp, BOOT_TIMEOUT_S, lambda s: True, "ESP nie wstal")
    print()

    remote = {f["name"]: f["sha256"] for f in status["files"]}
    wrong = [
        name for name in changed
        if remote.get(name) != hashlib.sha256(files[name]).hexdigest()
    ]

    if wrong:
        sys.exit("ESP dziala, ale ma inne wersje plikow (rollback?): %s"
                 % ", ".join(wrong))

    print("ESP wstal z nowymi plikami, czekam na potwierdzenie (~60 s)",
          end="", flush=True)

    wait_for(esp, CONFIRM_TIMEOUT_S, lambda s: not s.get("state"),
             "nowa wersja sie nie potwierdzila")
    print("\nGotowe - aktualizacja potwierdzona.")


if __name__ == "__main__":
    main()
