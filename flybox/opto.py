"""Optogenetic pulse-train control via the PCA9685 (ch2 red / ch3 blue).

Your PicoBuck PWM inputs are wired to the PCA9685, so opto is driven over I2C on
the same board as the strips. We generate the pulse train by gating the channel
fully ON/OFF in software (leaving the PCA9685's shared carrier frequency free for
flicker-free strip dimming). The PCA9685 duty during "ON" sets LED intensity.

Timing note: software/I2C gating has ~1-few ms jitter, which is fine for typical
opto pulses (>= a few ms). For sub-ms precision you'd rewire to hardware PWM.

Protocol is parameterized the experimentally-clear way:
    frequency_hz, pulse_width_ms, train_duration_s, rest_s, n_bursts, intensity
Duty (carrier) is not the stimulation frequency — the software gate is.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from config import OPTO_CHANNELS, OPTO_MAX_FREQ_HZ, OPTO_DEFAULT_INTENSITY

# Reuse the single shared PCA9685 instance owned by illumination.py so we don't
# open two conflicting handles on the same I2C device.
from illumination import lights


@dataclass
class Protocol:
    frequency_hz: float = 20.0
    pulse_width_ms: float = 10.0
    train_duration_s: float = 2.0
    rest_s: float = 5.0
    n_bursts: int = 5
    channel: str = "red"
    intensity: float = OPTO_DEFAULT_INTENSITY  # 0..1

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
        if self.n_bursts < 1:
            return "Need at least 1 burst."
        return None


class OptoController:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = {"running": False, "message": "idle", "hw": lights.hw}
        if not lights.hw:
            self.state["message"] = "MOCK (no PCA9685 I2C)"

    def _set(self, channel: str, level: float):
        lights.set_raw_channel(OPTO_CHANNELS[channel], level)

    def run(self, proto: Protocol) -> str | None:
        err = proto.validate()
        if err:
            return err
        if self.state["running"]:
            return "A protocol is already running."
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_protocol, args=(proto,), daemon=True)
        self._thread.start()
        return None

    def _run_protocol(self, proto: Protocol):
        self.state.update(running=True, message="running")
        ch = proto.channel
        try:
            for i in range(1, proto.n_bursts + 1):
                if self._stop.is_set():
                    break
                self.state["message"] = f"burst {i}/{proto.n_bursts}: stimulating"
                self._pulse_train(proto)
                self._set(ch, 0.0)  # ensure off between bursts
                if i < proto.n_bursts and not self._stop.is_set():
                    self.state["message"] = f"burst {i}/{proto.n_bursts}: resting"
                    self._sleep(proto.rest_s)
        finally:
            self._set(ch, 0.0)
            self.state.update(running=False,
                              message="aborted" if self._stop.is_set() else "complete")

    def _pulse_train(self, proto: Protocol):
        """Gate the channel on/off for train_duration_s at the requested freq."""
        ch = proto.channel
        if proto.frequency_hz <= 0:
            # continuous ON for the burst duration
            self._set(ch, proto.intensity)
            self._sleep(proto.train_duration_s)
            self._set(ch, 0.0)
            return
        on_s = proto.pulse_width_ms / 1000.0
        off_s = (proto.period_ms() - proto.pulse_width_ms) / 1000.0
        end = time.perf_counter() + proto.train_duration_s
        while time.perf_counter() < end and not self._stop.is_set():
            self._set(ch, proto.intensity)
            self._sleep(on_s)
            self._set(ch, 0.0)
            self._sleep(off_s)

    def _sleep(self, seconds: float):
        """Sleep that wakes early on stop; uses perf_counter for better accuracy."""
        end = time.perf_counter() + seconds
        while True:
            remaining = end - time.perf_counter()
            if remaining <= 0 or self._stop.is_set():
                return
            time.sleep(min(remaining, 0.005))

    def stop(self):
        self._stop.set()
        for name in OPTO_CHANNELS:
            self._set(name, 0.0)
        self.state.update(running=False, message="stopped by user")

    def cleanup(self):
        self.stop()


controller = OptoController()
