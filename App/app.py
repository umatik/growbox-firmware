import uasyncio as asyncio

import button
import controller
import display
import esp_config
import sensor
from ntp import sync_ntp
from server import (
    clock_scheduler,
    get_display_state,
    start_server,
)
from wifi import connect_wifi

fan_button = button.Button(
    pin=esp_config.PINS["btn_fan"],
    on_release=controller.toggle_fan,
)

light_button = button.Button(
    pin=esp_config.PINS["btn_light"],
    on_release=controller.toggle_light,
)


async def main():
    display.init()

    display.set_state_provider(
        get_display_state
    )

    sensor.init()

    await connect_wifi()
    await sync_ntp()

    await asyncio.gather(
        start_server(),
        clock_scheduler(),
        display.display_updater(),
        sensor.sensor_task(),
    )


asyncio.run(main())
