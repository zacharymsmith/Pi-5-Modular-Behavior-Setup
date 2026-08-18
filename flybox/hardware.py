"""Shared PCA9685 bus — the single owner of the I2C device.

Driven with a tiny DIRECT-REGISTER smbus2 driver — no Adafruit Blinka / CircuitPython,
so nothing here ever touches a gpiochip. That's deliberate: Blinka's Pi-5 gpiochip
auto-detection breaks when an `i2c-gpio` overlay adds an extra gpiochip (e.g. the
environmental sensor on its own dedicated bus), which used to knock the PCA9685 into
mock and kill the lights/opto. Talking I2C directly sidesteps that entirely and drops
the fragile Blinka + lgpio dependency, so the sensor can live on its own bus 3 while the
PCA9685 stays on bus 1 — both working at once.

Falls back to an in-memory mock when smbus2 / the device isn't present, so the app runs
on a laptop.
"""
from __future__ import annotations

import time
import threading

from config import PCA9685_I2C_ADDRESS, PCA9685_I2C_BUS, PCA9685_PWM_FREQ_HZ

# PCA9685 registers / mode bits
_MODE1, _MODE2, _PRESCALE, _LED0_ON_L = 0x00, 0x01, 0xFE, 0x06
_AI, _SLEEP, _RESTART, _ALLCALL, _OUTDRV = 0x20, 0x10, 0x80, 0x01, 0x04

try:
    from smbus2 import SMBus
    _HAVE_SMBUS = True
except Exception as e:                       # pragma: no cover - depends on host
    _HAVE_SMBUS = False
    _IMPORT_ERR = str(e)


class PCA9685Bus:
    """0..1 level per channel, thread-safe, with a mock fallback. No Blinka/gpiochip."""

    def __init__(self):
        self._lock = threading.Lock()
        self.channels = [0.0] * 16
        self._bus = None
        self._addr = PCA9685_I2C_ADDRESS
        if not _HAVE_SMBUS:
            self.hw = False
            self.message = f"mock PCA9685 (no smbus2: {_IMPORT_ERR})"
            return
        try:
            self._bus = SMBus(PCA9685_I2C_BUS)
            self._bus.read_byte_data(self._addr, _MODE1)     # probe: raises if not on the bus
            self._init_chip()
            self.hw = True
            self.message = f"PCA9685 @ 0x{self._addr:02x} on i2c-{PCA9685_I2C_BUS}"
        except Exception as e:                                # device not on the bus
            try:
                if self._bus:
                    self._bus.close()
            except Exception:
                pass
            self._bus = None
            self.hw = False
            self.message = f"PCA9685 not responding on i2c-{PCA9685_I2C_BUS} ({e})"

    # ---- chip setup ----
    def _init_chip(self):
        b, a = self._bus, self._addr
        b.write_byte_data(a, _MODE2, _OUTDRV)                 # totem-pole outputs
        b.write_byte_data(a, _MODE1, _ALLCALL)
        time.sleep(0.005)
        self._set_freq(PCA9685_PWM_FREQ_HZ)

    def _set_freq(self, freq_hz):
        b, a = self._bus, self._addr
        prescale = int(round(25_000_000.0 / (4096 * float(freq_hz))) - 1)
        prescale = max(3, min(255, prescale))
        old = b.read_byte_data(a, _MODE1)
        b.write_byte_data(a, _MODE1, (old & ~_RESTART) | _SLEEP)   # sleep to set prescale
        b.write_byte_data(a, _PRESCALE, prescale)
        b.write_byte_data(a, _MODE1, old)
        time.sleep(0.005)
        b.write_byte_data(a, _MODE1, old | _RESTART | _AI)         # restart + auto-increment

    def _set_pwm(self, ch, on, off):
        self._bus.write_i2c_block_data(
            self._addr, _LED0_ON_L + 4 * ch,
            [on & 0xFF, (on >> 8) & 0xFF, off & 0xFF, (off >> 8) & 0xFF])

    # ---- public API (unchanged) ----
    def set(self, channel: int, level: float):
        """level 0.0..1.0"""
        level = 0.0 if level < 0 else 1.0 if level > 1 else float(level)
        ch = int(channel)
        with self._lock:
            self.channels[ch] = level
            if self._bus is not None:
                try:
                    if level <= 0.0:
                        self._set_pwm(ch, 0, 0x1000)          # full off (bit 12)
                    elif level >= 1.0:
                        self._set_pwm(ch, 0x1000, 0)          # full on (bit 12)
                    else:
                        self._set_pwm(ch, 0, int(level * 4095))
                except Exception as e:
                    self.hw = False
                    self.message = f"PCA9685 write failed ({e})"

    def get(self, channel: int) -> float:
        return self.channels[int(channel)]

    def all_off(self):
        for ch in range(16):
            self.set(ch, 0.0)


# single shared instance
pca = PCA9685Bus()
