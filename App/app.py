import gc
import sys

import machine
import uasyncio as asyncio
import utime

import button
import config_store
import display
import esp_config
import events
import ota
import sd_logger
import sensor
import server
from ntp import ntp_task, sync_ntp
from server import (
    clock_scheduler,
    get_display_state,
    scheduler_age_ms,
    start_server,
)
from wifi import connect_wifi, wifi_watchdog

# --- Watchdog ---

# Sprzetowy watchdog: jesli nie zostanie "nakarmiony" przez tyle ms,
# ESP sam sie resetuje. Uwaga: dziala tez po Ctrl-C w REPL
# (np. mpremote / PyCharm) - wtedy ESP zrestartuje sie po tym czasie.
WDT_ENABLED = True
WDT_TIMEOUT_MS = 60000

# Karmimy tylko, gdy harmonogram dzien/noc obiegl sie niedawno
# (obieg co 30 s). Stoi dluzej -> przestajemy karmic -> reset.
SCHEDULER_MAX_AGE_MS = 120000

# Restart petli asyncio bez resetu plytki; po tylu awariach
# w oknie czasu - pelny reset.
MAX_LOOP_CRASHES = 3
LOOP_CRASH_WINDOW_MS = 10 * 60 * 1000

# Siatka bezpieczenstwa: gdy sterta GC urosnie kosztem sterty ESP-IDF,
# pamiec juz nie wraca - WiFi/lwIP nie maja buforow i serwer wisi, a WDT
# tego nie widzi (harmonogram dziala). Tyle kontroli (co 5 s) ponizej
# progu = reset.
IDF_MIN_FREE = 8000
IDF_LOW_CHECKS = 3

_wdt = None


def toggle_lcd():
    enabled = not display.is_enabled()

    display.toggle(enabled)

    # config.json to stan, ktory widzi aplikacja - bez zapisu przycisk
    # wylaczal LCD, a przelacznik w aplikacji dalej pokazywal ON
    server.config["display"]["enabled"] = enabled
    config_store.save(server.config)

    print("Display (button):", "ON" if enabled else "OFF")


lcd_button = button.Button(
    pin=esp_config.PINS["lcd_button"],
    on_release=toggle_lcd,
)


async def watchdog_task():
    global _wdt

    # wlaczany dopiero tutaj: start (WiFi, NTP, SD) moze trwac dluzej
    if _wdt is None:
        _wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
        print("WDT: enabled,", WDT_TIMEOUT_MS, "ms")

    warned = False
    idf_low = 0

    while True:
        idf_free = sd_logger.mem_info()[1]

        # -1 = nie udalo sie odczytac, to nie powod do resetu
        if 0 <= idf_free < IDF_MIN_FREE:
            idf_low += 1
            print("MEM: ESP-IDF heap low:", idf_free, "B")
        else:
            idf_low = 0

        if idf_low >= IDF_LOW_CHECKS:
            print("MEM: ESP-IDF heap exhausted -> machine.reset()")
            events.log("reset: ESP-IDF heap exhausted")
            utime.sleep_ms(500)
            machine.reset()

        if scheduler_age_ms() < SCHEDULER_MAX_AGE_MS:
            _wdt.feed()
            warned = False
        elif not warned:
            print("WDT: scheduler stalled, not feeding -> reset soon")
            warned = True

        await asyncio.sleep(5)


async def supervise(name, factory):
    """
    Uruchamia zadanie i wznawia je po awarii, zeby blad np. loggera
    albo HTTP nie zatrzymal reszty (przede wszystkim harmonogramu).
    """
    delay = 5

    while True:
        started = utime.ticks_ms()

        try:
            await factory()
            print("TASK:", name, "ended")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("TASK:", name, "crashed:", repr(e))
            sys.print_exception(e)

        # dzialalo dlugo bez bledu -> znow krotka przerwa przed wznowieniem
        if utime.ticks_diff(utime.ticks_ms(), started) > 10 * 60 * 1000:
            delay = 5

        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def ota_confirm_task():
    # bez supervise: po potwierdzeniu zadanie ma sie po prostu skonczyc
    try:
        await ota.confirm_task(lambda: server._server is not None)
    except Exception as e:
        print("OTA: confirm error:", repr(e))

    # potwierdzona (albo nie bylo aktualizacji) - z powrotem dashboard
    display.end_update()


async def run_tasks():
    # wentylator wlaczony od startu (stan z config.json) - rozruch dimmera
    if server.fan_relay.get_state():
        server._dimmer.kick()

    tasks = [
        # najwazniejsze najpierw: harmonogram i watchdog
        supervise("scheduler", clock_scheduler),
    ]

    if WDT_ENABLED:
        tasks.append(supervise("watchdog", watchdog_task))

    await asyncio.gather(
        *tasks,
        supervise("wifi", wifi_watchdog),
        supervise("ntp", ntp_task),
        supervise("server", start_server),
        supervise("display", display.display_updater),
        supervise("sensor", sensor.sensor_task),
        supervise("logger", sd_logger.task),
        ota_confirm_task(),
    )


async def boot():
    # przyczyna ostatniego restartu (WDT, reset...). Brownout MicroPython
    # pokazuje jako POWERON - rozrozni go dopiero log bootloadera na UART
    # ("rst:0xf (BROWNOUT_RST)").
    causes = {
        machine.PWRON_RESET: "POWERON",
        machine.HARD_RESET: "HARD",
        machine.WDT_RESET: "WDT",
        machine.DEEPSLEEP_RESET: "DEEPSLEEP",
        machine.SOFT_RESET: "SOFT",
    }
    cause = machine.reset_cause()
    print("BOOT: reset cause", causes.get(cause, cause))

    server.boot_info["reset_cause"] = causes.get(cause, str(cause))
    events.log("boot, reset cause " + server.boot_info["reset_cause"])

    display.init()

    display.set_state_provider(
        get_display_state
    )

    # wylaczony LCD zostaje ciemny juz od startu (init() zawsze wlacza)
    display.toggle(
        bool(server.config["display"]["enabled"])
    )

    # nowa wersja po OTA czeka na potwierdzenie (ota_confirm_task)
    if (ota.load_state() or {}).get("pending"):
        display.show_update("VERIFYING")

    sensor.init()

    display.boot_step("WIFI", 15)
    wlan = await connect_wifi()

    if wlan:
        # adres przez chwile - przydaje sie do aplikacji i deploy.py
        display.boot_step(wlan.ifconfig()[0], 40)
        await asyncio.sleep(1)
    else:
        display.boot_step("NO WIFI", 40)

    display.boot_step("CLOCK", 55)

    if not await sync_ntp():
        display.boot_step("NO CLOCK", 70)

    display.boot_step("SD CARD", 75)
    sd_ok = sd_logger.init(300)

    display.boot_step("READY" if sd_ok else "NO SD CARD", 100)
    await asyncio.sleep(1)
    display.finish_boot()


def main():
    # blad przy starcie (WiFi, SD, wyswietlacz) nie moze zablokowac
    # harmonogramu - przekazniki maja stan z config.json juz od importu
    try:
        asyncio.run(boot())
    except Exception as e:
        print("BOOT: error:", repr(e))
        sys.print_exception(e)
        asyncio.new_event_loop()

    crashes = []

    while True:
        try:
            asyncio.run(run_tasks())
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # awaria samej petli asyncio (np. OSError EIO w select)
            print("MAIN: event loop crashed:", repr(e))
            events.log("event loop crashed: " + repr(e))
            sys.print_exception(e)

        now = utime.ticks_ms()
        crashes = [
            t for t in crashes
            if utime.ticks_diff(now, t) < LOOP_CRASH_WINDOW_MS
        ]
        crashes.append(now)

        if len(crashes) >= MAX_LOOP_CRASHES:
            print("MAIN: too many crashes -> machine.reset()")
            events.log("reset: too many event loop crashes")
            utime.sleep_ms(500)
            machine.reset()

        print("MAIN: restarting event loop")
        asyncio.new_event_loop()

        # stary serwer trzyma port 80; po odpieciu referencji GC zamyka
        # jego socket (sprawdzone na ESP) i nowy moze sie zbindowac
        server._server = None
        gc.collect()

        utime.sleep_ms(1000)


main()
