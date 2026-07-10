"""Shared PCA9685 bus — the single owner of the I2C device.

Both illumination (strips) and opto (LEDs) talk through this one object, so
there's never a second handle fighting over the same I2C bus. Falls back to a
mock (in-memory channel values) when the hardware/library isn't present, so the
whole app runs on a laptop.
"""
from __future__ import annotations

import threading

from config import PCA9685_I2C_ADDRESS, PCA9685_PWM_FREQ_HZ

try:
    import board
    import busio
    from adafruit_pca9685 import PCA9685
    _HW = True
except Exception as e:  # pragma: no cover - depends on host
    _HW = False
    _IMPORT_ERR = str(e)


class PCA9685Bus:
    """0..1 level per channel, thread-safe, with a mock fallback."""

    def __init__(self):
        self._lock = threading.Lock()
        self.channels = [0.0] * 16
        if _HW:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self._pca = PCA9685(i2c, address=PCA9685_I2C_ADDRESS)
                self._pca.frequency = PCA9685_PWM_FREQ_HZ
                self.hw = True
                self.message = f"PCA9685 @ 0x{PCA9685_I2C_ADDRESS:02x}"
            except Exception as e:  # device not on the bus
                self._pca = None
                self.hw = False
                self.message = f"PCA9685 not responding ({e})"
        else:
            self._pca = None
            self.hw = False
            self.message = f"mock PCA9685 ({_IMPORT_ERR})"

    def set(self, channel: int, level: float):
        """level 0.0..1.0"""
        level = 0.0 if level < 0 else 1.0 if level > 1 else float(level)
        ch = int(channel)
        with self._lock:
            self.channels[ch] = level
            if self._pca is not None:
                self._pca.channels[ch].duty_cycle = int(level * 0xFFFF)

    def get(self, channel: int) -> float:
        return self.channels[int(channel)]

    def all_off(self):
        for ch in range(16):
            self.set(ch, 0.0)


# single shared instance
pca = PCA9685Bus()
