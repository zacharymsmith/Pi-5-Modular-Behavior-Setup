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
        self.force_mock = False
        self.frame_cb = None
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
                if self.recording and self._writer is not None:
                    self._writer.write(frame)
                preview = cv2.resize(annotated, PREVIEW_SIZE)
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
        self._writer = cv2.VideoWriter(self.record_path, fourcc, self.fps, tuple(self.size))
        if not self._writer.isOpened():
            self._writer = None
            return "Could not open the video writer (codec missing?)."
        self.recording = True
        return None

    def stop_recording(self) -> str:
        if not self.recording:
            return ""
        self.recording = False
        if self._writer is not None:
            self._writer.release()
            self._writer = None
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
        }


camera = Camera()
