# dimmer.py
from machine import Pin, PWM


class Dimmer:
    def __init__(self, pin, enabled=True, level=0, freq=200, max_level=78, on_change=None):
        self._pwm = PWM(Pin(pin), freq=freq)
        self._enabled = enabled
        self._level = level
        self._freq = freq
        self._max_level = max_level
        self._on_change = on_change
        self._apply()

    def _apply(self):
        if not self._enabled:
            self._pwm.duty(0)
            return
        level = max(0, min(self._max_level, self._level))
        # scale 0..max_level -> 0..1023
        if self._max_level > 0:
            duty = int((level / self._max_level) * 1023)
        else:
            duty = 0
        self._pwm.duty(duty)

    def set_level(self, level):
        if not isinstance(level, (int, float)):
            return
        level = max(0, min(self._max_level, level))
        if self._level == level:
            return
        self._level = level
        self._apply()
        if self._on_change:
            self._on_change(self._level)

    def get_level(self):
        return self._level

    def enable(self):
        if self._enabled:
            return
        self._enabled = True
        self._apply()

    def disable(self):
        if not self._enabled:
            return
        self._enabled = False
        self._pwm.duty(0)

    def is_enabled(self):
        return self._enabled
