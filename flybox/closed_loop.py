"""Closed-loop tracking stub (Phase 4).

Keep this simple and CPU-cheap: classical OpenCV centroid tracking on the LOW-RES
preview stream, not heavy pose models. When a trigger condition is met (e.g. fly
enters a region), fire an opto protocol.

This is a stub: fill in `detect()` and `trigger_condition()` for your assay.
"""
from __future__ import annotations

import threading
import time

from opto import controller as opto_controller, Protocol

try:
    import cv2
    import numpy as np
    _HAS_CV = True
except Exception:
    _HAS_CV = False


class ClosedLoop:
    def __init__(self, camera):
        self.camera = camera
        self._thread = None
        self._stop = threading.Event()
        self.running = False
        self.last_centroid = None

    def detect(self, frame_bgr):
        """Return (x, y) centroid of the largest dark blob (fly on light bg), or None.
        Tune threshold/inversion for your arena + IR backlight."""
        if not _HAS_CV:
            return None
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY_INV)
        cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        c = max(cnts, key=cv2.contourArea)
        M = cv2.moments(c)
        if M["m00"] == 0:
            return None
        return (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]))

    def trigger_condition(self, centroid) -> bool:
        """Example: trigger when the fly is in the left half of the frame.
        Replace with your real assay logic."""
        if centroid is None:
            return False
        x, _ = centroid
        return x < self.camera and False  # placeholder — always False until you edit

    def _loop(self):
        self.running = True
        while not self._stop.is_set():
            # TODO: pull the latest frame, decode JPEG -> ndarray, run detect().
            # frame = decode(self.camera._buffer.read_latest())
            # self.last_centroid = self.detect(frame)
            # if self.trigger_condition(self.last_centroid):
            #     opto_controller.run(Protocol(frequency_hz=20, pulse_width_ms=10,
            #                                  train_duration_s=1, rest_s=0, n_bursts=1))
            time.sleep(0.03)
        self.running = False

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
