"""Experiment session logger — turns each run into a reproducible data folder.

On start() it creates  recordings/<timestamp>_<name>/  containing:
  * config.json  — full setup (preset config), camera specs, calibration, git commit
  * events.csv   — every stimulation event (trigger source, channel, protocol, dose)
  * tracks.csv   — per-frame fly centroids (id, x/y in px and mm)
  * <video>.mp4  — the recording (written by camera into this folder)

Everything a reviewer needs to reproduce and analyze the run lives together.
"""
from __future__ import annotations

import os
import re
import csv
import json
import time
import threading
import subprocess
from datetime import datetime

import cv2
import numpy as np

from config import SESSIONS_DIR, OPTO_IRRADIANCE_MW_CM2

_TRAJ_COLORS = [(0, 150, 0), (200, 120, 0), (0, 0, 200), (160, 0, 160),
                (0, 140, 200), (120, 90, 0), (0, 90, 90), (150, 0, 60)]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_")[:50] or "session"


def _git_commit() -> str | None:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return subprocess.check_output(
            ["git", "-C", here, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def light_dose_mj_cm2(channel: str, proto: dict) -> float | None:
    """mJ/cm^2 delivered by one burst = irradiance * intensity * duty * duration."""
    irr = OPTO_IRRADIANCE_MW_CM2.get(channel)
    if not irr:
        return None
    freq = proto.get("frequency_hz", 0)
    pw = proto.get("pulse_width_ms", 0)
    duty = 1.0 if freq <= 0 else min(1.0, (pw / (1000.0 / freq)))
    return round(irr * proto.get("intensity", 1.0) * duty
                 * proto.get("train_duration_s", 0), 4)


class SessionLogger:
    EVENT_COLS = ["t_iso", "t_s", "source", "channel", "frequency_hz",
                  "pulse_width_ms", "train_duration_s", "intensity",
                  "dose_mJ_cm2", "detail"]
    TRACK_COLS = ["t_s", "frame", "id", "x_px", "y_px", "x_mm", "y_mm",
                  "vx_px", "vy_px", "speed_px"]

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.base_dir = SESSIONS_DIR
        self.dir = None
        self.name = None
        self.t0 = 0.0
        self.n_events = 0
        self.n_track_rows = 0
        self._ev_f = self._tr_f = None
        self._ev = self._tr = None
        self._meta = {}

    def set_base_dir(self, path: str | None):
        self.base_dir = os.path.expanduser(path) if path else SESSIONS_DIR

    def start(self, name: str, meta: dict) -> str:
        with self._lock:
            if self.running:
                return self.dir
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.name = _safe(name or "session")
            self.dir = os.path.join(self.base_dir, f"{ts}_{self.name}")
            os.makedirs(self.dir, exist_ok=True)
            self.t0 = time.time()
            self.n_events = self.n_track_rows = 0
            self._meta = meta or {}
            cfg = {"name": self.name, "started": datetime.now().isoformat(),
                   "git_commit": _git_commit(), **meta}
            with open(os.path.join(self.dir, "config.json"), "w") as f:
                json.dump(cfg, f, indent=2, default=str)
            self._ev_f = open(os.path.join(self.dir, "events.csv"), "w", newline="")
            self._ev = csv.DictWriter(self._ev_f, fieldnames=self.EVENT_COLS)
            self._ev.writeheader()
            self._tr_f = open(os.path.join(self.dir, "tracks.csv"), "w", newline="")
            self._tr = csv.DictWriter(self._tr_f, fieldnames=self.TRACK_COLS)
            self._tr.writeheader()
            self.running = True
            return self.dir

    def log_event(self, source: str, channel: str, proto: dict, detail: str = ""):
        with self._lock:
            if not self.running:
                return
            self._ev.writerow({
                "t_iso": datetime.now().isoformat(),
                "t_s": round(time.time() - self.t0, 3),
                "source": source, "channel": channel,
                "frequency_hz": proto.get("frequency_hz"),
                "pulse_width_ms": proto.get("pulse_width_ms"),
                "train_duration_s": proto.get("train_duration_s"),
                "intensity": proto.get("intensity"),
                "dose_mJ_cm2": light_dose_mj_cm2(channel, proto),
                "detail": detail,
            })
            self._ev_f.flush()
            self.n_events += 1

    def log_tracks(self, frame: int, tracks, mm_per_px=None):
        with self._lock:
            if not self.running:
                return
            t = round(time.time() - self.t0, 3)
            for tr in tracks:
                self._tr.writerow({
                    "t_s": t, "frame": frame, "id": tr["id"],
                    "x_px": tr["x"], "y_px": tr["y"],
                    "x_mm": round(tr["x"] * mm_per_px, 3) if mm_per_px else "",
                    "y_mm": round(tr["y"] * mm_per_px, 3) if mm_per_px else "",
                    "vx_px": round(tr.get("vx", 0.0), 2),
                    "vy_px": round(tr.get("vy", 0.0), 2),
                    "speed_px": round(tr.get("speed", 0.0), 2),
                })
                self.n_track_rows += 1
            if tracks:
                self._tr_f.flush()

    def stop(self) -> str:
        with self._lock:
            if not self.running:
                return ""
            self.running = False
            d = self.dir
            dur = round(time.time() - self.t0, 1)
            for f in (self._ev_f, self._tr_f):
                try:
                    f.close()
                except Exception:
                    pass
        # (outside the lock) build per-fly summary + trajectory image
        try:
            summary = self._summarize(d, dur)
        except Exception as e:
            summary = {"error": str(e)}
        try:
            p = os.path.join(d, "config.json")
            cfg = json.load(open(p))
            cfg.update({"ended": datetime.now().isoformat(), "duration_s": dur,
                        "n_events": self.n_events, "n_track_rows": self.n_track_rows,
                        "summary": summary})
            json.dump(cfg, open(p, "w"), indent=2, default=str)
        except Exception:
            pass
        return d

    # ---- end-of-session analysis --------------------------------------
    def _summarize(self, d, dur) -> dict:
        setup = (self._meta or {}).get("setup", {})
        cam = setup.get("camera", {})
        fps = float(cam.get("fps") or 30) or 30
        mmpp = setup.get("calibration")

        # read trajectories
        per = {}
        tpath = os.path.join(d, "tracks.csv")
        if os.path.exists(tpath):
            with open(tpath) as f:
                for row in csv.DictReader(f):
                    i = row["id"]
                    per.setdefault(i, {"pts": [], "sp": []})
                    per[i]["pts"].append((float(row["x_px"]), float(row["y_px"])))
                    per[i]["sp"].append(float(row["speed_px"] or 0))

        flies = {}
        for i, dat in per.items():
            pts, sp = dat["pts"], dat["sp"]
            path = sum(((pts[k][0] - pts[k - 1][0]) ** 2 + (pts[k][1] - pts[k - 1][1]) ** 2) ** 0.5
                       for k in range(1, len(pts)))
            mean_s = (sum(sp) / len(sp)) if sp else 0.0
            max_s = max(sp) if sp else 0.0
            rec = {"n_points": len(pts), "path_px": round(path, 1),
                   "mean_speed_px_s": round(mean_s * fps, 1),
                   "max_speed_px_s": round(max_s * fps, 1)}
            if mmpp:
                rec["path_mm"] = round(path * mmpp, 1)
                rec["mean_speed_mm_s"] = round(mean_s * fps * mmpp, 2)
                rec["max_speed_mm_s"] = round(max_s * fps * mmpp, 2)
            flies[i] = rec

        # stimulation summary from events.csv
        stim_counts, dose_totals = {}, {}
        epath = os.path.join(d, "events.csv")
        if os.path.exists(epath):
            with open(epath) as f:
                for row in csv.DictReader(f):
                    src = row.get("source", "")
                    if src.startswith("zone-enter") or src.startswith("zone-exit"):
                        continue
                    ch = row.get("channel") or "-"
                    stim_counts[ch] = stim_counts.get(ch, 0) + 1
                    try:
                        dose_totals[ch] = round(dose_totals.get(ch, 0.0)
                                                + float(row.get("dose_mJ_cm2") or 0), 3)
                    except (TypeError, ValueError):
                        pass

        self._draw_trajectory(d, per, cam)
        return {"duration_s": dur, "n_flies": len(per),
                "flies": flies, "stim_counts": stim_counts,
                "dose_mJ_cm2_total": dose_totals,
                "calibration_mm_per_px": mmpp}

    def _draw_trajectory(self, d, per, cam):
        w, h = int(cam.get("width") or 0), int(cam.get("height") or 0)
        if not (w and h):
            xs = [p[0] for dat in per.values() for p in dat["pts"]]
            ys = [p[1] for dat in per.values() for p in dat["pts"]]
            w = int(max(xs)) + 20 if xs else 640
            h = int(max(ys)) + 20 if ys else 480
        img = np.full((h, w, 3), 255, np.uint8)
        for idx, (i, dat) in enumerate(per.items()):
            col = _TRAJ_COLORS[idx % len(_TRAJ_COLORS)]
            pts = np.array(dat["pts"], np.int32)
            if len(pts) > 1:
                cv2.polylines(img, [pts], False, col, 1)
            if len(pts):
                cv2.circle(img, tuple(pts[-1]), 4, col, -1)
        cv2.imwrite(os.path.join(d, "trajectory.png"), img)

    def status(self):
        return {"running": self.running, "dir": self.dir, "name": self.name,
                "n_events": self.n_events, "n_track_rows": self.n_track_rows,
                "elapsed_s": round(time.time() - self.t0, 1) if self.running else 0}


logger = SessionLogger()
