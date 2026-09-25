from time import sleep_ms

from micropython import const

_CMD_TIMEOUT = const(100)
_TOKEN_DATA = const(0xFE)


class SDCard:
    def __init__(self, spi, cs, baudrate=40000):
        self.spi = spi
        self.cs = cs
        self.cs.init(cs.OUT, value=1)

        self.cmdbuf = bytearray(6)
        self.dummy = bytearray([0xFF])

        # cdv = mnożnik adresu bloku:
        #   512 -> karta SDSC, adresowanie bajtowe (arg = block_num * 512)
        #     1 -> karta SDHC/SDXC, adresowanie blokowe (arg = block_num)
        # Ustawiane poprawnie w _init_card().
        self.cdv = 512

        # Liczba sektorów (bloków 512-bajtowych) na karcie.
        # Wyliczane w _read_csd() na podstawie rejestru CSD karty.
        self.sectors = 0

        self.spi.init(
            baudrate=baudrate,
            polarity=0,
            phase=0
        )

        self._init_card()

    def _clock(self, count=1):
        for _ in range(count):
            self.spi.write(self.dummy)

    def _read_response(self, timeout=_CMD_TIMEOUT):
        for _ in range(timeout):
            response = self.spi.read(1, 0xFF)[0]

            if not (response & 0x80):
                return response

        return 0xFF

    def _cmd(self, cmd, arg=0, crc=0x01):
        self.cmdbuf[0] = 0x40 | cmd
        self.cmdbuf[1] = (arg >> 24) & 0xFF
        self.cmdbuf[2] = (arg >> 16) & 0xFF
        self.cmdbuf[3] = (arg >> 8) & 0xFF
        self.cmdbuf[4] = arg & 0xFF
        self.cmdbuf[5] = crc

        self.cs.value(0)
        self.spi.write(self.cmdbuf)

        return self._read_response()

    def _end_command(self):
        self.cs.value(1)
        self.spi.write(self.dummy)

    def _init_card(self):
        # At least 74 clock cycles with CS HIGH
        self.cs.value(1)
        self._clock(10)

        # CMD0 - GO_IDLE_STATE
        response = self._cmd(0, 0, 0x95)
        self._end_command()

        if response != 1:
            raise OSError("no SD card: CMD0")

        # CMD8 - SEND_IF_COND
        response = self._cmd(8, 0x1AA, 0x87)

        if response == 1:
            # R7 response
            self.spi.read(4, 0xFF)
            self._end_command()

            # ACMD41 with HCS
            for _ in range(1000):
                response = self._cmd(55, 0)
                self._end_command()

                if response > 1:
                    continue

                response = self._cmd(41, 0x40000000)
                self._end_command()

                if response == 0:
                    break

                sleep_ms(1)

            else:
                raise OSError("SD init timeout: ACMD41")

            # CMD58 - READ_OCR
            response = self._cmd(58, 0)

            if response == 0:
                ocr = self.spi.read(4, 0xFF)
                # Bit CCS (bit 30 OCR, tj. bit 0x40 pierwszego bajtu)
                # mówi, czy karta jest blokowo (SDHC/SDXC) czy bajtowo
                # (SDSC) adresowana.
                self.cdv = 1 if (ocr[0] & 0x40) else 512
            else:
                self.cdv = 512

            self._end_command()

        else:
            # Legacy SDSC card
            self._end_command()
            self.cdv = 512

            for _ in range(1000):
                response = self._cmd(55, 0)
                self._end_command()

                response = self._cmd(41, 0)
                self._end_command()

                if response == 0:
                    break

                sleep_ms(1)

            else:
                raise OSError(
                    "SD init timeout: legacy ACMD41"
                )

        # CMD9 - SEND_CSD: odczyt pojemności karty
        self._read_csd()

        # 512-byte blocks
        response = self._cmd(16, 512)
        self._end_command()

        if response != 0:
            raise OSError("SD block size error")

        # Faster SPI after initialization
        self.spi.init(
            baudrate=400000,
            polarity=0,
            phase=0
        )

    def _read_csd(self):
        response = self._cmd(9, 0)

        if response != 0:
            self._end_command()
            raise OSError("SD CSD read error")

        if not self._wait_for_token(_TOKEN_DATA):
            self._end_command()
            raise OSError("SD CSD read timeout")

        csd = bytearray(16)
        self.spi.readinto(csd)
        self.spi.read(2, 0xFF)

        self._end_command()

        if (csd[0] & 0xC0) == 0x40:
            # CSD version 2.0 (SDHC / SDXC)
            c_size = (csd[7] << 16) | (csd[8] << 8) | csd[9]
            self.sectors = (c_size + 1) * 1024
        else:
            # CSD version 1.0 (SDSC)
            c_size = (
                    ((csd[6] & 0x03) << 10)
                    | (csd[7] << 8)
                    | (csd[8] >> 6)
            )
            c_size_mult = (
                    ((csd[9] & 0x03) << 1) | (csd[10] >> 7)
            )
            capacity = (
                    (c_size + 1) * (2 ** (c_size_mult + 2)) * 512
            )
            self.sectors = capacity // 512

    def _wait_for_token(self, token):
        for _ in range(_CMD_TIMEOUT * 10):
            if self.spi.read(1, 0xFF)[0] == token:
                return True

        return False

    def readblocks(self, block_num, buf):
        nblocks = len(buf) // 512

        if nblocks == 1:
            response = self._cmd(
                17,
                block_num * self.cdv
            )

            if response != 0:
                self._end_command()
                raise OSError("SD read error")

            if not self._wait_for_token(_TOKEN_DATA):
                self._end_command()
                raise OSError("SD read timeout")

            self.spi.readinto(buf)
            self.spi.read(2, 0xFF)

            self._end_command()
            return

        response = self._cmd(
            18,
            block_num * self.cdv
        )

        if response != 0:
            self._end_command()
            raise OSError("SD multi-read error")

        for offset in range(0, len(buf), 512):
            if not self._wait_for_token(_TOKEN_DATA):
                self._end_command()
                raise OSError(
                    "SD multi-read timeout"
                )

            self.spi.readinto(
                memoryview(buf)[offset:offset + 512]
            )

            self.spi.read(2, 0xFF)

        self._cmd(12, 0)
        self._end_command()

    def writeblocks(self, block_num, buf):
        nblocks = len(buf) // 512

        if nblocks == 1:
            response = self._cmd(
                24,
                block_num * self.cdv
            )

            if response != 0:
                self._end_command()
                raise OSError("SD write error")

            self.spi.write(bytes([_TOKEN_DATA]))
            self.spi.write(buf)
            self.spi.write(b"\xFF\xFF")

            response = self.spi.read(
                1,
                0xFF
            )[0]

            if (response & 0x1F) != 0x05:
                self._end_command()
                raise OSError("SD write rejected")

            while self.spi.read(1, 0xFF)[0] == 0:
                pass

            self._end_command()
            return

        response = self._cmd(
            25,
            block_num * self.cdv
        )

        if response != 0:
            self._end_command()
            raise OSError("SD multi-write error")

        for offset in range(0, len(buf), 512):
            self.spi.write(bytes([_TOKEN_DATA]))

            self.spi.write(
                memoryview(buf)[offset:offset + 512]
            )

            self.spi.write(b"\xFF\xFF")

            response = self.spi.read(
                1,
                0xFF
            )[0]

            if (response & 0x1F) != 0x05:
                self._end_command()
                raise OSError(
                    "SD multi-write rejected"
                )

            while self.spi.read(1, 0xFF)[0] == 0:
                pass

        self.spi.write(bytes([0xFD]))
        self._end_command()

    def ioctl(self, op, arg):
        if op == 4:
            # Liczba bloków (sektorów) na karcie - wcześniej było
            # na sztywno 512, co dawało "dysk" o pojemności 256KB
            # niezależnie od realnej karty.
            return self.sectors

        if op == 5:
            # Rozmiar bloku w bajtach - wcześniej było 0, co psuło
            # montowanie systemu plików.
            return 512

        if op == 6:
            # Erase block: 0 = karta nie wymaga kasowania przed zapisem.
            return 0

        return 0
