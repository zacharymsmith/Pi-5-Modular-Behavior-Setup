"""Camera pipeline — one capture loop feeds preview + recording + tracking.

Resolution, fps, exposure and gain are runtime-configurable (see apply_config).
Falls back to a synthetic two-fly generator when picamera2 isn't present, and a
UI toggle (force_mock) can switch to that synthetic feed even with a live camera,
so the whole program can be tested without live flies.
"""
from __future__ import annotations

import os
import time
import threading
from datetime import datetime

import numpy as np
import cv2

from config import (
    PROCESS_SIZE, PREVIEW_SIZE, CAMERA_FPS, JPEG_QUALITY, RECORDING_DIR,
    CAMERA_AUTO_EXPOSURE, CAMERA_EXPOSURE_US, CAMERA_GAIN,
)

try:
    from picamera2 import Picamera2
    _HW_CAM = True
except Exception as e:  # pragma: no cover
    _HW_CAM = False
    _IMPORT_ERR = str(e)


class Camera:
    def __init__(self):
        self.hw = False
        self._cam = None
        self.message = ""
        self.size = list(PROCESS_SIZE)      # runtime resolution (w, h)
        self.fps = CAMERA_FPS
        self.controls = {"auto_exposure": CAMERA_AUTO_EXPOSURE,
                         "exposure_us": CAMERA_EXPOSURE_US,
                         "gain": CAMERA_GAIN}
        self._cond = threading.Condition()
        self._jpeg = None
        self._frame = None
        self._fps_est = 0.0
        self.recording = False
        self.record_path = None
        self._writer = None
        self._writer2 = None            # optional annotated (overlays burned in) video
        self.record_annotated = False
        self._rec_lock = threading.Lock()   # guards writer access across threads
        self.force_mock = False
        self.frame_cb = None
        self._brightness = 0.0
        self._focus = 0.0
        self._record_fps = 0.0
        self._rec_frame = 0
        self._rec_t0 = 0.0
        # configurable info overlay burned into the video (NOT the tracking markers)
        self.overlay = {"enabled": True, "title": "", "show_datetime": True,
                        "show_elapsed": True, "show_frame": True, "show_fps": True,
                        "corner": "tl"}
        os.makedirs(RECORDING_DIR, exist_ok=True)

        self._open()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    # ---- camera lifecycle ---------------------------------------------
    def _cam_controls(self):
        c = {"FrameRate": self.fps}
        if self.controls["auto_exposure"]:
            c["AeEnable"] = True
        else:
            c["AeEnable"] = False
            c["ExposureTime"] = int(self.controls["exposure_us"])
            c["AnalogueGain"] = float(self.controls["gain"])
        return c

    def _open(self):
        if not _HW_CAM:
            self.hw = False
            self._cam = None
            self.message = f"mock — picamera2 not importable ({_IMPORT_ERR})"
            return
        try:
            cam = Picamera2()
            cfg = cam.create_video_configuration(
                main={"size": tuple(self.size), "format": "RGB888"},
                controls=self._cam_controls(),
            )
            cam.configure(cfg)
            cam.start()
            self._cam = cam
            self.hw = True
            self.message = f"live · {self.size[0]}x{self.size[1]} @ {self.fps}fps"
        except Exception as e:
            self.hw = False
            self._cam = None
            self.message = f"mock — camera busy/unavailable: {e}"

    def reinit(self):
        try:
            if self._cam is not None:
                self._cam.stop()
                self._cam.close()
        except Exception:
            pass
        self._cam = None
        self.hw = False
        self._open()
        return self.message

    def apply_config(self, size=None, fps=None, auto_exposure=None,
                     exposure_us=None, gain=None) -> str | None:
        """Change camera specs at runtime. Resolution/fps changes reconfigure the
        camera; exposure/gain apply live. Refused while recording (would corrupt
        the file)."""
        needs_reconfigure = False
        if size is not None and list(size) != self.size:
            if self.recording:
                return "Stop recording before changing resolution."
            self.size = [int(size[0]), int(size[1])]
            needs_reconfigure = True
        if fps is not None and float(fps) != self.fps:
            self.fps = float(fps)
            needs_reconfigure = True
        if auto_exposure is not None:
            self.controls["auto_exposure"] = bool(auto_exposure)
        if exposure_us is not None:
            self.controls["exposure_us"] = int(exposure_us)
        if gain is not None:
            self.controls["gain"] = float(gain)

        if self.hw and self._cam is not None:
            try:
                if needs_reconfigure:
                    self._cam.stop()
                    cfg = self._cam.create_video_configuration(
                        main={"size": tuple(self.size), "format": "RGB888"},
                        controls=self._cam_controls())
                    self._cam.configure(cfg)
                    self._cam.start()
                else:
                    self._cam.set_controls(self._cam_controls())
                self.message = f"live · {self.size[0]}x{self.size[1]} @ {self.fps}fps"
            except Exception as e:
                return f"apply failed: {e}"
        return None

    # ---- capture loop --------------------------------------------------
    def _grab(self):
        if self.force_mock or not self.hw or self._cam is None:
            return self._mock_frame()
        return self._cam.capture_array("main")

    def _loop(self):
        last = time.perf_counter()
        while True:
            try:
                frame = self._grab()
                if frame is None:
                    time.sleep(0.01)
                    continue
                annotated = frame
                if self.frame_cb is not None:
                    try:
                        annotated = self.frame_cb(frame)
                    except Exception:
                        annotated = frame
                ov = self.overlay.get("enabled")
                # display / annotated frame = tracking markers + info overlay
                disp = annotated
                if ov:
                    disp = self._draw_overlay(annotated.copy(), recording=self.recording)
                # write video under a lock so Stop can't free the writer mid-write (segfault)
                with self._rec_lock:
                    if self.recording and self._writer is not None:
                        self._rec_frame += 1
                        rec = self._draw_overlay(frame.copy(), recording=True) if ov else frame
                        self._writer.write(rec)
                        if self._writer2 is not None:
                            self._writer2.write(disp)   # annotated version (all overlays)
                preview = cv2.resize(disp, PREVIEW_SIZE)
                self._brightness = float(preview.mean())   # live scene brightness 0..255
                # cheap focus score (sharpness) on the preview centre — higher = sharper
                pg = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
                ph, pw = pg.shape
                self._focus = float(cv2.Laplacian(
                    pg[int(ph * .15):int(ph * .85), int(pw * .15):int(pw * .85)],
                    cv2.CV_64F).var())
                ok, buf = cv2.imencode(".jpg", preview,
                                       [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    now = time.perf_counter()
                    dt = now - last
                    last = now
                    if dt > 0:
                        self._fps_est = 0.9 * self._fps_est + 0.1 * (1.0 / dt)
                    with self._cond:
                        self._jpeg = buf.tobytes()
                        self._frame = frame
                        self._cond.notify_all()
                if self.force_mock or not self.hw:
                    time.sleep(1.0 / max(self.fps, 1))
            except Exception:
                time.sleep(0.05)

    def set_overlay(self, **kw):
        for k, v in kw.items():
            if v is not None and k in self.overlay:
                self.overlay[k] = v
        return self.overlay

    def _draw_overlay(self, img, recording=False):
        o = self.overlay
        lines = []
        if o.get("title"):
            lines.append(str(o["title"]))
        if o.get("show_datetime"):
            lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if o.get("show_elapsed") and recording:
            el = time.time() - self._rec_t0
            lines.append(f"REC {int(el // 60):02d}:{int(el % 60):02d}")
        if o.get("show_frame") and recording:
            lines.append(f"frame {self._rec_frame}")
        if o.get("show_fps"):
            lines.append(f"{self._fps_est:.1f} fps")
        if not lines:
            return img
        h, w = img.shape[:2]
        scale = max(0.5, w / 1500.0)
        font = cv2.FONT_HERSHEY_SIMPLEX
        sizes = [cv2.getTextSize(t, font, scale, 2)[0] for t in lines]
        lineh = int(max(s[1] for s in sizes) * 2.0)
        boxw = max(s[0] for s in sizes)
        corner = o.get("corner", "tl")
        x0 = 10 if "l" in corner else max(4, w - boxw - 10)
        y = lineh if "t" in corner else h - lineh * (len(lines) - 1) - int(lineh * 0.3)
        for t in lines:
            # black outline + colored fill -> readable on any background, no box tint
            cv2.putText(img, t, (x0, y), font, scale, (0, 0, 0), max(3, int(5 * scale)), cv2.LINE_AA)
            cv2.putText(img, t, (x0, y), font, scale, (60, 255, 140), max(1, int(2 * scale)), cv2.LINE_AA)
            y += lineh
        return img

    def _mock_frame(self):
        w, h = self.size
        img = np.full((h, w, 3), 200, np.uint8)
        t = time.time()
        cx1 = int(w / 2 + (w / 3) * np.cos(t));       cy1 = int(h / 2 + (h / 3) * np.sin(t * 1.3))
        cx2 = int(w / 2 + (w / 3) * np.cos(t + 2.2)); cy2 = int(h / 2 + (h / 3) * np.sin(t * 1.1 + 1.0))
        cv2.circle(img, (cx1, cy1), 14, (30, 30, 30), -1)
        cv2.circle(img, (cx2, cy2), 14, (30, 30, 30), -1)
        return img

    # ---- consumers -----------------------------------------------------
    def mjpeg_generator(self):
        while True:
            with self._cond:
                self._cond.wait(timeout=1.0)
                jpg = self._jpeg
            if jpg:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")

    def latest_frame(self):
        with self._cond:
            return None if self._frame is None else self._frame.copy()

    def set_mock(self, on: bool) -> bool:
        self.force_mock = bool(on)
        return self.force_mock

    def _roi_mask(self, roi):
        """Boolean mask (at capture size) for an arena ROI dict, or None."""
        if not roi:
            return None
        w, h = self.size
        m = np.zeros((h, w), np.uint8)
        x1, y1 = int(roi["x1"] * w), int(roi["y1"] * h)
        x2, y2 = int(roi["x2"] * w), int(roi["y2"] * h)
        if roi.get("shape") == "rect":
            cv2.rectangle(m, (x1, y1), (x2, y2), 255, -1)
        else:
            cv2.ellipse(m, ((x1 + x2) // 2, (y1 + y2) // 2),
                        (max(1, (x2 - x1) // 2), max(1, (y2 - y1) // 2)), 0, 0, 360, 255, -1)
        return m > 0

    def autoexpose_arena(self, roi=None, target: int = 150, iters: int = 9) -> dict:
        """Iteratively set exposure (then gain) so the mean brightness INSIDE the
        arena ROI hits `target`, then lock it. Meters only the arena, so the bright
        rim/background doesn't fool it. Run after changing your IR."""
        if not (self.hw and self._cam is not None):
            return {"ok": False, "error": "no live camera (mock)"}
        try:
            mask = self._roi_mask(roi)
            exp = int(self.controls.get("exposure_us") or 8000)
            gain = float(self.controls.get("gain") or 1.0)
            mean = target
            for _ in range(iters):
                self._cam.set_controls({"AeEnable": False, "ExposureTime": max(200, int(exp)),
                                        "AnalogueGain": max(1.0, float(gain))})
                time.sleep(0.35)                       # let the setting take effect
                frame = self.latest_frame()
                if frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                region = gray[mask] if mask is not None else gray.ravel()
                mean = float(region.mean()) if region.size else float(gray.mean())
                if abs(target - mean) <= 6:
                    break
                ratio = min(2.0, max(0.5, target / max(mean, 1.0)))
                if exp < 30000:                        # raise exposure first, then gain
                    exp = min(33000, max(200, exp * ratio))
                else:
                    gain = min(16.0, max(1.0, gain * ratio))
            self.controls.update(auto_exposure=False, exposure_us=int(exp), gain=round(gain, 2))
            self._cam.set_controls(self._cam_controls())
            return {"ok": True, "exposure_us": int(exp), "gain": round(gain, 2),
                    "measured": round(mean, 1), "target": target, "metered_arena": mask is not None}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- recording -----------------------------------------------------
    def start_recording(self, tag: str = "", directory: str | None = None) -> str | None:
        if self.recording:
            return "Already recording."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}{('_' + tag) if tag else ''}.mp4"
        d = directory or RECORDING_DIR
        os.makedirs(d, exist_ok=True)
        self.record_path = os.path.join(d, name)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        # Record at the ACTUAL processing rate, not the target fps — otherwise the
        # file plays back too fast (the loop runs slower than the camera fps).
        rec_fps = round(self._fps_est, 1) if self._fps_est > 1 else self.fps
        self._record_fps = rec_fps
        with self._rec_lock:
            self._writer = cv2.VideoWriter(self.record_path, fourcc, rec_fps, tuple(self.size))
            if not self._writer.isOpened():
                self._writer = None
                return "Could not open the video writer (codec missing?)."
            self._writer2 = None
            if self.record_annotated:
                apath = self.record_path[:-4] + "_annotated.mp4"
                self._writer2 = cv2.VideoWriter(apath, fourcc, rec_fps, tuple(self.size))
                if not self._writer2.isOpened():
                    self._writer2 = None
            self._rec_frame = 0
            self._rec_t0 = time.time()
            self.recording = True
        return None

    def stop_recording(self) -> str:
        with self._rec_lock:
            if not self.recording:
                return ""
            self.recording = False
            for wname in ("_writer", "_writer2"):
                w = getattr(self, wname)
                if w is not None:
                    try:
                        w.release()
                    except Exception:
                        pass
                    setattr(self, wname, None)
        return self.record_path or ""

    def status(self):
        return {
            "hw": self.hw and not self.force_mock,
            "camera_present": self.hw,
            "mock": self.force_mock,
            "message": "test mock (forced)" if self.force_mock else self.message,
            "recording": self.recording,
            "path": self.record_path,
            "fps": round(self._fps_est, 1),
            "size": list(self.size),
            "target_fps": self.fps,
            "controls": dict(self.controls),
            "brightness": round(self._brightness, 1),
            "focus": round(self._focus, 0),
            "overlay": dict(self.overlay),
            "record_annotated": self.record_annotated,
        }


camera = Camera()
