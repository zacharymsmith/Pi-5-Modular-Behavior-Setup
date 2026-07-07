"""Session scheduler — run a timed experiment plan automatically.

A plan is a list of phases, each with a duration and an action:
  {"name": "baseline",  "duration_s": 60, "action": "none"}
  {"name": "red stim",  "duration_s": 10, "action": "stim",  "channel": "red"}
  {"name": "white on",  "duration_s": 30, "action": "light", "light": "white", "level": 0.6}

Stimulation uses the closed-loop channel protocols. Phase transitions are logged
to the active session so the timeline is reproducible. Recording/session start &
stop are handled by the caller via on_complete (see app.py).
"""
from __future__ import annotations

import time
import threading

from session import logger as session


class Scheduler:
    def __init__(self, opto, lights, loop):
        self.opto = opto
        self.lights = lights
        self.loop = loop
        self._thread = None
        self._stop = threading.Event()
        self.running = False
        self.phase = "—"
        self.remaining_s = 0.0
        self.phases = []
        self.on_complete = None

    def run(self, phases, on_complete=None) -> str | None:
        if self.running:
            return "Scheduler already running."
        if not phases:
            return "No phases provided."
        self.phases = phases
        self.on_complete = on_complete
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return None

    def _run(self):
        self.running = True
        try:
            for ph in self.phases:
                if self._stop.is_set():
                    break
                self.phase = ph.get("name", "phase")
                dur = float(ph.get("duration_s", 0))
                self._do(ph)
                if session.running:
                    session.log_event("scheduler", ph.get("channel", ""),
                                      self._proto_dict(ph),
                                      detail=f"phase:{self.phase}")
                end = time.time() + dur
                while time.time() < end and not self._stop.is_set():
                    self.remaining_s = round(end - time.time(), 1)
                    time.sleep(0.1)
                self._undo(ph)
        finally:
            self.running = False
            self.phase = "done" if not self._stop.is_set() else "stopped"
            self.remaining_s = 0.0
            if self.on_complete:
                try:
                    self.on_complete()
                except Exception:
                    pass

    def _proto_dict(self, ph):
        if ph.get("action") == "stim":
            ch = ph.get("channel", "red")
            p = self.loop.protocols.get(ch)
            if p:
                return {"frequency_hz": p.frequency_hz, "pulse_width_ms": p.pulse_width_ms,
                        "train_duration_s": p.train_duration_s, "intensity": p.intensity}
        return {}

    def _do(self, ph):
        a = ph.get("action")
        if a == "stim":
            ch = ph.get("channel", "red")
            if ch in self.loop.protocols:
                self.opto.run(self.loop.protocols[ch])
        elif a == "light":
            self.lights.set_light(ph.get("light", "white"), float(ph.get("level", 1.0)))

    def _undo(self, ph):
        if ph.get("action") == "light":
            self.lights.set_light(ph.get("light", "white"), 0.0)

    def stop(self):
        self._stop.set()

    def status(self):
        return {"running": self.running, "phase": self.phase,
                "remaining_s": self.remaining_s, "n_phases": len(self.phases)}
