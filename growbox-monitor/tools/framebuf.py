# framebuf.py - namiastka modulu framebuf z MicroPythona dla podgladu na
# komputerze (tools/preview.py). Tylko to, czego uzywa src/: RGB565
# i GS4_HMSB, fill, fill_rect, pixel, hline, vline, line, ellipse, blit.
# Zachowanie jak w extmod/modframebuf.c (klucz blit porownywany po palecie).

RGB565 = 1
GS4_HMSB = 2


class FrameBuffer:
    def __init__(self, buf, width, height, fmt):
        self.buf = buf
        self.width = width
        self.height = height
        self.fmt = fmt

    def _get(self, x, y):
        if self.fmt == RGB565:
            i = (y * self.width + x) * 2
            return self.buf[i] | (self.buf[i + 1] << 8)

        b = self.buf[(y * ((self.width + 1) // 2)) + x // 2]
        return (b >> 4) if x % 2 == 0 else (b & 0x0F)

    def _set(self, x, y, c):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return

        if self.fmt == RGB565:
            i = (y * self.width + x) * 2
            self.buf[i] = c & 0xFF
            self.buf[i + 1] = (c >> 8) & 0xFF
        else:
            raise NotImplementedError

    def pixel(self, x, y, c=None):
        if c is None:
            return self._get(x, y)
        self._set(x, y, c)

    def fill(self, c):
        self.fill_rect(0, 0, self.width, self.height, c)

    def fill_rect(self, x, y, w, h, c):
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                self._set(xx, yy, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def line(self, x0, y0, x1, y1, c):
        # Bresenham
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy

        while True:
            self._set(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def ellipse(self, cx, cy, xr, yr, c, f=False, m=0xF):
        # przyblizenie: piksele wewnatrz elipsy w wybranych cwiartkach
        for dy in range(-yr, yr + 1):
            for dx in range(-xr, xr + 1):
                if (dx * dx) / max(1, xr * xr) + (dy * dy) / max(1, yr * yr) > 1.0:
                    continue
                q = (1 if dx >= 0 and dy <= 0 else 0) | (2 if dx <= 0 and dy <= 0 else 0) \
                    | (4 if dx <= 0 and dy >= 0 else 0) | (8 if dx >= 0 and dy >= 0 else 0)
                if q & m:
                    self._set(cx + dx, cy + dy, c)

    def blit(self, src, x, y, key=-1, palette=None):
        for sy in range(src.height):
            for sx in range(src.width):
                col = src._get(sx, sy)
                if palette is not None:
                    col = palette._get(col, 0)
                if col != key:
                    self._set(x + sx, y + sy, col)
