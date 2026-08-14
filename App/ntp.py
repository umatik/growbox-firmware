import machine
import ntptime
import uasyncio as asyncio
import utime

import display


def polish_utc_offset(year, month, day):
    def last_sunday(y, m):
        for d in range(31, 24, -1):
            try:
                wd = utime.localtime(
                    utime.mktime(
                        (y, m, d, 0, 0, 0, 0, 0)
                    )
                )[6]

                if wd == 6:
                    return d

            except:
                pass

    march = last_sunday(year, 3)
    october = last_sunday(year, 10)

    if (
            (month > 3 and month < 10)
            or (month == 3 and day >= march)
            or (month == 10 and day < october)
    ):
        return 2

    return 1


async def sync_ntp():
    await asyncio.sleep(2)

    ntptime.host = "pool.ntp.org"

    synced = False

    for _ in range(3):
        try:
            ntptime.settime()

            print("NTP synced")
            display.text("NTP synced", 0, 0, clear=True)

            synced = True

            t = utime.localtime()

            offset = polish_utc_offset(
                t[0],
                t[1],
                t[2]
            )

            now = utime.time() + offset * 3600
            tm = utime.localtime(now)

            machine.RTC().datetime((
                tm[0],
                tm[1],
                tm[2],
                tm[6] + 1,
                tm[3],
                tm[4],
                tm[5],
                0
            ))

            break

        except Exception as e:
            print(
                "NTP retry failed:",
                e
            )
            display.text("NTP retry failed: " + str(e), 0, 0, clear=True)

            await asyncio.sleep(2)

    if not synced:
        print("NTP failed after retries")
        display.text("NTP failed", 0, 0, clear=True)
