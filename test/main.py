import os

from machine import Pin, SPI

import sdcard

# Piny VSPI na typowej płytce ESP32 DevKit.
# Jesli Twoja płytka ma inny układ, zmien numery GPIO ponizej.
spi = SPI(
    2,
    baudrate=400000,
    polarity=0,
    phase=0,
    sck=Pin(27),
    mosi=Pin(26),
    miso=Pin(25),
)

cs = Pin(4)

sd = sdcard.SDCard(spi, cs)

print("Karta wykryta, liczba sektorow:", sd.sectors)
print("Pojemnosc karty: {:.1f} MB".format(sd.sectors * 512 / 1024 / 1024))

# Montowanie karty jako systemu plikow FAT pod /sd
os.mount(sd, "/sd")

print("Pliki na karcie:", os.listdir("/sd"))

# Szybki test zapisu/odczytu
with open("/sd/test.txt", "w") as f:
    f.write("Hello from ESP32!\n")

with open("/sd/test.txt") as f:
    print("Zawartosc test.txt:", f.read())

os.umount("/sd")
print("Test zakonczony, karta odmontowana.")
