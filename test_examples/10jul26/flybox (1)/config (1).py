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
PROCESS_SIZE = (1024, 768)      # default capture/record/track resolution (w, h)
PREVIEW_SIZE = (640, 480)       # downscaled JPEG sent to the browser
CAMERA_FPS = 30
JPEG_QUALITY = 80
RECORDING_DIR = "recordings"    # created under the app folder
# Exposure/gain defaults (picamera2 controls). auto=True lets the camera decide.
CAMERA_AUTO_EXPOSURE = True
CAMERA_EXPOSURE_US = 8000       # microseconds, used when auto is off
CAMERA_GAIN = 1.0               # analogue gain, used when auto is off

# ---------------------------------------------------------------------------
# Presets (saved experiment configurations for reproducibility)
# ---------------------------------------------------------------------------
PRESETS_DIR = "presets"

# ---------------------------------------------------------------------------
# Motion trails overlay
# ---------------------------------------------------------------------------
TRAIL_ENABLED = False
TRAIL_LENGTH = 30               # frames of history to draw

# ---------------------------------------------------------------------------
# Tracking (OpenCV)
# ---------------------------------------------------------------------------
TRACK_AUTO_THRESHOLD = True    # Otsu auto-threshold (adapts to lighting) — robust default
TRACK_THRESHOLD = 60           # manual 0..255 grayscale threshold (used when auto is off)
TRACK_INVERT = True            # True = dark subject on light background
TRACK_MIN_AREA = 25            # px; ignore blobs smaller than this (fly ~50-100px at 1024px)
TRACK_MAX_AREA = 4000          # px; ignore blobs bigger than this (e.g. arena/reflections)
TRACK_MAX_BLOBS = 10           # cap detections per frame
TRACK_MATCH_DIST_PX = 90       # max px a fly can move between frames to keep its ID
TRACK_TOPHAT_KERNEL = 25       # px; ~2x fly body length (illumination-invariant method)
TRACK_MAX_MISSED = 18          # frames to coast a track through a detection gap (keeps ID)
TRACK_CONFIRM_FRAMES = 3       # a new blob must persist this many frames before it gets an ID
TRACK_EXPECTED_FLIES = 2       # cap on reported flies (0 = unlimited). This is the single
                               # biggest anti-over-detection lever: it keeps only the N most
                               # fly-like blobs, so bright rim/corner artefacts never become
                               # tracks. Set to your fly count per arena (2 = default pair assay).
TRACK_DETECT_MAX_W = 0         # downscale detection to this width (0 = full res); lets you
                               # record hi-res while tracking fast (e.g. 800)

# ---------------------------------------------------------------------------
# Spatial calibration
# ---------------------------------------------------------------------------
MM_PER_PX = None               # set via UI (measure a known distance); None = px only

# ---------------------------------------------------------------------------
# Closed loop
# ---------------------------------------------------------------------------
CLOSED_LOOP_COOLDOWN_S = 2.0    # min seconds between triggered stimulations

# ---------------------------------------------------------------------------
# Optogenetics safety + light dose
# ---------------------------------------------------------------------------
OPTO_MAX_INTENSITY = 1.0        # hard cap on commanded intensity (0..1)
OPTO_MAX_TRAIN_S = 30.0         # cap a single burst's ON duration (thermal safety)
# Measured irradiance at intensity=1.0, in mW/cm^2 at the fly plane. Fill after
# metering so the log can report actual dose instead of PWM %.
OPTO_IRRADIANCE_MW_CM2 = {"red": None, "blue": None}

# ---------------------------------------------------------------------------
# Sessions / experiment logging
# ---------------------------------------------------------------------------
SESSIONS_DIR = "recordings"     # each run gets a timestamped subfolder here
LOG_TRACKS_HZ = 0               # 0 = every frame; else throttle tracks.csv writes

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"   # all interfaces (LAN + AP mode)
PORT = 8000
