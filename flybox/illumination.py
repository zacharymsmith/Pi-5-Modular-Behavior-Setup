"""Illumination: white / IR strips via the shared PCA9685 bus.

Timing isn't critical here (steady illumination), so PCA9685 PWM is perfect.
"""
from __future__ import annotations

from config import LIGHT_CHANNELS
from hardware import pca


class Illumination:
    def __init__(self):
        self.levels = {name: 0.0 for name in LIGHT_CHANNELS}

    @property
    def hw(self) -> bool:
        return pca.hw

    @property
    def message(self) -> str:
        return pca.message

    def set_light(self, name: str, level: float) -> str | None:
        if name not in LIGHT_CHANNELS:
            return f"Unknown light '{name}'."
        level = max(0.0, min(1.0, float(level)))
        pca.set(LIGHT_CHANNELS[name], level)
        self.levels[name] = level
        return None

    def set_raw_channel(self, ch: int, level: float):
        """Discovery/identify: drive a bare PCA9685 channel to learn what it does."""
        pca.set(int(ch), float(level))

    def all_off(self):
        for name, ch in LIGHT_CHANNELS.items():
            pca.set(ch, 0.0)
            self.levels[name] = 0.0


lights = Illumination()
