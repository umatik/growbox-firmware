# config_store.py - zapis i odczyt konfiguracji z flash
import json

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "auto": {
        "enabled": False,
        "floweringStartDate": None,
        "nightFan": {"enabled": False},
        # kwitnienie, swiatlo OFF: sweet point wilgotnosci i poziom, przy
        # ktorym wentylator idzie na maxLevel (fan_auto.night_humidity_level)
        "nightHumidity": {"ideal": 50, "max": 55}
    },
    "relayLight": {"state": 0},
    "relayFan": {"state": 0},
    "dimmer": {
        "enabled": True,
        "day": {"level": 50},
        "night": {"level": 30}
    },
    "lightSchedule": [
        {"on": "18:00", "off": "06:00"},
    ],
    "sensor": {
        "enabled": True,
        "interval": 10000
    },
    "display": {
        "enabled": False,
    },
    # MANUAL (vege): poziom wentylatora wg temperatury. Progi jak VEG_TARGETS
    # w aplikacji - swiatlo ON 24-26 C, swiatlo OFF 20-22 C.
    "fanAuto": {
        "enabled": False,
        "minLevel": 20,
        "maxLevel": 100,
        "day": {"min": 24, "max": 26},
        "night": {"min": 20, "max": 22}
    },
    # podlewanie zapisywane z aplikacji (POST /api/feeding), daty ISO
    "feeding": {
        "lastFedAt": None,
        "count": 0,
        "history": []
    }
}


def _deep_merge(target, source):
    for key in source:
        if isinstance(source[key], dict):
            target[key] = target.get(key, {})
            _deep_merge(target[key], source[key])
        elif isinstance(source[key], list):
            target[key] = source[key]
        else:
            target[key] = source[key]


def load():
    try:
        with open(CONFIG_FILE, "r") as f:
            saved = json.load(f)

        # deep merge z DEFAULT_CONFIG
        import json as _json
        merged = _json.loads(_json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, saved)
        return merged

    except Exception:
        # Brak pliku lub błąd - zapisz domyślną konfigurację
        save(DEFAULT_CONFIG)
        import json as _json
        return _json.loads(_json.dumps(DEFAULT_CONFIG))


def save(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except Exception as e:
        print("Config save error:", e)
