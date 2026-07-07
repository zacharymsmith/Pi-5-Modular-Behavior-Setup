"""FlyBox FastAPI service — wires camera, illumination, opto, tracking, closed loop.

Run:  python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from config import LIGHT_CHANNELS, OPTO_CHANNELS, HOST, PORT
from camera import camera
from illumination import lights
from opto import controller as opto, Protocol
from tracker import Tracker
from closed_loop import ClosedLoop

app = FastAPI(title="FlyBox Controller")

# --- assemble the vision pipeline -----------------------------------------
tracker = Tracker()
loop = ClosedLoop(tracker)
camera.frame_cb = loop.on_frame       # every frame flows through tracking/closed-loop

HERE = os.path.dirname(__file__)
TEMPLATES = os.path.join(HERE, "templates")


# --- schemas ---------------------------------------------------------------
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
    level: float


class RawChannelIn(BaseModel):
    channel: int
    level: float


class TrackIn(BaseModel):
    enabled: bool | None = None
    threshold: int | None = None
    invert: bool | None = None
    min_area: int | None = None


class LoopIn(BaseModel):
    enabled: bool | None = None
    cooldown_s: float | None = None


class ZoneIn(BaseModel):
    nx1: float
    ny1: float
    nx2: float
    ny2: float
    channel: str = "red"


class ZoneRemoveIn(BaseModel):
    id: int


class ProximityIn(BaseModel):
    enabled: bool | None = None
    distance_px: int | None = None
    channel: str | None = None


# --- UI + stream -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(TEMPLATES, "index.html")) as f:
        return f.read()


@app.get("/stream.mjpg")
def stream():
    return StreamingResponse(camera.mjpeg_generator(),
                             media_type="multipart/x-mixed-replace; boundary=frame")


# --- status ----------------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "camera": camera.status(),
        "opto": opto.state,
        "lights": lights.levels,
        "light_names": list(LIGHT_CHANNELS.keys()),
        "opto_channels": list(OPTO_CHANNELS.keys()),
        "tracker": tracker.settings(),
        "loop": loop.status(),
    }


# --- opto ------------------------------------------------------------------
@app.post("/api/opto/run")
def opto_run(p: ProtocolIn):
    proto = Protocol(**p.dict())
    err = opto.run(proto)
    return {"ok": err is None, "error": err, "duty_pct": proto.duty_cycle_pct()}


@app.post("/api/opto/stop")
def opto_stop():
    opto.stop()
    return {"ok": True}


# --- illumination ----------------------------------------------------------
@app.post("/api/light")
def set_light(l: LightIn):
    return {"ok": lights.set_light(l.name, l.level) is None}


@app.post("/api/light/raw")
def set_raw(r: RawChannelIn):
    lights.set_raw_channel(r.channel, r.level)
    return {"ok": True}


@app.post("/api/light/off")
def lights_off():
    lights.all_off()
    return {"ok": True}


# --- recording -------------------------------------------------------------
@app.post("/api/record/start")
def record_start():
    err = camera.start_recording()
    return {"ok": err is None, "error": err, "path": camera.record_path}


@app.post("/api/record/stop")
def record_stop():
    return {"ok": True, "path": camera.stop_recording()}


# --- tracking --------------------------------------------------------------
@app.post("/api/track")
def set_track(t: TrackIn):
    if t.enabled is not None:
        tracker.enabled = t.enabled
    if t.threshold is not None:
        tracker.threshold = t.threshold
    if t.invert is not None:
        tracker.invert = t.invert
    if t.min_area is not None:
        tracker.min_area = t.min_area
    return {"ok": True, "tracker": tracker.settings()}


# --- closed loop -----------------------------------------------------------
@app.post("/api/loop")
def set_loop(l: LoopIn):
    if l.enabled is not None:
        loop.enabled = l.enabled
    if l.cooldown_s is not None:
        loop.cooldown_s = l.cooldown_s
    return {"ok": True, "loop": loop.status()}


@app.post("/api/loop/zone")
def add_zone(z: ZoneIn):
    zid = loop.add_zone(z.nx1, z.ny1, z.nx2, z.ny2, z.channel)
    return {"ok": True, "id": zid, "loop": loop.status()}


@app.post("/api/loop/zone/remove")
def remove_zone(z: ZoneRemoveIn):
    loop.remove_zone(z.id)
    return {"ok": True, "loop": loop.status()}


@app.post("/api/loop/zones/clear")
def clear_zones():
    loop.clear_zones()
    return {"ok": True}


@app.post("/api/loop/proximity")
def set_proximity(p: ProximityIn):
    loop.set_proximity(p.enabled, p.distance_px, p.channel)
    return {"ok": True, "loop": loop.status()}


@app.post("/api/loop/protocol")
def set_loop_protocol(p: ProtocolIn):
    """Set the protocol fired for one channel (p.channel = 'red' or 'blue')."""
    loop.set_protocol(Protocol(**p.dict()))
    return {"ok": True, "loop": loop.status()}


@app.post("/api/camera/retry")
def camera_retry():
    msg = camera.reinit()
    return {"ok": camera.hw, "hw": camera.hw, "message": msg}


@app.on_event("shutdown")
def _shutdown():
    opto.cleanup()
    lights.all_off()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT)
