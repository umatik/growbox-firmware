# main.py - GrowBox monitor: ESP32-C3 + TFT 1.8" ST7735 pokazuje stan
# GrowBoxa jak widget na Macu. Pyta glowne ESP po WiFi (GET /api/config
# co 15 s) i rysuje ekran (ui.py).
#
# Wgrywany kablem (README.md):
#   mpremote connect /dev/cu.usbmodem21101 cp src/*.py :
import gc
import sys

import machine
import utime
from machine import PWM, SPI, Pin, WDT

import config
import gfx
import model
import net
import ui
from st7735 import ST7735

WDT_TIMEOUT_MS = 30000

spi = SPI(
    1,
    baudrate=config.SPI_BAUDRATE,
    sck=Pin(config.PIN_SCK),
    mosi=Pin(config.PIN_MOSI),
    miso=Pin(config.PIN_MISO),
)
tft = ST7735(
    spi, config.PIN_CS, config.PIN_DC, config.PIN_RST,
    x_offset=config.TFT_X_OFFSET, y_offset=config.TFT_Y_OFFSET,
    bgr=config.TFT_BGR, flip=config.TFT_FLIP, invert=config.TFT_INVERT,
)

gfx.preload()
gc.collect()

_backlight = PWM(Pin(config.PIN_LED), freq=1000) if config.PIN_LED is not None else None
_backlight_level = None


def backlight(percent):
    global _backlight_level

    if _backlight is None or percent == _backlight_level:
        return

    _backlight_level = percent
    _backlight.duty_u16(percent * 65535 // 100)


_screen_on = True


def screen(on):
    global _screen_on

    if on == _screen_on:
        return

    _screen_on = on
    tft.power(on)

    if not on:
        backlight(0)


def night(now):
    if now is None:
        return False

    hour = model.civil(now)[3]

    if config.NIGHT_FROM > config.NIGHT_TO:
        return hour >= config.NIGHT_FROM or hour < config.NIGHT_TO

    return config.NIGHT_FROM <= hour < config.NIGHT_TO


wdt = WDT(timeout=WDT_TIMEOUT_MS)


def feed():
    wdt.feed()


def boot_screen(message, detail=""):
    ui.draw_boot(tft, message, detail)
    tft.show()


class Monitor:
    def __init__(self):
        self.host = net.load_host()
        self.response = None
        self.failures = 0
        self.next_config = 0
        self.next_ntp = 0
        self.last_search = None

    def due(self, at):
        return utime.ticks_diff(utime.ticks_ms(), at) >= 0

    def later(self, seconds):
        return utime.ticks_add(utime.ticks_ms(), seconds * 1000)

    def ensure_wifi(self):
        if net.wifi_connected():
            return True

        if self.response is None:
            boot_screen("Łączę z WiFi")

        ok = net.connect_wifi(feed)

        if not ok and self.response is None:
            boot_screen("Brak WiFi", "ponowię za chwilę")

        return ok

    def poll_config(self):
        self.next_config = self.later(config.POLL_CONFIG_S)

        try:
            self.response = net.get(self.host, "/api/config")
            self.failures = 0
            return
        except Exception as e:
            self.failures += 1
            print("ESP:", self.host, repr(e), "failures", self.failures)

        if self.failures < config.FAILURES_BEFORE_SEARCH:
            return

        # nie czesciej niz co minute - skan sieci trwa
        if self.last_search is not None and not self.due(self.last_search):
            return

        self.last_search = self.later(60)

        if self.response is None:
            boot_screen("Szukam GrowBoxa", net.my_ip())
        else:
            self.draw()

        found = net.find_esp(self.host, feed)

        if found:
            print("ESP: found at", found)
            self.host = found
            net.save_host(found)
            self.failures = 0
            self.next_config = 0
    
    def draw(self):
        # wl./wyl. i jasnosc ustawiane w aplikacji (config.monitor na ESP)
        settings = (self.response.get("config") or {}).get("monitor") or {}

        if not settings.get("enabled", True):
            screen(False)
            return

        now = net.local_now()
        view = model.build(
            self.response, now, net.utc_offset(),
            offline=self.failures >= 2,
        )
        ui.draw(tft, view)
        tft.show()
        screen(True)
        day = settings.get("brightness", config.BACKLIGHT_DAY)
        backlight(min(day, config.BACKLIGHT_NIGHT) if night(now) else day)

    def step(self):
        feed()

        if not self.ensure_wifi():
            utime.sleep_ms(5000)
            return

        if net.local_now() is None and self.due(self.next_ntp):
            self.next_ntp = self.later(60)

            if self.response is None:
                boot_screen("Ustawiam zegar")

            net.sync_clock()

        if self.due(self.next_config):
            if self.response is None:
                boot_screen("Łączę z GrowBoxem", self.host)

            self.poll_config()

            if self.response is not None:
                self.draw()

        utime.sleep_ms(500)


def main():
    backlight(config.BACKLIGHT_DAY)
    boot_screen("Start")
    monitor = Monitor()
    errors = 0

    while True:
        try:
            monitor.step()
            errors = 0
        except MemoryError:
            gc.collect()
            errors += 1
        except Exception as e:
            sys.print_exception(e)
            errors += 1
            utime.sleep_ms(1000)

        # cos sie stale sypie - czysty start
        if errors >= 10:
            machine.reset()


main()
