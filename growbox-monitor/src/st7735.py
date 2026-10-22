# st7735.py - sterownik ST7735 (TFT 1.8" 128x160, SPI) z buforem ramki.
# Caly obraz (40 KB RGB565) rysujemy w RAM-ie i wysylamy jednym zapisem.
#
# Moduly 1.8" maja rozne "zakladki" (black / green / red tab): przesuniecie
# obrazu o 1-2 piksele i kolejnosc RGB/BGR. Ustawia sie je w config.py,
# sprawdza test.py.
import framebuf
import utime
from machine import Pin

_SWRESET = 0x01
_SLPOUT = 0x11
_NORON = 0x13
_INVOFF = 0x20
_INVON = 0x21
_DISPOFF = 0x28
_DISPON = 0x29
_CASET = 0x2A
_RASET = 0x2B
_RAMWR = 0x2C
_MADCTL = 0x36
_COLMOD = 0x3A

# MADCTL: MY/MX obracaja obraz o 180 st., bit 0x08 = kolejnosc BGR
_MY = 0x80
_MX = 0x40
_BGR = 0x08

# sekwencja jak Adafruit initR (komenda, dane, opoznienie ms)
_INIT = (
    (0xB1, b"\x01\x2C\x2D", 0),  # FRMCTR1
    (0xB2, b"\x01\x2C\x2D", 0),  # FRMCTR2
    (0xB3, b"\x01\x2C\x2D\x01\x2C\x2D", 0),  # FRMCTR3
    (0xB4, b"\x07", 0),  # INVCTR
    (0xC0, b"\xA2\x02\x84", 0),  # PWCTR1
    (0xC1, b"\xC5", 0),  # PWCTR2
    (0xC2, b"\x0A\x00", 0),  # PWCTR3
    (0xC3, b"\x8A\x2A", 0),  # PWCTR4
    (0xC4, b"\x8A\xEE", 0),  # PWCTR5
    (0xC5, b"\x0E", 0),  # VMCTR1
    (_COLMOD, b"\x05", 0),  # 16 bitow na piksel
    (0xE0, b"\x02\x1C\x07\x12\x37\x32\x29\x2D\x29\x25\x2B\x39\x00\x01\x03\x10", 0),
    (0xE1, b"\x03\x1D\x07\x06\x2E\x2C\x29\x2D\x2E\x2E\x37\x3F\x00\x00\x02\x10", 0),
    (_NORON, b"", 10),
)


class ST7735(framebuf.FrameBuffer):
    def __init__(self, spi, cs, dc, rst, width=128, height=160,
                 x_offset=0, y_offset=0, bgr=False, flip=True, invert=False):
        self.spi = spi
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.dc = Pin(dc, Pin.OUT, value=0)
        self.rst = Pin(rst, Pin.OUT, value=1)
        self.width = width
        self.height = height
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.buffer = bytearray(width * height * 2)
        super().__init__(self.buffer, width, height, framebuf.RGB565)

        madctl = (_MY | _MX if flip else 0) | (_BGR if bgr else 0)
        self._init(madctl, invert)

    def _cmd(self, cmd, data=b""):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes((cmd,)))

        if data:
            self.dc(1)
            self.spi.write(data)

        self.cs(1)

    def _init(self, madctl, invert):
        self.rst(0)
        utime.sleep_ms(20)
        self.rst(1)
        utime.sleep_ms(150)

        self._cmd(_SWRESET)
        utime.sleep_ms(150)
        self._cmd(_SLPOUT)
        utime.sleep_ms(255)

        for cmd, data, delay in _INIT:
            self._cmd(cmd, data)

            if delay:
                utime.sleep_ms(delay)

        self._cmd(_INVON if invert else _INVOFF)
        self._cmd(_MADCTL, bytes((madctl,)))
        self.fill(0)
        self.show()
        self._cmd(_DISPON)
        utime.sleep_ms(100)

    def power(self, on):
        """Wylaczony = czarny ekran, nawet gdy LED wisi na stale na 3V3."""
        self._cmd(_DISPON if on else _DISPOFF)

    def show(self):
        x0 = self.x_offset
        y0 = self.y_offset
        x1 = x0 + self.width - 1
        y1 = y0 + self.height - 1
        self._cmd(_CASET, bytes((0, x0, 0, x1)))
        self._cmd(_RASET, bytes((0, y0, 0, y1)))
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes((_RAMWR,)))
        self.dc(1)
        self.spi.write(self.buffer)
        self.cs(1)
