# esp_config.py - konfiguracja pinów

PINS = {
    # 5V
    "relay_light": 23,
    "relay_fan": 22,

    # 5V
    "dimmer_pwm": 21,

    # 3.3V
    "sensor_sda": 32,
    "sensor_scl": 33,

    # 5V
    "oled_sda": 18,
    "oled_scl": 19,

    # SD card
    "sd_cs": 4,
    "sd_sck": 27,
    "sd_mosi": 26,
    "sd_miso": 25,

    # LCD button
    "lcd_button": 15,
    "lcd_button_led": 16,
}

DIMMER_DEFAULTS = {
    "enabled": True,
    "day_level": 60,
    "night_level": 30,
    # Kalibracja (2026-09-26, wentylator na obecnym module):
    # przy 1 kHz: <=30% stoi, ~32% trzyma obroty, ~35% rusza z postoju,
    # 75% = full, >=80% niestabilnie (rozpedza sie i hamuje).
    # 200 Hz i 5 kHz dawaly mniej stabilne obroty.
    "freq": 1000,
    "min_pct": 33,   # poziom 0 w aplikacji
    "max_pct": 75,   # poziom 100 w aplikacji
    "kick_pct": 75,  # rozruch po wlaczeniu wentylatora
    "kick_ms": 2000,
}
