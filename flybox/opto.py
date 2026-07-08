"""Optogenetic pulse trains via the shared PCA9685 — per channel, concurrent.

Each channel (red, blue) has its own worker thread and state, so red and blue can
stimulate independently and simultaneously (e.g. different trigger zones). Pulse
trains are software-gated ON/OFF; the "ON" level sets intensity.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict

from config import (OPTO_CHANNELS, OPTO_MAX_FREQ_HZ, OPTO_DEFAULT_INTENSITY,
                    OPTO_MAX_INTENSITY, OPTO_MAX_TRAIN_S)
from hardware import pca


@dataclass
class Protocol:
    frequency_hz: float = 20.0
    pulse_width_ms: float = 10.0
    train_duration_s: float = 2.0
    rest_s: float = 5.0
    n_bursts: int = 5
    channel: str = "red"
    intensity: float = OPTO_DEFAULT_INTENSITY

    def period_ms(self) -> float:
        return 1000.0 / self.frequency_hz if self.frequency_hz > 0 else 0.0

    def duty_cycle_pct(self) -> float:
        if self.frequency_hz <= 0:
            return 100.0
        return (self.pulse_width_ms / self.period_ms()) * 100.0

    def validate(self) -> str | None:
        if self.channel not in OPTO_CHANNELS:
            return f"Unknown channel '{self.channel}'."
        if self.frequency_hz < 0 or self.frequency_hz > OPTO_MAX_FREQ_HZ:
            return f"Frequency must be 0..{OPTO_MAX_FREQ_HZ} Hz."
        if self.frequency_hz > 0 and self.pulse_width_ms > self.period_ms():
            return (f"Pulse width {self.pulse_width_ms} ms exceeds the period "
                    f"{self.period_ms():.2f} ms at {self.frequency_hz} Hz.")
        if not (0.0 <= self.intensity <= 1.0):
            return "Intensity must be 0..1."
        if self.intensity > OPTO_MAX_INTENSITY:
            return f"Intensity exceeds safety cap {OPTO_MAX_INTENSITY}."
        if self.train_duration_s > OPTO_MAX_TRAIN_S:
            return f"Burst duration exceeds safety cap {OPTO_MAX_TRAIN_S}s (thermal)."
        if self.n_bursts < 1:
            return "Need at least 1 burst."
        return None


class _Chan:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.running = False
        self.message = "idle"


class OptoController:
    def __init__(self):
        self._chan = {name: _Chan() for name in OPTO_CHANNELS}

    # ---- public API ----------------------------------------------------
    def run(self, proto: Protocol) -> str | None:
        err = proto.validate()
        if err:
            return err
        c = self._chan[proto.channel]
        if c.running:
            return f"{proto.channel} is already running."
        c.stop.clear()
        c.thread = threading.Thread(target=self._run, args=(proto, c), daemon=True)
        c.thread.start()
        return None

    def is_running(self, channel: str) -> bool:
        c = self._chan.get(channel)
        return bool(c and c.running)

    def flash(self, channel: str, intensity: float = 1.0, seconds: float = 0.5) -> str | None:
        """Quick manual pulse to verify an LED — briefly on, then off."""
        if channel not in OPTO_CHANNELS:
            return f"Unknown channel '{channel}'."
        if self.is_running(channel):
            return f"{channel} is running a protocol."
        lvl = max(0.0, min(OPTO_MAX_INTENSITY, float(intensity)))
        secs = max(0.05, min(OPTO_MAX_TRAIN_S, float(seconds)))

        def _flash():
            pca.set(OPTO_CHANNELS[channel], lvl)
            time.sleep(secs)
            pca.set(OPTO_CHANNELS[channel], 0.0)

        threading.Thread(target=_flash, daemon=True).start()
        return None

    def stop(self, channel: str | None = None):
        names = [channel] if channel else list(self._chan)
        for n in names:
            c = self._chan[n]
            c.stop.set()
            pca.set(OPTO_CHANNELS[n], 0.0)
            c.running = False
            c.message = "stopped"

    def cleanup(self):
        self.stop()

    @property
    def state(self):
        chans = {n: {"running": c.running, "message": c.message}
                 for n, c in self._chan.items()}
        running = [n for n, c in self._chan.items() if c.running]
        return {
            "hw": pca.hw,
            "running": bool(running),
            "message": ("; ".join(f"{n}: {self._chan[n].message}" for n in running)
                        if running else "idle"),
            "channels": chans,
        }

    # ---- worker --------------------------------------------------------
    def _run(self, proto: Protocol, c: _Chan):
        c.running = True
        c.message = "running"
        ch = proto.channel
        try:
            for i in range(1, proto.n_bursts + 1):
                if c.stop.is_set():
                    break
                c.message = f"burst {i}/{proto.n_bursts}: stim"
                self._pulse_train(proto, c)
                pca.set(OPTO_CHANNELS[ch], 0.0)
                if i < proto.n_bursts and not c.stop.is_set():
                    c.message = f"burst {i}/{proto.n_bursts}: rest"
                    self._sleep(proto.rest_s, c)
        finally:
            pca.set(OPTO_CHANNELS[ch], 0.0)
            c.running = False
            c.message = "aborted" if c.stop.is_set() else "complete"

    def _pulse_train(self, proto: Protocol, c: _Chan):
        ch = OPTO_CHANNELS[proto.channel]
        if proto.frequency_hz <= 0:
            pca.set(ch, proto.intensity)
            self._sleep(proto.train_duration_s, c)
            pca.set(ch, 0.0)
            return
        on_s = proto.pulse_width_ms / 1000.0
        off_s = (proto.period_ms() - proto.pulse_width_ms) / 1000.0
        end = time.perf_counter() + proto.train_duration_s
        while time.perf_counter() < end and not c.stop.is_set():
            pca.set(ch, proto.intensity)
            self._sleep(on_s, c)
            pca.set(ch, 0.0)
            self._sleep(off_s, c)

    def _sleep(self, seconds: float, c: _Chan):
        end = time.perf_counter() + seconds
        while True:
            remaining = end - time.perf_counter()
            if remaining <= 0 or c.stop.is_set():
                return
            time.sleep(min(remaining, 0.005))


controller = OptoController()
