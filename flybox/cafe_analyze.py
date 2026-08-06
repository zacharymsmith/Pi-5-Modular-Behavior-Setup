"""Offline CAFE visit analysis: over the full recording, detect fly positions via a
full-video median background and score time/visits at the LEFT vs RIGHT capillary port.
Robust to still feeding flies (bg is built from the whole video, so it's fly-free)."""
import sys, cv2, numpy as np, csv, os

VID = "../test_examples/20260806_095436_cafe_test_06Aug2026/20260806_095436.mp4"
BG = "/tmp/cafe_bg.npy"
CSV = "/tmp/cafe_visits.csv"
SC = 0.5                       # detect at half-res for speed
L = (560, 384); R = (1200, 360); PORT_R = 62      # full-res port tips + radius (px)
STRIDE = 5                     # sample every 5th frame -> 1 Hz at 5 fps recording
ARENA = (500, 100, 1250, 760)  # x0,y0,x1,y1 crop that holds dish + both ports


def detect(gray, bg):
    diff = cv2.subtract(bg, gray)              # flies are DARKER than the bright dish
    x0, y0, x1, y1 = [int(v * SC) for v in ARENA]
    m = np.zeros_like(diff); m[y0:y1, x0:x1] = diff[y0:y1, x0:x1]
    _, th = cv2.threshold(m, 28, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    flies = []
    amin, amax = 10 * SC * SC * 4, 3000 * SC * SC
    for c in cnts:
        a = cv2.contourArea(c)
        if a < amin or a > amax: continue
        M = cv2.moments(c)
        if M["m00"] == 0: continue
        flies.append((M["m10"] / M["m00"] / SC, M["m01"] / M["m00"] / SC))  # full-res coords
    flies.sort(key=lambda f: -1)
    return flies[:3]


def main(start, end):
    bg = np.load(BG)
    bg = cv2.resize(bg, (0, 0), fx=SC, fy=SC)
    cap = cv2.VideoCapture(VID)
    fps = cap.get(cv2.CAP_PROP_FPS) or 5.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    new = not os.path.exists(CSV)
    fh = open(CSV, "a", newline=""); w = csv.writer(fh)
    if new: w.writerow(["frame", "t_s", "nflies", "dL", "dR", "atL", "atR"])
    fi = start
    while fi < end:
        ok, f = cap.read()
        if not ok: break
        if (fi - start) % STRIDE == 0:
            g = cv2.cvtColor(cv2.resize(f, (0, 0), fx=SC, fy=SC), cv2.COLOR_BGR2GRAY)
            flies = detect(g, bg)
            dL = min([((x-L[0])**2+(y-L[1])**2)**.5 for x, y in flies], default=9999)
            dR = min([((x-R[0])**2+(y-R[1])**2)**.5 for x, y in flies], default=9999)
            w.writerow([fi, round(fi/fps, 1), len(flies), round(dL, 1), round(dR, 1),
                        int(dL <= PORT_R), int(dR <= PORT_R)])
        fi += 1
    fh.close(); cap.release()
    print(f"done {start}..{fi}")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
