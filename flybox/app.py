"""FastAPI service for the Pi 5 fly-behavior box.

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Then open http://<pi-ip>:8000 in a browser (LAN, Pi Connect, or AP mode).

Owns: camera preview/record, illumination (PCA9685/MOSFET), opto pulse trains
(hardware PWM/PicoBuck). Closed-loop tracking is stubbed in closed_loop.py.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

from config import HOST, PORT, LIGHT_CHANNELS, OPTO_CHANNELS
from camera import camera
from illumination import lights
from opto import controller as opto, Protocol

app = FastAPI(title="FlyBox Controller")

HERE = os.path.dirname(__file__)
TEMPLATES = os.path.join(HERE, "templates")


# ---------- schemas ----------
class ProtocolIn(BaseModel):
    frequency_hz: float = 20.0
    pulse_width_ms: float = 10.0
    train_duration_s: float = 2.0
    rest_s: float = 5.0
    n_bursts: int = 5
    channel: str = "red"
    intensity: float = 1.0


class LightIn(BaseModel):
    name: str
    level: float  # 0..1


class RawChannelIn(BaseModel):
    channel: int   # PCA9685 0..15
    level: float   # 0..1


# ---------- UI ----------
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(TEMPLATES, "index.html")) as f:
        return f.read()


@app.get("/stream.mjpg")
def stream():
    return StreamingResponse(camera.mjpeg_generator(),
                             media_type="multipart/x-mixed-replace; boundary=frame")


# ---------- status ----------
@app.get("/api/status")
def status():
    return {
        "camera": camera.status(),
        "opto": opto.state,
        "lights": lights.levels,
        "light_names": list(LIGHT_CHANNELS.keys()),
        "opto_channels": list(OPTO_CHANNELS.keys()),
    }


# ---------- opto ----------
@app.post("/api/opto/run")
def opto_run(p: ProtocolIn):
    err = opto.run(Protocol(**p.dict()))
    return {"ok": err is None, "error": err, "duty_pct": Protocol(**p.dict()).duty_cycle_pct()}


@app.post("/api/opto/stop")
def opto_stop():
    opto.stop()
    return {"ok": True}


# ---------- illumination ----------
@app.post("/api/light")
def set_light(l: LightIn):
    err = lights.set_light(l.name, l.level)
    return {"ok": err is None, "error": err}


@app.post("/api/light/raw")
def set_raw(r: RawChannelIn):
    """Discovery/identify: drive a bare PCA9685 channel to learn what it lights."""
    lights.set_raw_channel(r.channel, r.level)
    return {"ok": True}


@app.post("/api/light/off")
def lights_off():
    lights.all_off()
    return {"ok": True}


# ---------- recording ----------
@app.post("/api/record/start")
def record_start():
    err = camera.start_recording()
    return {"ok": err is None, "error": err, "path": camera.record_path}


@app.post("/api/record/stop")
def record_stop():
    return {"ok": True, "path": camera.stop_recording()}


@app.on_event("shutdown")
def _shutdown():
    opto.cleanup()
    lights.all_off()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT)
