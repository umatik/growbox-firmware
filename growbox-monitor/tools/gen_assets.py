#!/usr/bin/env python3
# gen_assets.py - generuje src/assets.py: czcionki i ikony jako bitmapy
# GS4 (16 odcieni = wygladzone krawedzie), rysowane na ESP przez
# framebuf.blit z paleta.
#
#   pip install pillow
#   python3 tools/gen_assets.py [--fonts /sciezka/do/dejavu]
#
# Potrzebne DejaVuSans.ttf i DejaVuSans-Bold.ttf (na Linuksie w
# /usr/share/fonts/truetype/dejavu, na Macu: brew install --cask font-dejavu,
# ~/Library/Fonts).
import argparse
import math
import os

from PIL import Image, ImageDraw, ImageFont

ASCII = "".join(chr(c) for c in range(32, 127))
POLISH = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ°–"
DIGITS = "0123456789"

# nazwa: (plik, rozmiar w px, znaki)
FONTS = {
    "r8": ("DejaVuSans.ttf", 8, ASCII + POLISH),
    "b8": ("DejaVuSans-Bold.ttf", 8, " " + DIGITS + "ABCDEFGHIJKLMNOPQRSTUVWXYZ-"),
    "b11": ("DejaVuSans-Bold.ttf", 11, ASCII + POLISH),
    "b14": ("DejaVuSans-Bold.ttf", 14, " " + DIGITS + ".%°-–?GrowBx"),
    "b16": ("DejaVuSans-Bold.ttf", 16, " " + DIGITS + "-–?"),
    "b22": ("DejaVuSans-Bold.ttf", 22, " " + DIGITS + ".%°C-–?"),
}

# rysowanie w 4x i zmniejszenie - tak samo jak podglad
SS = 4


def gs4(img):
    """Obraz L (0-255) -> bajty GS4_HMSB (pierwszy piksel w starszym polbajcie)."""
    w, h = img.size
    px = img.load()
    out = bytearray()

    for y in range(h):
        for x in range(0, w, 2):
            a = (px[x, y] * 15 + 127) // 255
            b = (px[x + 1, y] * 15 + 127) // 255 if x + 1 < w else 0
            out.append((a << 4) | b)

    return bytes(out)


def font(path, size, chars):
    f = ImageFont.truetype(path, size * SS)
    ascent, descent = f.getmetrics()
    h = math.ceil((ascent + descent) / SS)
    widths = bytearray()
    data = bytearray()

    for ch in chars:
        w = max(1, round(f.getlength(ch) / SS))
        big = Image.new("L", (w * SS, h * SS), 0)
        ImageDraw.Draw(big).text((0, 0), ch, fill=255, font=f)
        small = big.resize((w, h), Image.LANCZOS)
        widths.append(w)
        data += gs4(small)

    return h, chars, bytes(widths), bytes(data)


ICON = 26  # ikony 26x26, srodek = srodek pierscienia
K = ICON / 20  # ksztalty rysowane w skali 20 px


def fan_icon():
    img = Image.new("L", (ICON * SS, ICON * SS), 0)
    d = ImageDraw.Draw(img)
    cx = cy = ICON / 2

    for k in range(3):
        a0 = math.radians(-90 + k * 120)
        pts = [(cx * SS, cy * SS)]

        for j in range(21):
            u = j / 20
            bend = 0.25 * math.sin(math.pi * u)
            ang = a0 + math.radians(-28 + 56 * u) + bend
            rr = K * 0.9 * (2 + 5.5 * math.sin(math.pi * u) ** 0.6)
            pts.append(((cx + rr * math.cos(ang)) * SS, (cy + rr * math.sin(ang)) * SS))

        d.polygon(pts, fill=255)

    # piasta: przerwa (tlo karty) i kropka
    hub, dot = 1.6 * K, 0.9 * K
    d.ellipse([(cx - hub) * SS, (cy - hub) * SS, (cx + hub) * SS, (cy + hub) * SS], fill=0)
    d.ellipse([(cx - dot) * SS, (cy - dot) * SS, (cx + dot) * SS, (cy + dot) * SS], fill=255)
    return img.resize((ICON, ICON), Image.LANCZOS)


def drop_icon():
    img = Image.new("L", (ICON * SS, ICON * SS), 0)
    d = ImageDraw.Draw(img)
    k = 0.9 * K
    cx = ICON / 2
    cy = ICON / 2 + 1.6 * K
    r = 4.2 * k
    top = (cx * SS, (cy - 8.2 * k) * SS)
    arc = [
        ((cx + r * math.cos(a)) * SS, (cy + r * math.sin(a)) * SS)
        for a in [math.radians(-40 + i * 5) for i in range(53)]
    ]
    d.line([top] + arc + [top], fill=255, width=int(1.1 * K * SS), joint="curve")
    d.arc(
        [(cx - 2.2 * k) * SS, (cy - 1.8 * k) * SS, (cx + 2.2 * k) * SS, (cy + 2.4 * k) * SS],
        60, 140, fill=255, width=int(0.8 * K * SS),
    )
    return img.resize((ICON, ICON), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", default="/usr/share/fonts/truetype/dejavu")
    ap.add_argument("--out", default=os.path.join(here, "..", "src", "assets.py"))
    args = ap.parse_args()

    lines = [
        "# assets.py - wygenerowane przez tools/gen_assets.py, nie edytowac recznie.",
        "# Czcionki: nazwa -> (wysokosc, znaki, szerokosci, bitmapy GS4_HMSB).",
        "# Ikony: nazwa -> (szerokosc, wysokosc, bitmapa GS4_HMSB).",
        "",
        "FONTS = {",
    ]

    for name, (file, size, chars) in FONTS.items():
        h, chars, widths, data = font(os.path.join(args.fonts, file), size, chars)
        lines.append("    %r: (%d, %r, %r, %r)," % (name, h, chars, widths, data))

    lines += ["}", "", "ICONS = {"]

    for name, img in (("fan", fan_icon()), ("drop", drop_icon())):
        lines.append("    %r: (%d, %d, %r)," % (name, img.size[0], img.size[1], gs4(img)))

    lines += ["}", ""]

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("written", os.path.normpath(args.out), os.path.getsize(args.out), "bytes")


if __name__ == "__main__":
    main()
