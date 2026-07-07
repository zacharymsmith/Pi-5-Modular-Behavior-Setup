"""Optogenetic pulse-train control via true hardware PWM into the PicoBuck.

Timing matters here, so we use rpi-hardware-pwm (RP1 hardware PWM on the Pi 5)
rather than software PWM. If the library or hardware is unavailable (e.g. you're
developing on a laptop), a MockPWM is used so the app still runs.

Protocol is parameterized the experimentally-clear way:
    frequency_hz, pulse_width_ms, train_duration_s, rest_s, n_bursts
Duty cycle is derived internally.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict

from config import (
    OPTO_PWM_CHIP,
    OPTO_CHANNELS,
    OPTO_MAX_FREQ_HZ,
)

try:
    from rpi_hardware_pwm import HardwarePWM
    _HW_PWM = True
except Exception as e:  # pragma: no cover - depends on host
    _HW_PWM = False
    _IMPORT_ERR = str(e)


class MockPWM:
    """Stand-in so the app runs off-Pi. Logs instead of driving a pin."""

    def __init__(self, pwm_channel, hz, chip):
        self.channel, self.hz, self.chip = pwm_channel, hz, chip
        self._running = False

    def start(self, duty):
        self._running = True
        print(f"[MockPWM ch{self.channel}] start duty={duty}%")

    def stop(self):
        self._running = False
        print(f"[MockPWM ch{self.channel}] stop")

    def change_frequency(self, hz):
        self.hz = hz

    def change_duty_cycle(self, duty):
        print(f"[MockPWM ch{self.channel}] duty={duty}%")


@dataclass
class Protocol:
    frequency_hz: float = 20.0
    pulse_width_ms: float = 10.0
    train_duration_s: float = 2.0
    rest_s: float = 5.0
    n_bursts: int = 5
    channel: str = "blue_left"

    def duty_cycle_pct(self) -> float:
        if self.frequency_hz <= 0:
            return 100.0  # continuous ON
        period_ms = 1000.0 / self.frequency_hz
        return (self.pulse_width_ms / period_ms) * 100.0

    def validate(self) -> str | None:
        if self.channel not in OPTO_CHANNELS:
            return f"Unknown channel '{self.channel}'."
        if self.frequency_hz < 0 or self.frequency_hz > OPTO_MAX_FREQ_HZ:
            return f"Frequency must be 0..{OPTO_MAX_FREQ_HZ} Hz."
        if self.frequency_hz > 0 and self.duty_cycle_pct() > 100.0:
            period_ms = 1000.0 / self.frequency_hz
            return (f"Pulse width {self.pulse_width_ms} ms exceeds the period "
                    f"{period_ms:.2f} ms at {self.frequency_hz} Hz.")
        if self.n_bursts < 1:
            return "Need at least 1 burst."
        return None


class OptoController:
    """Owns one PWM handle per configured channel and runs protocols in a thread."""

    def __init__(self):
        self._pwm: dict[str, object] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = {"running": False, "message": "idle",
                      "hw_pwm": _HW_PWM}
        if not _HW_PWM:
            self.state["message"] = f"MockPWM (no hardware PWM: {_IMPORT_ERR})"

    def _handle(self, channel: str, hz: float):
        """Lazily create/reuse a PWM handle for a channel."""
        if channel not in self._pwm:
            ch = OPTO_CHANNELS[channel]
            cls = HardwarePWM if _HW_PWM else MockPWM
            pwm = cls(pwm_channel=ch, hz=max(hz, 1), chip=OPTO_PWM_CHIP)
            pwm.start(0)
            self._pwm[channel] = pwm
        return self._pwm[channel]

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
        pwm = self._handle(proto.channel, proto.frequency_hz)
        duty = proto.duty_cycle_pct()
        try:
            for i in range(1, proto.n_bursts + 1):
                if self._stop.is_set():
                    break
                self.state["message"] = f"burst {i}/{proto.n_bursts}: stimulating"
                if proto.frequency_hz > 0:
                    pwm.change_frequency(proto.frequency_hz)
                    pwm.change_duty_cycle(duty)
                else:
                    pwm.change_duty_cycle(100)  # continuous
                if self._wait(proto.train_duration_s):
                    break
                pwm.change_duty_cycle(0)  # burst off
                if i < proto.n_bursts:
                    self.state["message"] = f"burst {i}/{proto.n_bursts}: resting"
                    if self._wait(proto.rest_s):
                        break
        finally:
            pwm.change_duty_cycle(0)
            self.state.update(running=False,
                              message="aborted" if self._stop.is_set() else "complete")

    def _wait(self, seconds: float) -> bool:
        """Sleep in small slices; return True if stop was requested."""
        end = time.time() + seconds
        while time.time() < end:
            if self._stop.is_set():
                return True
            time.sleep(0.02)
        return False

    def stop(self):
        self._stop.set()
        for pwm in self._pwm.values():
            pwm.change_duty_cycle(0)
        self.state.update(running=False, message="stopped by user")

    def cleanup(self):
        self.stop()
        for pwm in self._pwm.values():
            try:
                pwm.stop()
            except Exception:
                pass
        self._pwm.clear()


# module-level singleton
controller = OptoController()
