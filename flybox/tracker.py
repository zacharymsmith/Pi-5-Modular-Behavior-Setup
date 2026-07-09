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
                    TRACK_TOPHAT_KERNEL, TRACK_MAX_MISSED, TRACK_CONFIRM_FRAMES,
                    TRACK_EXPECTED_FLIES, TRACK_DETECT_MAX_W, TRAIL_ENABLED, TRAIL_LENGTH)

_ID_COLORS = [(0, 230, 0), (0, 200, 255), (255, 160, 0), (255, 80, 200),
              (0, 255, 255), (200, 100, 255), (120, 255, 120), (255, 255, 0)]
_KERNEL = np.ones((3, 3), np.uint8)


@dataclass
class Tracker:
    enabled: bool = False
    method: str = "tophat"       # "tophat" | "threshold" | "bgsub" | "adaptive"
    auto_threshold: bool = TRACK_AUTO_THRESHOLD
    threshold: int = TRACK_THRESHOLD
    invert: bool = TRACK_INVERT
    blur: bool = True
    clahe: bool = False          # local contrast enhancement (helps faint flies)
    _clahe: object = None
    tophat_kernel: int = TRACK_TOPHAT_KERNEL   # feature size for illumination-invariant method
    bgsub_var: int = 25          # MOG2 sensitivity (lower = more sensitive)
    adaptive_block: int = 51     # adaptive-threshold neighborhood (odd)
    adaptive_C: int = 5          # adaptive-threshold offset
    _bgsub: object = None
    _ref: object = None          # captured empty-arena reference (for refsub method)
    min_area: int = TRACK_MIN_AREA
    max_area: int = TRACK_MAX_AREA
    max_blobs: int = TRACK_MAX_BLOBS
    match_dist: int = TRACK_MATCH_DIST_PX
    max_missed: int = TRACK_MAX_MISSED         # frames to coast a lost track
    confirm_frames: int = TRACK_CONFIRM_FRAMES # new blob must persist this long to become a track
    expected_flies: int = TRACK_EXPECTED_FLIES # cap on reported flies (0 = unlimited)
    detect_max_w: int = TRACK_DETECT_MAX_W     # downscale detection to this width (0 = full res)
    trails: bool = TRAIL_ENABLED
    trail_len: int = TRAIL_LENGTH
    computed_threshold: int = TRACK_THRESHOLD
    roi: object = None          # {"cx","cy","rx","ry"} normalized ellipse, or None
    tracks: List[Dict] = field(default_factory=list)
    _prev: List[Dict] = field(default_factory=list)
    _next_id: int = 1
    _trailmap: Dict[int, deque] = field(default_factory=dict)
    _mask: object = None
    _mask_shape: object = None

    # ---- arena ROI (limit tracking to inside the dish) -----------------
    def set_arena(self, nx1, ny1, nx2, ny2, shape="ellipse"):
        x1, x2 = sorted((nx1, nx2))
        y1, y2 = sorted((ny1, ny2))
        self.roi = {"shape": shape, "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        self._mask = None

    def clear_arena(self):
        self.roi = None
        self._mask = None

    def _roi_px(self, w, h):
        r = self.roi
        return (int(r["x1"] * w), int(r["y1"] * h), int(r["x2"] * w), int(r["y2"] * h))

    def _get_mask(self, w, h):
        if self.roi is None:
            return None
        if self._mask is not None and self._mask_shape == (h, w):
            return self._mask
        m = np.zeros((h, w), np.uint8)
        x1, y1, x2, y2 = self._roi_px(w, h)
        if self.roi["shape"] == "rect":
            cv2.rectangle(m, (x1, y1), (x2, y2), 255, -1)
        else:
            cv2.ellipse(m, ((x1 + x2) // 2, (y1 + y2) // 2),
                        (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)), 0, 0, 360, 255, -1)
        self._mask, self._mask_shape = m, (h, w)
        return m

    # ---- segmentation --------------------------------------------------
    def reset_bg(self):
        """Re-learn the background model (for the background-subtraction method)."""
        self._bgsub = None

    def capture_background(self, frame_bgr):
        """Snapshot the current frame as the reference (use with an empty arena)."""
        gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        self._ref = gray
        return {"ok": True, "shape": list(gray.shape)}

    def build_background(self, frames_bgr):
        """Per-pixel MEDIAN of several frames -> a fly-free reference even when
        flies are present (they move, so the median at each pixel is the arena).
        Let the flies move around while these frames are captured."""
        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_bgr if f is not None]
        if len(grays) < 3:
            return {"ok": False, "error": "not enough frames"}
        med = np.median(np.stack(grays), axis=0).astype(np.uint8)
        self._ref = cv2.GaussianBlur(med, (5, 5), 0)
        return {"ok": True, "n_frames": len(grays)}

    def patch_background(self, frame_bgr, nx1, ny1, nx2, ny2):
        """Copy the CURRENT frame's pixels in a drawn box into the reference —
        drag over any spot with no fly to fix/refine the background there."""
        gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self._ref is None or self._ref.shape != gray.shape:
            self._ref = gray.copy()
        h, w = gray.shape
        x1, x2 = sorted((int(nx1 * w), int(nx2 * w)))
        y1, y2 = sorted((int(ny1 * h), int(ny2 * h)))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            self._ref[y1:y2, x1:x2] = gray[y1:y2, x1:x2]
        return {"ok": True}

    def _binarize(self, frame_bgr, scale=1.0):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self.blur:
            gray = cv2.GaussianBlur(gray, (5, 5), 0)
        if self.clahe:
            if self._clahe is None:
                self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = self._clahe.apply(gray)
        mode = cv2.THRESH_BINARY_INV if self.invert else cv2.THRESH_BINARY
        if self.method == "refsub":
            # difference from a captured reference — colour/lighting proof, keeps
            # stationary flies. Reference is resized to match the detection frame.
            if self._ref is None:
                th = np.zeros(gray.shape, np.uint8)
            else:
                ref = self._ref
                if ref.shape != gray.shape:
                    ref = cv2.resize(ref, (gray.shape[1], gray.shape[0]))
                diff = cv2.absdiff(gray, ref)
                if int(diff.max()) < 20:
                    th = np.zeros(gray.shape, np.uint8)      # nothing changed
                else:
                    _, th = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        elif self.method == "tophat":
            # remove large-scale illumination + rim glow, keep the small fly.
            # black-hat isolates dark spots on a bright bg; top-hat the reverse.
            k = max(9, int(self.tophat_kernel * scale) | 1)   # kernel scales with detect res
            ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            op = cv2.MORPH_BLACKHAT if self.invert else cv2.MORPH_TOPHAT
            feat = cv2.morphologyEx(gray, op, ker)
            _, th = cv2.threshold(feat, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            self.computed_threshold = 0
        elif self.method == "bgsub":
            # moving foreground vs learned static background — ignores fixed
            # rim shadows/reflections, which is ideal for cluttered arenas.
            if self._bgsub is None:
                self._bgsub = cv2.createBackgroundSubtractorMOG2(
                    history=200, varThreshold=int(self.bgsub_var), detectShadows=False)
            fg = self._bgsub.apply(gray)
            _, th = cv2.threshold(fg, 127, 255, cv2.THRESH_BINARY)
        elif self.method == "adaptive":
            # local threshold — robust to uneven illumination (bright centre / dark edge).
            blk = int(self.adaptive_block) | 1        # force odd
            th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       mode, max(3, blk), int(self.adaptive_C))
        else:  # threshold (Otsu or manual)
            if self.auto_threshold:
                val, th = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
                self.computed_threshold = int(val)
            else:
                _, th = cv2.threshold(gray, int(self.threshold), 255, mode)
                self.computed_threshold = int(self.threshold)
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, _KERNEL)
        mask = self._get_mask(th.shape[1], th.shape[0])   # limit to arena ROI
        if mask is not None:
            th = cv2.bitwise_and(th, mask)
        return th

    def _detect(self, frame_bgr, scale=1.0):
        th = self._binarize(frame_bgr, scale)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        min_a, max_a = self.min_area * scale * scale, self.max_area * scale * scale
        pts = []
        for c in cnts:
            a = cv2.contourArea(c)
            if a < min_a or a > max_a:
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
        """Try BOTH polarities with the current method and pick the one that finds
        the fewest sensible (1..max_blobs) fly-sized blobs — robust on coloured
        backgrounds where simple pixel-counting picks the wrong subject."""
        self.auto_threshold = True
        target = self.expected_flies or 1
        best = None                          # (score, invert, n)
        for inv in (True, False):
            self.invert = inv
            n = len(self._detect(frame_bgr))
            if 1 <= n <= self.max_blobs:
                score = abs(n - target)      # prefer count near expected, few blobs
            else:
                score = 1000 + (n if n > self.max_blobs else 500)  # 0 or too many = bad
            if best is None or score < best[0]:
                best = (score, inv, n)
        self.invert = best[1]
        n = len(self._detect(frame_bgr))
        return {"invert": self.invert, "threshold": int(self.computed_threshold),
                "detected": n}

    # ---- identity association (velocity-predicted -> fewer ID swaps) ----
    def _assign(self, pts):
        prev = list(self._prev)
        for p in prev:  # predict where each fly should be this frame
            p["px"] = p["x"] + p.get("vx", 0.0)
            p["py"] = p["y"] + p.get("vy", 0.0)
        tracks, used = [], set()
        a = 0.5  # velocity smoothing
        for (x, y) in pts:
            best, bestd = None, self.match_dist ** 2
            for p in prev:
                if p["id"] in used:
                    continue
                d = (x - p["px"]) ** 2 + (y - p["py"]) ** 2   # match to prediction
                if d < bestd:
                    best, bestd = p, d
            if best is not None:
                used.add(best["id"])
                vx = a * (x - best["x"]) + (1 - a) * best.get("vx", 0.0)
                vy = a * (y - best["y"]) + (1 - a) * best.get("vy", 0.0)
                tracks.append({"id": best["id"], "x": x, "y": y, "vx": vx, "vy": vy,
                               "speed": (vx * vx + vy * vy) ** 0.5,
                               "missed": 0, "coasting": False, "age": best.get("age", 0) + 1})
            else:
                tracks.append({"id": self._next_id, "x": x, "y": y, "vx": 0.0, "vy": 0.0,
                               "speed": 0.0, "missed": 0, "coasting": False, "age": 1})
                self._next_id += 1
        # coast unmatched previous tracks through the gap (holds identity + trigger)
        for p in prev:
            if p["id"] in used:
                continue
            missed = p.get("missed", 0) + 1
            if missed > self.max_missed:
                continue
            vx, vy = p.get("vx", 0.0) * 0.85, p.get("vy", 0.0) * 0.85
            tracks.append({"id": p["id"], "x": int(round(p["px"])), "y": int(round(p["py"])),
                           "vx": vx, "vy": vy, "speed": (vx * vx + vy * vy) ** 0.5,
                           "missed": missed, "coasting": True, "age": p.get("age", 0)})
        return tracks

    def process(self, frame_bgr):
        annotated = frame_bgr.copy()
        if self.roi is not None:   # show the arena boundary
            h, w = annotated.shape[:2]
            x1, y1, x2, y2 = self._roi_px(w, h)
            if self.roi["shape"] == "rect":
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 255, 0), 1)
            else:
                cv2.ellipse(annotated, ((x1 + x2) // 2, (y1 + y2) // 2),
                            (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)),
                            0, 0, 360, (255, 255, 0), 1)
        if self.trails:
            self._draw_trails(annotated)

        # detect on a downscaled copy (fast) while the caller keeps full res for
        # recording/preview — lets you record hi-res and track fast simultaneously
        fw = frame_bgr.shape[1]
        if self.detect_max_w and fw > self.detect_max_w:
            sc = self.detect_max_w / float(fw)
            det = cv2.resize(frame_bgr, (0, 0), fx=sc, fy=sc)
            pts = [(int(x / sc), int(y / sc)) for (x, y) in self._detect(det, sc)]
        else:
            pts = self._detect(frame_bgr)
        pool = self._assign(pts)
        self._prev = pool                       # full pool (incl. tentative) carries age
        # report only CONFIRMED tracks (survived confirm_frames) -> kills phantoms
        confirmed = [t for t in pool if t.get("age", 0) >= self.confirm_frames]
        if self.expected_flies and len(confirmed) > self.expected_flies:
            confirmed = sorted(confirmed, key=lambda t: t.get("age", 0),
                               reverse=True)[:self.expected_flies]
        tracks = confirmed
        self.tracks = tracks

        live_ids = {t["id"] for t in tracks}
        for t in tracks:
            coasting = t.get("coasting", False)
            col = (140, 140, 140) if coasting else _ID_COLORS[t["id"] % len(_ID_COLORS)]
            if self.trails:
                dq = self._trailmap.setdefault(t["id"], deque(maxlen=self.trail_len))
                if dq.maxlen != self.trail_len:
                    dq = deque(dq, maxlen=self.trail_len)
                    self._trailmap[t["id"]] = dq
                dq.append((t["x"], t["y"]))
            cv2.circle(annotated, (t["x"], t["y"]), 10, col, 1 if coasting else 2)
            cv2.putText(annotated, str(t["id"]) + ("?" if coasting else ""),
                        (t["x"] + 8, t["y"] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
        for gid in [k for k in self._trailmap if k not in live_ids]:
            if not self.trails:
                self._trailmap.pop(gid, None)

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

    def mask_jpeg(self, frame_bgr):
        """The binary detection mask (what the tracker actually sees) as JPEG."""
        th = self._binarize(frame_bgr)
        ok, buf = cv2.imencode(".jpg", th)
        return buf.tobytes() if ok else b""

    def settings(self):
        return {"enabled": self.enabled, "method": self.method,
                "auto_threshold": self.auto_threshold,
                "threshold": self.threshold, "computed_threshold": self.computed_threshold,
                "invert": self.invert, "min_area": self.min_area, "max_area": self.max_area,
                "tophat_kernel": self.tophat_kernel, "max_missed": self.max_missed,
                "confirm_frames": self.confirm_frames, "expected_flies": self.expected_flies,
                "detect_max_w": self.detect_max_w, "clahe": self.clahe,
                "bgsub_var": self.bgsub_var, "adaptive_block": self.adaptive_block,
                "adaptive_C": self.adaptive_C,
                "trails": self.trails, "trail_len": self.trail_len,
                "roi": self.roi, "has_roi": self.roi is not None,
                "count": len(self.tracks)}
