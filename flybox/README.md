# FlyBox Controller

Web-based control for the Pi 5 modular fly-behavior box: live camera preview +
recording, illumination (white/IR strips), optogenetic pulse trains (red/blue Cree
LEDs), real-time OpenCV **fly tracking**, and **closed-loop stimulation** (fire the
LEDs when a fly enters a zone you draw on the video).

Open it in any browser on the LAN, over Pi Connect, or via the Pi's own WiFi (AP mode).
Replaces the single-user tkinter GUI.

> ✅ **Wiring verified 2026-07-07 via the discovery panel:** everything runs through the
> **PCA9685** — ch0=IR strip, ch1=white strip, ch2=red opto LED, ch3=blue opto LED (the
> PicoBuck PWM inputs are on the PCA9685, not the Pi's GPIO). This is captured in
> `config.py`; re-verify with the discovery panel if you rewire.

## Architecture (modular)
```
                 app.py  (FastAPI: routes + wiring)
                   │
   ┌───────────────┼───────────────────────────┐
   │               │                            │
 camera.py     hardware.py (shared PCA9685)   vision
 (capture loop) │            │                 ├─ tracker.py   (OpenCV centroids)
   │            ├ illumination.py (strips)     └─ closed_loop.py (zone → opto)
   │            └ opto.py (pulse trains)
   └── one loop → preview + recording + frame callback (tracking/closed-loop)
```

| File | Purpose |
|------|---------|
| `config.py` | **All hardware settings — edit to match your wiring** |
| `hardware.py` | Single shared PCA9685 bus (mock-aware) |
| `illumination.py` | White/IR strips + channel discovery |
| `opto.py` | Optogenetic pulse-train protocols (red/blue via PCA9685) |
| `camera.py` | Unified capture loop: preview + recording + frame source |
| `tracker.py` | Real-time OpenCV centroid tracking |
| `closed_loop.py` | Trigger-zone → opto stimulation |
| `app.py` | FastAPI server + all routes |
| `templates/index.html` | Dashboard UI (tracking overlay, drag-to-set trigger zone) |
| `flybox.service` | systemd unit (start on boot) |

Every hardware module has a **mock fallback** (the camera even generates a moving blob),
so the whole app — including tracking and closed loop — runs and can be demoed on a
laptop with no Pi hardware attached.

## Setup on the Pi 5
Just run the setup script from the repo root — it installs deps, enables I²C, runs
diagnostics, and can launch the app:
```
bash setup.sh          # install + diagnose
bash setup.sh run      # + launch at http://<pi-ip>:8000
```
`numpy` and OpenCV come from **apt** (`python3-numpy`, `python3-opencv`), never pip —
a pip numpy breaks picamera2's binary compatibility.

## Using it
1. **Illumination** — white/IR sliders (or the discovery panel to re-check wiring).
2. **Tracking** — flip "Enable tracking overlay," tune the threshold until the fly is
   circled, confirm "dark subject on light background" matches your IR backlight.
3. **Closed loop** — drag a rectangle on the video to set the trigger zone, pick the
   protocol (▶ set it from the opto panel), then "Arm closed loop." A fly entering the
   zone fires the LEDs, rate-limited by the cooldown.
4. **Record** — writes an MP4 at the processing resolution.

## Remote access
- **LAN:** `http://<pi-ip>:8000` or `http://raspberrypi.local:8000`
- **Pi Connect:** remote/off-site without firewall setup
- **AP mode:** Pi broadcasts its own WiFi; connect and open the same URL

## Safety before running LEDs
See `../HARDWARE_AND_SOFTWARE_GUIDE.md` §8: barrel polarity, common ground, PicoBuck
current with a series ammeter (start low), and irradiance at the fly plane in mW/cm².

## Data output (reproducibility)
Each experiment session writes a self-contained folder under `recordings/`:

```
recordings/20260707_143012_myassay/
  config.json   # full setup: protocols, zones, proximity, tracking, camera, calibration, git commit
  events.csv    # every stimulation: t_s, source (zone/proximity/scheduler), channel, protocol, dose_mJ_cm2
  tracks.csv    # per-frame centroids: t_s, frame, id, x_px, y_px, x_mm, y_mm
  *.mp4         # the recording
```

Set the mm/px calibration and per-channel irradiance (mW/cm²) so distances log in mm and
stimulation logs actual light dose. Save a **preset** to reproduce the whole setup later.

## Roadmap
1. ✅ Preview + record, illumination, opto protocols.
2. ✅ Wiring map verified via discovery panel.
3. ✅ Real-time tracking + closed-loop stimulation (multi-zone red/blue + proximity).
4. ✅ Identity trajectories, motion trails, presets, camera specs.
5. ✅ Experiment session logging, spatial calibration, safety caps + light dose,
   timed scheduler, live analytics (occupancy heatmap + inter-fly distance).
6. Next ideas: multi-arena grids, velocity/heading metrics, identity-swap correction,
   auto disk-space management, web-UI auth for shared networks.
