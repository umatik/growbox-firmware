# main.py - mini ESP (ESP32-C3) w nawilzaczu: przekaznik sterowany przez
# glowne ESP po ESP-NOW (App/humidifier.py).
#
# Bezpieczenstwo: przekaznik jest wylaczony od startu, wylacza sie po
# LINK_TIMEOUT_MS bez wiadomosci od glownego ESP, a watchdog resetuje
# zawieszona plytke (po resecie znow wylaczony).
#
# Glowne ESP siedzi na kanale routera WiFi, ktory moze sie zmienic, wiec
# po utracie lacznosci mini sprawdza kanaly 1..13: unicast "HEL" dostaje
# potwierdzenie tylko na kanale, na ktorym glowne ESP slucha.
#
# Adresu glownego ESP nie ma w kodzie: mini slucha na kolejnych kanalach,
# az przyjdzie "HUM" (wysyla je tylko ESP, ktore ma mini w configu jako
# peer), i zapisuje nadawce w MAIN_FILE. Gdy zapisane ESP nie odpowiada
# przez MISSES_BEFORE_SCAN obiegow, szuka od nowa - wymiana glownego ESP
# nie wymaga wgrywania mini kablem.
#
# Wgrywany kablem:
#   mpremote connect /dev/cu.usbmodem21101 cp Humidifier/main.py :main.py
import espnow
import machine
import network
import utime

MAIN_FILE = "main_mac.txt"

RELAY_PIN = 4
# HW-307 (PNP S8550) wlacza sie stanem niskim, a 3,3 V z ESP go nie
# wylacza, wiec IN zwiera do GND tranzystor NPN (BC547A: GPIO -> 1k -> baza,
# 10k baza-GND). GPIO wysoki = przekaznik wlaczony; przy starcie i resecie
# pin jest wejsciem, 10k trzyma tranzystor zamkniety = przekaznik wylaczony.
RELAY_ACTIVE_LOW = False

# glowne ESP wysyla stan co 5 s
LINK_TIMEOUT_MS = 20000
WDT_TIMEOUT_MS = 10000
CHANNELS = range(1, 14)
# glowne ESP wysyla HUM co 5 s - tyle sluchamy na kazdym kanale
LISTEN_MS = 6000
MISSES_BEFORE_SCAN = 3

_OFF = 1 if RELAY_ACTIVE_LOW else 0
_relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=_OFF)
# dioda na plytce (GPIO 8, swieci stanem niskim): zapalona = jest lacznosc
_led = machine.Pin(8, machine.Pin.OUT, value=1)

_state = 0


def set_relay(on):
    global _state

    on = 1 if on else 0

    if on != _state:
        print("RELAY:", "ON" if on else "OFF")

    _state = on
    _relay.value(on ^ RELAY_ACTIVE_LOW)


def load_main():
    try:
        with open(MAIN_FILE) as f:
            return bytes.fromhex(f.read().strip())
    except (OSError, ValueError):
        return None


def save_main(mac):
    with open(MAIN_FILE, "w") as f:
        f.write(mac.hex())


def add_peer(e, mac):
    try:
        e.add_peer(mac)
    except OSError:
        pass  # juz dodany (ESP_ERR_ESPNOW_EXIST)


def find_channel(sta, e, wdt, main_mac):
    """Kanal glownego ESP albo None po pelnym obiegu."""
    for ch in CHANNELS:
        wdt.feed()
        sta.config(channel=ch)

        try:
            if e.send(main_mac, b"HEL"):
                print("LINK: main ESP on channel", ch)
                return ch
        except OSError:
            pass

    return None


def discover(sta, e, wdt):
    """Adres ESP, ktore wysyla nam HUM, albo None po pelnym obiegu."""
    print("LINK: searching for main ESP")

    for ch in CHANNELS:
        sta.config(channel=ch)
        deadline = utime.ticks_add(utime.ticks_ms(), LISTEN_MS)

        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            wdt.feed()
            mac, msg = e.recv(500)

            if msg and msg[:3] == b"HUM" and len(msg) == 4:
                print("LINK: found main ESP", mac.hex(":"), "on channel", ch)
                return bytes(mac)

    return None


def main():
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.disconnect()

    e = espnow.ESPNow()
    e.active(True)

    main_mac = load_main()

    if main_mac:
        add_peer(e, main_mac)

    print("Humidifier node", sta.config("mac").hex(":"),
          "main", main_mac.hex(":") if main_mac else "unknown")

    last_msg = None
    misses = 0

    while True:
        wdt.feed()

        linked = (
            last_msg is not None
            and utime.ticks_diff(utime.ticks_ms(), last_msg) < LINK_TIMEOUT_MS
        )

        if not linked:
            if last_msg is not None:
                print("LINK: lost -> relay OFF")
                last_msg = None

            set_relay(0)
            _led.value(1)

            if main_mac and find_channel(sta, e, wdt, main_mac):
                misses = 0
            elif main_mac and misses < MISSES_BEFORE_SCAN:
                misses += 1
                utime.sleep_ms(1000)
                continue
            else:
                found = discover(sta, e, wdt)

                if found is None:
                    continue

                if found != main_mac:
                    add_peer(e, found)
                    save_main(found)
                    main_mac = found

                misses = 0

            # kanal znaleziony - czekamy na HUM (glowne odpowiada na HEL)
            last_msg = utime.ticks_ms()

        mac, msg = e.recv(1000)

        if mac != main_mac or not msg:
            continue

        if msg[:3] == b"HUM" and len(msg) == 4:
            last_msg = utime.ticks_ms()
            _led.value(0)
            set_relay(msg[3])

            try:
                e.send(main_mac, b"HST" + bytes([_state]), False)
            except OSError as err:
                print("LINK: reply error:", err)


try:
    main()
finally:
    # wyjatek albo Ctrl-C: nawilzacz nie moze zostac wlaczony bez nadzoru
    set_relay(0)
