"""Camera pipeline — one capture loop feeds everything.

A single background thread grabs frames and fans them out to:
  * preview   — downscaled JPEG for the browser (<img src=/stream.mjpg>)
  * recording — full PROCESS_SIZE frames to an .mp4 via cv2.VideoWriter
  * tracking  — an optional frame callback that returns an annotated frame

This unified design is what makes closed-loop possible (tracking sees every
frame) and avoids the fragile multi-encoder setups. Falls back to a synthetic
moving-blob generator when picamera2 isn't present, so tracking and closed-loop
can be developed and demoed on a laptop.
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
        self._cond = threading.Condition()
        self._jpeg = None            # latest preview JPEG bytes
        self._frame = None           # latest raw BGR frame (for on-demand grabs)
        self._fps_est = 0.0
        self.recording = False
        self.record_path = None
        self._writer = None
        # callback(frame_bgr) -> annotated_bgr ; set by the app for tracking
        self.frame_cb = None
        os.makedirs(RECORDING_DIR, exist_ok=True)

        self._open()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _open(self):
        """Try to open the real camera; fall back to mock with a clear reason."""
        if not _HW_CAM:
            self.hw = False
            self._cam = None
            self.message = f"mock — picamera2 not importable ({_IMPORT_ERR})"
            return
        try:
            cam = Picamera2()
            cfg = cam.create_video_configuration(
                main={"size": PROCESS_SIZE, "format": "RGB888"},
                controls={"FrameRate": CAMERA_FPS},
            )
            cam.configure(cfg)
            cam.start()
            self._cam = cam
            self.hw = True
            self.message = f"live · Picamera2 {PROCESS_SIZE[0]}x{PROCESS_SIZE[1]}"
        except Exception as e:
            self.hw = False
            self._cam = None
            # most common cause: another process already owns the camera
            self.message = f"mock — camera busy/unavailable: {e}"

    def reinit(self):
        """Release any handle and try to open the camera again (no app restart)."""
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

    # ---- capture loop --------------------------------------------------
    def _grab(self):
        if self.hw and self._cam is not None:
            # picamera2 "RGB888" arrays are byte-ordered such that OpenCV reads
            # them as BGR — fine for our purposes (grayscale tracking).
            return self._cam.capture_array("main")
        return self._mock_frame()

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
                        annotated = frame  # never let tracking kill the stream

                if self.recording and self._writer is not None:
                    self._writer.write(frame)

                preview = cv2.resize(annotated, PREVIEW_SIZE)
                ok, buf = cv2.imencode(
                    ".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
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

                if not self.hw:
                    time.sleep(1.0 / CAMERA_FPS)  # pace the mock
            except Exception:
                time.sleep(0.05)  # keep the loop alive no matter what

    def _mock_frame(self):
        """Light background with TWO dark blobs orbiting — exercises the tracker,
        multi-zone triggers, and the proximity trigger (they periodically cross)."""
        w, h = PROCESS_SIZE
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
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg + b"\r\n")

    def latest_frame(self):
        with self._cond:
            return None if self._frame is None else self._frame.copy()

    # ---- recording -----------------------------------------------------
    def start_recording(self, tag: str = "") -> str | None:
        if self.recording:
            return "Already recording."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}{('_' + tag) if tag else ''}.mp4"
        self.record_path = os.path.join(RECORDING_DIR, name)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(self.record_path, fourcc, CAMERA_FPS, PROCESS_SIZE)
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
            "hw": self.hw,
            "message": self.message,
            "recording": self.recording,
            "path": self.record_path,
            "fps": round(self._fps_est, 1),
            "size": list(PROCESS_SIZE),
        }


camera = Camera()
