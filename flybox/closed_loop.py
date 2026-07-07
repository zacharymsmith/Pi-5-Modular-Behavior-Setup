"""Closed-loop control: tracking -> triggers -> optogenetic stimulation.

Two independent trigger types, any number of each, all evaluated every frame:

  * Zone triggers  — draw a rectangle on the video, tag it red or blue. A tracked
                     fly inside the zone fires that channel's protocol.
  * Proximity      — fire when any two tracked centroids come within a settable
                     distance (for social-interaction / body-distance assays).

Red and blue fire independently (the opto controller is per-channel), so you can
have a red zone and a blue zone active at once. Coordinates are normalized (0..1)
so they're resolution-independent; the proximity distance is in frame pixels.
"""
from __future__ import annotations

import time

import cv2

from config import CLOSED_LOOP_COOLDOWN_S
from opto import Protocol, controller as opto

# BGR colors for overlays
_CHAN_COLOR = {"red": (60, 60, 230), "blue": (230, 150, 40)}
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _default_protocol(channel):
    return Protocol(channel=channel, frequency_hz=20, pulse_width_ms=10,
                    train_duration_s=1.0, rest_s=0.0, n_bursts=1)


class ClosedLoop:
    def __init__(self, tracker):
        self.tracker = tracker
        self.enabled = False
        self.cooldown_s = CLOSED_LOOP_COOLDOWN_S
        self.zones = []                     # {id, roi:(nx1,ny1,nx2,ny2), channel, _last}
        self._next_id = 1
        self.protocols = {"red": _default_protocol("red"),
                          "blue": _default_protocol("blue")}
        self.proximity = {"enabled": False, "distance_px": 80,
                          "channel": "blue", "_last": 0.0}
        self.fires = 0
        self.last_event = "—"

    # ---- per-frame -----------------------------------------------------
    def on_frame(self, frame_bgr):
        if not self.tracker.enabled:
            return frame_bgr
        annotated, pts = self.tracker.process(frame_bgr)
        h, w = frame_bgr.shape[:2]

        # zone triggers
        for z in self.zones:
            x1, y1, x2, y2 = self._roi_px(z["roi"], w, h)
            col = _CHAN_COLOR.get(z["channel"], (200, 200, 200))
            draw = col if self.enabled else (110, 110, 110)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), draw, 2)
            cv2.putText(annotated, z["channel"], (x1 + 4, max(y1 - 6, 12)),
                        _FONT, 0.5, draw, 1)
            inside = any(x1 <= px <= x2 and y1 <= py <= y2 for px, py in pts)
            if inside and self.enabled:
                self._fire(z["channel"], z, annotated, (x1, y1, x2, y2))

        # proximity trigger
        if self.proximity["enabled"]:
            d = self.proximity["distance_px"]
            pairs = self._close_pairs(pts, d)
            pcol = _CHAN_COLOR.get(self.proximity["channel"], (200, 200, 200))
            for a, b in pairs:
                cv2.line(annotated, a, b, pcol, 2)
                mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
                cv2.putText(annotated, "<d", (mid[0] + 4, mid[1]), _FONT, 0.4, pcol, 1)
            if pts:  # reference ring showing the trigger radius
                cv2.circle(annotated, pts[0], int(d), (90, 90, 90), 1)
            if pairs and self.enabled:
                self._fire(self.proximity["channel"], self.proximity, annotated,
                           (0, 0, w - 1, h - 1))
        return annotated

    def _fire(self, channel, source, annotated=None, box=None):
        now = time.time()
        if now - source["_last"] < self.cooldown_s:
            return
        if opto.is_running(channel):
            return
        source["_last"] = now
        self.fires += 1
        self.last_event = f"{time.strftime('%H:%M:%S')} · {channel}"
        opto.run(self.protocols[channel])
        if annotated is not None and box is not None:
            cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]),
                          _CHAN_COLOR.get(channel, (0, 0, 255)), 3)

    # ---- geometry ------------------------------------------------------
    @staticmethod
    def _roi_px(roi, w, h):
        nx1, ny1, nx2, ny2 = roi
        x1, x2 = sorted((int(nx1 * w), int(nx2 * w)))
        y1, y2 = sorted((int(ny1 * h), int(ny2 * h)))
        return x1, y1, x2, y2

    @staticmethod
    def _close_pairs(pts, d):
        out = []
        dd = d * d
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[j]
                if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= dd:
                    out.append((pts[i], pts[j]))
        return out

    # ---- config from the UI -------------------------------------------
    def add_zone(self, nx1, ny1, nx2, ny2, channel="red"):
        z = {"id": self._next_id, "roi": (float(nx1), float(ny1), float(nx2), float(ny2)),
             "channel": channel, "_last": 0.0}
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

    def status(self):
        def psum(p):
            return {"channel": p.channel, "frequency_hz": p.frequency_hz,
                    "pulse_width_ms": p.pulse_width_ms,
                    "train_duration_s": p.train_duration_s, "intensity": p.intensity}
        return {
            "enabled": self.enabled,
            "cooldown_s": self.cooldown_s,
            "zones": [{"id": z["id"], "roi": z["roi"], "channel": z["channel"]}
                      for z in self.zones],
            "proximity": {"enabled": self.proximity["enabled"],
                          "distance_px": self.proximity["distance_px"],
                          "channel": self.proximity["channel"]},
            "protocols": {c: psum(p) for c, p in self.protocols.items()},
            "fires": self.fires,
            "last_event": self.last_event,
        }
