"""Closed-loop controller: tracking -> trigger -> optogenetic stimulation.

Wired as the camera's frame callback. Every frame it runs the tracker, draws the
overlay + trigger zone, and — when enabled and a tracked point is inside the
trigger zone — fires an opto protocol (rate-limited by a cooldown).

The trigger zone is stored in NORMALIZED coordinates (0..1) so it's independent
of preview vs. process resolution; the UI sends normalized values.
"""
from __future__ import annotations

import time

import cv2

from config import CLOSED_LOOP_COOLDOWN_S, PROCESS_SIZE
from opto import Protocol, controller as opto


class ClosedLoop:
    def __init__(self, tracker):
        self.tracker = tracker
        self.enabled = False
        self.cooldown_s = CLOSED_LOOP_COOLDOWN_S
        self.trigger_roi = None            # (nx1, ny1, nx2, ny2) normalized, or None
        self.protocol = Protocol(channel="red", frequency_hz=20, pulse_width_ms=10,
                                 train_duration_s=1.0, rest_s=0.0, n_bursts=1)
        self._last_fire = 0.0
        self.fires = 0
        self.last_event = "—"

    # ---- called on every camera frame ---------------------------------
    def on_frame(self, frame_bgr):
        # only pay the CPU cost when tracking is on
        if not self.tracker.enabled:
            return frame_bgr
        annotated, pts = self.tracker.process(frame_bgr)
        h, w = frame_bgr.shape[:2]

        inside = False
        if self.trigger_roi is not None:
            x1, y1, x2, y2 = self._roi_px(w, h)
            armed = self.enabled
            color = (0, 165, 255) if armed else (120, 120, 120)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(annotated, "trigger" if armed else "trigger (off)",
                        (x1 + 4, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 1)
            inside = any(x1 <= px <= x2 and y1 <= py <= y2 for px, py in pts)

        if self.enabled and inside:
            self._maybe_fire()
            cv2.rectangle(annotated, (0, 0), (w - 1, h - 1), (0, 0, 255), 4)
        return annotated

    def _roi_px(self, w, h):
        nx1, ny1, nx2, ny2 = self.trigger_roi
        x1, x2 = sorted((int(nx1 * w), int(nx2 * w)))
        y1, y2 = sorted((int(ny1 * h), int(ny2 * h)))
        return x1, y1, x2, y2

    def _maybe_fire(self):
        now = time.time()
        if now - self._last_fire < self.cooldown_s:
            return
        if opto.state.get("running"):
            return
        self._last_fire = now
        self.fires += 1
        self.last_event = time.strftime("%H:%M:%S")
        opto.run(self.protocol)

    # ---- config from the UI -------------------------------------------
    def set_roi(self, nx1, ny1, nx2, ny2):
        self.trigger_roi = (float(nx1), float(ny1), float(nx2), float(ny2))

    def clear_roi(self):
        self.trigger_roi = None

    def status(self):
        return {
            "enabled": self.enabled,
            "cooldown_s": self.cooldown_s,
            "trigger_roi": self.trigger_roi,
            "fires": self.fires,
            "last_event": self.last_event,
            "protocol": {
                "channel": self.protocol.channel,
                "frequency_hz": self.protocol.frequency_hz,
                "pulse_width_ms": self.protocol.pulse_width_ms,
                "train_duration_s": self.protocol.train_duration_s,
                "intensity": self.protocol.intensity,
            },
        }
