# FlyBox — Raspberry Pi 5 Modular Fly-Behavior Rig

A complete, browser-controlled behavior box for *Drosophila*: record high-quality
video, track flies in real time, and deliver optogenetic light stimulation — including
**closed-loop** stimulation triggered by what the flies are doing. Everything runs on a
Raspberry Pi 5 and is controlled from any web browser (on the lab network, over Pi
Connect, or on the Pi's own screen).

No coding required to use it. If you can plug in a camera and open a web page, you can run
an experiment.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Hardware — parts & wiring](#2-hardware--parts--wiring)
3. [Install it on your Pi (start to finish)](#3-install-it-on-your-pi-start-to-finish)
4. [First run — your first tracked recording](#4-first-run--your-first-tracked-recording)
5. [The interface, panel by panel](#5-the-interface-panel-by-panel)
6. [Getting the tracking flawless](#6-getting-the-tracking-flawless)
7. [What each experiment saves (your data)](#7-what-each-experiment-saves-your-data)
8. [Cameras: HQ vs Camera Module 3 NoIR](#8-cameras-hq-vs-camera-module-3-noir)
9. [Safety before you power the LEDs](#9-safety-before-you-power-the-leds)
10. [Troubleshooting](#10-troubleshooting)
11. [How the code is organized](#11-how-the-code-is-organized)
12. [Credits & methods](#12-credits--methods)

---

## 1. What it does

- **Live video + recording** from a Raspberry Pi HQ camera or Camera Module 3, with
  exposure/gain/contrast/brightness/sharpness controls, auto-exposure, autofocus (on
  cameras that support it), a highlight-clipping meter, and a configurable info overlay
  burned into the video.
- **Real-time fly tracking** (OpenCV, reference-subtraction based) with stable identities,
  an arena you draw on the video, a live "detection health" readout, and a trajectory map
  coloured per fly.
- **Optogenetic stimulation** — define pulse-train protocols (frequency, pulse width, burst
  length, rest, repeats, intensity) for red and blue channels, fire them manually, or…
- **Closed-loop stimulation** — draw trigger zones on the video (a fly entering a red/blue
  zone fires that channel) or trigger on **proximity** (two flies within a set distance).
- **Reproducible experiment sessions** — every run saves the video, per-frame trajectories,
  every stimulation event, and a complete `config.json` recipe of *every* setting used.
- **Presets** — save your whole setup and reload it in one click.
- **Runs without hardware** — a built-in mock mode (synthetic moving flies) lets you learn
  and demo the entire app on a laptop with no Pi or camera attached.

---

## 2. Hardware — parts & wiring

> This section is decoded from the actual boards in the build photos. The deep-dive
> reference (with the pre-solder checklist and measured facts) lives in
> **[`HARDWARE_AND_SOFTWARE_GUIDE.md`](HARDWARE_AND_SOFTWARE_GUIDE.md)**. Always re-verify
> against your physical boards and a multimeter before permanent soldering.

### Bill of materials

| Part | What it is | Role |
|------|------------|------|
| **Raspberry Pi 5 (8 GB)** | Single-board computer | Runs everything: camera, tracking, web app |
| **Pi HQ Camera (IMX477)** *or* **Camera Module 3 NoIR (IMX708)** | CSI camera | Overhead video of the arena |
| **Blue "signaler" board = PCA9685** | 16-channel, 12-bit I²C PWM (HW-170) | Generates all the timed PWM signals |
| **Green "power director" = 4-channel MOSFET board** | Four `60N03` FETs, opto-isolated inputs | Switches the 12 V white/IR LED strips |
| **Small red board = SparkFun PicoBuck** | 3-channel constant-current LED driver | Drives the Cree red/blue opto LEDs at regulated current |
| **White + IR LED strips (12 V)** | Illumination / backlight around the arena | Lighting for imaging |
| **Cree star LEDs (red + blue) on bendable arms** | High-power opto emitters | Optogenetic stimulation |
| **12 V wall adapter + barrel breakout** | Power supply | Powers the LED circuits (**never** the Pi's job) |

### How it's all connected

Everything the software controls goes through the **single PCA9685 PWM board** over I²C.
The PCA9685's outputs then fan out to the two independent high-power paths:

```
        ┌──────────────── Raspberry Pi 5 ────────────────┐
        │  I²C:  SDA = GPIO2 (pin 3),  SCL = GPIO3 (pin 5) │
        │  common GND (pin 6/9/14/…)                       │
        └───────────────┬─────────────────────────────────┘
                        │  I²C
                        ▼
                ┌──────────────┐
                │   PCA9685     │   (16-channel PWM)
                └──┬───┬───┬───┬┘
      ch0 (IR) ────┘   │   │   └──── ch3 (blue opto) ─┐
      ch1 (white) ─────┘   └──────── ch2 (red opto) ──┤
             │                                        │
             ▼                                        ▼
   ┌───────────────────┐                     ┌──────────────────┐
   │ 4-ch MOSFET board │                     │    PicoBuck       │  (constant-current)
   │  gates 12 V strips│                     │  PWM dimming in   │
   └─────────┬─────────┘                     └────────┬─────────┘
             ▼                                        ▼
      White / IR LED strips                    Cree red / blue LEDs

   12 V wall adapter ──► MOSFET V+ (strips)  and  ──► PicoBuck VIN (opto)
   12 V (−) ──► shared common ground with the Pi and every board
```

**Two golden rules of this wiring** (both learned the hard way, see the guide):

1. **Common ground.** The Pi, PCA9685, MOSFET board, PicoBuck, and the 12 V supply's
   negative rail must all share a ground, or the control signals have no reference and
   nothing switches reliably.
2. **Never chop a regulated output.** The MOSFET switches the *strips*. The opto LEDs are
   dimmed by pulsing the **PicoBuck's PWM input**, never by putting a MOSFET in the
   driver's output. (One Cree LED per PicoBuck channel keeps the two sides current-matched.)

### The channel map (this is the important bit)

The software's entire understanding of your wiring lives in **[`flybox/config.py`](flybox/config.py)**.
The verified mapping is:

| PCA9685 channel | Drives |
|:---:|---|
| **ch 0** | IR LED strip |
| **ch 1** | White LED strip |
| **ch 2** | Red opto LED (via PicoBuck) |
| **ch 3** | Blue opto LED (via PicoBuck) |

If you wire yours differently, **edit `config.py`** (`LIGHT_CHANNELS` and `OPTO_CHANNELS`)
— or use the app's **channel-discovery panel** (Illumination → Advanced) to toggle a raw
channel and see what lights up, then update the file.

---

## 3. Install it on your Pi (start to finish)

You need a Raspberry Pi 5 running **Raspberry Pi OS (Bookworm, 64-bit)** with the camera
connected to the CSI port and I²C wired to the PCA9685.

### Step 1 — get the code onto the Pi

```bash
cd ~
git clone https://github.com/zacharymsmith/Pi-5-Modular-Behavior-Setup.git
cd Pi-5-Modular-Behavior-Setup
```

### Step 2 — run the setup script

```bash
bash setup.sh
```

This one script:
- installs the Python dependencies (FastAPI, uvicorn, the Adafruit PCA9685 libraries),
- makes sure the camera and I²C are enabled,
- checks that `picamera2`, OpenCV, and NumPy import cleanly,
- runs a self-test that reports whether it sees real hardware or is falling back to mock,
- prints a summary of anything that needs attention.

> **Important:** NumPy and OpenCV must come from **apt** (`python3-numpy`, `python3-opencv`),
> **never pip**. A pip-installed NumPy silently breaks `picamera2`. The setup script guards
> against this and can auto-heal it.

### Step 3 — launch the app

```bash
bash setup.sh run
```

Then open a browser to **`http://<your-pi-ip>:8000`** (or `http://raspberrypi.local:8000`).
On the Pi's own screen it auto-opens.

### Step 4 (optional) — start automatically on boot

```bash
sudo cp flybox/flybox.service /etc/systemd/system/
sudo systemctl enable --now flybox
```

Now the app comes up every time the Pi powers on. Check it with
`systemctl status flybox` and see logs with `journalctl -u flybox -f`.

### Accessing it remotely

- **Lab network:** `http://<pi-ip>:8000`
- **Pi Connect** (Raspberry Pi's official remote access): works off-site with no firewall
  changes — this is the recommended way to reach the box from anywhere.
- A self-contained Wi-Fi hotspot on the Pi's built-in radio proved unreliable; if you need
  one, use a USB Wi-Fi dongle. (`hotspot.sh` is parked reference only.)

---

## 4. First run — your first tracked recording

1. **Check the camera.** The chip at the top should show your sensor name (e.g. `imx477`
   or `imx708`) with a green dot. If it says "no camera," reseat the CSI ribbon and hit
   **↻ Retry camera**.
2. **Light the arena.** Under **Illumination**, raise the White (or IR) slider until you can
   see the flies clearly.
3. **Set exposure.** Open **Camera setup**, click **Auto-expose to arena**. Watch the
   **Highlights clipped** readout — if it's amber/red, lower the exposure or brightness
   until it's green (blown-out highlights hide detail).
4. **Focus.** HQ cam: turn the lens ring to maximize the **Focus score**. Camera Module 3:
   click **🎯 Autofocus now** (it focuses once and locks).
5. **Draw the arena.** With the **Draw → Arena** tool, click the centre of your dish and
   drag to its edge. Draw it to cover the **whole dish** — flies outside the arena are
   ignored.
6. **Enable tracking.** Flip **Enable tracking overlay**. The reference background builds
   automatically; within a second or two you'll see coloured circles on the flies. Set
   **Expected flies** to your actual count.
7. **Check detection health.** The **Detection health** readout should be green (≥85%). If
   not, raise **Detection sensitivity** or fix the lighting/exposure.
8. **Record.** Hit **● Record** (or start a full **Experiment session** to also log
   trajectories and events).

That's a complete tracked recording. Everything else below is refinement.

---

## 5. The interface, panel by panel

**Camera & recording** — the live view, draw tools (Arena / Red zone / Blue zone, ellipse
or rectangle), Record/Stop, and the per-fly **trajectory map**. A **Fullscreen video**
button enlarges the preview. *Camera setup* (collapsible) holds resolution/FPS (auto-filled
for your sensor), exposure/gain, the Contrast / Brightness / Sharpness / Saturation
sliders, auto-expose, autofocus, the brightness + highlight-clipping + focus readouts, and
the burned-in overlay configuration. *Diagnostics* (collapsible) has the mock test feed.

**Tracking** — enable, auto-tune, detection method (Reference subtraction is the
recommended default), **Expected flies**, **Detection sensitivity**, **Detect motionless
flies**, the detection-mask preview, live **Currently tracked** + **Detection health**, and
an *Advanced tuning* panel (blob size, coast/confirm frames, identity assignment, ellipse
fitting, contrast boost, motion trails).

**Optogenetics protocol** — pick a channel (red/blue) and set frequency, pulse width, burst
length, rest, repeats, and intensity. Run it, stop it, or fire a quick **Test flash**. This
same protocol is what the closed loop fires.

**Closed-loop stimulation** — **Arm closed loop**, set a **cooldown** (minimum time between
firings), enable the **proximity trigger** (fires when two flies get within a distance), set
the **mm/px calibration**, and see the live fire counts. Trigger zones are drawn on the
video in the Camera panel.

**Experiment session** — the reproducible way to run: give it an experimenter/genotype/notes,
hit **Start**, and it records the video + `events.csv` + `tracks.csv` + `config.json`
together into one timestamped folder (and can arm the closed loop with it).

**Presets · reproducibility** — save your whole setup (or just parts — tick Stim / Tracking /
Zones / Camera / Calibration) and reload it exactly. Also sets the data folder for
everything.

**Scheduler** (collapsible) — build a timed phase plan (e.g. baseline → stim → rest) that
runs automatically with recording and logging.

**Illumination** — white/IR sliders and, under Advanced, the raw PCA9685 channel-discovery
tool for verifying/relabeling your wiring.

---

## 6. Getting the tracking flawless

The tracker was tuned against 20+ real recordings. The defaults are strong; these are the
levers when a specific arena is fussy:

- **Draw the arena to cover the whole dish.** The #1 cause of lost flies is an arena drawn
  too small — a fly that wanders past the boundary is masked out and vanishes.
- **Get Detection health green.** If it's low, the *image* is the problem, not the tracker:
  raise **Detection sensitivity**, raise **Contrast**, or improve the lighting/exposure.
  Low contrast (faint flies on a blown-out dish) is the usual culprit — the clipping meter
  helps you spot it.
- **Set Expected flies to the true count.** This caps over-detection: only the N most
  fly-like blobs become tracks, so rim/corner artifacts never turn into phantom flies.
- **Reference subtraction is the best method** for a static arena. It builds a background
  automatically and self-heals, so lighting drift and a briefly-still fly can't blind it.
  Turn on **Detect motionless flies** if a fly may sit completely still (or is dead).
- **Higher frame rate = fewer ID swaps.** Flies move less between frames. Video encoding on
  the Pi 5 is the fps bottleneck (no hardware encoder), so for maximum tracking rate, turn
  **off** the "annotated copy" and/or drop the resolution — encoding is threaded so tracking
  already runs decoupled from it.
- **Identical flies crossing** is the one hard limit: no real-time, appearance-blind tracker
  can perfectly tell two identical flies apart through a tight crossing. The tracker
  prefers to *fragment* (give a new ID) rather than silently *swap* identities, so your data
  is never quietly corrupted. Switch identity assignment to **Hungarian** (Advanced) if you
  want zero swaps at the cost of a few more fragments.

### Illustrated: what each adjustment does

**A healthy tracked frame** — the yellow arena covers the dish and each fly has a coloured
ID marker:

![Tracking overlay](docs/images/01_tracking_overlay.jpg)

**The detection mask** is what the tracker actually "sees." Turn on **Show detection mask**
to check it: clean, separate white blobs (one per fly) means detection is healthy. If it's
speckly, noisy, or blank, fix the image before worrying about identities.

![Detection mask](docs/images/02_detection_mask.jpg)

**Exposure / highlight clipping.** When the dish is too bright, pixels blow out to pure
white (left, red = clipped — detail lost, and flies crossing the bright area can vanish).
Lower the exposure/brightness until the **Highlights clipped** meter goes green (right):

![Highlight clipping before and after](docs/images/03_highlight_clipping.jpg)

**Saturation → 0 (greyscale).** The tracker works on brightness, not colour. Dropping
saturation removes the pink dish hue and makes the dark flies stand out — especially useful
under IR with a NoIR camera:

![Colour vs greyscale](docs/images/04_saturation_greyscale.jpg)

**Draw the arena to cover the whole dish** (left). If it's too small (right), any fly that
wanders to the edge is masked out and its track is lost — the single most common cause of
"a fly disappeared":

![Arena sizing good vs too small](docs/images/05_arena_sizing.jpg)

**The trajectory map** (Camera panel) draws each fly's path in its own colour, thicker where
it spent more time — an at-a-glance view of identities and behaviour:

![Trajectory map](docs/images/06_trajectory_map.jpg)

---

## 7. What each experiment saves (your data)

Every **Experiment session** writes a self-contained, reproducible folder:

```
recordings/20260713_130514_myassay/
├── 20260713_130514.mp4            # the raw recording
├── 20260713_130514_annotated.mp4  # optional: with tracking + overlay burned in
├── config.json                    # COMPLETE recipe: every tracker + camera setting,
│                                   #   protocols, zones, calibration, git commit,
│                                   #   plus an end-of-run summary (per-fly stats, dose)
├── events.csv                     # every stimulation + zone enter/exit (t, source,
│                                   #   channel, protocol, dose_mJ_cm2)
├── tracks.csv                     # per-frame trajectories, aligned 1:1 to the video:
│                                   #   t_s, frame, id, x/y (px & mm), vx/vy, speed, angle
└── trajectory.png                 # rendered per-fly trajectory plot
```

`config.json` captures **every** setting automatically, so a run is fully reproducible —
someone can read it and recreate exactly how the video was captured and tracked, down to
the brightness slider. Set the **mm/px calibration** and per-channel **irradiance (mW/cm²)**
so distances log in millimetres and stimulation logs real light dose (mJ/cm²).

---

## 8. Cameras: HQ vs Camera Module 3 NoIR

The app **auto-detects** the sensor and fills in its native resolution/FPS modes — no
config needed when you swap cameras.

- **HQ Camera (IMX477):** manual-focus lens (use the on-screen Focus score to set it). Has
  an IR-cut filter, so for pure IR imaging you'd want an IR-pass filter or the NoIR variant.
- **Camera Module 3 NoIR (IMX708):** has **autofocus** (a 🎯 Autofocus now button appears,
  focuses once and locks — ideal for a fixed dish) and **no IR-cut filter**. Under IR
  illumination this images flies as high-contrast dark shapes and makes the red/blue opto
  flashes nearly invisible to the tracker — the best setup for clean, opto-immune tracking.
  Drop **Saturation to 0** for a clean greyscale image.

---

## 9. Safety before you power the LEDs

From the [hardware guide](HARDWARE_AND_SOFTWARE_GUIDE.md) — do these **before** running any
LEDs:

- **Barrel polarity** — meter it; confirm ~+12 V, center-positive as expected for your supply.
- **Common ground** — Pi ↔ PCA9685 ↔ MOSFET ↔ PicoBuck ↔ 12 V (−) all tied together.
- **PicoBuck current** — set it with a series ammeter, power off when rewiring, start low.
  One LED per channel.
- **Irradiance at the fly plane** — measure in **mW/cm²** with a power meter; do not infer
  optogenetic dose from drive current. Enter it in the app so dose logs correctly.
- **Thermal** — heatsink the star LEDs and check temperature under your *longest* protocol.

The software also enforces caps (`OPTO_MAX_INTENSITY`, `OPTO_MAX_TRAIN_S`) in `config.py`.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Chip shows "no camera" | Reseat the CSI ribbon (correct orientation), hit **↻ Retry camera**. On the Pi: `libcamera-hello --list-cameras`. |
| Setup self-test says "camera in use" | Not a fault — the app is already running and holding the camera. Stop it first if you want to run the diagnostic. |
| App runs but flies aren't detected | Turn on **Show detection mask** to see what the tracker sees (see the illustrated guide in §6). Detection health low → raise **Detection sensitivity** / **Contrast**, fix lighting/clipping, and make sure the **arena** covers the dish and **Expected flies** is set. |
| A fly keeps disappearing | Usually the **arena is drawn too small** — redraw it over the whole dish (see §6 arena image). |
| Video too bright / washed out | The **Highlights clipped** meter is amber/red — lower exposure or brightness until green (§6 clipping image). |
| Video plays too fast / low fps | Pi 5 has no hardware video encoder. Turn off the annotated copy, lower resolution; encoding is threaded so tracking stays fast. |
| Lights/opto don't respond | Check **common ground**, then use Illumination → Advanced channel discovery to confirm the `config.py` channel map. |
| `RPi.GPIO` / `pigpio` errors | Those libraries are dead on the Pi 5. This project uses `lgpio` + the Adafruit PCA9685 stack (over I²C). |
| Everything says "mock" | `picamera2` / I²C not available — run `bash setup.sh` and check the summary. The app still fully works in mock mode for learning. |

---

## 11. How the code is organized

```
app.py            FastAPI server — all HTTP routes, wires the modules together
camera.py         One capture loop → preview + recording + frame callback;
                    sensor auto-detect, exposure/focus, threaded video encoder
tracker.py        Real-time OpenCV tracking (detection + identity assignment)
closed_loop.py    Zone/proximity triggers → opto; live analytics (trajectories)
opto.py           Optogenetic pulse-train protocols (red/blue via PCA9685)
illumination.py   White/IR strips + raw channel discovery
hardware.py       Single shared PCA9685 bus (mock-aware)
session.py        Experiment session logging (config.json / csv / trajectory.png)
scheduler.py      Timed phase plans
presets.py        Save/load full setups
config.py         *** ALL hardware settings — edit to match your wiring ***
templates/index.html   The entire web UI
flybox.service    systemd unit for start-on-boot
```

Every hardware module has a **mock fallback**, so the complete app — tracking and closed
loop included — runs on a laptop with no Pi hardware for development or demos.

---

## 12. Credits & methods

Built for a Raspberry Pi 5 *Drosophila* optogenetics rig. The tracking approach
(reference/background subtraction with an adaptive, self-healing background) is in the
spirit of established open tools such as the Gilestro lab's **ethoscope** and **Ctrax**
(Branson et al., *Nature Methods*, 2009); identity assignment uses velocity-predicted
nearest-neighbour with an optional optimal (Hungarian / Kuhn–Munkres) matcher.

Deep-dive hardware reference and pre-solder checklist:
**[`HARDWARE_AND_SOFTWARE_GUIDE.md`](HARDWARE_AND_SOFTWARE_GUIDE.md)**.
