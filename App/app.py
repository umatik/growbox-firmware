import uasyncio as asyncio

import button
import display
import esp_config
import sd_logger
import sensor
from ntp import sync_ntp
from server import (
    clock_scheduler,
    get_display_state,
    start_server,
)
from wifi import connect_wifi


def toggle_lcd():
    display.toggle(
        not display.is_enabled()
    )


lcd_button = button.Button(
    pin=esp_config.PINS["lcd_button"],
    on_release=toggle_lcd,
)


async def main():
    display.init()

    display.set_state_provider(
        get_display_state
    )

    sensor.init()

    await connect_wifi()
    await sync_ntp()

    sd_logger.init(120)

    await asyncio.gather(
        start_server(),
        clock_scheduler(),
        display.display_updater(),
        sensor.sensor_task(),
        sd_logger.task(),
    )


asyncio.run(main())
