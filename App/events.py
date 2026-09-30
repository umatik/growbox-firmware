# events.py - krotki dziennik zdarzen na flashu (restarty, awarie petli,
# utrata WiFi), czytany zdalnie przez GET /api/status - bez kabla nie ma
# innego wgladu w to, co sie dzialo.

import os

import utime

EVENTS_FILE = "events.log"

# po przekroczeniu zostaje druga polowa pliku
MAX_SIZE = 4096


def log(message):
    try:
        t = utime.localtime()
        line = "%04d-%02d-%02d %02d:%02d:%02d %s\n" % (
            t[0], t[1], t[2], t[3], t[4], t[5], message
        )

        try:
            size = os.stat(EVENTS_FILE)[6]
        except OSError:
            size = 0

        if size > MAX_SIZE:
            with open(EVENTS_FILE) as f:
                f.seek(size // 2)
                f.readline()  # urwana linia
                rest = f.read()

            with open(EVENTS_FILE, "w") as f:
                f.write(rest)

        with open(EVENTS_FILE, "a") as f:
            f.write(line)

        print("EVENT:", message)

    except Exception as e:
        # dziennik nie moze niczego wywrocic
        print("EVENT log error:", repr(e))


# koncowka pliku czytana przez tail() - starcza na ~12 linii po ~60 B
TAIL_BYTES = 1024


def tail(count=12):
    # tylko koncowka pliku: caly plik (do 4 KB) + split to ~10 KB smieci
    # na jedno GET /api/status - sterta GC rosla kosztem sterty ESP-IDF
    # i konczylo sie resetem "heap exhausted"
    try:
        size = os.stat(EVENTS_FILE)[6]

        with open(EVENTS_FILE) as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()  # urwana linia

            lines = f.read().split("\n")
    except OSError:
        return []

    return [line for line in lines if line][-count:]
