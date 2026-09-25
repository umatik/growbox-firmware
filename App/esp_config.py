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
}

DIMMER_DEFAULTS = {
    "enabled": True,
    "day_level": 60,
    "night_level": 30,
    "max_level": 78,
    "freq": 200,
}
