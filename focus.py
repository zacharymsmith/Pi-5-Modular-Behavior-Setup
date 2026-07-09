#!/usr/bin/env python3
"""
FlyBox focus helper — a big, standalone live preview of the HQ camera with a
live sharpness meter, so you can dial in the lens focus.

    python3 focus.py

Turn the lens ring until the FOCUS number (and the green bar) is as high as
possible. The number is the variance-of-Laplacian sharpness of the centre region.

Keys:
    q / Esc   quit
    f         toggle fullscreen (biggest preview)
    z         toggle 2x centre zoom (focus on the middle precisely)
    r         reset the peak-hold

NOTE: run this on its own — stop the FlyBox web app first (Ctrl-C in its
terminal), because only one process can own the camera at a time.
"""
import time
import cv2
import numpy as np

WIN = "FlyBox Focus  —  maximise the FOCUS score"

try:
    from picamera2 import Picamera2
    _HW = True
except Exception as e:                       # pragma: no cover
    _HW = False
    print("picamera2 not available (running mock):", e)


def sharpness(gray):
    """Variance of the Laplacian — higher = sharper/in-focus."""
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def open_camera():
    cam = Picamera2()
    # 2028x1520 = full field-of-view binned mode (~40 fps), plenty for focusing
    cfg = cam.create_preview_configuration(main={"size": (1520, 1140), "format": "RGB888"})
    cam.configure(cfg)
    cam.start()
    time.sleep(0.5)
    return cam


def main():
    cam = open_camera() if _HW else None
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    fullscreen, zoom = True, False
    peak, peak_t = 0.0, time.time()

    while True:
        if _HW:
            frame = cam.capture_array("main")
        else:
            frame = np.random.randint(60, 200, (1140, 1520, 3), np.uint8)

        if zoom:
            h, w = frame.shape[:2]
            cw, ch = w // 4, h // 4
            crop = frame[h // 2 - ch:h // 2 + ch, w // 2 - cw:w // 2 + cw]
            frame = cv2.resize(crop, (w, h))

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        centre = gray[h // 3:2 * h // 3, w // 3:2 * w // 3]   # score the middle
        score = sharpness(centre)
        if score > peak or (time.time() - peak_t) > 5:
            peak, peak_t = max(score, 1.0), time.time()

        disp = frame
        # focus bar (relative to the running peak)
        frac = min(1.0, score / max(peak, 1.0))
        cv2.rectangle(disp, (20, 20), (20 + int(frac * (w - 40)), 54), (0, 255, 120), -1)
        cv2.rectangle(disp, (20, 20), (w - 20, 54), (255, 255, 255), 2)
        cv2.putText(disp, f"FOCUS {score:8.0f}   peak {peak:8.0f}", (24, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(disp, f"FOCUS {score:8.0f}   peak {peak:8.0f}", (24, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 120), 2, cv2.LINE_AA)
        cv2.rectangle(disp, (w // 3, h // 3), (2 * w // 3, 2 * h // 3), (0, 200, 255), 1)
        cv2.putText(disp, "turn lens to MAXIMISE  |  q quit  f fullscreen  z zoom  r reset",
                    (24, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(disp, "turn lens to MAXIMISE  |  q quit  f fullscreen  z zoom  r reset",
                    (24, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow(WIN, disp)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord('q'), 27):
            break
        elif k == ord('f'):
            fullscreen = not fullscreen
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
        elif k == ord('z'):
            zoom = not zoom
        elif k == ord('r'):
            peak, peak_t = 1.0, time.time()

    if cam is not None:
        cam.stop()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
