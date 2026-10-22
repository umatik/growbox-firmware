# ui.py - ekran 128x160 (pion). Uklad jak w widgecie na Macu, bez
# wykresu (slaby kontrast wyswietlacza): naglowek, temperatura
# i wilgotnosc, pierscienie (tydzien kwitnienia, wentylator, nawilzacz)
# i podlewanie - duzymi znakami na cala wysokosc.
#
# Rysuje tylko z gotowego slownika `view` (model.py), bez sieci i zegara -
# tak samo dziala na ESP i w tools/preview.py na komputerze.
from gfx import card, icon, rgb, ring, text

W = 128
H = 160

# czarne tlo i jasne kolory - TFT ma slaby kontrast
BG = rgb(0, 0, 0)
CARD = rgb(12, 30, 32)
EDGE = rgb(50, 90, 92)
YEL = rgb(255, 210, 0)
BLU = rgb(90, 190, 255)
GRN = rgb(40, 235, 90)
RED = rgb(255, 70, 60)
GRY = rgb(175, 195, 195)
DIM = rgb(110, 135, 137)
WHT = rgb(255, 255, 255)

FEED_COLORS = {"ok": GRN, "late": YEL, "overdue": RED}


def _fmt(value, unit):
    if value is None:
        return "--" + unit

    return "%.1f%s" % (value, unit)


def _header(fb, view):
    text(fb, 4, 1, "Box Panel", WHT, BG, "b11")

    if view.get("offline"):
        text(fb, 124, 3, "OFFLINE", RED, BG, "b8", "r")
    elif view.get("mode"):
        mode = view["mode"]
        text(fb, 124, 3, mode, GRN if mode == "AUTO" else BLU, BG, "b8", "r")


def _climate(fb, view):
    card(fb, 2, 17, 124, 62, CARD, EDGE)
    # wiersz na wartosc, bez podpisow - kolor i jednostka wystarcza
    for y, value, unit, color in (
            (20, view.get("temp"), "°C", YEL),
            (49, view.get("hum"), "%", BLU)):
        text(fb, 7, y, _fmt(value, unit), color, CARD, "b22")

    fb.hline(8, 48, 112, EDGE)


RING_Y = 104
RING_R = 18
RING_W = 3


def _rings(fb, view):
    card(fb, 2, 81, 124, 47, CARD, EDGE)

    # tydzien kwitnienia
    week = view.get("week")

    if week:
        ring(fb, 25, RING_Y, RING_R, RING_W, week[1], EDGE, GRN, CARD)
        text(fb, 26, RING_Y, str(week[0]), GRN, CARD, "b16", "m", "m")
    else:
        ring(fb, 25, RING_Y, RING_R, RING_W, 0, EDGE, GRN, CARD)
        text(fb, 26, RING_Y, "–", GRY, CARD, "b16", "m", "m")

    # wentylator: pierscien = moc
    fan_on, level = view.get("fan") or (False, 0)
    ring(fb, 64, RING_Y, RING_R, RING_W, level / 100 if fan_on else 0, EDGE, GRN, CARD)
    icon(fb, 64, RING_Y, "fan", GRN if fan_on and level else GRY, CARD)

    # nawilzacz: pelny niebieski pierscien, gdy pracuje
    state = view.get("humidifier") or "off"
    color = {"on": BLU, "idle": GRY, "offline": RED}.get(state, DIM)
    ring(fb, 102, RING_Y, RING_R, RING_W, 1 if state == "on" else 0, EDGE, BLU, CARD)
    icon(fb, 102, RING_Y, "drop", color, CARD)


def _feeding(fb, view):
    card(fb, 2, 130, 124, 30, CARD, EDGE)
    feed = view.get("feed")

    if not feed:
        text(fb, 7, 145, "Karmienie: brak danych", DIM, CARD, "r8", "l", "m")
        return

    # kolor kropki i napisu mowi, czy podlac
    status, label, _ = feed
    color = FEED_COLORS[status]
    fb.ellipse(12, 145, 5, 5, color, True)
    text(fb, 22, 145, label, WHT if status == "ok" else color, CARD, "b11", "l", "m")


def draw(fb, view):
    fb.fill(BG)
    _header(fb, view)
    _climate(fb, view)
    _rings(fb, view)
    _feeding(fb, view)


def draw_boot(fb, message, detail=""):
    """Ekran startu: nazwa i biezacy krok (WiFi, szukanie ESP...)."""
    fb.fill(BG)
    icon(fb, 64, 56, "fan", GRN, BG)
    text(fb, 64, 74, "GrowBox", WHT, BG, "b14", "m")
    text(fb, 64, 96, message, GRY, BG, "r8", "m")

    if detail:
        text(fb, 64, 108, detail, DIM, BG, "r8", "m")
