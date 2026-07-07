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

from config import SESSIONS_DIR, OPTO_IRRADIANCE_MW_CM2


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
    TRACK_COLS = ["t_s", "frame", "id", "x_px", "y_px", "x_mm", "y_mm"]

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.dir = None
        self.name = None
        self.t0 = 0.0
        self.n_events = 0
        self.n_track_rows = 0
        self._ev_f = self._tr_f = None
        self._ev = self._tr = None

    def start(self, name: str, meta: dict) -> str:
        with self._lock:
            if self.running:
                return self.dir
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.name = _safe(name or "session")
            self.dir = os.path.join(SESSIONS_DIR, f"{ts}_{self.name}")
            os.makedirs(self.dir, exist_ok=True)
            self.t0 = time.time()
            self.n_events = self.n_track_rows = 0
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
            for f in (self._ev_f, self._tr_f):
                try:
                    f.close()
                except Exception:
                    pass
            # append a summary to config.json
            try:
                p = os.path.join(d, "config.json")
                cfg = json.load(open(p))
                cfg.update({"ended": datetime.now().isoformat(),
                            "duration_s": round(time.time() - self.t0, 1),
                            "n_events": self.n_events,
                            "n_track_rows": self.n_track_rows})
                json.dump(cfg, open(p, "w"), indent=2, default=str)
            except Exception:
                pass
            return d

    def status(self):
        return {"running": self.running, "dir": self.dir, "name": self.name,
                "n_events": self.n_events, "n_track_rows": self.n_track_rows,
                "elapsed_s": round(time.time() - self.t0, 1) if self.running else 0}


logger = SessionLogger()
