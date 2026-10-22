#!/usr/bin/env python3
# preview.py - rysuje ekran kodem z src/ (ui.py, model.py) na komputerze
# i zapisuje PNG. Z przykladowymi danymi albo z prawdziwego ESP:
#
#   pip install pillow
#   python3 tools/preview.py                       # przykladowe dane
#   python3 tools/preview.py --esp 192.168.18.100 --token <API_TOKEN>
#   python3 tools/preview.py --boot "Łączę z WiFi"
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# namiastka framebuf z tools/ przed src/
sys.path[:0] = [HERE, os.path.join(HERE, "..", "src")]

import framebuf  # noqa: E402
import model  # noqa: E402
import ui  # noqa: E402
from PIL import Image  # noqa: E402


def sample(now):
    """Kwitnienie od 4.10 21:50, swiatlo 18-06, podlane 3 dni temu."""
    fed = model.format_local(now - 3 * model.DAY - 3600)
    response = {
        "config": {
            "auto": {"enabled": True, "floweringStartDate": "2026-10-04",
                     "floweringStartedAt": "2026-10-04 21:50:00"},
            "relayLight": {"state": 1},
            "relayFan": {"state": 1},
            "lightSchedule": [{"on": "18:00", "off": "06:00"}],
            "humidifier": {"auto": {"enabled": True, "min": 45, "max": 50},
                           "manual": {"enabled": False, "min": 58, "max": 65}},
            "feeding": {"count": 3, "history": [fed[:10] + "T" + fed[11:] + ".000Z"]},
        },
        "sensor": {"temperature": 25.8, "humidity": 45.9},
        "status": {"mode": "AUTO", "fanLevel": 70,
                   "humidifier": {"online": True, "want": False, "on": False}},
    }
    return response


def fetch(host, token, path):
    req = urllib.request.Request("http://%s%s" % (host, path),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def to_png(fb_buf, path, scale):
    img = Image.new("RGB", (ui.W, ui.H))
    px = img.load()

    for y in range(ui.H):
        for x in range(ui.W):
            i = (y * ui.W + x) * 2
            # bufor ma bajty jak na ekranie: starszy pierwszy
            c = (fb_buf[i] << 8) | fb_buf[i + 1]
            px[x, y] = ((c >> 8) & 0xF8, (c >> 3) & 0xFC, (c << 3) & 0xF8)

    img.resize((ui.W * scale, ui.H * scale), Image.NEAREST).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esp")
    ap.add_argument("--token")
    ap.add_argument("--boot")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="preview.png")
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args()

    utc = int(time.time()) - 946684800  # sekundy od 2000 w UTC
    offset = model.polish_utc_offset(utc)
    now = utc + offset

    buf = bytearray(ui.W * ui.H * 2)
    fb = framebuf.FrameBuffer(buf, ui.W, ui.H, framebuf.RGB565)

    if args.boot:
        ui.draw_boot(fb, args.boot, "192.168.18.100")
    else:
        if args.esp:
            response = fetch(args.esp, args.token, "/api/config")
        else:
            response = sample(now)

        ui.draw(fb, model.build(response, now, offset, args.offline))

    to_png(buf, args.out, args.scale)
    print("written", args.out)


if __name__ == "__main__":
    main()
