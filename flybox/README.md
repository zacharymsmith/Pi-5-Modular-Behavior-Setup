# FlyBox Controller

Web-based control for the Pi 5 modular fly-behavior box: live camera preview +
recording, illumination (PCA9685 → MOSFET board → 12 V strips), and optogenetic
pulse trains (RP1 hardware PWM → PicoBuck). Closed-loop tracking is stubbed for later.

Open it in any browser on the LAN, over Pi Connect, or via the Pi's own WiFi (AP mode).
Replaces the single-user tkinter GUI.

> ⚠️ **Wiring is not yet verified.** The board→load mapping in `config.py` is a best
> guess and your wiring may currently be jumbled. Use the **Channel discovery** panel
> in the UI (or `illumination.lights.sweep()`) to learn what each channel actually
> drives, then correct `config.py`. See `../HARDWARE_AND_SOFTWARE_GUIDE.md`.

## Files
| File | Purpose |
|------|---------|
| `app.py` | FastAPI server + routes |
| `config.py` | **All pin/channel/camera settings — edit this to match your wiring** |
| `opto.py` | Hardware-PWM pulse-train protocols → PicoBuck |
| `illumination.py` | PCA9685 illumination + channel discovery |
| `camera.py` | picamera2 preview + recording |
| `closed_loop.py` | OpenCV tracking → opto trigger (stub, Phase 4) |
| `templates/index.html` | Browser UI |
| `flybox.service` | systemd unit (start on boot) |

Every hardware module has a **mock fallback**, so the app runs on a laptop for UI work
without any Pi hardware attached.

## Setup on the Pi 5

1. Enable hardware PWM for the opto pins. Edit `/boot/firmware/config.txt`:
   ```
   dtoverlay=pwm          # GPIO18 only.  Use pwm-2chan if you also wire GPIO19.
   dtparam=i2c_arm=on     # for the PCA9685
   ```
   Reboot.

2. Install deps (use the **system** Python so picamera2 is importable):
   ```
   sudo apt install -y python3-picamera2 python3-pip i2c-tools
   pip install --break-system-packages -r requirements.txt
   ```

3. Confirm hardware is visible:
   ```
   i2cdetect -y 1              # expect 0x40 for the PCA9685
   ls /sys/class/pwm/pwmchip2  # RP1 PWM present
   cat /proc/device-tree/model # confirm "Raspberry Pi 5"
   ```

4. Run:
   ```
   cd flybox
   python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
   ```
   Open `http://<pi-ip>:8000`.

5. (Optional) Start on boot: install `flybox.service` (see comments in that file).

## Remote access options
- **LAN:** `http://<pi-ip>:8000` or `http://raspberrypi.local:8000`.
- **Pi Connect:** for remote/off-site access without firewall setup.
- **AP mode:** make the Pi broadcast its own WiFi (like your environmental monitor) so
  the box is self-contained — connect to its SSID, open the same URL.

## Safety before running LEDs
Do the pre-solder checklist in `../HARDWARE_AND_SOFTWARE_GUIDE.md` §8 first:
verify barrel polarity, common ground, PicoBuck current with a series ammeter (start low),
and irradiance at the fly plane in mW/cm².

## Roadmap
1. ✅ Preview + record, illumination, opto protocols (this scaffold).
2. Verify/rebuild wiring map via discovery panel.
3. Tune camera exposure/IR, add experiment logging (protocol + timestamps → CSV/JSON).
4. Closed loop: implement `closed_loop.detect()` + `trigger_condition()`.
