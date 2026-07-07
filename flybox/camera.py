"""Camera: picamera2 live MJPEG preview + full-res recording to disk.

Pi 5 has no hardware H.264 encoder, so recording uses software encoding. Keep the
preview low-res (config.CAMERA_PREVIEW_SIZE) and record separately at full res.
Falls back to a mock (grey frames) so the app runs off-Pi.
"""
from __future__ import annotations

import io
import os
import time
import threading
from datetime import datetime

from config import (
    CAMERA_PREVIEW_SIZE,
    CAMERA_RECORD_SIZE,
    CAMERA_FPS,
    RECORDING_DIR,
)

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder, MJPEGEncoder
    from picamera2.outputs import FileOutput
    _HW_CAM = True
except Exception as e:  # pragma: no cover
    _HW_CAM = False
    _IMPORT_ERR = str(e)


class _StreamBuffer(io.BufferedIOBase):
    """Holds the latest JPEG frame; readers block until a new one arrives."""

    def __init__(self):
        self.frame = None
        self.cond = threading.Condition()

    def write(self, buf):
        with self.cond:
            self.frame = buf
            self.cond.notify_all()

    def read_latest(self, timeout=1.0):
        with self.cond:
            self.cond.wait(timeout)
            return self.frame


class Camera:
    def __init__(self):
        self.hw = _HW_CAM
        self.recording = False
        self.record_path = None
        self._buffer = _StreamBuffer()
        os.makedirs(RECORDING_DIR, exist_ok=True)
        if _HW_CAM:
            self._cam = Picamera2()
            cfg = self._cam.create_video_configuration(
                main={"size": CAMERA_RECORD_SIZE},
                lores={"size": CAMERA_PREVIEW_SIZE},
                controls={"FrameRate": CAMERA_FPS},
            )
            self._cam.configure(cfg)
            self._cam.start_recording(MJPEGEncoder(), FileOutput(self._buffer), name="lores")
            self.message = "Picamera2 linked"
        else:
            self._cam = None
            self.message = f"MockCamera (no picamera2: {_IMPORT_ERR})"
            threading.Thread(target=self._mock_frames, daemon=True).start()

    def _mock_frames(self):
        """Emit a tiny static JPEG so the preview endpoint works off-Pi."""
        # 1x1 grey JPEG
        import base64
        jpg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAP//////////////////////////////"
            "////////////////////////////////////////////////////wgALCAABAAEB"
            "AREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
        while True:
            self._buffer.write(jpg)
            time.sleep(1.0 / max(CAMERA_FPS, 1))

    def mjpeg_generator(self):
        """Yields multipart MJPEG for the browser <img> preview."""
        while True:
            frame = self._buffer.read_latest()
            if frame is None:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + frame + b"\r\n")

    def start_recording(self, tag: str = "") -> str | None:
        if self.recording:
            return "Already recording."
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}{('_' + tag) if tag else ''}.h264"
        self.record_path = os.path.join(RECORDING_DIR, name)
        if _HW_CAM:
            self._cam.start_encoder(H264Encoder(), FileOutput(self.record_path), name="main")
        else:
            open(self.record_path, "wb").close()  # touch a placeholder off-Pi
        self.recording = True
        return None

    def stop_recording(self) -> str:
        if not self.recording:
            return ""
        if _HW_CAM:
            self._cam.stop_encoder(encoders=None)  # stops the main-stream encoder
        self.recording = False
        return self.record_path or ""

    def status(self):
        return {"hw": self.hw, "recording": self.recording,
                "path": self.record_path, "message": self.message}


camera = Camera()
