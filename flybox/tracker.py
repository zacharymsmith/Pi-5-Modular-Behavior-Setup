"""Robust OpenCV fly tracker with identity-preserving trajectories.

Detection pipeline (kept cheap enough for real-time on a Pi 5):
  grayscale -> blur -> threshold (auto/Otsu or manual) -> morphological open
  -> contours -> area filter -> centroids -> greedy nearest-neighbor IDs.

Auto-threshold (Otsu) adapts to lighting so it "just works" on both the live
camera and the simulated feed. `auto_tune()` inspects the current frame and picks
the polarity (dark-on-light vs light-on-dark) and threshold automatically.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict

import cv2
import numpy as np

from config import (TRACK_THRESHOLD, TRACK_INVERT, TRACK_MIN_AREA, TRACK_MAX_AREA,
                    TRACK_MAX_BLOBS, TRACK_MATCH_DIST_PX, TRACK_AUTO_THRESHOLD,
                    TRAIL_ENABLED, TRAIL_LENGTH)

_ID_COLORS = [(0, 230, 0), (0, 200, 255), (255, 160, 0), (255, 80, 200),
              (0, 255, 255), (200, 100, 255), (120, 255, 120), (255, 255, 0)]
_KERNEL = np.ones((3, 3), np.uint8)


@dataclass
class Tracker:
    enabled: bool = False
    auto_threshold: bool = TRACK_AUTO_THRESHOLD
    threshold: int = TRACK_THRESHOLD
    invert: bool = TRACK_INVERT
    blur: bool = True
    min_area: int = TRACK_MIN_AREA
    max_area: int = TRACK_MAX_AREA
    max_blobs: int = TRACK_MAX_BLOBS
    match_dist: int = TRACK_MATCH_DIST_PX
    trails: bool = TRAIL_ENABLED
    trail_len: int = TRAIL_LENGTH
    computed_threshold: int = TRACK_THRESHOLD
    tracks: List[Dict] = field(default_factory=list)
    _prev: List[Dict] = field(default_factory=list)
    _next_id: int = 1
    _trailmap: Dict[int, deque] = field(default_factory=dict)

    # ---- segmentation --------------------------------------------------
    def _binarize(self, frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self.blur:
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
        mode = cv2.THRESH_BINARY_INV if self.invert else cv2.THRESH_BINARY
        if self.auto_threshold:
            val, th = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
            self.computed_threshold = int(val)
        else:
            _, th = cv2.threshold(gray, int(self.threshold), 255, mode)
            self.computed_threshold = int(self.threshold)
        return cv2.morphologyEx(th, cv2.MORPH_OPEN, _KERNEL)

    def _detect(self, frame_bgr):
        th = self._binarize(frame_bgr)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        pts = []
        for c in cnts:
            a = cv2.contourArea(c)
            if a < self.min_area or a > self.max_area:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            pts.append((int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])))
            if len(pts) >= self.max_blobs:
                break
        return pts

    # ---- auto-tune from a live frame -----------------------------------
    def auto_tune(self, frame_bgr):
        """Pick polarity + threshold from the current frame. The smaller pixel
        class after an Otsu split is assumed to be the flies (foreground)."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        val, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        dark = int((gray <= val).sum())
        light = int((gray > val).sum())
        self.invert = dark <= light          # dark minority -> flies are dark
        self.auto_threshold = True
        self.computed_threshold = int(val)
        n = len(self._detect(frame_bgr))
        return {"invert": self.invert, "threshold": int(val), "detected": n}

    # ---- identity association ------------------------------------------
    def _assign(self, pts):
        prev, tracks, used = list(self._prev), [], set()
        for (x, y) in pts:
            best, bestd = None, self.match_dist ** 2
            for p in prev:
                if p["id"] in used:
                    continue
                d = (x - p["x"]) ** 2 + (y - p["y"]) ** 2
                if d < bestd:
                    best, bestd = p, d
            if best is not None:
                used.add(best["id"])
                tracks.append({"id": best["id"], "x": x, "y": y})
            else:
                tracks.append({"id": self._next_id, "x": x, "y": y})
                self._next_id += 1
        return tracks

    def process(self, frame_bgr):
        annotated = frame_bgr.copy()
        if self.trails:
            self._draw_trails(annotated)

        tracks = self._assign(self._detect(frame_bgr))
        self._prev = tracks
        self.tracks = tracks

        live_ids = {t["id"] for t in tracks}
        for t in tracks:
            col = _ID_COLORS[t["id"] % len(_ID_COLORS)]
            if self.trails:
                dq = self._trailmap.setdefault(t["id"], deque(maxlen=self.trail_len))
                if dq.maxlen != self.trail_len:
                    dq = deque(dq, maxlen=self.trail_len)
                    self._trailmap[t["id"]] = dq
                dq.append((t["x"], t["y"]))
            cv2.circle(annotated, (t["x"], t["y"]), 10, col, 2)
            cv2.putText(annotated, str(t["id"]), (t["x"] + 8, t["y"] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
        for gid in [k for k in self._trailmap if k not in live_ids]:
            if not self.trails:
                self._trailmap.pop(gid, None)

        tag = f"{len(tracks)} tracked  (thr {self.computed_threshold}{'*' if self.auto_threshold else ''})"
        cv2.putText(annotated, tag, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
        return annotated, tracks

    def _draw_trails(self, img):
        for gid, dq in self._trailmap.items():
            if len(dq) < 2:
                continue
            col = _ID_COLORS[gid % len(_ID_COLORS)]
            pts = list(dq)
            for i in range(1, len(pts)):
                cv2.line(img, pts[i - 1], pts[i], col, 1)

    def clear_trails(self):
        self._trailmap.clear()

    def settings(self):
        return {"enabled": self.enabled, "auto_threshold": self.auto_threshold,
                "threshold": self.threshold, "computed_threshold": self.computed_threshold,
                "invert": self.invert, "min_area": self.min_area, "max_area": self.max_area,
                "trails": self.trails, "trail_len": self.trail_len,
                "count": len(self.tracks)}
