# main.py - siatka bezpieczenstwa dla aktualizacji OTA (ota.py).
#
# Wgrywany TYLKO kablem i celowo samodzielny (bez importu ota.py,
# ktory tez moze byc zepsuty). Po aktualizacji ota_state.json ma
# "pending": kazdy start jest liczony, a gdy nowa wersja nie potwierdzi
# sie przez MAX_BOOTS startow (blad importu, WDT, petla resetow),
# pliki wracaja z .bak.

import json
import os
import sys

import machine
import utime

STATE_FILE = "ota_state.json"
MAX_BOOTS = 3


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _rollback(files):
    for name in files:
        try:
            os.stat(name + ".bak")
        except OSError:
            # plik dodany w aktualizacji - nie bylo poprzedniej wersji
            try:
                os.remove(name)
            except OSError:
                pass
            continue

        try:
            os.remove(name)
        except OSError:
            pass

        os.rename(name + ".bak", name)


_state = _load_state()

if _state and _state.get("pending"):
    _state["boots"] = _state.get("boots", 0) + 1

    if _state["boots"] > MAX_BOOTS:
        print("OTA: update not confirmed -> rollback", _state.get("files"))

        try:
            _rollback(_state.get("files", []))
            os.remove(STATE_FILE)
        except Exception as e:
            sys.print_exception(e)

        utime.sleep_ms(500)
        machine.reset()

    print("OTA: pending update, boot", _state["boots"], "/", MAX_BOOTS)
    _save_state(_state)

try:
    import app  # noqa: F401 - app.py uruchamia sie przy imporcie
except KeyboardInterrupt:
    raise
except Exception as e:
    # blad przy starcie (np. SyntaxError po aktualizacji): reset zamiast
    # martwego REPL-a; przy pending kolejne starty prowadza do rollbacku.
    # 10 s zapasu, zeby mpremote zdazyl przerwac petle resetow.
    print("MAIN: app crashed at startup:", repr(e))
    sys.print_exception(e)
    utime.sleep(10)
    machine.reset()
