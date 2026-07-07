"""Central configuration for the FlyBox behavior rig.

Everything reads from here, so you only edit hardware facts in one place.
Values marked "verified via discovery panel" were confirmed empirically on the
real box on 2026-07-07.
"""

# ---------------------------------------------------------------------------
# PCA9685 (single I2C board that drives BOTH the strips and the opto LEDs)
# ---------------------------------------------------------------------------
PCA9685_I2C_ADDRESS = 0x40   # default address pads
PCA9685_I2C_BUS = 1          # /dev/i2c-1
PCA9685_PWM_FREQ_HZ = 1000   # carrier: high enough to be flicker-free on camera

# Illumination strips  (PCA9685 -> 4-channel MOSFET board -> 12V strips)
# Verified: white/IR were reversed from the initial guess.
LIGHT_CHANNELS = {
    "white": 1,   # PCA9685 ch1 -> white strip
    "ir": 0,      # PCA9685 ch0 -> IR strip
}

# Optogenetics  (PCA9685 -> PicoBuck PWM inputs -> Cree LEDs)
# Verified: ch2 = RED, ch3 = BLUE. Pulse trains are software-gated ON/OFF so the
# PCA9685 carrier stays free for strip dimming; duty during "ON" = intensity.
OPTO_CHANNELS = {
    "red": 2,
    "blue": 3,
}
OPTO_MAX_FREQ_HZ = 100          # software-gated; keep modest for timing fidelity
OPTO_DEFAULT_FREQ_HZ = 20
OPTO_DEFAULT_INTENSITY = 1.0    # 0..1

# ---------------------------------------------------------------------------
# Camera (picamera2 / IMX477 HQ cam)
# ---------------------------------------------------------------------------
# One capture stream feeds preview + recording + tracking. Keep it modest so the
# Pi 5 can track in real time; record at the same resolution.
PROCESS_SIZE = (1024, 768)      # capture/record/track resolution (w, h)
PREVIEW_SIZE = (640, 480)       # downscaled JPEG sent to the browser
CAMERA_FPS = 30
JPEG_QUALITY = 80
RECORDING_DIR = "recordings"    # created under the app folder

# ---------------------------------------------------------------------------
# Tracking (OpenCV)
# ---------------------------------------------------------------------------
TRACK_THRESHOLD = 60            # 0..255 grayscale threshold
TRACK_INVERT = True            # True = dark subject on light background
TRACK_MIN_AREA = 5             # px; ignore blobs smaller than this
TRACK_MAX_BLOBS = 10           # cap detections per frame

# ---------------------------------------------------------------------------
# Closed loop
# ---------------------------------------------------------------------------
CLOSED_LOOP_COOLDOWN_S = 2.0    # min seconds between triggered stimulations

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"   # all interfaces (LAN + AP mode)
PORT = 8000
