"""OpenCV fly tracker — classical, CPU-cheap, real-time on the Pi 5.

Threshold the grayscale frame, find contours, return centroids, and optionally
draw fading motion trails (a temporal overlay of recent positions).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import List, Tuple

import cv2

from config import (TRACK_THRESHOLD, TRACK_INVERT, TRACK_MIN_AREA, TRACK_MAX_BLOBS,
                    TRAIL_ENABLED, TRAIL_LENGTH)


@dataclass
class Tracker:
    enabled: bool = False
    threshold: int = TRACK_THRESHOLD
    invert: bool = TRACK_INVERT
    min_area: int = TRACK_MIN_AREA
    max_blobs: int = TRACK_MAX_BLOBS
    trails: bool = TRAIL_ENABLED
    trail_len: int = TRAIL_LENGTH
    points: List[Tuple[int, int]] = field(default_factory=list)
    _history: deque = field(default_factory=lambda: deque(maxlen=TRAIL_LENGTH))

    def process(self, frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mode = cv2.THRESH_BINARY_INV if self.invert else cv2.THRESH_BINARY
        _, th = cv2.threshold(gray, int(self.threshold), 255, mode)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)

        pts: List[Tuple[int, int]] = []
        annotated = frame_bgr.copy()

        # trails first, so live markers draw on top
        if self.trails:
            self._draw_trails(annotated)

        for c in cnts:
            if cv2.contourArea(c) < self.min_area:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            x = int(M["m10"] / M["m00"])
            y = int(M["m01"] / M["m00"])
            pts.append((x, y))
            cv2.circle(annotated, (x, y), 10, (0, 230, 0), 2)
            cv2.drawMarker(annotated, (x, y), (0, 230, 0), cv2.MARKER_CROSS, 14, 1)
            if len(pts) >= self.max_blobs:
                break

        self.points = pts
        if self.trails:
            if self._history.maxlen != self.trail_len:
                self._history = deque(self._history, maxlen=self.trail_len)
            self._history.append(pts)

        cv2.putText(annotated, f"{len(pts)} tracked", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
        return annotated, pts

    def _draw_trails(self, img):
        n = len(self._history)
        for i, frame_pts in enumerate(self._history):
            fade = (i + 1) / max(n, 1)           # older = dimmer
            col = (int(60 * fade), int(220 * fade), int(220 * fade))
            for (x, y) in frame_pts:
                cv2.circle(img, (x, y), 2, col, -1)

    def clear_trails(self):
        self._history.clear()

    def settings(self):
        return {"enabled": self.enabled, "threshold": self.threshold,
                "invert": self.invert, "min_area": self.min_area,
                "trails": self.trails, "trail_len": self.trail_len,
                "count": len(self.points)}
