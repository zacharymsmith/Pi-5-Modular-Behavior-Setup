#!/usr/bin/env python3
"""
FlyBox focus helper — a big NATIVE live preview of the HQ camera with a live
sharpness meter, so you can dial in the lens focus.

    python3 focus.py

Turn the lens ring until the FOCUS number (and the green bar) is as high as
possible — that's sharpest focus. It scores the centre region (where the arena
sits) and holds a running peak. Maximise the preview window for the biggest view.
Quit with Ctrl-C.

Uses picamera2's own hardware preview (not OpenCV windows), because the Pi's
apt OpenCV is the headless build with no GUI. Run this on its own — stop the
FlyBox web app first (Ctrl-C in its terminal), since only one process can own
the camera at a time.

If no preview backend is available (e.g. headless / over some remote sessions),
use the live "Focus" readout that's now in the web app's Camera Options instead.
"""
import sys
import time
import signal

import numpy as np
import cv2
from picamera2 import Picamera2, Preview, MappedArray

picam2 = Picamera2()
# 1332x990 native-ish mode: sharp detail, plenty of speed for focusing
cfg = picam2.create_preview_configuration(main={"size": (1332, 990)})
picam2.configure(cfg)

_state = {"peak": 1.0, "pt": time.time()}


def _draw(request):
    with MappedArray(request, "main") as m:
        a = m.array
        ch = a.shape[2] if a.ndim == 3 else 1
        gray = cv2.cvtColor(a, cv2.COLOR_BGRA2GRAY if ch == 4 else cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        y1, y2, x1, x2 = int(h * .15), int(h * .85), int(w * .15), int(w * .85)
        c = gray[y1:y2, x1:x2]                       # wide centre region (needs texture)
        s = float(cv2.Laplacian(c, cv2.CV_64F).var())
        if s > _state["peak"] or (time.time() - _state["pt"]) > 5:
            _state["peak"], _state["pt"] = max(s, 1.0), time.time()
        col = (0, 255, 120, 255) if ch == 4 else (0, 255, 120)
        white = (255, 255, 255, 255) if ch == 4 else (255, 255, 255)
        cyan = (0, 200, 255, 255) if ch == 4 else (0, 200, 255)
        frac = min(1.0, s / _state["peak"])
        cv2.rectangle(a, (20, 20), (20 + int(frac * (w - 40)), 54), col, -1)
        cv2.rectangle(a, (20, 20), (w - 20, 54), white, 2)
        cv2.putText(a, f"FOCUS {s:8.0f}   peak {_state['peak']:8.0f}", (24, 102),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 2, cv2.LINE_AA)
        cv2.rectangle(a, (x1, y1), (x2, y2), cyan, 1)


picam2.pre_callback = _draw

started = None
for pv in (Preview.QTGL, Preview.QT, Preview.DRM):
    try:
        picam2.start_preview(pv)
        started = pv
        break
    except Exception as e:
        print(f"  preview backend {pv} unavailable: {e}")

if started is None:
    print("No display/preview backend available. Use the web app's Camera Options "
          "'Focus' readout instead (it works over Pi Connect).")
    sys.exit(1)

picam2.start()
print(f"\nFocus preview running ({started}). Maximise the window, turn the lens to "
      f"peak the FOCUS number. Ctrl-C to quit.\n")
try:
    signal.pause()
except KeyboardInterrupt:
    pass
finally:
    picam2.stop()
