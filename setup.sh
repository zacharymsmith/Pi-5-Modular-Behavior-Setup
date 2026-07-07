#!/usr/bin/env bash
#
# FlyBox one-shot setup + diagnostic for Raspberry Pi 5.
#
#   bash setup.sh          install deps, enable PWM/I2C, run diagnostics
#   bash setup.sh check    diagnostics only (change nothing)
#   bash setup.sh run      setup + diagnostics, then launch the web app
#
# Safe to run more than once. It only appends config lines that are missing.

set -uo pipefail
MODE="${1:-full}"

# --- pretty output -------------------------------------------------------
G="\033[32m"; Y="\033[33m"; R="\033[31m"; B="\033[34m"; N="\033[0m"
ok()   { echo -e "  ${G}✓${N} $1"; }
warn() { echo -e "  ${Y}!${N} $1"; ISSUES+=("$1"); }
fail() { echo -e "  ${R}✗${N} $1"; ISSUES+=("$1"); }
info() { echo -e "${B}$1${N}"; }
ISSUES=()
REBOOT_NEEDED=0

# locate the app folder relative to this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/flybox"
CONFIG_TXT="/boot/firmware/config.txt"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="/boot/config.txt"   # older layout

echo
info "════════ FlyBox setup ($MODE) ════════"

# --- 0. Confirm we're on a Pi -------------------------------------------
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
echo
info "Board"
if echo "$MODEL" | grep -qi "raspberry pi 5"; then
  ok "$MODEL"
elif echo "$MODEL" | grep -qi "raspberry pi"; then
  warn "$MODEL — this scaffold targets the Pi 5; PWM channel/chip may differ."
else
  fail "Not a Raspberry Pi ($MODEL). Run this on the Pi over SSH."
fi

# --- 1. Install + enable (skipped in check mode) ------------------------
if [ "$MODE" != "check" ]; then
  echo
  info "Installing system packages (may prompt for your password)"
  sudo apt-get update -qq
  # numpy + opencv come from apt (NOT pip) so they match picamera2's ABI.
  sudo apt-get install -y -qq python3-picamera2 python3-pip i2c-tools \
       python3-libcamera python3-numpy python3-opencv git >/dev/null \
       && ok "apt packages installed" \
       || warn "apt install had problems — see output above"

  echo
  info "Installing Python packages"
  pip install --break-system-packages -q -r "$APP_DIR/requirements.txt" \
       && ok "pip packages installed" \
       || warn "pip install had problems — see output above"

  # Guard: a pip numpy/opencv in ~/.local or /usr/local shadows the apt builds
  # and breaks picamera2/simplejpeg with an ABI error ("numpy.dtype size
  # changed"). Detect and remove the shadow so the apt versions load.
  if python3 -c "import numpy,sys; sys.exit(0 if ('.local' in numpy.__file__ or '/usr/local' in numpy.__file__) else 1)" 2>/dev/null; then
    warn "found a pip numpy shadowing the system numpy — removing it"
    pip uninstall -y --break-system-packages numpy opencv-python opencv-python-headless >/dev/null 2>&1 || true
    sudo apt-get install -y -qq --reinstall python3-numpy python3-opencv >/dev/null 2>&1 || true
  fi
  if python3 -c "import picamera2" 2>/dev/null; then
    ok "picamera2 imports cleanly"
  else
    warn "picamera2 not importable — try: pip uninstall -y numpy (removes a shadowing copy)"
  fi

  echo
  info "Enabling hardware PWM + I2C in $CONFIG_TXT"
  ensure_line() {  # $1 = line to guarantee present
    if grep -qxF "$1" "$CONFIG_TXT"; then
      ok "already set: $1"
    else
      echo "$1" | sudo tee -a "$CONFIG_TXT" >/dev/null
      ok "added: $1"
      REBOOT_NEEDED=1
    fi
  }
  ensure_line "dtoverlay=pwm"        # GPIO18 hardware PWM (RP1). Use pwm-2chan for GPIO18+19.
  ensure_line "dtparam=i2c_arm=on"   # PCA9685 I2C bus
fi

# --- 2. Diagnostics ------------------------------------------------------
echo
info "Hardware checks"

# Hardware PWM — opto runs via the PCA9685 now, so this is informational only.
# (Only matters if you later rewire the PicoBuck inputs to GPIO18/19.)
if [ -d /sys/class/pwm/pwmchip2 ]; then
  ok "hardware PWM present (pwmchip2) — available if you ever rewire opto to GPIO"
else
  echo "  · hardware PWM (pwmchip2) not active — not needed; opto runs via PCA9685"
fi

# I2C + PCA9685 at 0x40
if command -v i2cdetect >/dev/null; then
  if i2cdetect -y 1 2>/dev/null | grep -qiE '(^|[[:space:]])40([[:space:]]|$)'; then
    ok "PCA9685 detected on I2C bus 1 at 0x40"
  else
    warn "PCA9685 (0x40) not seen on I2C — check wiring/power, or reboot if I2C was just enabled. Scan: i2cdetect -y 1"
  fi
else
  warn "i2c-tools not installed yet (run full setup, not check)."
fi

# Camera
CAM_CMD=""
command -v rpicam-hello >/dev/null && CAM_CMD="rpicam-hello"
[ -z "$CAM_CMD" ] && command -v libcamera-hello >/dev/null && CAM_CMD="libcamera-hello"
if [ -n "$CAM_CMD" ]; then
  if $CAM_CMD --list-cameras 2>/dev/null | grep -qi "imx\|camera"; then
    ok "camera detected ($CAM_CMD --list-cameras)"
  else
    warn "no camera reported — check the CSI ribbon seating/orientation. Try: $CAM_CMD --list-cameras"
  fi
else
  warn "libcamera tools not found — install with full setup."
fi

# --- 3. Python import checks --------------------------------------------
echo
info "Python library checks"
check_py() {  # $1 = import name, $2 = friendly, $3 = fix
  if python3 -c "import $1" 2>/dev/null; then ok "$2"; else warn "$2 missing — $3"; fi
}
check_py fastapi      "fastapi"            "pip install --break-system-packages fastapi"
check_py uvicorn      "uvicorn"            "pip install --break-system-packages 'uvicorn[standard]'"
check_py picamera2    "picamera2"          "sudo apt install python3-picamera2 (use system python3)"
check_py cv2          "opencv (cv2)"       "sudo apt install python3-opencv"
check_py numpy        "numpy"              "sudo apt install python3-numpy"
check_py adafruit_pca9685 "adafruit PCA9685" "pip install --break-system-packages adafruit-circuitpython-pca9685 adafruit-blinka"

# App self-test: import modules and report mock vs real hardware
echo
info "App self-test"
( cd "$APP_DIR" && python3 - <<'PY' 2>/dev/null
import hardware, illumination, opto, tracker, closed_loop, camera
print("  PCA9685:", hardware.pca.message)
print("  opto:", "PCA9685" if opto.controller.state.get("hw") else "MOCK")
print("  camera:", camera.camera.message)
print("  tracker + closed-loop: loaded")
PY
) && ok "app modules import cleanly" || warn "app modules failed to import — see errors above"

# --- 4. Summary ----------------------------------------------------------
echo
info "════════ Summary ════════"
if [ ${#ISSUES[@]} -eq 0 ]; then
  ok "No issues found. You're ready."
else
  echo -e "${Y}${#ISSUES[@]} thing(s) to look at:${N}"
  for i in "${ISSUES[@]}"; do echo "   - $i"; done
fi
if [ "$REBOOT_NEEDED" -eq 1 ]; then
  echo
  echo -e "${Y}⚠ Config changed — reboot before the PWM/I2C checks will pass:${N}"
  echo "     sudo reboot"
  echo "   then re-run:  bash setup.sh check"
fi

# --- 5. Optional launch --------------------------------------------------
if [ "$MODE" = "run" ] && [ "$REBOOT_NEEDED" -eq 0 ]; then
  echo
  info "Launching FlyBox at http://$(hostname -I | awk '{print $1}'):8000  (Ctrl-C to stop)"
  # auto-open the browser once the server is accepting connections
  ( for _ in $(seq 1 60); do
      (exec 3<>/dev/tcp/localhost/8000) 2>/dev/null && { exec 3>&- ; break; }
      sleep 0.3
    done
    for b in xdg-open sensible-browser chromium-browser firefox; do
      command -v "$b" >/dev/null 2>&1 && { "$b" "http://localhost:8000" >/dev/null 2>&1 & break; }
    done ) &
  cd "$APP_DIR" && exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
elif [ "$MODE" = "run" ]; then
  echo -e "\n${Y}Reboot first, then: bash setup.sh run${N}"
else
  echo
  echo "Next:  bash setup.sh run    # to start the web app"
fi
