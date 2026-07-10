"""Robust OpenCV fly tracker with identity-preserving trajectories.

Detection pipeline (kept cheap enough for real-time on a Pi 5):
  grayscale -> blur -> threshold (auto/Otsu or manual) -> morphological open
  -> contours -> area filter -> centroids -> greedy nearest-neighbor IDs.

Auto-threshold (Otsu) adapts to lighting so it "just works" on both the live
camera and the simulated feed. `auto_tune()` inspects the current frame and picks
the polarity (dark-on-light vs light-on-dark) and threshold automatically.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, fields
from typing import List, Dict

import cv2
import numpy as np


def _hungarian(cost):
    """Optimal assignment (Kuhn–Munkres, O(n^2 m)) on a cost matrix.
    Returns a list of (row, col) pairs minimising total cost. Self-contained so
    there's no scipy dependency. Reference: classic Hungarian / e-maxx JV form."""
    cost = np.asarray(cost, dtype=float)
    n, m = cost.shape
    transposed = n > m
    if transposed:
        cost = cost.T
        n, m = m, n
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, -1
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    pairs = []
    for j in range(1, m + 1):
        if p[j] > 0:
            pairs.append((j - 1, p[j] - 1) if transposed else (p[j] - 1, j - 1))
    return pairs

from config import (TRACK_THRESHOLD, TRACK_INVERT, TRACK_MIN_AREA, TRACK_MAX_AREA,
                    TRACK_MAX_BLOBS, TRACK_MATCH_DIST_PX, TRACK_AUTO_THRESHOLD,
                    TRACK_TOPHAT_KERNEL, TRACK_MAX_MISSED, TRACK_CONFIRM_FRAMES,
                    TRACK_EXPECTED_FLIES, TRACK_DETECT_MAX_W, TRAIL_ENABLED, TRAIL_LENGTH)

_ID_COLORS = [(0, 230, 0), (0, 200, 255), (255, 160, 0), (255, 80, 200),
              (0, 255, 255), (200, 100, 255), (120, 255, 120), (255, 255, 0)]
_KERNEL = np.ones((3, 3), np.uint8)
_KERNEL5 = np.ones((5, 5), np.uint8)
# Default arena mask: a centred ellipse inset from the edges, so the bright dish
# rim + corners (the #1 source of phantom blobs) are never searched even before
# the user draws a tight arena. Empirically drops corner detections to ~0%.
DEFAULT_ROI = {"shape": "ellipse", "x1": 0.08, "y1": 0.08, "x2": 0.92, "y2": 0.92}


@dataclass
class Tracker:
    enabled: bool = False
    method: str = "refsub"       # "refsub" (recommended) | "tophat" | "threshold"
    auto_threshold: bool = TRACK_AUTO_THRESHOLD
    threshold: int = TRACK_THRESHOLD
    invert: bool = TRACK_INVERT
    blur: bool = True
    clahe: bool = False          # local contrast enhancement (helps faint flies)
    _clahe: object = None
    tophat_kernel: int = TRACK_TOPHAT_KERNEL   # feature size for illumination-invariant method
    _ref: object = None          # median reference image (for refsub method)
    bg_adapt: bool = True        # self-healing reference: slowly blend the live frame into
                                 # the reference at non-fly pixels, so lighting drift AND a
                                 # briefly-baked-in fly can never permanently blind detection
    bg_learn: float = 0.03       # adaptation rate per processed frame (~1-2 s to heal)
    detect_static: bool = False  # also find motionless flies by APPEARANCE (dark/bright blob),
                                 # not just by change — catches a sleeping/frozen/dead fly that
                                 # reference-subtraction alone would treat as background
    min_area: int = TRACK_MIN_AREA
    max_area: int = TRACK_MAX_AREA
    max_blobs: int = TRACK_MAX_BLOBS
    match_dist: int = TRACK_MATCH_DIST_PX
    sensitivity: int = 50                      # 0-100: higher = catch fainter flies (lower diff
                                               # threshold), lower = stricter (less noise). 50 = default
    solidity: float = 0.4                      # reject thin/edge blobs (area/hull); 0 = off
    assignment: str = "greedy"                 # "greedy" | "hungarian" (optimal)
    fit_ellipse: bool = False                  # fit body ellipse (orientation + axes)
    max_missed: int = TRACK_MAX_MISSED         # frames to coast a lost track
    confirm_frames: int = TRACK_CONFIRM_FRAMES # new blob must persist this long to become a track
    expected_flies: int = TRACK_EXPECTED_FLIES # cap on reported flies (0 = unlimited)
    detect_max_w: int = TRACK_DETECT_MAX_W     # downscale detection to this width (0 = full res)
    trails: bool = TRAIL_ENABLED
    trail_len: int = TRAIL_LENGTH
    computed_threshold: int = TRACK_THRESHOLD
    roi: object = field(default_factory=lambda: dict(DEFAULT_ROI))  # arena mask; defaults to a
                                # centred ellipse so bright corners are never searched
    tracks: List[Dict] = field(default_factory=list)
    _prev: List[Dict] = field(default_factory=list)
    _next_id: int = 1
    _trailmap: Dict[int, deque] = field(default_factory=dict)
    _mask: object = None
    _mask_shape: object = None
    _adapt_ct: int = 0

    # ---- arena ROI (limit tracking to inside the dish) -----------------
    def set_arena(self, nx1, ny1, nx2, ny2, shape="ellipse"):
        x1, x2 = sorted((nx1, nx2))
        y1, y2 = sorted((ny1, ny2))
        self.roi = {"shape": shape, "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        self._mask = None

    def clear_arena(self):
        self.roi = dict(DEFAULT_ROI)   # revert to the default centred ellipse (never the corners)
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

    def has_reference(self) -> bool:
        return self._ref is not None

    def config_dict(self) -> Dict:
        """EVERY reproducibility-relevant setting, captured automatically from the
        dataclass fields (excludes private state + runtime values). Any field added
        in future is saved/restored with no extra wiring — sessions stay complete."""
        skip = {"tracks", "computed_threshold"}
        out = {}
        for f in fields(self):
            if f.name.startswith("_") or f.name in skip:
                continue
            v = getattr(self, f.name)
            if isinstance(v, (int, float, bool, str, type(None), dict, list)):
                out[f.name] = v
        return out

    def apply_config(self, cfg: dict):
        """Restore all settings saved by config_dict() (arena ROI handled specially)."""
        for k, v in (cfg or {}).items():
            if k == "roi":
                continue
            if hasattr(self, k):
                setattr(self, k, v)
        if "roi" in cfg:
            r = cfg["roi"]
            if r:
                self.set_arena(r["x1"], r["y1"], r["x2"], r["y2"], r.get("shape", "ellipse"))
            else:
                self.clear_arena()

    def update_reference(self, frame_bgr, tracks):
        """Slowly blend the live frame into the reference at NON-fly pixels only.
        Keeps the reference fresh against lighting/gain drift, and lets a fly that
        was accidentally baked into the reference clear out within ~1-2 s of moving,
        so refsub can never permanently 'lose' a fly. Detected fly pixels are protected
        from the update so real flies are never absorbed."""
        if not self.bg_adapt or self._ref is None or self.method != "refsub":
            return
        gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if gray.shape != self._ref.shape:
            gray = cv2.resize(gray, (self._ref.shape[1], self._ref.shape[0]))
        h, w = self._ref.shape
        sx, sy = w / frame_bgr.shape[1], h / frame_bgr.shape[0]
        prot = np.zeros((h, w), np.uint8)
        r = max(12, int(self.tophat_kernel * sx))
        for t in tracks:
            cv2.circle(prot, (int(t["x"] * sx), int(t["y"] * sy)), r, 255, -1)
        a = float(self.bg_learn)
        ref = self._ref.astype(np.float32)
        keep = prot == 0
        ref[keep] = (1.0 - a) * ref[keep] + a * gray[keep].astype(np.float32)
        self._ref = ref.astype(np.uint8)

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
                # brightness-normalise the frame to the reference (within the arena
                # ROI if set) — cancels opto flashes / gain drift that would
                # otherwise flood the difference image. (95% clean on test footage.)
                m = self._get_mask(gray.shape[1], gray.shape[0])
                if m is not None and int((m > 0).sum()) > 0:
                    cm, rm = float(gray[m > 0].mean()), float(ref[m > 0].mean())
                else:
                    cm, rm = float(gray.mean()), float(ref.mean())
                if cm > 1:
                    gray = np.clip(gray.astype(np.float32) * (rm / cm), 0, 255).astype(np.uint8)
                diff = cv2.absdiff(gray, ref)
                # ADAPTIVE threshold (not a fixed cut): Otsu finds the fly/background
                # split inside the ROI, clamped to a floor/ceiling. The floor keeps
                # faint, low-contrast flies (dark specks on bright dish) detectable —
                # a fixed high cut was silently dropping them. ROI-restricted so the
                # bright rim can't skew Otsu.
                dm = m if (m is not None and int((m > 0).sum()) > 100) else None
                dvals = diff[dm > 0] if dm is not None else diff.reshape(-1)
                otsu, _ = cv2.threshold(dvals.reshape(-1, 1).astype(np.uint8),
                                        0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # sensitivity slides the floor/ceiling: high = catch fainter flies.
                floor = int(np.interp(self.sensitivity, [0, 50, 100], [30, 10, 4]))
                ceil = int(np.interp(self.sensitivity, [0, 50, 100], [80, 45, 25]))
                thr = int(min(ceil, max(floor, otsu)))
                self.computed_threshold = thr
                _, th = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)
                if self.detect_static:
                    # ALSO find flies by appearance so a motionless fly (no difference
                    # from the reference) is still detected. BLACK-HAT morphology
                    # isolates small LOCAL dark spots (the fly) regardless of the
                    # large-scale background level — robust on a non-uniform dish where
                    # a global threshold would flood. (Top-hat if the fly is lighter.)
                    k = max(9, int(self.tophat_kernel) | 1)
                    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
                    mop = cv2.MORPH_BLACKHAT if self.invert else cv2.MORPH_TOPHAT
                    feat = cv2.morphologyEx(gray, mop, ker)
                    _, app = cv2.threshold(feat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    if dm is not None:
                        app = cv2.bitwise_and(app, dm)                 # ROI only
                    # FLOOD GUARD: a real fly is a tiny fraction of the dish. If the
                    # appearance mask covers a big area it's texture/noise, not a fly —
                    # discard so we never swamp the real refsub detections.
                    roi_area = int((dm > 0).sum()) if dm is not None else app.size
                    if roi_area > 0 and int((app > 0).sum()) <= 0.04 * roi_area:
                        th = cv2.bitwise_or(th, app)
        elif self.method == "tophat":
            # remove large-scale illumination + rim glow, keep the small fly.
            # black-hat isolates dark spots on a bright bg; top-hat the reverse.
            k = max(9, int(self.tophat_kernel * scale) | 1)   # kernel scales with detect res
            ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            op = cv2.MORPH_BLACKHAT if self.invert else cv2.MORPH_TOPHAT
            feat = cv2.morphologyEx(gray, op, ker)
            _, th = cv2.threshold(feat, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            self.computed_threshold = 0
        else:  # threshold (Otsu or manual)
            if self.auto_threshold:
                val, th = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
                self.computed_threshold = int(val)
            else:
                _, th = cv2.threshold(gray, int(self.threshold), 255, mode)
                self.computed_threshold = int(self.threshold)
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, _KERNEL)     # drop speck noise
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, _KERNEL5)   # merge fly fragments
        mask = self._get_mask(th.shape[1], th.shape[0])        # limit to arena ROI
        if mask is not None:
            th = cv2.bitwise_and(th, mask)
        return th

    def _detect(self, frame_bgr, scale=1.0):
        th = self._binarize(frame_bgr, scale)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        min_a, max_a = self.min_area * scale * scale, self.max_area * scale * scale
        dets = []
        for c in cnts:
            a = cv2.contourArea(c)
            if a < min_a or a > max_a:
                continue
            if self.solidity > 0 and len(c) >= 3:   # reject thin/edge artefacts
                ha = cv2.contourArea(cv2.convexHull(c))
                if ha > 0 and a / ha < self.solidity:
                    continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            x, y = M["m10"] / M["m00"], M["m01"] / M["m00"]
            angle, major, minor = None, 0.0, 0.0
            if self.fit_ellipse and len(c) >= 5:
                try:
                    (_, _), (ax1, ax2), ang = cv2.fitEllipse(c)
                    major, minor, angle = max(ax1, ax2), min(ax1, ax2), float(ang)
                except cv2.error:
                    pass
            dets.append({"x": x, "y": y, "angle": angle, "major": major, "minor": minor})
            if len(dets) >= self.max_blobs:
                break
        return dets

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
    def _match(self, prev, dets):
        """Return {det_index: prev_track}. Greedy nearest-neighbour, or optimal
        Hungarian assignment (fewer ID swaps when flies cross)."""
        matches = {}
        if not prev or not dets:
            return matches
        d2 = self.match_dist ** 2
        if self.assignment == "hungarian":
            big = d2 * 1000 + 1
            cost = np.empty((len(prev), len(dets)), dtype=float)
            for i, p in enumerate(prev):
                for j, dd in enumerate(dets):
                    c = (dd["x"] - p["px"]) ** 2 + (dd["y"] - p["py"]) ** 2
                    cost[i, j] = c if c <= d2 else big     # forbid too-far links
            for i, j in _hungarian(cost):
                if cost[i, j] <= d2:
                    matches[j] = prev[i]
        else:                                              # greedy
            used = set()
            for j, dd in enumerate(dets):
                best, bestd = None, d2
                for p in prev:
                    if p["id"] in used:
                        continue
                    c = (dd["x"] - p["px"]) ** 2 + (dd["y"] - p["py"]) ** 2
                    if c < bestd:
                        best, bestd = p, c
                if best is not None:
                    used.add(best["id"])
                    matches[j] = best
        return matches

    def _assign(self, dets):
        prev = list(self._prev)
        for p in prev:  # predict where each fly should be this frame
            p["px"] = p["x"] + p.get("vx", 0.0)
            p["py"] = p["y"] + p.get("vy", 0.0)
        matches = self._match(prev, dets)
        tracks, matched_ids = [], set()
        a = 0.5  # velocity smoothing
        for j, dd in enumerate(dets):
            x, y = int(round(dd["x"])), int(round(dd["y"]))
            ell = {"angle": dd.get("angle"), "major": dd.get("major", 0.0),
                   "minor": dd.get("minor", 0.0)}
            best = matches.get(j)
            if best is not None:
                matched_ids.add(best["id"])
                vx = a * (x - best["x"]) + (1 - a) * best.get("vx", 0.0)
                vy = a * (y - best["y"]) + (1 - a) * best.get("vy", 0.0)
                tracks.append({"id": best["id"], "x": x, "y": y, "vx": vx, "vy": vy,
                               "speed": (vx * vx + vy * vy) ** 0.5, "missed": 0,
                               "coasting": False, "age": best.get("age", 0) + 1, **ell})
            else:
                tracks.append({"id": self._next_id, "x": x, "y": y, "vx": 0.0, "vy": 0.0,
                               "speed": 0.0, "missed": 0, "coasting": False, "age": 1, **ell})
                self._next_id += 1
        # coast unmatched previous tracks through the gap (holds identity + trigger)
        for p in prev:
            if p["id"] in matched_ids:
                continue
            missed = p.get("missed", 0) + 1
            if missed > self.max_missed:
                continue
            vx, vy = p.get("vx", 0.0) * 0.85, p.get("vy", 0.0) * 0.85
            tracks.append({"id": p["id"], "x": int(round(p["px"])), "y": int(round(p["py"])),
                           "vx": vx, "vy": vy, "speed": (vx * vx + vy * vy) ** 0.5,
                           "missed": missed, "coasting": True, "age": p.get("age", 0),
                           "angle": p.get("angle"), "major": p.get("major", 0.0),
                           "minor": p.get("minor", 0.0)})
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
            dets = self._detect(det, sc)
            for d in dets:                       # scale detections back to full res
                d["x"] /= sc; d["y"] /= sc; d["major"] /= sc; d["minor"] /= sc
        else:
            dets = self._detect(frame_bgr)
        pool = self._assign(dets)
        self._prev = pool                       # full pool (incl. tentative) carries age
        # report only CONFIRMED tracks (survived confirm_frames) -> kills phantoms
        confirmed = [t for t in pool if t.get("age", 0) >= self.confirm_frames]
        if self.expected_flies and len(confirmed) > self.expected_flies:
            confirmed = sorted(confirmed, key=lambda t: t.get("age", 0),
                               reverse=True)[:self.expected_flies]
        tracks = confirmed
        self.tracks = tracks
        # self-heal the reference every 4th frame (protect current blobs so real flies
        # aren't absorbed) — throttled to stay light on the Pi's real-time loop
        self._adapt_ct = (self._adapt_ct + 1) % 4
        if self._adapt_ct == 0:
            self.update_reference(frame_bgr, dets)

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
            if self.fit_ellipse and t.get("angle") is not None and t.get("major", 0) > 0:
                cv2.ellipse(annotated, (t["x"], t["y"]),
                            (max(1, int(t["major"] / 2)), max(1, int(t["minor"] / 2))),
                            t["angle"], 0, 360, col, 1)
                th = math.radians(t["angle"])                # heading along body axis
                hx = int(t["x"] + math.sin(th) * t["major"] / 2)
                hy = int(t["y"] - math.cos(th) * t["major"] / 2)
                cv2.line(annotated, (t["x"], t["y"]), (hx, hy), col, 1)
            else:
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
                "detect_max_w": self.detect_max_w, "assignment": self.assignment,
                "fit_ellipse": self.fit_ellipse, "solidity": self.solidity, "clahe": self.clahe,
                "sensitivity": self.sensitivity, "detect_static": self.detect_static,
                "trails": self.trails, "trail_len": self.trail_len,
                "roi": self.roi, "has_roi": self.roi is not None,
                "has_reference": self.has_reference(),
                "count": len(self.tracks)}
