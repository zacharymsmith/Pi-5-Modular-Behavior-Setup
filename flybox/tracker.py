"""OpenCV fly tracker — classical, CPU-cheap, real-time on the Pi 5.

Threshold the grayscale frame, find contours, return centroids. Deliberately
lightweight (no ML) so it can run in the capture loop and drive closed-loop
stimulation. Heavy pose models belong in offline re-analysis, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import cv2

from config import TRACK_THRESHOLD, TRACK_INVERT, TRACK_MIN_AREA, TRACK_MAX_BLOBS


@dataclass
class Tracker:
    enabled: bool = False
    threshold: int = TRACK_THRESHOLD
    invert: bool = TRACK_INVERT
    min_area: int = TRACK_MIN_AREA
    max_blobs: int = TRACK_MAX_BLOBS
    points: List[Tuple[int, int]] = field(default_factory=list)

    def process(self, frame_bgr):
        """Return (annotated_bgr, points). points are (x, y) in frame pixels."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mode = cv2.THRESH_BINARY_INV if self.invert else cv2.THRESH_BINARY
        _, th = cv2.threshold(gray, int(self.threshold), 255, mode)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # largest blobs first, capped
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        pts: List[Tuple[int, int]] = []
        annotated = frame_bgr.copy()
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
        cv2.putText(annotated, f"{len(pts)} tracked", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
        return annotated, pts

    def settings(self):
        return {"enabled": self.enabled, "threshold": self.threshold,
                "invert": self.invert, "min_area": self.min_area,
                "count": len(self.points)}
