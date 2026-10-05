# config_store.py - zapis i odczyt konfiguracji z flash
import json

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "auto": {
        "enabled": False,
        "floweringStartDate": None,
        # "YYYY-MM-DD HH:MM:SS" czasu lokalnego, ustawiane razem z data
        "floweringStartedAt": None,
        # swiatlo OFF: wentylator na dimmer.night.level (bez automatu)
        "nightFan": {"enabled": False},
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
    # AUTO (kwitnienie): to samo co fanAuto, ale osobne progi - lampa idzie
    # wtedy na ok. 80 % mocy zamiast 40-50 % i grzeje duzo mocniej.
    # W nocy z nightFan obroty nie spadaja ponizej poziomu od wilgotnosci.
    "fanAutoFlower": {
        "enabled": True,
        "minLevel": 40,
        "maxLevel": 100,
        "day": {"min": 23, "max": 26},
        "night": {"min": 20, "max": 22}
    },
    # nawilzacz na mini ESP (humidifier.py), tylko przy zapalonym swietle:
    # wlacza ponizej "min", wylacza od "max", osobno MANUAL i AUTO. W czasie
    # pracy automatyka wentylatora nie idzie ponad fan.maxLevel, chyba ze
    # temperatura przekroczy fan.tempLimit.
    "humidifier": {
        "peer": "10003baf0ae8",
        "manual": {"enabled": False, "min": 60, "max": 65},
        "auto": {"enabled": False, "min": 45, "max": 50},
        "fan": {"maxLevel": 40, "tempLimit": 27}
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
