# test.py - sprawdzenie podlaczenia i wariantu wyswietlacza, bez WiFi:
#   mpremote connect /dev/cu.usbmodem21101 cp src/config.py src/st7735.py :
#   mpremote connect /dev/cu.usbmodem21101 run src/test.py
#
# Powinno byc widac: biala ramke dokladnie na krawedziach, napis GORA
# u gory i paski CZERWONY / ZIELONY / NIEBIESKI w tych kolorach.
# - zamienione czerwony i niebieski -> TFT_BGR = True
# - napis do gory nogami -> TFT_FLIP odwrotnie
# - ramka ucieta albo smieci przy krawedzi -> TFT_X_OFFSET / TFT_Y_OFFSET (2, 1)
# - kolory w negatywie -> TFT_INVERT = True
from machine import PWM, SPI, Pin

import config
from st7735 import ST7735


def rgb(r, g, b):
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)


spi = SPI(1, baudrate=config.SPI_BAUDRATE, sck=Pin(config.PIN_SCK),
          mosi=Pin(config.PIN_MOSI), miso=Pin(config.PIN_MISO))
tft = ST7735(spi, config.PIN_CS, config.PIN_DC, config.PIN_RST,
             x_offset=config.TFT_X_OFFSET, y_offset=config.TFT_Y_OFFSET,
             bgr=config.TFT_BGR, flip=config.TFT_FLIP, invert=config.TFT_INVERT)

if config.PIN_LED is not None:
    PWM(Pin(config.PIN_LED), freq=1000).duty_u16(65535)

WHITE = rgb(255, 255, 255)
tft.fill(rgb(0, 0, 0))
tft.rect(0, 0, 128, 160, WHITE)
tft.text("GORA", 48, 8, WHITE)

for i, (name, color) in enumerate((
        ("CZERWONY", rgb(255, 0, 0)),
        ("ZIELONY", rgb(0, 255, 0)),
        ("NIEBIESKI", rgb(0, 0, 255)),
)):
    y = 30 + i * 40
    tft.fill_rect(8, y, 112, 28, color)
    tft.text(name, 64 - len(name) * 4, y + 10, rgb(0, 0, 0))

tft.show()
print("test screen drawn")
