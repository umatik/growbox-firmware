# esp_config.py - konfiguracja pinów

PINS = {
    # 5V
    "relay_light": 23,
    "relay_fan": 22,

    # 5V
    "dimmer_pwm": 21,

    # 3.3V
    "btn_light_led": 25,
    "btn_light": 13,

    # 3.3V
    "btn_fan_led": 26,
    "btn_fan": 14,

    # 3.3V
    "btn_auto_led": 17,
    "btn_auto": 27,

    # 3.3V
    "sensor_sda": 32,
    "sensor_scl": 33,

    # 5V
    "oled_sda": 18,
    "oled_scl": 19,
}

DIMMER_DEFAULTS = {
    "enabled": True,
    "day_level": 60,
    "night_level": 30,
    "max_level": 78,
    "freq": 200,
}
