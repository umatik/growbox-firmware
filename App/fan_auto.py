# fan_auto.py - poziom wentylatora wg czujnika
#
# MANUAL (vege), temperatura. Preferowane niskie obroty: do srodka pasma
# (sweet point) wentylator stoi na minLevel, od srodka do "max" + zapas
# rosnie liniowo do maxLevel, w krokach co 10. Histereza: nowy poziom
# dopiero gdy temperatura ruszy sie o tyle od ostatniej zmiany - szum
# czujnika na granicy kroku nie szarpie wentylatorem.
#
# AUTO (kwitnienie): ta sama krzywa z osobnymi progami (fanAutoFlower),
# bo lampa grzeje wtedy mocniej. Noc z wlaczonym nightFan: wilgotnosc nie moze
# przekraczac sweet pointu. Powyzej auto.nightHumidity.ideal obroty rosna
# od nocnego poziomu do maxLevel przy "max". Wracaja do nocnego poziomu
# dopiero HUM_HYSTERESIS ponizej sweet pointu.
import utime

import humidifier
import sensor

HEADROOM = 1
STEP = 10
HYSTERESIS = 0.3
HUM_HYSTERESIS = 2
# starszy odczyt = czujnik padl, nie sterujemy na slepo
MAX_SENSOR_AGE_S = 120

_last_temp = None
_night_humid = False


def active_key(config):
    """Klucz ustawien automatu dla biezacego trybu."""
    if config.get("auto", {}).get("enabled", False):
        return "fanAutoFlower"
    return "fanAuto"


def _cfg(config):
    cfg = config.get(active_key(config))
    return cfg if cfg and cfg.get("enabled", False) else None


def enabled(config):
    return _cfg(config) is not None


def _ratio(value, low, high):
    return max(0, min(1, (value - low) / (high - low)))


def _step(level):
    return int(round(level / STEP) * STEP)


def _fresh(data):
    return utime.time() - data["ts"] <= MAX_SENSOR_AGE_S


def target(cfg, temperature, light_on):
    band = cfg["day"] if light_on else cfg["night"]

    ideal = (band["min"] + band["max"]) / 2
    ratio = _ratio(temperature, ideal, band["max"] + HEADROOM)

    level = cfg["minLevel"] + (cfg["maxLevel"] - cfg["minLevel"]) * ratio
    return _step(level)


def apply(config, light_on, dimmer, force=False):
    global _last_temp

    cfg = _cfg(config)

    if cfg is None:
        return

    data = sensor.get()
    temperature = data["temperature"]

    if temperature is None or not _fresh(data):
        return

    level = humidifier.fan_cap(
        config,
        target(cfg, temperature, light_on),
        temperature
    )

    # AUTO noc: wilgotnosc ponad sweet point moze wymusic wiecej
    if config["auto"]["enabled"] and not light_on:
        humid_level = night_humidity_level(config, level)

        if humid_level:
            level = max(level, humid_level)
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


def night_humidity_level(config, base_level):
    # AUTO noc: poziom wentylatora przy wilgotnosci ponad sweet point,
    # None = wilgotnosc w normie albo brak swiezego odczytu
    global _night_humid

    band = config.get("auto", {}).get("nightHumidity")
    data = sensor.get()
    humidity = data["humidity"]

    if not band or humidity is None or not _fresh(data):
        _night_humid = False
        return None

    was_humid = _night_humid

    if humidity > band["ideal"]:
        _night_humid = True
    elif humidity < band["ideal"] - HUM_HYSTERESIS:
        _night_humid = False

    if _night_humid != was_humid:
        print("NIGHT HUMIDITY:", humidity, "% ->", _night_humid)

    if not _night_humid:
        return None

    max_level = (_cfg(config) or config["fanAuto"])["maxLevel"]
    ratio = _ratio(humidity, band["ideal"], band["max"])
    return _step(base_level + (max_level - base_level) * ratio)
