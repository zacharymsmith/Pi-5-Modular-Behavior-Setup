"""FlyBox FastAPI service — wires camera, illumination, opto, tracking, closed loop.

Run:  python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from pydantic import BaseModel

import config
from config import LIGHT_CHANNELS, OPTO_CHANNELS, HOST, PORT
from camera import camera
from illumination import lights
from opto import controller as opto, Protocol
from tracker import Tracker
from closed_loop import ClosedLoop
from session import logger as session
from scheduler import Scheduler
import presets

app = FastAPI(title="FlyBox Controller")

# --- assemble the vision pipeline -----------------------------------------
tracker = Tracker()
loop = ClosedLoop(tracker)
scheduler = Scheduler(opto, lights, loop)
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
    method: str | None = None
    auto_threshold: bool | None = None
    threshold: int | None = None
    invert: bool | None = None
    min_area: int | None = None
    max_area: int | None = None
    tophat_kernel: int | None = None
    max_missed: int | None = None
    confirm_frames: int | None = None
    expected_flies: int | None = None
    detect_max_w: int | None = None
    assignment: str | None = None
    fit_ellipse: bool | None = None
    clahe: bool | None = None
    sensitivity: int | None = None
    detect_static: bool | None = None
    trails: bool | None = None
    trail_len: int | None = None


class CameraConfigIn(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    auto_exposure: bool | None = None
    exposure_us: int | None = None
    gain: float | None = None
    contrast: float | None = None
    brightness: float | None = None
    sharpness: float | None = None
    saturation: float | None = None


class PresetIn(BaseModel):
    name: str


class PresetSaveIn(BaseModel):
    name: str
    sections: list[str] | None = None   # None = everything


# which config keys belong to each user-facing section
SECTION_KEYS = {
    "stim": ["protocols"],
    "tracking": ["tracker"],
    "triggers": ["zones", "proximity", "cooldown_s"],
    "camera": ["camera"],
    "calibration": ["calibration"],
}


def _filter_config(cfg: dict, sections) -> dict:
    if not sections:
        return cfg
    keep = set()
    for s in sections:
        keep.update(SECTION_KEYS.get(s, []))
    return {k: v for k, v in cfg.items() if k in keep}


class SessionStartIn(BaseModel):
    name: str = "session"
    save_dir: str | None = None
    experimenter: str | None = None
    genotype: str | None = None
    notes: str | None = None


class CalibIn(BaseModel):
    mm_per_px: float | None = None


class IrradianceIn(BaseModel):
    channel: str
    mw_cm2: float | None = None


class PhaseIn(BaseModel):
    name: str = "phase"
    duration_s: float = 10.0
    action: str = "none"       # none | stim | light
    channel: str | None = None
    light: str | None = None
    level: float | None = None


class SchedulerIn(BaseModel):
    name: str = "session"
    phases: list[PhaseIn]


class LoopIn(BaseModel):
    enabled: bool | None = None
    cooldown_s: float | None = None


class ZoneIn(BaseModel):
    nx1: float
    ny1: float
    nx2: float
    ny2: float
    channel: str = "red"
    shape: str = "rect"


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
        html = f.read()
    # no-store so a browser never serves a stale UI after an app restart
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/selftest")
def selftest():
    """One-click known-good state: mock feed + tracking + a centered zone + armed,
    auto-tuned. Lets you verify the whole pipeline without live flies."""
    camera.set_mock(True)
    tracker.enabled = True
    tracker.auto_threshold = True
    loop.clear_zones()
    loop.add_zone(0.3, 0.3, 0.7, 0.7, "red")
    loop.enabled = True
    frame = camera.latest_frame()
    tuned = tracker.auto_tune(frame) if frame is not None else {}
    return {"ok": True, "tuned": tuned, "tracker": tracker.settings(),
            "loop": loop.status()}


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
        "session": session.status(),
        "scheduler": scheduler.status(),
        "irradiance": config.OPTO_IRRADIANCE_MW_CM2,
        "paths": {"presets_dir": presets.current_dir(),
                  "sessions_dir": session.base_dir},
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


class FlashIn(BaseModel):
    channel: str = "red"
    intensity: float = 1.0
    seconds: float = 0.4


@app.post("/api/opto/flash")
def opto_flash(f: FlashIn):
    err = opto.flash(f.channel, f.intensity, f.seconds)
    return {"ok": err is None, "error": err}


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
    if t.method is not None:
        tracker.method = t.method
    if t.auto_threshold is not None:
        tracker.auto_threshold = t.auto_threshold
    if t.threshold is not None:
        tracker.threshold = t.threshold
    if t.invert is not None:
        tracker.invert = t.invert
    if t.min_area is not None:
        tracker.min_area = t.min_area
    if t.max_area is not None:
        tracker.max_area = t.max_area
    if t.tophat_kernel is not None:
        tracker.tophat_kernel = t.tophat_kernel
    if t.max_missed is not None:
        tracker.max_missed = t.max_missed
    if t.confirm_frames is not None:
        tracker.confirm_frames = t.confirm_frames
    if t.expected_flies is not None:
        tracker.expected_flies = t.expected_flies
    if t.detect_max_w is not None:
        tracker.detect_max_w = t.detect_max_w
    if t.assignment is not None:
        tracker.assignment = t.assignment
    if t.fit_ellipse is not None:
        tracker.fit_ellipse = t.fit_ellipse
    if t.clahe is not None:
        tracker.clahe = t.clahe
    if t.sensitivity is not None:
        tracker.sensitivity = max(0, min(100, t.sensitivity))
    if t.detect_static is not None:
        tracker.detect_static = t.detect_static
    if t.trails is not None:
        tracker.trails = t.trails
        if not t.trails:
            tracker.clear_trails()
    if t.trail_len is not None:
        tracker.trail_len = t.trail_len
    return {"ok": True, "tracker": tracker.settings()}


@app.post("/api/track/autotune")
def track_autotune():
    frame = camera.latest_frame()
    if frame is None:
        return {"ok": False, "error": "no camera frame yet"}
    res = tracker.auto_tune(frame)
    return {"ok": True, **res, "tracker": tracker.settings()}


class ArenaIn(BaseModel):
    nx1: float
    ny1: float
    nx2: float
    ny2: float
    shape: str = "ellipse"


@app.post("/api/track/roi")
def set_arena(a: ArenaIn):
    tracker.set_arena(a.nx1, a.ny1, a.nx2, a.ny2, a.shape)
    return {"ok": True, "tracker": tracker.settings()}


@app.post("/api/track/roi/clear")
def clear_arena():
    tracker.clear_arena()
    return {"ok": True}


class BuildBgIn(BaseModel):
    seconds: float = 3.0


@app.post("/api/track/build_bg")
def build_bg(b: BuildBgIn):
    """Median background from frames sampled over a few seconds — works with flies
    present as long as they move a little. This is the reference the recommended
    'reference subtraction' method subtracts each frame against."""
    n = max(6, int(b.seconds * 8))
    frames = []
    for _ in range(n):
        f = camera.latest_frame()
        if f is not None:
            frames.append(f)
        time.sleep(max(0.02, b.seconds / n))
    res = tracker.build_background(frames)
    if res.get("ok"):
        tracker.method = "refsub"          # auto-switch so the reference is used
    return res


@app.get("/api/track/mask.jpg")
def track_mask():
    frame = camera.latest_frame()
    if frame is None:
        return Response(content=b"", media_type="image/jpeg")
    return Response(content=tracker.mask_jpeg(frame), media_type="image/jpeg")


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
    zid = loop.add_zone(z.nx1, z.ny1, z.nx2, z.ny2, z.channel, z.shape)
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


class MockIn(BaseModel):
    enabled: bool


@app.post("/api/camera/mock")
def camera_mock(m: MockIn):
    return {"ok": True, "mock": camera.set_mock(m.enabled)}


class OverlayIn(BaseModel):
    enabled: bool | None = None
    title: str | None = None
    show_datetime: bool | None = None
    show_elapsed: bool | None = None
    show_frame: bool | None = None
    show_fps: bool | None = None
    corner: str | None = None


@app.post("/api/camera/overlay")
def camera_overlay(o: OverlayIn):
    return {"ok": True, "overlay": camera.set_overlay(**o.dict())}


class RecOptIn(BaseModel):
    record_annotated: bool


@app.post("/api/camera/record_opts")
def camera_record_opts(r: RecOptIn):
    camera.record_annotated = r.record_annotated
    return {"ok": True, "record_annotated": camera.record_annotated}


@app.post("/api/camera/autoexpose")
def camera_autoexpose():
    """Auto-set exposure/gain to hit a good brightness INSIDE the arena ROI, then
    lock it. Meters the tracker's arena so the rim/background doesn't skew it."""
    res = camera.autoexpose_arena(roi=tracker.roi)
    return res


@app.post("/api/camera/config")
def camera_config(c: CameraConfigIn):
    size = None
    if c.width is not None and c.height is not None:
        size = (c.width, c.height)
    err = camera.apply_config(size=size, fps=c.fps, auto_exposure=c.auto_exposure,
                              exposure_us=c.exposure_us, gain=c.gain,
                              contrast=c.contrast, brightness=c.brightness,
                              sharpness=c.sharpness, saturation=c.saturation)
    return {"ok": err is None, "error": err, "camera": camera.status()}


# --- presets (reproducibility) --------------------------------------------
def _gather_config() -> dict:
    s = camera.status()
    return {
        "protocols": {c: asdict(p) for c, p in loop.protocols.items()},
        "zones": [{"roi": list(z["roi"]), "channel": z["channel"]} for z in loop.zones],
        "proximity": {"enabled": loop.proximity["enabled"],
                      "distance_px": loop.proximity["distance_px"],
                      "channel": loop.proximity["channel"]},
        "cooldown_s": loop.cooldown_s,
        "calibration": loop.mm_per_px,
        "tracker": {"method": tracker.method, "threshold": tracker.threshold,
                    "invert": tracker.invert, "auto_threshold": tracker.auto_threshold,
                    "min_area": tracker.min_area, "max_area": tracker.max_area,
                    "tophat_kernel": tracker.tophat_kernel, "max_missed": tracker.max_missed,
                    "confirm_frames": tracker.confirm_frames, "expected_flies": tracker.expected_flies,
                    "detect_max_w": tracker.detect_max_w, "assignment": tracker.assignment,
                    "fit_ellipse": tracker.fit_ellipse, "clahe": tracker.clahe,
                    "solidity": tracker.solidity, "sensitivity": tracker.sensitivity,
                    "detect_static": tracker.detect_static,
                    "roi": tracker.roi,
                    "trails": tracker.trails, "trail_len": tracker.trail_len},
        "camera": {"width": s["size"][0], "height": s["size"][1],
                   "fps": s["target_fps"], "overlay": s["overlay"], **s["controls"]},
    }


def _apply_config(d: dict):
    for ch, p in d.get("protocols", {}).items():
        loop.set_protocol(Protocol(**p))
    if "zones" in d:                     # only touch zones if the preset has them
        loop.clear_zones()
        for z in d["zones"]:
            loop.add_zone(*z["roi"], z.get("channel", "red"), z.get("shape", "rect"))
    px = d.get("proximity", {})
    if px:
        loop.set_proximity(px.get("enabled"), px.get("distance_px"), px.get("channel"))
    if "cooldown_s" in d:
        loop.cooldown_s = d["cooldown_s"]
    if "calibration" in d:
        loop.set_calibration(d["calibration"])
    tk = d.get("tracker", {})
    for k in ("method", "auto_threshold", "threshold", "invert", "min_area", "max_area",
              "tophat_kernel", "max_missed", "confirm_frames", "expected_flies",
              "detect_max_w", "assignment", "fit_ellipse", "clahe", "solidity",
              "sensitivity", "detect_static", "trails", "trail_len"):
        if k in tk:
            setattr(tracker, k, tk[k])
    if "roi" in tk:                      # arena ROI (rebuild mask on load)
        r = tk["roi"]
        if r:
            tracker.set_arena(r["x1"], r["y1"], r["x2"], r["y2"], r.get("shape", "ellipse"))
        else:
            tracker.clear_arena()
    cam = d.get("camera", {})
    if cam:
        if cam.get("overlay"):
            camera.set_overlay(**cam["overlay"])
        camera.apply_config(
            size=(cam.get("width"), cam.get("height"))
            if cam.get("width") and cam.get("height") else None,
            fps=cam.get("fps"), auto_exposure=cam.get("auto_exposure"),
            exposure_us=cam.get("exposure_us"), gain=cam.get("gain"),
            contrast=cam.get("contrast"), brightness=cam.get("brightness"),
            sharpness=cam.get("sharpness"), saturation=cam.get("saturation"))


@app.get("/api/presets")
def list_presets():
    return {"presets": presets.list_presets()}


@app.post("/api/presets/save")
def save_preset(p: PresetSaveIn):
    data = _filter_config(_gather_config(), p.sections)
    data["_sections"] = p.sections or list(SECTION_KEYS.keys())
    name = presets.save(p.name, data)
    return {"ok": True, "name": name, "presets": presets.list_presets()}


@app.post("/api/presets/load")
def load_preset(p: PresetIn):
    data = presets.load(p.name)
    if data is None:
        return {"ok": False, "error": "preset not found"}
    _apply_config(data)
    return {"ok": True, "loop": loop.status(), "tracker": tracker.settings(),
            "camera": camera.status()}


@app.post("/api/presets/delete")
def delete_preset(p: PresetIn):
    return {"ok": presets.delete(p.name), "presets": presets.list_presets()}


# --- sessions (experiment logging) ----------------------------------------
@app.post("/api/session/start")
def session_start(s: SessionStartIn):
    if session.running:
        return {"ok": False, "error": "session already running"}
    session.set_base_dir(s.save_dir)
    meta = {"info": {"experimenter": s.experimenter, "genotype": s.genotype,
                     "notes": s.notes},
            "setup": _gather_config()}
    try:
        d = session.start(s.name, meta)
    except Exception as e:
        return {"ok": False, "error": f"could not create session folder: {e}"}
    camera.start_recording(directory=d)
    return {"ok": True, "dir": d, "session": session.status()}


@app.post("/api/session/stop")
def session_stop():
    camera.stop_recording()
    d = session.stop()
    return {"ok": True, "dir": d}


# --- calibration + light dose ---------------------------------------------
@app.post("/api/calibration")
def set_calibration(c: CalibIn):
    loop.set_calibration(c.mm_per_px)
    return {"ok": True, "mm_per_px": loop.mm_per_px}


@app.post("/api/opto/irradiance")
def set_irradiance(i: IrradianceIn):
    if i.channel in config.OPTO_IRRADIANCE_MW_CM2:
        config.OPTO_IRRADIANCE_MW_CM2[i.channel] = i.mw_cm2
    return {"ok": True, "irradiance": config.OPTO_IRRADIANCE_MW_CM2}


class DataDirIn(BaseModel):
    path: str | None = None


@app.post("/api/datadir")
def set_datadir(d: DataDirIn):
    """Set ONE data folder for everything: presets go to <root>/presets and
    sessions (video+csv+config) to <root>/recordings."""
    if d.path:
        root = os.path.expanduser(d.path)
        try:
            os.makedirs(root, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": f"cannot create {root}: {e}"}
        presets.set_dir(os.path.join(root, "presets"))
        session.set_base_dir(os.path.join(root, "recordings"))
    else:
        presets.set_dir(None)
        session.set_base_dir(None)
    return {"ok": True, "presets_dir": presets.current_dir(),
            "sessions_dir": session.base_dir}


# --- scheduler (timed experiment plan) ------------------------------------
@app.post("/api/scheduler/run")
def scheduler_run(s: SchedulerIn):
    if session.running or scheduler.running:
        return {"ok": False, "error": "a session or scheduler is already running"}
    d = session.start(s.name, _gather_config())
    camera.start_recording(directory=d)

    def _done():
        camera.stop_recording()
        session.stop()

    err = scheduler.run([p.dict() for p in s.phases], on_complete=_done)
    if err:
        camera.stop_recording()
        session.stop()
        return {"ok": False, "error": err}
    return {"ok": True, "dir": d}


@app.post("/api/scheduler/stop")
def scheduler_stop():
    scheduler.stop()
    return {"ok": True}


# --- analytics -------------------------------------------------------------
@app.get("/api/analytics/heatmap.jpg")
def heatmap():
    return Response(content=loop.heatmap_jpeg(), media_type="image/jpeg")


@app.post("/api/analytics/reset")
def reset_analytics():
    loop.reset_heatmap()
    return {"ok": True}


@app.on_event("shutdown")
def _shutdown():
    scheduler.stop()
    if session.running:
        camera.stop_recording()
        session.stop()
    opto.cleanup()
    lights.all_off()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT)
