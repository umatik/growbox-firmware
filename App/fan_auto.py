# fan_auto.py - MANUAL (vege): poziom wentylatora wg temperatury
#
# Ponizej "min" wentylator idzie na minLevel, od "max" + zapas na maxLevel,
# pomiedzy liniowo, w krokach co 10. Histereza: nowy poziom dopiero gdy
# temperatura ruszy sie o tyle od ostatniej zmiany - szum czujnika na
# granicy kroku nie szarpie wentylatorem.
import utime

import sensor

HEADROOM = 1
STEP = 10
HYSTERESIS = 0.3
# starszy odczyt = czujnik padl, nie sterujemy na slepo
MAX_SENSOR_AGE_S = 120

_last_temp = None


def enabled(config):
    return (
        not config.get("auto", {}).get("enabled", False)
        and config.get("fanAuto", {}).get("enabled", False)
    )


def target(cfg, temperature, light_on):
    band = cfg["day"] if light_on else cfg["night"]

    low = band["min"]
    high = band["max"] + HEADROOM
    ratio = max(0, min(1, (temperature - low) / (high - low)))

    level = cfg["minLevel"] + (cfg["maxLevel"] - cfg["minLevel"]) * ratio
    return int(round(level / STEP) * STEP)


def apply(config, light_on, dimmer, force=False):
    global _last_temp

    if not enabled(config):
        return

    data = sensor.get()
    temperature = data["temperature"]

    if temperature is None or utime.time() - data["ts"] > MAX_SENSOR_AGE_S:
        return

    level = target(config["fanAuto"], temperature, light_on)
    current = dimmer.get_level()

    if level == current:
        return

    # histereza tylko dla sasiedniego kroku - skok (np. zmiana dzien/noc)
    # przechodzi od razu
    if (
            not force
            and abs(level - current) <= STEP
            and _last_temp is not None
            and abs(temperature - _last_temp) < HYSTERESIS
    ):
        return

    print("FAN AUTO:", temperature, "C ->", level, "%")
    _last_temp = temperature
    dimmer.set_level(level)
