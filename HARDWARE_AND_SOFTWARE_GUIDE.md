# Pi 5 Modular Fly-Behavior Box — Canonical Hardware & Software Guide

_Last updated: 2026-07-07. This supersedes the earlier ChatGPT handoff, which named
several parts that are **not** present in the current photos (no Adafruit STEMMA MOSFET,
no LD24AJTA green drivers, no FemtoBuck). Where this guide and the old handoff disagree,
trust this guide — but always re-verify against the physical boards and a multimeter
before permanent soldering._

---

## 1. System purpose

A Raspberry Pi 5 controlled behavior rig for _Drosophila_:

- Overhead HQ camera for IR video tracking.
- White + IR LED strips around the arena base (12 V) for illumination / backlighting.
- Two Cree royal-blue star LEDs on bendable arms for optogenetic stimulation.
- Software-controlled ON/OFF, pulse trains, and (eventually) closed-loop stimulation
  triggered from OpenCV tracking.
- 12 V wall supply powers the high-power LED circuits. **The Pi never powers the LEDs** —
  the Pi only provides logic-level control signals.

---

## 2. Actual boards (decoded from photos)

| Board (your words)        | What it really is                          | Role                                                                 |
|---------------------------|--------------------------------------------|----------------------------------------------------------------------|
| Blue "signaler" board     | **PCA9685** 16-channel, 12-bit I²C PWM (HW-170) | Generates timed PWM. Pi talks to it over I²C (SDA/SCL).         |
| Green "power director"    | **4-channel MOSFET switch** (four `60N03` DPAK FETs, opto-isolated inputs, IN1–IN4 / OUT1–OUT4) | Switches the 12 V white/IR strips. |
| Small red board           | **SparkFun PicoBuck** — 3-channel constant-current LED driver (AL8805, `330` inductors) | Drives the Cree red/blue opto LEDs at regulated current. |
| Pi 5 in white case        | Raspberry Pi 5 (8 GB)                       | Camera capture, control logic, web app.                              |
| Orange ribbon             | CSI camera cable → HQ camera                | Video in.                                                            |

> **Verify before trusting wavelengths/currents:** confirm the Cree emitter SKU from your
> original order (royal-blue ~450 nm is typical for CsChrimson-adjacent tools, but check),
> and confirm the PicoBuck version (v12+ has a solder jumper that raises max current from
> 350 mA to 660 mA per channel).

---

## 3. Signal & power chain (corrected)

```
                    ┌─────────────── Raspberry Pi 5 ───────────────┐
                    │  I²C (SDA=GPIO2/pin3, SCL=GPIO3/pin5)         │
                    │        │                                     │
                    │        └──► PCA9685 ──► MOSFET board IN ──► 12V white / IR strips
                    │                                             │
                    │  Hardware PWM (GPIO18 / pin12, PWM chan 2)   │
                    │        └──────────────► PicoBuck PWM input ──► Cree opto LED(s)
                    │  GND (pin6/9/14…) ──── common ground to every board
                    └──────────────────────────────────────────────┘

12 V wall adapter ──► barrel breakout ──┬──► MOSFET board V+  (strip power)
                                        └──► PicoBuck VIN     (opto LED power)
```

**Grounding rule:** the Pi, PCA9685, MOSFET board, and PicoBuck must share a **common
ground** with the 12 V supply's negative rail, or the logic signals have no reference.
(This is exactly the symptom the old handoff hit: the MOSFET board only switched correctly
once its logic power/ground reference was connected.)

---

## 4. Control paths (opto vs. strips are SEPARATE)

**Confirmed by the builder: the MOSFET board is _not_ in line with the PicoBuck.** Good —
that is the correct topology. The two 12 V loads are controlled by two independent paths:

- **Strips (white / IR):** PCA9685 PWM → **4-channel MOSFET board** gates → 12 V strips.
  The MOSFET switches the strip current only. It is not in the opto/LED-driver path.
- **Opto LEDs (Cree):** **PicoBuck** regulates current; you modulate ON/OFF and intensity by
  driving the **PicoBuck's dedicated PWM dimming input** — no MOSFET in this path.

This avoids the failure mode from the old handoff (a MOSFET interrupting a constant-current
driver's output, which causes inductor overshoot and ringing pulse edges). Never chop a
regulated output; always pulse the driver's control input.

**PicoBuck PWM input** accepts a logic PWM signal per channel:

- PWM low level must be **< 0.4 V**, high level **> 2.4 V** (3.3 V Pi logic satisfies this).
- Gives a full 0–100 % range; you can drive each channel independently or tie inputs together.
- Set the **baseline current** with the driver, then **modulate ON/OFF and intensity with PWM**.

Same principle for the strips: PWM the MOSFET gate (via PCA9685), never interrupt a
regulated output.

### Recommended opto wiring
```
12V+  ─────────────► PicoBuck VIN
12V-  ─────────────► PicoBuck GND  (also common ground to Pi)
PicoBuck LED ch1 +/- ─► Cree LED #1   (one LED per channel is simplest & current-matched)
PicoBuck LED ch2 +/- ─► Cree LED #2
Pi GPIO18 (pin12) ──► PicoBuck IN1 (PWM)   ← hardware PWM, clean pulse trains
Pi GPIO19 (pin35) ──► PicoBuck IN2 (PWM)   ← optional independent 2nd side
Pi GND (pin6)     ──► PicoBuck GND / signal ground
```
Running **one LED per PicoBuck channel** sidesteps the unequal-current-sharing problem you
measured in parallel (0.92 A vs 0.3 A) and keeps both sides identical and independently
controllable.

---

## 5. Pi 5 GPIO / PWM software stack

The Pi 5's RP1 I/O chip **broke the old libraries**:

- `RPi.GPIO` → `RuntimeError: Cannot determine SOC peripheral base address`. **Dead. Do not use.**
- `pigpio` → daemon fails: "this system does not appear to be a raspberry pi". **Dead. Do not use.**
- `lgpio` / `gpiozero` (lgpio backend) → **works**. Use for simple on/off and PCA9685 I²C.
- **True hardware PWM** for opto timing → `rpi-hardware-pwm` (uses the RP1 PWM via sysfs).

### Why hardware PWM for opto
`gpiozero.PWMOutputDevice` is software/timer PWM — it jitters under CPU load, which will be
happening constantly once the camera + tracking are running. Optogenetic pulse trains need
stable edges, so generate them with the RP1 hardware PWM instead.

### Pi 5 hardware-PWM setup (differs from Pi 4!)
1. Edit `/boot/firmware/config.txt`, add:
   ```
   dtoverlay=pwm          # single channel (GPIO18). Use pwm-2chan ONLY if you also need GPIO19.
   ```
   Note: on the Pi 5 use `dtoverlay=pwm` (RP1), **not** the Pi 4 `pwm-2chan` targeting the SoC.
2. Reboot.
3. In `rpi-hardware-pwm`, GPIO18 is **channel 2 on pwmchip2**:
   ```python
   from rpi_hardware_pwm import HardwarePWM
   pwm = HardwarePWM(pwm_channel=2, hz=50, chip=2)   # chip=2 => RP1 on Pi 5
   pwm.start(0)          # duty %
   pwm.change_frequency(20)
   pwm.change_duty_cycle(20)   # 20% => 10 ms pulse at 20 Hz
   ```
   Channel map: chan0→GPIO12, chan1→GPIO13, chan2→GPIO18, chan3→GPIO19.

### Pulse-protocol parameterization (keep this)
Define protocols as **frequency_Hz + pulse_width_ms + train_duration_s + rest_s + n_bursts**,
not just frequency + duty. "20 Hz, 10 ms pulses" is unambiguous; duty% hides the pulse width.
The app converts pulse width → duty internally: `duty% = (pulse_width_ms / (1000/freq)) * 100`,
and rejects `duty > 100` (pulse wider than the period).

---

## 6. Camera & video

- Use `picamera2` (libcamera). The HQ cam (IMX477) works on the Pi 5 CSI port.
- **Pi 5 has no hardware H.264 encoder** — encoding is software (ffmpeg). Quality is fine,
  but it competes for CPU with tracking. Pattern: **record full-res to disk**, run **tracking
  on a downscaled/cropped or MJPEG stream**. Budget CPU before enabling closed loop.
- For IR tracking, the HQ cam has an IR-cut filter — for pure IR imaging you may want the
  NoIR variant or a filter swap. Verify your sensor sees your IR wavelength.

---

## 7. Recommended app architecture

One **FastAPI service on the Pi**, browser UI, replacing the tkinter GUI (which needs a
screen/VNC, is single-user, and fights the camera). The service owns:

1. **Camera** — live MJPEG preview + full-res recording (`picamera2`).
2. **Illumination** — white/IR via PCA9685 → MOSFET board (timing not critical).
3. **Opto** — pulse-train protocols via hardware PWM → PicoBuck (timing critical).
4. **Closed loop (later)** — OpenCV centroid tracking → trigger opto.

Access: lab-network web UI as primary, **Pi Connect** for remote, **AP mode** as a portable
fallback. The scaffold in `/flybox` implements 1–3 and stubs 4.

The provided tkinter script is fine as a **throwaway bench tester** to confirm an LED pulses
today, but do not build on it — it uses software PWM and references FemtoBuck limits that
aren't your hardware.

---

## 8. Pre-solder verification checklist

- [ ] **Barrel polarity** — DC-volts, black on barrel outer, red on center; expect ~+12 V.
      Don't assume center-positive; check the label/meter.
- [ ] **Common ground** — Pi GND ↔ PCA9685 GND ↔ MOSFET GND ↔ PicoBuck GND ↔ 12 V (−).
- [ ] **PicoBuck current setpoint** — measure in series with an ammeter, power off when
      rewiring, start low, adjust up. The trimmer is very sensitive and non-linear.
- [ ] **One LED per PicoBuck channel** — avoids the 0.92 A vs 0.3 A imbalance seen in parallel.
- [ ] **Logic levels into PicoBuck PWM** — confirm Pi 3.3 V PWM reaches >2.4 V high, <0.4 V low.
- [ ] **Irradiance at the fly plane** — measure in **mW/cm²** with a power meter; do not infer
      optogenetic dose from LED drive current.
- [ ] **Thermal** — high-power stars need heatsinking; measure star temperature under your
      **longest** intended protocol, not just a short pulse.
- [ ] **Confirm Pi model / OS** — `cat /proc/device-tree/model` and `uname -a`.

---

## 9. Empirical facts worth preserving

- 12 V supply successfully powered the driver; single LED very bright at high current.
- LED survived a temporary shutoff and passed diode-mode test.
- Measured ~0.92 A in one branch, ~0.3 A in a parallel second branch (→ don't parallel).
- Driver later adjusted toward ~0.3–0.4 A (approximate; re-measure in final circuit).
- Both LEDs illuminate; series chain lights when LED2− returns to driver −.
- MOSFET/PCA SIG responds to control; board switches only once logic power+ground referenced.
- `RPi.GPIO` and `pigpio` both fail on this Pi 5; `lgpio`/`gpiozero` work.

---

## 10. Sources

- Pi 5 hardware PWM (RP1, `dtoverlay=pwm`, pwmchip2/channel 2): rpi-hardware-pwm on PyPI and
  Raspberry Pi forums.
- PicoBuck PWM dimming (0.4 V / 2.4 V thresholds, 350/660 mA): SparkFun PicoBuck Hookup Guide.
- Pi 5 software video encoding: raspberrypi/picamera2 issue #1135.
