"""Illumination control: PCA9685 (I2C) -> 4-channel MOSFET board -> 12V strips.

Timing is not critical here (steady illumination / backlight), so PCA9685 PWM is
ideal. Falls back to a mock if the I2C hardware/library isn't present.

NOTE: which PCA9685 channel drives which physical load depends on your wiring,
which may currently be jumbled. Use the discovery helper (`sweep()` / the web
"Identify" buttons) to confirm the LIGHT_CHANNELS map in config.py empirically.
"""
from __future__ import annotations

from config import (
    PCA9685_I2C_ADDRESS,
    PCA9685_PWM_FREQ_HZ,
    LIGHT_CHANNELS,
)

try:
    import board
    import busio
    from adafruit_pca9685 import PCA9685
    _HW_I2C = True
except Exception as e:  # pragma: no cover
    _HW_I2C = False
    _IMPORT_ERR = str(e)


class _MockPCA:
    def __init__(self):
        self.frequency = 0
        self.channels = [type("Ch", (), {"duty_cycle": 0})() for _ in range(16)]


class Illumination:
    def __init__(self):
        self.hw = _HW_I2C
        if _HW_I2C:
            i2c = busio.I2C(board.SCL, board.SDA)
            self._pca = PCA9685(i2c, address=PCA9685_I2C_ADDRESS)
            self._pca.frequency = PCA9685_PWM_FREQ_HZ
            self.message = "PCA9685 linked"
        else:
            self._pca = _MockPCA()
            self.message = f"MockPCA (no I2C: {_IMPORT_ERR})"
        self.levels = {name: 0.0 for name in LIGHT_CHANNELS}

    def set_light(self, name: str, level: float) -> str | None:
        """level: 0.0..1.0 brightness."""
        if name not in LIGHT_CHANNELS:
            return f"Unknown light '{name}'."
        level = max(0.0, min(1.0, float(level)))
        ch = LIGHT_CHANNELS[name]
        # PCA9685 duty_cycle is a 16-bit value (0..65535)
        self._pca.channels[ch].duty_cycle = int(level * 0xFFFF)
        self.levels[name] = level
        return None

    def set_raw_channel(self, ch: int, level: float):
        """Directly drive a PCA9685 channel 0..15 — used by discovery/identify."""
        level = max(0.0, min(1.0, float(level)))
        self._pca.channels[int(ch)].duty_cycle = int(level * 0xFFFF)

    def all_off(self):
        for ch in range(16):
            self._pca.channels[ch].duty_cycle = 0
        self.levels = {name: 0.0 for name in LIGHT_CHANNELS}

    def sweep(self, on_level: float = 1.0, dwell_s: float = 1.5):
        """CLI discovery: light each PCA channel one at a time so you can see
        which physical load it maps to. Run with: python -c 'import illumination,\
 time; illumination.lights.sweep()'"""
        import time
        for ch in range(16):
            self.all_off()
            self.set_raw_channel(ch, on_level)
            print(f"PCA9685 channel {ch} ON — note what lit up")
            time.sleep(dwell_s)
        self.all_off()


lights = Illumination()
