"""Central hardware configuration for the fly-behavior box.

Edit these to match your wiring. Everything else reads from here so you never
hunt for a pin number in the middle of a module.
"""

# ---------------------------------------------------------------------------
# Optogenetics — hardware PWM into the PicoBuck PWM dimming input(s)
# ---------------------------------------------------------------------------
# On the Pi 5, rpi-hardware-pwm addresses the RP1 controller as chip=2.
# Channel map:  chan0->GPIO12, chan1->GPIO13, chan2->GPIO18, chan3->GPIO19.
OPTO_PWM_CHIP = 2          # RP1 on Pi 5
OPTO_CHANNELS = {
    # name: rpi-hardware-pwm channel  (GPIO18 = chan 2, GPIO19 = chan 3)
    "blue_left": 2,        # GPIO18 / physical pin 12  -> PicoBuck IN1
    "blue_right": 3,       # GPIO19 / physical pin 35  -> PicoBuck IN2  (optional 2nd side)
}
OPTO_MAX_FREQ_HZ = 2000    # sane cap; PicoBuck/optogenetics rarely need more
OPTO_DEFAULT_FREQ_HZ = 20

# ---------------------------------------------------------------------------
# Illumination — PCA9685 (I2C) -> 4-channel MOSFET board -> 12V strips
# ---------------------------------------------------------------------------
PCA9685_I2C_ADDRESS = 0x40   # default; matches the solder-jumper address pads
PCA9685_PWM_FREQ_HZ = 1000   # strips: high enough to be flicker-free on camera
# Map a friendly light name to the PCA9685 output channel that drives the
# corresponding MOSFET input (IN1..IN4).
LIGHT_CHANNELS = {
    "white": 0,   # PCA9685 ch0 -> MOSFET IN1 -> white strip
    "ir": 1,      # PCA9685 ch1 -> MOSFET IN2 -> IR strip
}

# ---------------------------------------------------------------------------
# Camera (picamera2 / IMX477 HQ cam)
# ---------------------------------------------------------------------------
CAMERA_PREVIEW_SIZE = (640, 480)     # low-res MJPEG for the browser preview
CAMERA_RECORD_SIZE = (1920, 1080)    # full-res recording to disk
CAMERA_FPS = 30
RECORDING_DIR = "recordings"         # created under the app folder

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"   # listen on all interfaces (LAN + AP mode)
PORT = 8000
