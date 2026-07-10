"""Closed-loop control: identity tracking -> triggers -> stimulation, with logging.

Trigger types (any number of each, evaluated every frame):
  * Zone      — a fly inside a red/blue-tagged rectangle fires that channel.
  * Proximity — two flies within a settable distance fire a channel.

Also drives live analytics (occupancy heatmap, min inter-fly distance) and, while
a session is recording, logs every trigger event and every frame's tracks.
"""
from __future__ import annotations

import time
from dataclasses import asdict

import cv2
import numpy as np

from config import CLOSED_LOOP_COOLDOWN_S, MM_PER_PX
from opto import Protocol, controller as opto
from session import logger as session

_CHAN_COLOR = {"red": (60, 60, 230), "blue": (230, 150, 40)}
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_HEAT_W, _HEAT_H = 64, 48
# per-identity trail colours (BGR) — MATCH the tracker's live marker palette so a
# fly is the same colour in the preview and in the trajectory analytics
_TRAIL_COLORS = [(0, 230, 0), (0, 200, 255), (255, 160, 0), (255, 80, 200),
                 (0, 255, 255), (200, 100, 255), (120, 255, 120), (255, 255, 0)]


def _default_protocol(channel):
    return Protocol(channel=channel, frequency_hz=20, pulse_width_ms=10,
                    train_duration_s=1.0, rest_s=0.0, n_bursts=1)


class ClosedLoop:
    def __init__(self, tracker):
        self.tracker = tracker
        self.enabled = False
        self.cooldown_s = CLOSED_LOOP_COOLDOWN_S
        self.mm_per_px = MM_PER_PX
        self.zones = []
        self._next_id = 1
        self.protocols = {"red": _default_protocol("red"),
                          "blue": _default_protocol("blue")}
        self.proximity = {"enabled": False, "distance_px": 80,
                          "channel": "blue", "_last": 0.0}
        self.fires = 0
        self.last_event = "—"
        self.frame_i = 0
        self.min_dist_px = None
        self._heat = np.zeros((_HEAT_H, _HEAT_W), np.float32)
        self._trails = {}     # fly id -> {"pts":[(nx,ny)...], "last":frame_i}
        self._prev_count = 0
        self._prev_min_dist = None
        self._zone_occ = {}   # zone id -> set of fly ids currently inside

    # ---- per-frame -----------------------------------------------------
    def on_frame(self, frame_bgr):
        if not self.tracker.enabled:
            return frame_bgr
        annotated, tracks = self.tracker.process(frame_bgr)
        h, w = frame_bgr.shape[:2]
        self.frame_i += 1
        pts = [(t["x"], t["y"]) for t in tracks]

        # analytics: occupancy grid (dwell/time) + per-identity trajectory paths
        for (x, y) in pts:
            gx = min(_HEAT_W - 1, max(0, int(x / w * _HEAT_W)))
            gy = min(_HEAT_H - 1, max(0, int(y / h * _HEAT_H)))
            self._heat[gy, gx] += 1.0          # every frame -> true time-spent map
        for t in tracks:
            fid = t["id"]
            nx, ny = t["x"] / w, t["y"] / h
            tr = self._trails.get(fid)
            if tr is None:
                self._trails[fid] = {"pts": [(nx, ny)], "last": self.frame_i}
            else:
                px, py = tr["pts"][-1]
                if (nx - px) ** 2 + (ny - py) ** 2 > 1.6e-5:   # only add on real movement
                    tr["pts"].append((nx, ny))
                    if len(tr["pts"]) > 1600:                  # decimate, keep full extent
                        tr["pts"][:] = tr["pts"][::2]
                tr["last"] = self.frame_i
        if len(self._trails) > 24:              # prune stalest identities (bounded memory)
            for k in sorted(self._trails, key=lambda k: self._trails[k]["last"])[:len(self._trails) - 24]:
                del self._trails[k]
        self.min_dist_px = self._min_dist(pts)

        # zone triggers (+ enter/exit occupancy logging)
        for z in self.zones:
            x1, y1, x2, y2 = self._roi_px(z["roi"], w, h)
            shape = z.get("shape", "rect")
            col = _CHAN_COLOR.get(z["channel"], (200, 200, 200))
            draw = col if self.enabled else (110, 110, 110)
            if shape == "ellipse":
                cv2.ellipse(annotated, ((x1 + x2) // 2, (y1 + y2) // 2),
                            (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)), 0, 0, 360, draw, 2)
            else:
                cv2.rectangle(annotated, (x1, y1), (x2, y2), draw, 2)
            cv2.putText(annotated, f"{z['channel']} #{z['id']}", (x1 + 4, max(y1 - 6, 12)),
                        _FONT, 0.5, draw, 1)
            occ = {t["id"] for t in tracks if self._pt_in(shape, x1, y1, x2, y2, t["x"], t["y"])}
            prev = self._zone_occ.get(z["id"], set())
            if session.running:
                for i in occ - prev:
                    session.log_event("zone-enter", z["channel"], {}, f"zone#{z['id']} id{i}")
                for i in prev - occ:
                    session.log_event("zone-exit", z["channel"], {}, f"zone#{z['id']} id{i}")
            self._zone_occ[z["id"]] = occ
            if self.enabled and occ:
                self._fire(z["channel"], z, f"zone#{z['id']}", annotated, (x1, y1, x2, y2))

        # proximity trigger (merge-aware: two flies often fuse into one blob at
        # contact, so also fire when a close pair collapses to a single track)
        count = len(pts)
        if self.proximity["enabled"]:
            d = self.proximity["distance_px"]
            pairs = self._close_pairs(pts, d)
            pcol = _CHAN_COLOR.get(self.proximity["channel"], (200, 200, 200))
            for a, b in pairs:
                cv2.line(annotated, a, b, pcol, 2)
            if pts:
                cv2.circle(annotated, pts[0], int(d), (90, 90, 90), 1)
            md = self.min_dist_px
            cv2.putText(annotated,
                        f"flies:{count}  min:{md if md is not None else '-'}px  trig<{d}px",
                        (8, 44), _FONT, 0.5, pcol, 1)
            merged = (self._prev_count >= 2 and count <= 1
                      and self._prev_min_dist is not None and self._prev_min_dist < d * 1.8)
            if self.enabled and (pairs or merged):
                self._fire(self.proximity["channel"], self.proximity,
                           "proximity" if pairs else "proximity-merge",
                           annotated, (0, 0, w - 1, h - 1))
        self._prev_count = count
        self._prev_min_dist = self.min_dist_px

        # session logging (tracks every frame while recording)
        if session.running:
            session.log_tracks(self.frame_i, tracks, self.mm_per_px)
        return annotated

    def _fire(self, channel, source, label, annotated=None, box=None):
        now = time.time()
        if now - source["_last"] < self.cooldown_s:
            return
        if opto.is_running(channel):
            return
        source["_last"] = now
        self.fires += 1
        self.last_event = f"{time.strftime('%H:%M:%S')} · {channel} ({label})"
        proto = self.protocols[channel]
        opto.run(proto)
        if session.running:
            session.log_event(label, channel, asdict(proto))
        if annotated is not None and box is not None:
            cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]),
                          _CHAN_COLOR.get(channel, (0, 0, 255)), 3)

    # ---- analytics -----------------------------------------------------
    @staticmethod
    def _min_dist(pts):
        best = None
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[j]
                d = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
                if best is None or d < best:
                    best = d
        return round(best, 1) if best is not None else None

    def trajectory_jpeg(self):
        """Render per-identity trajectory paths for the analytics panel. Each fly's
        path is drawn in its live-marker colour; line thickness grows where the fly
        spent more time (from the dwell grid), and a faint heat underlay shows the
        most-occupied areas. Makes identities + where they lingered easy to read."""
        W, H = 480, 360
        img = np.full((H, W, 3), 18, np.uint8)
        hn = None
        if self._heat.max() > 0:
            d = cv2.GaussianBlur(self._heat, (0, 0), 1.0)
            hn = d / d.max()
            under = cv2.applyColorMap(
                (cv2.resize(hn, (W, H)) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
            img = cv2.addWeighted(img, 1.0, under, 0.22, 0)   # subtle time-spent glow
        for fid, tr in self._trails.items():
            pts = tr["pts"]
            if len(pts) < 2:
                continue
            col = _TRAIL_COLORS[fid % len(_TRAIL_COLORS)]
            for i in range(1, len(pts)):
                x0, y0 = int(pts[i - 1][0] * W), int(pts[i - 1][1] * H)
                x1, y1 = int(pts[i][0] * W), int(pts[i][1] * H)
                th = 1
                if hn is not None:                            # thicker where more time spent
                    gx = min(_HEAT_W - 1, int(pts[i][0] * _HEAT_W))
                    gy = min(_HEAT_H - 1, int(pts[i][1] * _HEAT_H))
                    th = 1 + int(round(hn[gy, gx] * 5))
                cv2.line(img, (x0, y0), (x1, y1), col, th, cv2.LINE_AA)
            hx, hy = int(pts[-1][0] * W), int(pts[-1][1] * H)   # current head + id label
            cv2.circle(img, (hx, hy), 4, col, -1)
            cv2.putText(img, str(fid), (hx + 6, hy - 6), _FONT, 0.45, col, 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img)
        return buf.tobytes() if ok else b""

    # kept for backwards-compatible callers
    def heatmap_jpeg(self):
        return self.trajectory_jpeg()

    def reset_heatmap(self):
        self._heat[:] = 0.0
        self._trails.clear()

    # ---- geometry ------------------------------------------------------
    @staticmethod
    def _roi_px(roi, w, h):
        nx1, ny1, nx2, ny2 = roi
        x1, x2 = sorted((int(nx1 * w), int(nx2 * w)))
        y1, y2 = sorted((int(ny1 * h), int(ny2 * h)))
        return x1, y1, x2, y2

    @staticmethod
    def _pt_in(shape, x1, y1, x2, y2, px, py):
        if shape == "ellipse":
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            rx, ry = max(1, (x2 - x1) / 2), max(1, (y2 - y1) / 2)
            return ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0
        return x1 <= px <= x2 and y1 <= py <= y2

    @staticmethod
    def _close_pairs(pts, d):
        out, dd = [], d * d
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[j]
                if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= dd:
                    out.append((pts[i], pts[j]))
        return out

    # ---- config from the UI -------------------------------------------
    def add_zone(self, nx1, ny1, nx2, ny2, channel="red", shape="rect"):
        z = {"id": self._next_id, "roi": (float(nx1), float(ny1), float(nx2), float(ny2)),
             "channel": channel, "shape": shape, "_last": 0.0}
        self.zones.append(z)
        self._next_id += 1
        return z["id"]

    def remove_zone(self, zid):
        self.zones = [z for z in self.zones if z["id"] != int(zid)]

    def clear_zones(self):
        self.zones = []

    def set_proximity(self, enabled=None, distance_px=None, channel=None):
        if enabled is not None:
            self.proximity["enabled"] = bool(enabled)
        if distance_px is not None:
            self.proximity["distance_px"] = int(distance_px)
        if channel is not None:
            self.proximity["channel"] = channel

    def set_protocol(self, proto: Protocol):
        self.protocols[proto.channel] = proto

    def set_calibration(self, mm_per_px):
        self.mm_per_px = float(mm_per_px) if mm_per_px else None

    def _mm(self, px):
        return round(px * self.mm_per_px, 2) if (px is not None and self.mm_per_px) else None

    def status(self):
        def psum(p):
            return {"channel": p.channel, "frequency_hz": p.frequency_hz,
                    "pulse_width_ms": p.pulse_width_ms,
                    "train_duration_s": p.train_duration_s, "intensity": p.intensity}
        return {
            "enabled": self.enabled,
            "cooldown_s": self.cooldown_s,
            "mm_per_px": self.mm_per_px,
            "zones": [{"id": z["id"], "roi": z["roi"], "channel": z["channel"],
                       "shape": z.get("shape", "rect")} for z in self.zones],
            "proximity": {"enabled": self.proximity["enabled"],
                          "distance_px": self.proximity["distance_px"],
                          "distance_mm": self._mm(self.proximity["distance_px"]),
                          "channel": self.proximity["channel"]},
            "protocols": {c: psum(p) for c, p in self.protocols.items()},
            "fires": self.fires,
            "last_event": self.last_event,
            "analytics": {"min_dist_px": self.min_dist_px,
                          "min_dist_mm": self._mm(self.min_dist_px),
                          "frames": self.frame_i},
        }
