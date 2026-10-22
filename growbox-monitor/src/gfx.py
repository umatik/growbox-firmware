# gfx.py - rysowanie na framebuf RGB565: kolory, tekst z wygladzaniem,
# karty z zaokraglonymi rogami, pierscienie.
#
# Kolory trzymamy z zamienionymi bajtami: framebuf zapisuje piksel jako
# little-endian, a ST7735 czyta starszy bajt pierwszy. Dzieki temu bufor
# idzie na ekran bez przepisywania.
import math

import framebuf

import assets


def rgb(r, g, b):
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)


def _unpack(c):
    c = ((c & 0xFF) << 8) | (c >> 8)
    return (c >> 8) & 0xF8, (c >> 3) & 0xFC, (c << 3) & 0xF8


def mix(a, b, t):
    """Kolor posrodku: t=0 -> a, t=1 -> b."""
    ar, ag, ab = _unpack(a)
    br, bg, bb = _unpack(b)
    return rgb(
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


# --- tekst i ikony ---

_fonts = {}
_palettes = {}


class _Font:
    def __init__(self, spec):
        self.height, self.chars, self.widths, data = spec
        # FrameBuffer chce bufora do zapisu
        self.data = memoryview(bytearray(data))
        self.offsets = []
        offset = 0

        for w in self.widths:
            self.offsets.append(offset)
            offset += self.height * ((w + 1) // 2)

        self.glyphs = {}

    def glyph(self, ch):
        g = self.glyphs.get(ch)

        if g is None:
            i = self.chars.find(ch)

            if i < 0:
                i = self.chars.find("?")

            w = self.widths[i]
            size = self.height * ((w + 1) // 2)
            start = self.offsets[i]
            buf = self.data[start:start + size]
            g = (framebuf.FrameBuffer(buf, w, self.height, framebuf.GS4_HMSB), w)
            self.glyphs[ch] = g

        return g

    def width(self, text):
        return sum(self.glyph(ch)[1] for ch in text)


def font(name):
    f = _fonts.get(name)

    if f is None:
        # pop: oryginalne bytes z assets nie sa potrzebne po skopiowaniu
        # (FrameBuffer chce bytearray) - nie trzymamy dwoch kopii w RAM-ie
        f = _fonts[name] = _Font(assets.FONTS.pop(name))

    return f


def preload():
    """Wszystkie czcionki i ikony od razu przy starcie, gdy sterta jest
    jeszcze w jednym kawalku - pozniej kilkukilobajtowe bloki potrafily
    sie nie zmiescic miedzy drobnymi obiektami."""
    for name in list(assets.FONTS):
        font(name)

    for name in list(assets.ICONS):
        _icon(name)


def palette(fg, bg):
    """16 odcieni od tla do koloru - wygladzone krawedzie na danym tle."""
    key = (fg, bg)
    p = _palettes.get(key)

    if p is None:
        if len(_palettes) > 40:
            _palettes.clear()

        p = framebuf.FrameBuffer(bytearray(32), 16, 1, framebuf.RGB565)

        for i in range(16):
            p.pixel(i, 0, mix(bg, fg, i / 15))

        _palettes[key] = p

    return p


def text(fb, x, y, s, fg, bg, name, align="l", valign="t"):
    """Tekst od (x, y); align l/m/r, valign t (gora) / m (srodek)."""
    f = font(name)

    if align != "l":
        w = f.width(s)
        x -= w // 2 if align == "m" else w

    if valign == "m":
        y -= f.height // 2

    pal = palette(fg, bg)

    for ch in s:
        g, w = f.glyph(ch)
        # klucz jest porownywany po palecie: odcien 0 = tlo, nie rysujemy
        fb.blit(g, x, y, bg, pal)
        x += w

    return x


_icons = {}


def _icon(name):
    ic = _icons.get(name)

    if ic is None:
        w, h, data = assets.ICONS.pop(name)
        ic = _icons[name] = (framebuf.FrameBuffer(bytearray(data), w, h, framebuf.GS4_HMSB), w, h)

    return ic


def icon(fb, cx, cy, name, fg, bg):
    """Ikona wysrodkowana na (cx, cy)."""
    g, w, h = _icon(name)
    fb.blit(g, cx - w // 2, cy - h // 2, bg, palette(fg, bg))


# --- ksztalty ---

def rrect(fb, x, y, w, h, r, color):
    """Wypelniony prostokat z zaokraglonymi rogami."""
    fb.fill_rect(x + r, y, w - 2 * r, h, color)
    fb.fill_rect(x, y + r, w, h - 2 * r, color)
    # cwiartki elipsy: 1 prawa-gora, 2 lewa-gora, 4 lewa-dol, 8 prawa-dol
    fb.ellipse(x + r, y + r, r, r, color, True, 2)
    fb.ellipse(x + w - 1 - r, y + r, r, r, color, True, 1)
    fb.ellipse(x + r, y + h - 1 - r, r, r, color, True, 4)
    fb.ellipse(x + w - 1 - r, y + h - 1 - r, r, r, color, True, 8)


def card(fb, x, y, w, h, fill, edge):
    rrect(fb, x, y, w, h, 4, edge)
    rrect(fb, x + 1, y + 1, w - 2, h - 2, 3, fill)


def ring(fb, cx, cy, r_out, width, frac, track, color, bg):
    """Pierscien z wygladzonymi krawedziami; postep od gory zgodnie
    z ruchem wskazowek zegara (frac 0..1)."""
    r_in = r_out - width
    end = 360 * max(0.0, min(1.0, frac))
    n = int(r_out) + 1
    fcx = cx + 0.5
    fcy = cy + 0.5

    for py in range(cy - n, cy + n + 1):
        dy = py + 0.5 - fcy

        for px in range(cx - n, cx + n + 1):
            dx = px + 0.5 - fcx
            d = math.sqrt(dx * dx + dy * dy)
            cover = min(d - r_in, r_out - d) + 0.5

            if cover <= 0:
                continue

            angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
            c = color if angle <= end and end > 0 else track
            fb.pixel(px, py, mix(bg, c, min(1.0, cover)))
